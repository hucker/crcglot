"""C# CRC generator.

Emits a complete ``.cs`` file declaring a ``public static class``
containing five methods:

  - ``<fname>_init()`` -- return the starting state
  - ``<fname>_update(state, data)`` -- feed bytes, return new state
  - ``<fname>_finalize(state)`` -- apply output reflection + xorout
  - ``<fname>(data)`` -- one-shot wrapper (init + update + finalize)
  - ``<fname>_self_test()`` -- True iff the algorithm reproduces the
    reveng catalogue's canonical check value

The streaming primitives let callers compute a CRC over data that
arrives in chunks (file streams, network buffers, log shards) without
buffering everything.  The one-shot wrapper preserves the simple API
for the common case.  ``_self_test()`` lets a downstream caller
verify the implementation works on their .NET runtime before trusting
its output -- callable from xUnit / NUnit / MSTest, a startup
self-check, or anywhere.

C# integer specifics handled here:

* ``byte ^ byte`` evaluates to ``int``, so direct assignment to a
  narrower type requires an explicit cast.  The generator emits
  ``(byte)(...)`` / ``(ushort)(...)`` wrappers where the destination is
  narrower than ``int``.  Compound assignment (``crc ^= b``) implicitly
  casts back, so we use compound ops wherever possible.
* Hex literals exceeding ``int.MaxValue`` need ``u`` (uint) or ``UL``
  (ulong) suffixes; the per-width hex helper adds them.

Class name defaults to the PascalCase'd function name so multiple
generated files coexist in the same project without collision.  The
file emits ``using System;`` to keep boilerplate minimal -- the only
.NET BCL surface needed beyond the language primitives.

Verified at build time by ``tests.test_csharp_gen.TestGenerateCSharp``
(structural) and ``TestGeneratedCSharpExecutes`` (compile + run via
``dotnet script`` or ``dotnet run``; slow-marked).
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


def _cs_hex(value: int, width: int) -> str:
    """Format an integer as a C# hex literal with the right type suffix.

    Width 8 / 16 fit in ``int`` without a suffix; width 32 uses ``u``
    (uint), width 64 uses ``UL`` (ulong).  Without the suffix, the
    compiler infers ``int`` and rejects values above ``int.MaxValue``.
    """
    hex_w = (width + 3) // 4
    body = f"0x{value:0{hex_w}X}"
    if width <= 16:
        return body
    if width <= 32:
        return f"{body}u"
    return f"{body}UL"


def _cs_type(width: int) -> str:
    """Pick the C# unsigned integer type for the algorithm width."""
    if width <= 8:
        return "byte"
    if width <= 16:
        return "ushort"
    if width <= 32:
        return "uint"
    return "ulong"


def _cs_pascal_class(fname: str) -> str:
    """Derive a PascalCase class name from a snake_case function name.

    ``crc16_modbus`` -> ``Crc16Modbus``; ``crc32`` -> ``Crc32``.
    Used as the default container class so multiple generated files
    don't collide on a generic name like ``Crc``.
    """
    parts = fname.split("_")
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _cs_cast(width: int, expr: str) -> str:
    """Wrap ``expr`` with an explicit cast to the state's unsigned type.

    Always emitted, not just for byte / ushort.  The reason it's needed
    for ``uint`` / ``ulong`` too: ``byte << 24`` evaluates to ``int``
    (because ``<<`` promotes its left operand), and compound assignment
    ``uint ^= int`` is not auto-cast-back-able (the int -> uint
    conversion is not implicit, and ``^=`` is not a shift operator so
    the compound-assignment shift exception doesn't rescue it).
    Wrapping the rhs with ``(uint)(...)`` is an explicit conversion
    that compiles cleanly.  The cast is redundant when the rhs is
    already of the state's type (e.g. ``(crc << 1) ^ poly`` at uint),
    but C# accepts redundant casts without warning, so it's cheaper
    to always emit than to thread "do I need it here" through every
    call site.
    """
    if width <= 8:
        return f"(byte)({expr})"
    if width <= 16:
        return f"(ushort)({expr})"
    if width <= 32:
        return f"(uint)({expr})"
    return f"(ulong)({expr})"


