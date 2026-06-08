"""TypeScript CRC generator.

Emits a single ``.ts`` file containing five exported functions:

  - ``<fname>_init() -> <T>`` -- return the starting state
  - ``<fname>_update(state, data) -> <T>`` -- feed bytes, return new state
  - ``<fname>_finalize(state) -> <T>`` -- apply output reflection + xorout
  - ``<fname>(data) -> <T>`` -- one-shot wrapper (init + update + finalize)
  - ``<fname>_self_test() -> boolean`` -- true iff the one-shot
    reproduces the reveng catalogue check value for ``b"123456789"``

The state type ``<T>`` is ``number`` for widths 8 / 16 / 32 and
``bigint`` for width 64.  ``number`` is JS's float64, which represents
all unsigned-32-bit values exactly; ``bigint`` covers 64-bit cleanly
with native ``&``, ``^``, ``<<``, ``>>`` operators (and no
``2^53 - 1`` precision ceiling).

Output is pure TypeScript with no runtime-specific imports: runs
under Node, Bun, Deno, browser ES modules, or any bundler that
accepts modern TS.  The self-test is a plain exported function the
caller invokes; no ``cargo test`` / ``vitest`` framework needed
(though it composes fine with one).

Uint32 coercion: JS bitwise operators treat the operand as a signed
int32.  For CRC-32 with a left-shift step, the result can land in
the negative int32 range, which silently breaks subsequent arithmetic
unless coerced back to uint32 via ``>>> 0``.  The non-reflected
bitwise / table / slice8 paths apply that coercion at the right
points; the reflected paths do not need it (``>>>`` is unsigned and
keeps values in [0, 2^32)).

Verified at build time by ``tests.test_typescript_gen
.TestGeneratedTypeScriptExecutes`` (one-shot path via ``tsx``) and
``TestGeneratedTypeScriptStreaming`` (streaming splittability
invariant via a synthesized runner).
"""

# ruff: noqa: F541  - f-strings without placeholders used for code alignment

from __future__ import annotations

from typing import Literal

from crcglot._helpers import (
    _build_slice8_tables,
    _build_table,
    _func_name,
    _hex,
    _variant_to_flags,
    crc_function_names,
)
from crcglot.catalogue import ALGORITHMS, AlgorithmInfo, _reflect
from crcglot.comments import (
    AlgoMeta,
    DocParam,
    UsageExample,
    comment_style_for,
    standard_doc_blocks,
)


def _ts_type(width: int) -> str:
    """Pick the TypeScript state type for a given CRC width."""
    return "bigint" if width == 64 else "number"


def _ts_lit(value: int, width: int) -> str:
    """Format an integer constant as a TypeScript literal.

    Width 64 emits a ``bigint`` literal (``0x...n`` suffix); widths
    8/16/32 emit a plain ``number`` literal.
    """
    hex_w = (width + 3) // 4
    suffix = "n" if width == 64 else ""
    return f"0x{value:0{hex_w}X}{suffix}"


def _format_table_ts(table: list[int], width: int, ttype: str) -> str:
    """Format a lookup table as a TypeScript ``const`` array."""
    hex_w = (width + 3) // 4
    suffix = "n" if ttype == "bigint" else ""
    lines = [f"const CRC_TABLE: {ttype}[] = ["]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}{suffix}"
            for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append("];")
    return "\n".join(lines)


def _format_slice8_tables_ts(
    tables: list[list[int]], width: int, ttype: str,
) -> str:
    """Format the 8 slice-by-8 tables as a TypeScript 2D ``const``."""
    hex_w = (width + 3) // 4
    suffix = "n" if ttype == "bigint" else ""
    lines = [f"const CRC_SLICE_TABLES: {ttype}[][] = ["]
    for t_idx, table in enumerate(tables):
        lines.append(f"    // T{t_idx}")
        lines.append(f"    [")
        for row in range(0, 256, 8):
            vals = ", ".join(
                f"0x{table[i]:0{hex_w}X}{suffix}"
                for i in range(row, min(row + 8, 256))
            )
            lines.append(f"        {vals},")
        lines.append(f"    ],")
    lines.append("];")
    return "\n".join(lines)


