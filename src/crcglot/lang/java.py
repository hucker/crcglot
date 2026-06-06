"""Java CRC generator.

Emits a single ``.java`` file declaring one ``public final class`` (a
flat container) holding, for every requested algorithm, five
``public static`` methods:

  - ``<fname>_init()`` -- return the starting state
  - ``<fname>_update(state, data)`` -- feed bytes, return new state
  - ``<fname>_finalize(state)`` -- apply output reflection + xorout
  - ``<fname>(data)`` -- one-shot wrapper (init + update + finalize)
  - ``<fname>_self_test()`` -- ``true`` iff the algorithm reproduces the
    reveng catalogue's canonical check value

Unlike the C# generator (one ``class`` per algorithm), Java allows only
one public top-level class per file, so crcglot puts every algorithm's
methods FLAT in one container class.  The container is named ``CrcGlot``
by default; the CLI/``combine_java`` rename it from ``file=STEM`` so the
public class always matches its file.  Because the methods share one class
scope, the lookup tables are named per symbol (``crcglot_table_<fname>`` /
``crcglot_slice_<fname>``) the same way C / Rust / TypeScript do, so
several algorithms coexist without colliding.

Java integer specifics handled here (Java has NO unsigned types):

* State type is ``int`` for width <= 32, ``long`` for width 64.  ``byte``
  is signed, so every ``data[i]`` is zero-extended with ``& 0xFF``.
* CRC right shifts use ``>>>`` (logical), never ``>>`` (arithmetic /
  sign-extending).
* Masking: ``& 0xFF`` (width 8), ``& 0xFFFF`` (width 16); widths 32 / 64
  wrap naturally in ``int`` / ``long``.
* A 32-bit hex literal like ``0xCBF43926`` is a legal ``int`` bit pattern
  even past ``Integer.MAX_VALUE`` -- no ``u`` / ``UL`` suffix (unlike C#);
  width-64 literals take an ``L`` suffix.  ``==`` compares bit patterns,
  so ``_self_test()`` matches the signed-int check literal at width 32.
* Slice-by-8 at width 64 widens each byte to ``long`` *before* shifting
  (``int << 56`` masks the count) and casts array indices to ``int``
  (a ``long`` can't index a Java array).

Bundle size limit: because every algorithm shares one class, their table
literals all land in that class's static initializer, and Java caps a
single method (``<clinit>`` included) at 64 KB of bytecode.  Bundling a
handful of algorithms is fine; bundling dozens of *table* / *slice8*
variants into one file can exceed the limit and fail to compile -- split
such a bundle across files, or use the bit-by-bit variant (no tables).

Verified by ``tests.test_java_gen`` (structural + ``javac`` / ``java``
execution).
"""

# ruff: noqa: F541  - f-strings without placeholders used for code alignment

from __future__ import annotations

from typing import Literal

from crcglot._helpers import (
    _build_slice8_tables,
    _build_table,
    _func_name,
    _variant_to_flags,
)
from crcglot.catalogue import ALGORITHMS, AlgorithmInfo, _reflect
from crcglot.comments import (
    AlgoMeta,
    DocParam,
    UsageExample,
    comment_style_for,
    standard_doc_blocks,
)

_DEFAULT_CLASS = "CrcGlot"


def _java_type(width: int) -> str:
    """Java signed integer type for the algorithm width: int (<=32) / long."""
    return "long" if width == 64 else "int"


def _java_hex(value: int, width: int) -> str:
    """Format an integer as a Java hex literal.

    Width <= 32 emits a plain ``0x..`` int literal (a 32-bit hex literal is
    a legal int bit pattern in Java even above ``Integer.MAX_VALUE``); width
    64 appends ``L``.  No ``u`` / ``UL`` suffixes -- Java has no unsigned
    types.
    """
    hex_w = (width + 3) // 4
    body = f"0x{value:0{hex_w}X}"
    return f"{body}L" if width == 64 else body