def _cs_byte_lit(b: int) -> str:
    """C# byte hex literal: ``0xNN``."""
    return f"0x{b:02X}"


def _check_input_bytes_cs() -> str:
    """Emit ``new byte[] { 0x31, 0x32, ... }`` for ASCII ``"123456789"``.

    Avoids pulling in ``System.Text.Encoding`` just to spell the canonical
    reveng check string -- the bytes are constant and self-evidently the
    ASCII digits 1-9.
    """
    return "new byte[] { " + ", ".join(
        _cs_byte_lit(ord(c)) for c in "123456789"
    ) + " }"


def _format_table_csharp(table: list[int], width: int, cstype: str) -> str:
    """Format a lookup table as a C# ``private static readonly`` array."""
    hex_w = (width + 3) // 4
    suffix = "u" if width <= 32 and width > 16 else ("UL" if width > 32 else "")
    lines = [f"    private static readonly {cstype}[] _crcTable = new {cstype}[] {{"]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}{suffix}"
            for i in range(row, min(row + 8, 256))
        )
        lines.append(f"        {vals},")
    lines.append("    };")
    return "\n".join(lines)


def _format_slice8_tables_csharp(
    tables: list[list[int]], width: int, cstype: str,
) -> str:
    """Format the 8 slice-by-8 tables as a C# 2D ``private static readonly``
    array.  Uses a multi-dimensional array (``[,]``) rather than a jagged
    array (``[][]``) -- compiles to a single contiguous block, slightly
    faster indexing than chasing pointers through outer-array entries.
    """
    hex_w = (width + 3) // 4
    suffix = "u" if width <= 32 and width > 16 else ("UL" if width > 32 else "")
    lines = [
        f"    private static readonly {cstype}[,] _crcSliceTables "
        f"= new {cstype}[,] {{",
    ]
    for t_idx, table in enumerate(tables):
        lines.append(f"        {{  // T{t_idx}")
        for row in range(0, 256, 8):
            vals = ", ".join(
                f"0x{table[i]:0{hex_w}X}{suffix}"
                for i in range(row, min(row + 8, 256))
            )
            lines.append(f"            {vals},")
        lines.append(f"        }},")
    lines.append("    };")
    return "\n".join(lines)