def _update_loop_ts(
    w: int,
    poly: int,
    refin: bool,
    table: bool,
) -> list[str]:
    """Emit the per-byte main-loop lines for the update function.

    Variable ``crc`` (already declared in the enclosing function) is
    updated in place over ``data``.  Returns the lines starting with
    the ``for`` header.
    """
    is_bigint = w == 64
    if is_bigint:
        # bigint: native &, ^, <<, >> with no width ceiling.
        byte_expr = "BigInt(byte)"
        if table:
            if refin:
                return [
                    "    for (const byte of data) {",
                    f"        crc = CRC_TABLE[Number((crc ^ {byte_expr}) & 0xFFn)] ^ (crc >> 8n);",
                    "    }",
                ]
            return [
                "    for (const byte of data) {",
                f"        crc = CRC_TABLE[Number(((crc >> {w - 8}n) ^ {byte_expr}) & 0xFFn)] ^ ((crc << 8n) & {_ts_lit(_mask_int(w), w)});",
                "    }",
            ]
        if refin:
            ref_poly = _reflect(poly, w)
            return [
                "    for (const byte of data) {",
                f"        crc ^= {byte_expr};",
                "        for (let j = 0; j < 8; j++) {",
                "            if ((crc & 1n) !== 0n) {",
                f"                crc = (crc >> 1n) ^ {_ts_lit(ref_poly, w)};",
                "            } else {",
                "                crc >>= 1n;",
                "            }",
                "        }",
                "    }",
            ]
        return [
            "    for (const byte of data) {",
            f"        crc ^= {byte_expr} << {w - 8}n;",
            "        for (let j = 0; j < 8; j++) {",
            f"            if ((crc & {_ts_lit(1 << (w - 1), w)}) !== 0n) {{",
            f"                crc = (crc << 1n) ^ {_ts_lit(poly, w)};",
            "            } else {",
            "                crc <<= 1n;",
            "            }",
            f"            crc &= {_ts_lit(_mask_int(w), w)};",
            "        }",
            "    }",
        ]
    # number path (w == 8, 16, or 32)
    if table:
        if w == 8:
            # Table lookup IS the algorithm; no shifts/masks needed.
            return [
                "    for (const byte of data) {",
                "        crc = CRC_TABLE[crc ^ byte];",
                "    }",
            ]
        if refin:
            return [
                "    for (const byte of data) {",
                f"        crc = CRC_TABLE[(crc ^ byte) & 0xFF] ^ (crc >>> 8);",
                "    }",
            ]
        # Non-reflected width 16/32: coerce left-shift back to uint via
        # `>>> 0` for w=32; mask for w<32.
        if w == 32:
            return [
                "    for (const byte of data) {",
                f"        crc = (CRC_TABLE[((crc >>> {w - 8}) ^ byte) & 0xFF] ^ (crc << 8)) >>> 0;",
                "    }",
            ]
        return [
            "    for (const byte of data) {",
            f"        crc = (CRC_TABLE[((crc >>> {w - 8}) ^ byte) & 0xFF] ^ (crc << 8)) & {_hex((1 << w) - 1, w)};",
            "    }",
        ]
    # bitwise (non-table)
    if refin:
        ref_poly = _reflect(poly, w)
        return [
            "    for (const byte of data) {",
            "        crc ^= byte;",
            "        for (let j = 0; j < 8; j++) {",
            "            if ((crc & 1) !== 0) {",
            f"                crc = (crc >>> 1) ^ {_hex(ref_poly, w)};",
            "            } else {",
            "                crc >>>= 1;",
            "            }",
            "        }",
            "    }",
        ]
    if w < 8:
        # Sub-byte non-reflected: bit-by-bit, MSB first.  The byte-aligned
        # ``byte << (w - 8)`` fold is a negative shift for width < 8.
        wmask = _hex((1 << w) - 1, w)
        return [
            "    for (const byte of data) {",
            "        for (let j = 7; j >= 0; j--) {",
            f"            const bit = (byte >>> j) & 1;",
            f"            if ((((crc >>> {w - 1}) & 1) ^ bit) !== 0) {{",
            f"                crc = ((crc << 1) ^ {_hex(poly, w)}) & {wmask};",
            "            } else {",
            f"                crc = (crc << 1) & {wmask};",
            "            }",
            "        }",
            "    }",
        ]
    # Non-reflected bitwise.  Width 32 needs `>>> 0` after the XOR to
    # stay in uint32; smaller widths mask explicitly.
    coerce = ">>> 0" if w == 32 else f"& {_hex((1 << w) - 1, w)}"
    return [
        "    for (const byte of data) {",
        f"        crc ^= byte << {w - 8};",
        "        for (let j = 0; j < 8; j++) {",
        f"            if ((crc & {_hex(1 << (w - 1), w)}) !== 0) {{",
        f"                crc = ((crc << 1) ^ {_hex(poly, w)}) {coerce};",
        "            } else {",
        f"                crc = (crc << 1) {coerce};",
        "            }",
        "        }",
        "    }",
    ]