def _check_input_bytes_java() -> str:
    """Emit ``new byte[] { 0x31, ... }`` for ASCII ``"123456789"``.

    The bytes 0x31..0x39 all fit in Java's signed ``byte`` range, so no
    cast is needed in the array initializer.
    """
    return "new byte[] { " + ", ".join(f"0x{ord(c):02X}" for c in "123456789") + " }"


def _format_table_java(table: list[int], width: int, jtype: str) -> str:
    """Format a lookup table as a Java ``private static final`` array.

    Emits the fixed placeholder name ``CRC_TABLE`` -- rewritten to a
    per-symbol name at the assembly point.
    """
    hex_w = (width + 3) // 4
    suffix = "L" if width == 64 else ""
    lines = [f"    private static final {jtype}[] CRC_TABLE = {{"]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}{suffix}"
            for i in range(row, min(row + 8, 256))
        )
        lines.append(f"        {vals},")
    lines.append("    };")
    return "\n".join(lines)


def _format_slice8_tables_java(
    tables: list[list[int]], width: int, jtype: str,
) -> str:
    """Format the 8 slice-by-8 tables as a Java jagged 2D array.

    Java has no rectangular array type, so this uses ``jtype[][]`` with one
    inner ``{ // Tn ... }`` block per table.  Placeholder name
    ``CRC_SLICE_TABLES`` is rewritten per symbol at assembly.
    """
    hex_w = (width + 3) // 4
    suffix = "L" if width == 64 else ""
    lines = [f"    private static final {jtype}[][] CRC_SLICE_TABLES = {{"]
    for t_idx, table in enumerate(tables):
        lines.append(f"        {{  // T{t_idx}")
        for row in range(0, 256, 8):
            vals = ", ".join(
                f"0x{table[i]:0{hex_w}X}{suffix}"
                for i in range(row, min(row + 8, 256))
            )
            lines.append(f"            {vals},")
        lines.append("        },")
    lines.append("    };")
    return "\n".join(lines)


def _update_loop_java(w: int, poly: int, refin: bool, table: bool) -> list[str]:
    """Emit the per-byte main-loop lines for the update method.

    Bytes are zero-extended (``& 0xFF``); right shifts are logical
    (``>>>``); widths 8 / 16 mask their left-shifts, 32 / 64 wrap natively.
    """
    if table:
        if w == 8:
            return [
                "        for (byte b : data) {",
                "            crc = CRC_TABLE[(crc ^ (b & 0xFF)) & 0xFF];",
                "        }",
            ]
        # At width 64 ``crc`` is a long, so the table index is a long and
        # must be cast to int (a Java array can't be indexed by a long).
        cast = "(int)" if w == 64 else ""
        if refin:
            return [
                "        for (byte b : data) {",
                f"            crc = CRC_TABLE[{cast}((crc ^ (b & 0xFF)) & 0xFF)] ^ (crc >>> 8);",
                "        }",
            ]
        left = "((crc << 8) & 0xFFFF)" if w == 16 else "(crc << 8)"
        return [
            "        for (byte b : data) {",
            f"            crc = CRC_TABLE[{cast}(((crc >>> {w - 8}) ^ (b & 0xFF)) & 0xFF)] ^ {left};",
            "        }",
        ]
    if refin:
        ref_poly = _java_hex(_reflect(poly, w), w)
        return [
            "        for (byte b : data) {",
            "            crc ^= (b & 0xFF);",
            "            for (int i = 0; i < 8; i++) {",
            "                if ((crc & 1) != 0)",
            f"                    crc = (crc >>> 1) ^ {ref_poly};",
            "                else",
            "                    crc >>>= 1;",
            "            }",
            "        }",
        ]
    # bitwise, non-reflected
    if w == 8:
        b_aligned = "(b & 0xFF)"
    elif w == 64:
        b_aligned = "((long)(b & 0xFF)) << 56"
    else:
        b_aligned = f"(b & 0xFF) << {w - 8}"
    lines = [
        "        for (byte b : data) {",
        f"            crc ^= {b_aligned};",
        "            for (int i = 0; i < 8; i++) {",
        f"                if ((crc & {_java_hex(1 << (w - 1), w)}) != 0)",
        f"                    crc = (crc << 1) ^ {_java_hex(poly, w)};",
        "                else",
        "                    crc <<= 1;",
    ]
    if w == 8:
        lines.append("                crc &= 0xFF;")
    elif w == 16:
        lines.append("                crc &= 0xFFFF;")
    lines.append("            }")
    lines.append("        }")
    return lines