def _update_loop_csharp_slice8(
    w: int, refin: bool, cstype: str,
) -> list[str]:
    """Emit the per-8-byte slice-by-8 main loop + byte-by-byte tail for C#.

    Variable ``crc`` (of type ``cstype``) is assumed to already hold the
    incoming state.  Walks ``data`` 8 bytes at a time via 8 chained table
    lookups, then falls back to single-byte via T0 for any 1-7 trailing
    bytes.  Only valid for w == 32 or w == 64.

    C# specifics:
    - ``byte << 24`` evaluates to ``int``; the ``uint`` state requires
      an explicit cast.  For w=64, ``byte << 56`` would silently lose
      bits (int shift count masked to 5 bits), so widen ``data[i]`` to
      the state type *before* shifting.
    - Multi-dim array indexing: ``_crcSliceTables[t, byte_value]`` --
      byte_value widens to int implicitly.
    """
    if w == 32:
        if refin:
            return [
                "        int i = 0;",
                "        while (i + 8 <= data.Length) {",
                "            uint b03 = (uint)data[i] | (uint)data[i + 1] << 8"
                " | (uint)data[i + 2] << 16 | (uint)data[i + 3] << 24;",
                "            uint b47 = (uint)data[i + 4] | (uint)data[i + 5] << 8"
                " | (uint)data[i + 6] << 16 | (uint)data[i + 7] << 24;",
                "            uint xored = crc ^ b03;",
                "            crc = _crcSliceTables[7, (byte)xored]"
                " ^ _crcSliceTables[6, (byte)(xored >> 8)]",
                "                ^ _crcSliceTables[5, (byte)(xored >> 16)]"
                " ^ _crcSliceTables[4, (byte)(xored >> 24)]",
                "                ^ _crcSliceTables[3, (byte)b47]"
                " ^ _crcSliceTables[2, (byte)(b47 >> 8)]",
                "                ^ _crcSliceTables[1, (byte)(b47 >> 16)]"
                " ^ _crcSliceTables[0, (byte)(b47 >> 24)];",
                "            i += 8;",
                "        }",
                "        while (i < data.Length) {",
                "            crc = _crcSliceTables[0, (byte)(crc ^ data[i])]"
                " ^ (crc >> 8);",
                "            i++;",
                "        }",
            ]
        return [
            "        int i = 0;",
            "        while (i + 8 <= data.Length) {",
            "            uint b03 = (uint)data[i] << 24 | (uint)data[i + 1] << 16"
            " | (uint)data[i + 2] << 8 | (uint)data[i + 3];",
            "            uint b47 = (uint)data[i + 4] << 24 | (uint)data[i + 5] << 16"
            " | (uint)data[i + 6] << 8 | (uint)data[i + 7];",
            "            uint xored = crc ^ b03;",
            "            crc = _crcSliceTables[7, (byte)(xored >> 24)]"
            " ^ _crcSliceTables[6, (byte)(xored >> 16)]",
            "                ^ _crcSliceTables[5, (byte)(xored >> 8)]"
            " ^ _crcSliceTables[4, (byte)xored]",
            "                ^ _crcSliceTables[3, (byte)(b47 >> 24)]"
            " ^ _crcSliceTables[2, (byte)(b47 >> 16)]",
            "                ^ _crcSliceTables[1, (byte)(b47 >> 8)]"
            " ^ _crcSliceTables[0, (byte)b47];",
            "            i += 8;",
            "        }",
            "        while (i < data.Length) {",
            "            byte top = (byte)(crc >> 24);",
            "            crc = _crcSliceTables[0, (byte)(top ^ data[i])]"
            " ^ (crc << 8);",
            "            i++;",
            "        }",
        ]
    # w == 64.  Each byte must be widened to ulong BEFORE shifting --
    # otherwise byte promotes to int, and `int << 56` masks the shift
    # count to 5 bits, silently corrupting the high bytes.
    if refin:
        return [
            "        int i = 0;",
            "        while (i + 8 <= data.Length) {",
            "            ulong b = (ulong)data[i] | (ulong)data[i + 1] << 8"
            " | (ulong)data[i + 2] << 16 | (ulong)data[i + 3] << 24",
            "                | (ulong)data[i + 4] << 32 | (ulong)data[i + 5] << 40"
            " | (ulong)data[i + 6] << 48 | (ulong)data[i + 7] << 56;",
            "            ulong xored = crc ^ b;",
            "            crc = _crcSliceTables[7, (byte)xored]"
            " ^ _crcSliceTables[6, (byte)(xored >> 8)]",
            "                ^ _crcSliceTables[5, (byte)(xored >> 16)]"
            " ^ _crcSliceTables[4, (byte)(xored >> 24)]",
            "                ^ _crcSliceTables[3, (byte)(xored >> 32)]"
            " ^ _crcSliceTables[2, (byte)(xored >> 40)]",
            "                ^ _crcSliceTables[1, (byte)(xored >> 48)]"
            " ^ _crcSliceTables[0, (byte)(xored >> 56)];",
            "            i += 8;",
            "        }",
            "        while (i < data.Length) {",
            "            crc = _crcSliceTables[0, (byte)(crc ^ data[i])]"
            " ^ (crc >> 8);",
            "            i++;",
            "        }",
        ]
    return [
        "        int i = 0;",
        "        while (i + 8 <= data.Length) {",
        "            ulong b = (ulong)data[i] << 56 | (ulong)data[i + 1] << 48"
        " | (ulong)data[i + 2] << 40 | (ulong)data[i + 3] << 32",
        "                | (ulong)data[i + 4] << 24 | (ulong)data[i + 5] << 16"
        " | (ulong)data[i + 6] << 8 | (ulong)data[i + 7];",
        "            ulong xored = crc ^ b;",
        "            crc = _crcSliceTables[7, (byte)(xored >> 56)]"
        " ^ _crcSliceTables[6, (byte)(xored >> 48)]",
        "                ^ _crcSliceTables[5, (byte)(xored >> 40)]"
        " ^ _crcSliceTables[4, (byte)(xored >> 32)]",
        "                ^ _crcSliceTables[3, (byte)(xored >> 24)]"
        " ^ _crcSliceTables[2, (byte)(xored >> 16)]",
        "                ^ _crcSliceTables[1, (byte)(xored >> 8)]"
        " ^ _crcSliceTables[0, (byte)xored];",
        "            i += 8;",
        "        }",
        "        while (i < data.Length) {",
        "            byte top = (byte)(crc >> 56);",
        "            crc = _crcSliceTables[0, (byte)(top ^ data[i])]"
        " ^ (crc << 8);",
        "            i++;",
        "        }",
    ]