def _update_loop_ts_slice8(w: int, refin: bool) -> list[str]:
    """Emit the slice-by-8 main loop + byte-by-byte tail.

    Only valid for w == 32 or w == 64.  For w == 32 the slice tables
    are ``number[]``; for w == 64 they are ``bigint[]`` and array
    indices require ``Number(...)`` casts on the bigint slice expressions.
    """
    if w == 32:
        if refin:
            return [
                "    const n = data.length;",
                "    let i = 0;",
                "    while (i + 8 <= n) {",
                "        const b03 = data[i] | (data[i+1] << 8) | (data[i+2] << 16) | (data[i+3] << 24);",
                "        const b47 = data[i+4] | (data[i+5] << 8) | (data[i+6] << 16) | (data[i+7] << 24);",
                "        const xored = (crc ^ b03) >>> 0;",
                "        crc = (",
                "            CRC_SLICE_TABLES[7][ xored         & 0xFF]",
                "          ^ CRC_SLICE_TABLES[6][(xored >>>  8) & 0xFF]",
                "          ^ CRC_SLICE_TABLES[5][(xored >>> 16) & 0xFF]",
                "          ^ CRC_SLICE_TABLES[4][(xored >>> 24) & 0xFF]",
                "          ^ CRC_SLICE_TABLES[3][ b47          & 0xFF]",
                "          ^ CRC_SLICE_TABLES[2][(b47   >>>  8) & 0xFF]",
                "          ^ CRC_SLICE_TABLES[1][(b47   >>> 16) & 0xFF]",
                "          ^ CRC_SLICE_TABLES[0][(b47   >>> 24) & 0xFF]",
                "        ) >>> 0;",
                "        i += 8;",
                "    }",
                "    while (i < n) {",
                "        crc = (CRC_SLICE_TABLES[0][(crc ^ data[i]) & 0xFF] ^ (crc >>> 8)) >>> 0;",
                "        i += 1;",
                "    }",
            ]
        return [
            "    const n = data.length;",
            "    let i = 0;",
            "    while (i + 8 <= n) {",
            "        const b03 = ((data[i] << 24) | (data[i+1] << 16) | (data[i+2] << 8) | data[i+3]) >>> 0;",
            "        const b47 = ((data[i+4] << 24) | (data[i+5] << 16) | (data[i+6] << 8) | data[i+7]) >>> 0;",
            "        const xored = (crc ^ b03) >>> 0;",
            "        crc = (",
            "            CRC_SLICE_TABLES[7][(xored >>> 24) & 0xFF]",
            "          ^ CRC_SLICE_TABLES[6][(xored >>> 16) & 0xFF]",
            "          ^ CRC_SLICE_TABLES[5][(xored >>>  8) & 0xFF]",
            "          ^ CRC_SLICE_TABLES[4][ xored         & 0xFF]",
            "          ^ CRC_SLICE_TABLES[3][(b47   >>> 24) & 0xFF]",
            "          ^ CRC_SLICE_TABLES[2][(b47   >>> 16) & 0xFF]",
            "          ^ CRC_SLICE_TABLES[1][(b47   >>>  8) & 0xFF]",
            "          ^ CRC_SLICE_TABLES[0][ b47          & 0xFF]",
            "        ) >>> 0;",
            "        i += 8;",
            "    }",
            "    while (i < n) {",
            "        const top = crc >>> 24;",
            "        crc = (CRC_SLICE_TABLES[0][(top ^ data[i]) & 0xFF] ^ ((crc << 8) >>> 0)) >>> 0;",
            "        i += 1;",
            "    }",
        ]
    # w == 64 (bigint slice tables)
    if refin:
        return [
            "    const n = data.length;",
            "    let i = 0;",
            "    while (i + 8 <= n) {",
            "        const b = BigInt(data[i])",
            "            | (BigInt(data[i+1]) << 8n)",
            "            | (BigInt(data[i+2]) << 16n)",
            "            | (BigInt(data[i+3]) << 24n)",
            "            | (BigInt(data[i+4]) << 32n)",
            "            | (BigInt(data[i+5]) << 40n)",
            "            | (BigInt(data[i+6]) << 48n)",
            "            | (BigInt(data[i+7]) << 56n);",
            "        const xored = crc ^ b;",
            "        crc = CRC_SLICE_TABLES[7][Number( xored         & 0xFFn)]",
            "            ^ CRC_SLICE_TABLES[6][Number((xored >>  8n) & 0xFFn)]",
            "            ^ CRC_SLICE_TABLES[5][Number((xored >> 16n) & 0xFFn)]",
            "            ^ CRC_SLICE_TABLES[4][Number((xored >> 24n) & 0xFFn)]",
            "            ^ CRC_SLICE_TABLES[3][Number((xored >> 32n) & 0xFFn)]",
            "            ^ CRC_SLICE_TABLES[2][Number((xored >> 40n) & 0xFFn)]",
            "            ^ CRC_SLICE_TABLES[1][Number((xored >> 48n) & 0xFFn)]",
            "            ^ CRC_SLICE_TABLES[0][Number((xored >> 56n) & 0xFFn)];",
            "        i += 8;",
            "    }",
            "    while (i < n) {",
            "        crc = CRC_SLICE_TABLES[0][Number((crc ^ BigInt(data[i])) & 0xFFn)] ^ (crc >> 8n);",
            "        i += 1;",
            "    }",
        ]
    return [
        "    const n = data.length;",
        "    let i = 0;",
        "    while (i + 8 <= n) {",
        "        const b = (BigInt(data[i]) << 56n)",
        "            | (BigInt(data[i+1]) << 48n)",
        "            | (BigInt(data[i+2]) << 40n)",
        "            | (BigInt(data[i+3]) << 32n)",
        "            | (BigInt(data[i+4]) << 24n)",
        "            | (BigInt(data[i+5]) << 16n)",
        "            | (BigInt(data[i+6]) << 8n)",
        "            | BigInt(data[i+7]);",
        "        const xored = crc ^ b;",
        "        crc = CRC_SLICE_TABLES[7][Number((xored >> 56n) & 0xFFn)]",
        "            ^ CRC_SLICE_TABLES[6][Number((xored >> 48n) & 0xFFn)]",
        "            ^ CRC_SLICE_TABLES[5][Number((xored >> 40n) & 0xFFn)]",
        "            ^ CRC_SLICE_TABLES[4][Number((xored >> 32n) & 0xFFn)]",
        "            ^ CRC_SLICE_TABLES[3][Number((xored >> 24n) & 0xFFn)]",
        "            ^ CRC_SLICE_TABLES[2][Number((xored >> 16n) & 0xFFn)]",
        "            ^ CRC_SLICE_TABLES[1][Number((xored >>  8n) & 0xFFn)]",
        "            ^ CRC_SLICE_TABLES[0][Number( xored         & 0xFFn)];",
        "        i += 8;",
        "    }",
        "    while (i < n) {",
        "        const top = crc >> 56n;",
        "        crc = CRC_SLICE_TABLES[0][Number((top ^ BigInt(data[i])) & 0xFFn)] ^ ((crc << 8n) & 0xFFFFFFFFFFFFFFFFn);",
        "        i += 1;",
        "    }",
    ]