def _update_loop_java_slice8(w: int, refin: bool) -> list[str]:
    """Emit the slice-by-8 main loop + byte-by-byte tail (width 32 / 64).

    Width 64 widens each byte to ``long`` before shifting and casts every
    array index to ``int`` (a ``long`` cannot index a Java array).
    """
    if w == 32:
        if refin:
            return [
                "        int i = 0;",
                "        while (i + 8 <= data.length) {",
                "            int b03 = (data[i] & 0xFF) | ((data[i + 1] & 0xFF) << 8)"
                " | ((data[i + 2] & 0xFF) << 16) | ((data[i + 3] & 0xFF) << 24);",
                "            int b47 = (data[i + 4] & 0xFF) | ((data[i + 5] & 0xFF) << 8)"
                " | ((data[i + 6] & 0xFF) << 16) | ((data[i + 7] & 0xFF) << 24);",
                "            int xored = crc ^ b03;",
                "            crc = CRC_SLICE_TABLES[7][xored & 0xFF]"
                " ^ CRC_SLICE_TABLES[6][(xored >>> 8) & 0xFF]",
                "                ^ CRC_SLICE_TABLES[5][(xored >>> 16) & 0xFF]"
                " ^ CRC_SLICE_TABLES[4][(xored >>> 24) & 0xFF]",
                "                ^ CRC_SLICE_TABLES[3][b47 & 0xFF]"
                " ^ CRC_SLICE_TABLES[2][(b47 >>> 8) & 0xFF]",
                "                ^ CRC_SLICE_TABLES[1][(b47 >>> 16) & 0xFF]"
                " ^ CRC_SLICE_TABLES[0][(b47 >>> 24) & 0xFF];",
                "            i += 8;",
                "        }",
                "        while (i < data.length) {",
                "            crc = CRC_SLICE_TABLES[0][(crc ^ (data[i] & 0xFF)) & 0xFF]"
                " ^ (crc >>> 8);",
                "            i++;",
                "        }",
            ]
        return [
            "        int i = 0;",
            "        while (i + 8 <= data.length) {",
            "            int b03 = ((data[i] & 0xFF) << 24) | ((data[i + 1] & 0xFF) << 16)"
            " | ((data[i + 2] & 0xFF) << 8) | (data[i + 3] & 0xFF);",
            "            int b47 = ((data[i + 4] & 0xFF) << 24) | ((data[i + 5] & 0xFF) << 16)"
            " | ((data[i + 6] & 0xFF) << 8) | (data[i + 7] & 0xFF);",
            "            int xored = crc ^ b03;",
            "            crc = CRC_SLICE_TABLES[7][(xored >>> 24) & 0xFF]"
            " ^ CRC_SLICE_TABLES[6][(xored >>> 16) & 0xFF]",
            "                ^ CRC_SLICE_TABLES[5][(xored >>> 8) & 0xFF]"
            " ^ CRC_SLICE_TABLES[4][xored & 0xFF]",
            "                ^ CRC_SLICE_TABLES[3][(b47 >>> 24) & 0xFF]"
            " ^ CRC_SLICE_TABLES[2][(b47 >>> 16) & 0xFF]",
            "                ^ CRC_SLICE_TABLES[1][(b47 >>> 8) & 0xFF]"
            " ^ CRC_SLICE_TABLES[0][b47 & 0xFF];",
            "            i += 8;",
            "        }",
            "        while (i < data.length) {",
            "            int top = (crc >>> 24) & 0xFF;",
            "            crc = CRC_SLICE_TABLES[0][(top ^ (data[i] & 0xFF)) & 0xFF]"
            " ^ (crc << 8);",
            "            i++;",
            "        }",
        ]
    # w == 64: widen bytes to long before shifting; cast indices to int.
    if refin:
        return [
            "        int i = 0;",
            "        while (i + 8 <= data.length) {",
            "            long b = (long)(data[i] & 0xFF) | ((long)(data[i + 1] & 0xFF) << 8)"
            " | ((long)(data[i + 2] & 0xFF) << 16) | ((long)(data[i + 3] & 0xFF) << 24)",
            "                | ((long)(data[i + 4] & 0xFF) << 32) | ((long)(data[i + 5] & 0xFF) << 40)"
            " | ((long)(data[i + 6] & 0xFF) << 48) | ((long)(data[i + 7] & 0xFF) << 56);",
            "            long xored = crc ^ b;",
            "            crc = CRC_SLICE_TABLES[7][(int)(xored & 0xFF)]"
            " ^ CRC_SLICE_TABLES[6][(int)((xored >>> 8) & 0xFF)]",
            "                ^ CRC_SLICE_TABLES[5][(int)((xored >>> 16) & 0xFF)]"
            " ^ CRC_SLICE_TABLES[4][(int)((xored >>> 24) & 0xFF)]",
            "                ^ CRC_SLICE_TABLES[3][(int)((xored >>> 32) & 0xFF)]"
            " ^ CRC_SLICE_TABLES[2][(int)((xored >>> 40) & 0xFF)]",
            "                ^ CRC_SLICE_TABLES[1][(int)((xored >>> 48) & 0xFF)]"
            " ^ CRC_SLICE_TABLES[0][(int)((xored >>> 56) & 0xFF)];",
            "            i += 8;",
            "        }",
            "        while (i < data.length) {",
            "            crc = CRC_SLICE_TABLES[0][(int)((crc ^ (data[i] & 0xFF)) & 0xFF)]"
            " ^ (crc >>> 8);",
            "            i++;",
            "        }",
        ]
    return [
        "        int i = 0;",
        "        while (i + 8 <= data.length) {",
        "            long b = ((long)(data[i] & 0xFF) << 56) | ((long)(data[i + 1] & 0xFF) << 48)"
        " | ((long)(data[i + 2] & 0xFF) << 40) | ((long)(data[i + 3] & 0xFF) << 32)",
        "                | ((long)(data[i + 4] & 0xFF) << 24) | ((long)(data[i + 5] & 0xFF) << 16)"
        " | ((long)(data[i + 6] & 0xFF) << 8) | (long)(data[i + 7] & 0xFF);",
        "            long xored = crc ^ b;",
        "            crc = CRC_SLICE_TABLES[7][(int)((xored >>> 56) & 0xFF)]"
        " ^ CRC_SLICE_TABLES[6][(int)((xored >>> 48) & 0xFF)]",
        "                ^ CRC_SLICE_TABLES[5][(int)((xored >>> 40) & 0xFF)]"
        " ^ CRC_SLICE_TABLES[4][(int)((xored >>> 32) & 0xFF)]",
        "                ^ CRC_SLICE_TABLES[3][(int)((xored >>> 24) & 0xFF)]"
        " ^ CRC_SLICE_TABLES[2][(int)((xored >>> 16) & 0xFF)]",
        "                ^ CRC_SLICE_TABLES[1][(int)((xored >>> 8) & 0xFF)]"
        " ^ CRC_SLICE_TABLES[0][(int)(xored & 0xFF)];",
        "            i += 8;",
        "        }",
        "        while (i < data.length) {",
        "            int top = (int)((crc >>> 56) & 0xFF);",
        "            crc = CRC_SLICE_TABLES[0][(top ^ (data[i] & 0xFF)) & 0xFF]"
        " ^ (crc << 8);",
        "            i++;",
        "        }",
    ]