def _update_loop_csharp(
    w: int, poly: int, refin: bool, cstype: str, table: bool,
) -> list[str]:
    """Emit the per-byte main-loop lines for the update method."""
    mask = _cs_hex((1 << w) - 1, w)
    if table:
        if w == 8:
            return [
                "        foreach (byte b in data) {",
                "            crc = _crcTable[crc ^ b];",
                "        }",
            ]
        if refin:
            # XOR low byte of crc with input byte -> table index; shift crc right.
            return [
                "        foreach (byte b in data) {",
                f"            crc = {_cs_cast(w, f'_crcTable[(byte)(crc ^ b)] ^ (crc >> 8)')};",
                "        }",
            ]
        return [
            "        foreach (byte b in data) {",
            f"            crc = {_cs_cast(w, f'_crcTable[(byte)((crc >> {w - 8}) ^ b)] ^ (crc << 8) & {mask}')};",
            "        }",
        ]
    if refin:
        ref_poly = _reflect(poly, w)
        return [
            "        foreach (byte b in data) {",
            "            crc ^= b;",
            "            for (int i = 0; i < 8; i++) {",
            f"                if ((crc & {_cs_hex(1, w)}) != 0)",
            f"                    crc = {_cs_cast(w, f'(crc >> 1) ^ {_cs_hex(ref_poly, w)}')};",
            "                else",
            f"                    crc >>= 1;",
            "            }",
            "        }",
        ]
    shift_left = (
        f"crc = {_cs_cast(w, 'crc << 1')};" if w <= 16 else "crc <<= 1;"
    )
    # For widths where ``w - 8`` is >= 32, C# masks shift counts on
    # int down to 5 bits, so ``b << 56`` would compile to ``b << (56 &
    # 0x1F)`` and lose the high bits.  Widen ``b`` to the state type
    # *before* shifting to keep the full count.
    if w == 8:
        b_aligned = "b"
    elif w >= 64:
        b_aligned = f"({cstype})b << {w - 8}"
    else:
        b_aligned = f"b << {w - 8}"
    return [
        "        foreach (byte b in data) {",
        f"            crc ^= {_cs_cast(w, b_aligned)};",
        "            for (int i = 0; i < 8; i++) {",
        f"                if ((crc & {_cs_hex(1 << (w - 1), w)}) != 0)",
        f"                    crc = {_cs_cast(w, f'(crc << 1) ^ {_cs_hex(poly, w)}')};",
        "                else",
        f"                    {shift_left}",
        f"                crc &= {mask};",
        "            }",
        "        }",
    ]


def _self_test_csharp(fname: str, check: int, width: int) -> list[str]:
    """Emit a static method returning true on success."""
    return [
        f"    public static bool {fname}_self_test() {{",
        f"        return {fname}({_check_input_bytes_cs()}) == "
        f"{_cs_hex(check, width)};",
        f"    }}",
    ]