def _mask_int(width: int) -> int:
    """Return ``(1 << width) - 1`` as a Python int."""
    return (1 << width) - 1


def _self_test_ts(names, check, width, style, docs) -> list[str]:
    """Emit a TS self-test returning true iff one-shot matches reveng."""
    return [
        f"",
        *style.doc_block(docs["self_test"]),
        f"export function {names['self_test']}(): boolean {{",
        f'    const input = new Uint8Array([0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39]);',
        f"    return {names['oneshot']}(input) === {_ts_lit(check, width)};",
        f"}}",
    ]


def generate_typescript(
    name: str,
    symbol: str | None = None,
    variant: Literal["bitwise", "table", "slice8"] = "bitwise",
    comment_style: str = "plain",
    naming: str = "camel",
) -> str | None:
    """Look up a CRC algorithm by name and generate TypeScript source.

    Thin wrapper around :func:`generate_typescript_from_entry`; use
    the latter directly for custom (non-catalogue) algorithm specs.
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_typescript_from_entry(
        name, algo, symbol=symbol, variant=variant,
        comment_style=comment_style, naming=naming,
    )


def generate_typescript_from_entry(
    name: str,
    algo: AlgorithmInfo,
    symbol: str | None = None,
    variant: Literal["bitwise", "table", "slice8"] = "bitwise",
    comment_style: str = "plain",
    naming: str = "camel",
) -> str:
    """Generate TypeScript source from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).
        variant: Implementation shape -- ``"bitwise"`` (default),
            ``"table"`` (256-entry lookup), or ``"slice8"`` (8 tables;
            requires ``algo.width`` to be 32 or 64; ``ValueError``
            otherwise).

    Returns:
        TypeScript source code string.
    """
    table, slice8 = _variant_to_flags(variant)
    w = algo.width
    if w < 8 and table:
        # Sub-byte CRCs are bit-by-bit only (see variants_for_width); a stray
        # table request degrades to bitwise rather than emitting a byte-wise
        # table update for a register narrower than a byte.
        table = False
    poly = algo.poly
    init = algo.init
    refin = algo.refin
    refout = algo.refout
    xorout = algo.xorout
    check = algo.check
    desc = algo.desc
    from crcglot.targets import naming_convention_for

    naming = naming_convention_for("typescript", naming)
    base = symbol if symbol else _func_name(name)
    names = crc_function_names(base, naming, is_override=symbol is not None)
    ttype = _ts_type(w)

    if slice8 and w not in (32, 64):
        raise ValueError(
            f"variant='slice8' requires width=32 or width=64 (got width={w}). "
            "Slice-by-8 is a high-throughput optimization that only "
            "makes sense at those widths."
        )

    init_state = _reflect(init, w) if refin else init

    style = comment_style_for("typescript", comment_style)
    meta = AlgoMeta(
        name=name, desc=desc, width=w, poly=poly, init=init, refin=refin,
        refout=refout, xorout=xorout, check=check, variant=variant,
    )
    usage = UsageExample(
        streaming=(
            f"let s = {names['init']}();",
            f"s = {names['update']}(s, chunk);  // over each chunk of the message",
            f"const crc = {names['finalize']}(s);",
        ),
        oneshot=f"{names['oneshot']}(data)",
        selftest=f"{names['self_test']}()",
        selftest_returns="returns true on success",
        caveats=(
            ("Variant: slice-by-8 (8 tables, ~10x throughput vs a plain "
             "table for large buffers).",)
            if slice8 else ()
        ),
    )
    docs = standard_doc_blocks(
        names, state_type=ttype,
        data_params=(DocParam("data", "the message bytes."),),
        selftest_returns="true",
        refin=refin, refout=refout, xorout=xorout,
    )

    lines: list[str] = []

    # Tables (if any).
    if slice8:
        slice_tables = _build_slice8_tables(w, poly, refin)
        lines.append(_format_slice8_tables_ts(slice_tables, w, ttype))
        lines.append("")
    elif table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_ts(tbl, w, ttype))
        lines.append("")

    # File header.
    lines += style.file_header(meta, usage)
    lines.append("")

    # ----- <fname>_init() -----
    lines += style.doc_block(docs["init"])
    lines.append(f"export function {names['init']}(): {ttype} {{")
    lines.append(f"    return {_ts_lit(init_state, w)};")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines += style.doc_block(docs["update"])
    lines.append(
        f"export function {names['update']}(state: {ttype}, data: Uint8Array): {ttype} {{"
    )
    lines.append(f"    let crc: {ttype} = state;")
    if slice8:
        lines.extend(_update_loop_ts_slice8(w, refin))
    else:
        lines.extend(_update_loop_ts(w, poly, refin, table))
    lines.append(f"    return crc;")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines += style.doc_block(docs["finalize"])
    lines.append(
        f"export function {names['finalize']}(state: {ttype}): {ttype} {{"
    )
    if refout != refin:
        # Emit a bit-reflection loop matching the language idiom.
        if ttype == "bigint":
            lines.append(f"    let reflected: bigint = 0n;")
            lines.append(f"    for (let k = 0n; k < {w}n; k++) {{")
            lines.append(
                f"        reflected |= ((state >> k) & 1n) << ({w - 1}n - k);"
            )
            lines.append(f"    }}")
            lines.append(f"    state = reflected;")
        else:
            lines.append(f"    let reflected: number = 0;")
            lines.append(f"    for (let k = 0; k < {w}; k++) {{")
            lines.append(
                f"        reflected |= ((state >>> k) & 1) << ({w - 1} - k);"
            )
            lines.append(f"    }}")
            # For w=32 the assignment can produce a negative int32 if
            # the high bit is set; coerce via >>> 0.
            if w == 32:
                lines.append(f"    state = reflected >>> 0;")
            else:
                lines.append(f"    state = reflected;")
    # For w=32 (number type) JS bitwise ops produce signed int32 internally
    # -- when the top bit is set, intermediate XORs flip the value into the
    # negative range.  Coerce to uint32 on every w=32 return so the caller
    # gets the same Number value as the catalogue's check field (a positive
    # 32-bit literal).  Smaller widths (8/16) stay in safe range; bigint
    # has no width ceiling so no coercion needed.
    if xorout:
        if ttype == "bigint":
            lines.append(f"    return state ^ {_ts_lit(xorout, w)};")
        elif w == 32:
            lines.append(f"    return (state ^ {_ts_lit(xorout, w)}) >>> 0;")
        else:
            lines.append(f"    return state ^ {_ts_lit(xorout, w)};")
    else:
        if w == 32 and ttype == "number":
            lines.append(f"    return state >>> 0;")
        else:
            lines.append(f"    return state;")
    lines.append(f"}}")
    lines.append("")

    # ----- one-shot wrapper -----
    lines += style.doc_block(docs["oneshot"])
    lines.append(
        f"export function {names['oneshot']}(data: Uint8Array): {ttype} {{"
    )
    lines.append(
        f"    return {names['finalize']}({names['update']}({names['init']}(), data));"
    )
    lines.append(f"}}")

    # ----- self-test -----
    lines.extend(_self_test_ts(names, check, w, style, docs))

    module = "\n".join(lines)
    # Namespace the lookup-table identifiers per symbol so multiple
    # generated modules (different algorithms, or the same algorithm in
    # several variants) can coexist in one file/translation unit without
    # colliding.  The emitters above use the fixed placeholders
    # ``CRC_TABLE`` / ``CRC_SLICE_TABLES``; rewrite them to
    # ``crcglot_table_<symbol>`` / ``crcglot_slice_<symbol>`` here -- the
    # single assembly point -- so the loop bodies reference the unique
    # name directly with no aliasing.  Slice first (its name is a strict
    # superset spelling, though not a substring of the table token).
    module = module.replace("CRC_SLICE_TABLES", f"crcglot_slice_{base}")
    module = module.replace("CRC_TABLE", f"crcglot_table_{base}")
    return module