def _self_test_java(fname, check, width, style, docs) -> list[str]:
    """Emit a static method returning true iff the one-shot matches reveng."""
    return [
        *style.doc_block(docs["self_test"], indent=4),
        f"    public static boolean {fname}_self_test() {{",
        f"        return {fname}({_check_input_bytes_java()}) == "
        f"{_java_hex(check, width)};",
        f"    }}",
    ]


def combine_java(outputs: list[str], stem: str | None = None) -> str:
    """Combine several Java outputs into one flat container class.

    Each output is a ``public final class CrcGlot { ... }``.  Java permits
    only one public top-level class per file, so the members of each output
    are lifted out (everything between the class's opening ``{`` and its
    closing ``}``) and re-emitted under ONE ``public final class <stem>``.
    Per-symbol table names and algorithm-named methods keep the members
    collision-free.  Used for every Java emission (single algorithm too), so
    the container name flows from one place.

    Args:
        outputs: Individual :func:`generate_java` results, one per algorithm.
        stem: Container class name (Java requires it to match the file name);
            defaults to ``"CrcGlot"``.

    Returns:
        One ``public final class <stem>`` source string with every algorithm.

    Examples:
        >>> a = generate_java("crc32")
        >>> b = generate_java("crc16-modbus")
        >>> combine_java([a, b], "MyCrcs").count("public final class")
        1
    """
    cls = stem or _DEFAULT_CLASS
    members = [
        o.split("{", 1)[1].rsplit("}", 1)[0].strip("\n")
        for o in outputs
    ]
    return (
        "// crcglot-generated CRC bundle\n"
        "//\n"
        f"// One-shot:  call {cls}.<algorithm>(data).\n"
        "\n"
        f"public final class {cls} {{\n"
        + "\n\n".join(members)
        + "\n}\n"
    )