def generate_csharp(
    name: str,
    symbol: str | None = None,
    variant: Literal["bitwise", "table", "slice8"] = "bitwise",
) -> str | None:
    """Look up a CRC algorithm by name and generate C# source for it.

    Thin wrapper around :func:`generate_csharp_from_entry`; use the
    latter directly when generating from a custom (non-catalogue)
    algorithm spec.
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_csharp_from_entry(
        name, algo, symbol=symbol, variant=variant,
    )


def generate_csharp_from_entry(
    name: str,
    algo: AlgorithmInfo,
    symbol: str | None = None,
    variant: Literal["bitwise", "table", "slice8"] = "bitwise",
) -> str:
    """Generate a C# source file from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments and as the default
            function-name source).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).  The class name is
            derived as the PascalCase'd form of the function name.
        variant: Implementation shape -- ``"bitwise"`` (default),
            ``"table"`` (256-entry lookup), or ``"slice8"`` (8 tables;
            requires ``algo.width`` to be 32 or 64; ``ValueError``
            otherwise).

    Returns:
        C# source code string declaring a ``public static class``.
    """
    table, slice8 = _variant_to_flags(variant)
    w = algo.width
    poly = algo.poly
    init = algo.init
    refin = algo.refin
    refout = algo.refout
    xorout = algo.xorout
    check = algo.check
    desc = algo.desc
    fname = symbol if symbol else _func_name(name)
    cstype = _cs_type(w)
    cls = _cs_pascal_class(fname)
    mask = _cs_hex((1 << w) - 1, w)

    if slice8 and w not in (32, 64):
        raise ValueError(
            f"slice8=True requires width=32 or width=64 (got width={w}). "
            "Slice-by-8 is a high-throughput optimization that only "
            "makes sense at those widths; smaller CRCs would need a "
            "different chunking scheme."
        )

    init_state = _reflect(init, w) if refin else init

    lines: list[str] = []
    lines.append(f"// {fname}.cs -- generated by crcglot from reveng/{name}")
    lines.append(f"// {desc}")
    lines.append(f'// check: {cls}.{fname}({{0x31..0x39}}) == {_cs_hex(check, w)}')
    lines.append(f"//")
    lines.append(f"// Streaming: init -> update (any number of times) -> finalize.")
    lines.append(f"// One-shot:  call {cls}.{fname}(data).")
    lines.append(f"// Verify:    call {cls}.{fname}_self_test() (returns true on success).")
    lines.append(f"")
    lines.append(f"using System;")
    lines.append(f"")
    lines.append(f"public static class {cls}")
    lines.append(f"{{")

    if slice8:
        slice_tables = _build_slice8_tables(w, poly, refin)
        lines.append(_format_slice8_tables_csharp(slice_tables, w, cstype))
        lines.append("")
    elif table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_csharp(tbl, w, cstype))
        lines.append("")

    # ----- <fname>_init() -----
    lines.append(f"    public static {cstype} {fname}_init() {{")
    lines.append(f"        return {_cs_hex(init_state, w)};")
    lines.append(f"    }}")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines.append(
        f"    public static {cstype} {fname}_update({cstype} state, byte[] data) {{"
    )
    lines.append(f"        {cstype} crc = state;")
    if slice8:
        lines.extend(_update_loop_csharp_slice8(w, refin, cstype))
    else:
        lines.extend(_update_loop_csharp(w, poly, refin, cstype, table))
    lines.append(f"        return crc;")
    lines.append(f"    }}")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines.append(f"    public static {cstype} {fname}_finalize({cstype} state) {{")
    if refout != refin:
        lines.append(f"        // reflect output (refout != refin)")
        lines.append(f"        {cstype} reflected = 0;")
        lines.append(f"        for (int k = 0; k < {w}; k++)")
        lines.append(
            f"            reflected |= "
            f"{_cs_cast(w, f'((state >> k) & 1) << ({w - 1} - k)')};"
        )
        lines.append(f"        state = reflected;")
    if xorout:
        lines.append(f"        return {_cs_cast(w, f'state ^ {_cs_hex(xorout, w)}')};")
    else:
        lines.append(f"        return state;")
    lines.append(f"    }}")
    lines.append("")

    # ----- one-shot wrapper -----
    lines.append(f"    public static {cstype} {fname}(byte[] data) {{")
    lines.append(
        f"        return {fname}_finalize({fname}_update({fname}_init(), data));"
    )
    lines.append(f"    }}")
    lines.append("")

    # ----- self-test -----
    lines.extend(_self_test_csharp(fname, check, w))

    lines.append(f"}}")
    _ = mask  # currently unused at top-level scope; consumed via inline helpers
    return "\n".join(lines)