def generate_java(
    name: str,
    symbol: str | None = None,
    variant: Literal["bitwise", "table", "slice8"] = "bitwise",
    comment_style: str = "plain",
) -> str | None:
    """Look up a CRC algorithm by name and generate Java source for it.

    Thin wrapper around :func:`generate_java_from_entry`; use the latter
    directly when generating from a custom (non-catalogue) algorithm spec.

    Examples:
        >>> src = generate_java("crc32")
        >>> "public static int crc32(byte[] data)" in src
        True
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_java_from_entry(
        name, algo, symbol=symbol, variant=variant, comment_style=comment_style,
    )


def generate_java_from_entry(
    name: str,
    algo: AlgorithmInfo,
    symbol: str | None = None,
    variant: Literal["bitwise", "table", "slice8"] = "bitwise",
    comment_style: str = "plain",
) -> str:
    """Generate Java source from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments and as the default
            function-name source).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        symbol: Optional override for the emitted method-name stem
            (default: ``_func_name(name)``).
        variant: ``"bitwise"`` (default), ``"table"`` (256-entry lookup),
            or ``"slice8"`` (8 tables; width 32 / 64 only, else
            ``ValueError``).

    Returns:
        Java source declaring a ``public final class`` (named ``CrcGlot``;
        :func:`combine_java` renames it from the file stem).
    """
    table, slice8 = _variant_to_flags(variant)
    w = algo.width
    poly = algo.poly
    refin = algo.refin
    refout = algo.refout
    xorout = algo.xorout
    check = algo.check
    desc = algo.desc
    fname = symbol if symbol else _func_name(name)
    jtype = _java_type(w)

    if slice8 and w not in (32, 64):
        raise ValueError(
            f"variant='slice8' requires width=32 or width=64 (got width={w}). "
            "Slice-by-8 is a high-throughput optimization that only "
            "makes sense at those widths; smaller CRCs would need a "
            "different chunking scheme."
        )

    init_state = _reflect(algo.init, w) if refin else algo.init

    cls = _DEFAULT_CLASS
    # Java has no unsigned integer types; width-32 CRCs come back as a signed
    # int whose bit pattern is correct but whose decimal value is negative
    # when the high bit is set.  Surface that in the header and on the two
    # functions that hand a finished value back to the caller.
    signed32 = w == 32
    unsigned_note = (
        "width-32 results are a signed int bit pattern (negative when the "
        "high bit is set); call Integer.toUnsignedLong(x) for a 0..2^32-1 value.",
    )
    style = comment_style_for("java", comment_style)
    meta = AlgoMeta(
        name=name, desc=desc, width=w, poly=poly, init=algo.init, refin=refin,
        refout=refout, xorout=xorout, check=check, variant=variant,
    )
    usage = UsageExample(
        streaming=(
            f"{jtype} s = {cls}.{fname}_init();",
            f"s = {cls}.{fname}_update(s, chunk);  // over each chunk",
            f"{jtype} crc = {cls}.{fname}_finalize(s);",
        ),
        oneshot=f"{cls}.{fname}(data)",
        selftest=f"{cls}.{fname}_self_test()",
        selftest_returns="returns true on success",
        caveats=unsigned_note if signed32 else (),
    )
    docs = standard_doc_blocks(
        fname, state_type=jtype,
        data_params=(DocParam("data", "the message bytes."),),
        selftest_returns="true",
        refin=refin, refout=refout, xorout=xorout,
        extra_notes=(
            {"finalize": unsigned_note, "oneshot": unsigned_note}
            if signed32 else None
        ),
    )

    lines: list[str] = []
    lines += style.file_header(meta, usage)
    lines.append(f"")
    lines.append(f"public final class {cls} {{")

    if slice8:
        slice_tables = _build_slice8_tables(w, poly, refin)
        lines.append(_format_slice8_tables_java(slice_tables, w, jtype))
        lines.append("")
    elif table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_java(tbl, w, jtype))
        lines.append("")

    # ----- <fname>_init() -----
    lines += style.doc_block(docs["init"], indent=4)
    lines.append(f"    public static {jtype} {fname}_init() {{")
    lines.append(f"        return {_java_hex(init_state, w)};")
    lines.append(f"    }}")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines += style.doc_block(docs["update"], indent=4)
    lines.append(
        f"    public static {jtype} {fname}_update({jtype} state, byte[] data) {{"
    )
    lines.append(f"        {jtype} crc = state;")
    if slice8:
        lines.extend(_update_loop_java_slice8(w, refin))
    else:
        lines.extend(_update_loop_java(w, poly, refin, table))
    lines.append(f"        return crc;")
    lines.append(f"    }}")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines += style.doc_block(docs["finalize"], indent=4)
    lines.append(f"    public static {jtype} {fname}_finalize({jtype} state) {{")
    if refout != refin:
        one = "1L" if w == 64 else "1"
        zero = "0L" if w == 64 else "0"
        lines.append(f"        // reflect output (refout != refin)")
        lines.append(f"        {jtype} reflected = {zero};")
        lines.append(f"        for (int k = 0; k < {w}; k++)")
        lines.append(
            f"            reflected |= ((state >>> k) & {one}) << ({w - 1} - k);"
        )
        lines.append(f"        state = reflected;")
    if xorout:
        lines.append(f"        return state ^ {_java_hex(xorout, w)};")
    else:
        lines.append(f"        return state;")
    lines.append(f"    }}")
    lines.append("")

    # ----- one-shot wrapper -----
    lines += style.doc_block(docs["oneshot"], indent=4)
    lines.append(f"    public static {jtype} {fname}(byte[] data) {{")
    lines.append(
        f"        return {fname}_finalize({fname}_update({fname}_init(), data));"
    )
    lines.append(f"    }}")
    lines.append("")

    # ----- self-test -----
    lines.extend(_self_test_java(fname, check, w, style, docs))

    lines.append(f"}}")

    source = "\n".join(lines)
    # Namespace the lookup tables per symbol so multiple algorithms can
    # share the one flat container class without colliding.
    source = source.replace("CRC_SLICE_TABLES", f"crcglot_slice_{fname}")
    source = source.replace("CRC_TABLE", f"crcglot_table_{fname}")
    return source
