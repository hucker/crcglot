"""Go CRC generator.

Emits a complete ``.go`` file with five package-level functions:

  - ``<fname>_init() uint{8,16,32,64}`` -- return the starting state
  - ``<fname>_update(state, data) ...`` -- feed bytes, return new state
  - ``<fname>_finalize(state) ...`` -- apply output reflection + xorout
  - ``<fname>(data) ...`` -- one-shot wrapper (init + update + finalize)
  - ``<fname>_self_test() bool`` -- True iff the algorithm reproduces
    the reveng catalogue's canonical check value

The streaming primitives let callers compute a CRC over data that
arrives in chunks (large files, network streams) without buffering
everything.  The one-shot wrapper preserves the simple API for the
common case.  ``_self_test()`` lets a downstream caller verify the
implementation on their toolchain before trusting its output --
callable from ``go test``, a startup boot-check, or anywhere.

The file declares ``package crc`` by default; rename freely to drop
into an existing package, or pass ``symbol=`` to rename the function
trio (e.g. ``symbol=Crc32`` for Go-idiomatic exported names).  Lookup
tables (``--table`` and ``--slice8`` variants) are named after the
function symbol so multiple generated CRCs can coexist in one package
without table-name collisions.

Verified at build time by ``tests.test_go_gen.TestGenerateGo``
(structural) and ``TestGeneratedGoExecutes`` (compile + run via
``go run``; slow-marked, skipped without ``go`` on PATH).
"""

# ruff: noqa: F541  - f-strings without placeholders used for code alignment

from __future__ import annotations

from typing import Literal

from crcglot._helpers import (
    _build_slice8_tables,
    _build_table,
    _func_name,
    _hex,
    _mask,
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


def _format_table_go(
    table: list[int], width: int, gtype: str, fname: str,
) -> str:
    """Format a lookup table as a Go ``var`` array literal.

    Variable name is prefixed with ``fname`` so multiple generated
    CRCs can live in the same package without table-name collisions.
    """
    hex_w = (width + 3) // 4
    lines = [f"var _{fname}_table = [256]{gtype}{{"]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append("}")
    return "\n".join(lines)


def _format_slice8_tables_go(
    tables: list[list[int]], width: int, gtype: str, fname: str,
) -> str:
    """Format the 8 slice-by-8 tables as a Go 2D ``var`` array literal.

    Variable name is prefixed with ``fname`` so multiple generated
    CRCs can live in the same package without collisions.
    """
    hex_w = (width + 3) // 4
    lines = [f"var _{fname}_sliceTables = [8][256]{gtype}{{"]
    for t_idx, table in enumerate(tables):
        lines.append(f"    {{ // T{t_idx}")
        for row in range(0, 256, 8):
            vals = ", ".join(
                f"0x{table[i]:0{hex_w}X}"
                for i in range(row, min(row + 8, 256))
            )
            lines.append(f"        {vals},")
        lines.append(f"    }},")
    lines.append("}")
    return "\n".join(lines)


def _update_loop_go_slice8(
    w: int, refin: bool, gtype: str, fname: str,
) -> list[str]:
    """Emit the per-8-byte slice-by-8 main loop + byte-by-byte tail for Go.

    Variable ``crc`` (of type ``gtype``) is assumed to already hold the
    incoming state.  Walks ``data`` 8 bytes at a time via 8 chained
    table lookups, then falls back to single-byte via the slice-by-8
    table T0 for any 1-7 trailing bytes.

    Only valid for w == 32 or w == 64.
    """
    t = f"_{fname}_sliceTables"
    if w == 32:
        if refin:
            # Reflected: little-endian load, low byte of state XOR'd
            # into the first 4 input bytes, T7..T0 walk from low to high.
            return [
                "    for len(data) >= 8 {",
                "        b03 := uint32(data[0]) | uint32(data[1])<<8 | uint32(data[2])<<16 | uint32(data[3])<<24",
                "        b47 := uint32(data[4]) | uint32(data[5])<<8 | uint32(data[6])<<16 | uint32(data[7])<<24",
                "        xored := crc ^ b03",
                f"        crc = {t}[7][byte(xored)] ^ {t}[6][byte(xored>>8)] ^",
                f"            {t}[5][byte(xored>>16)] ^ {t}[4][byte(xored>>24)] ^",
                f"            {t}[3][byte(b47)] ^ {t}[2][byte(b47>>8)] ^",
                f"            {t}[1][byte(b47>>16)] ^ {t}[0][byte(b47>>24)]",
                "        data = data[8:]",
                "    }",
                "    for _, b := range data {",
                f"        crc = {t}[0][byte(crc)^b] ^ (crc >> 8)",
                "    }",
            ]
        # Non-reflected w=32: big-endian load, top of state XOR'd into
        # first 4 input bytes' top byte; T[7-k] indexes byte at position k.
        return [
            "    for len(data) >= 8 {",
            "        b03 := uint32(data[0])<<24 | uint32(data[1])<<16 | uint32(data[2])<<8 | uint32(data[3])",
            "        b47 := uint32(data[4])<<24 | uint32(data[5])<<16 | uint32(data[6])<<8 | uint32(data[7])",
            "        xored := crc ^ b03",
            f"        crc = {t}[7][byte(xored>>24)] ^ {t}[6][byte(xored>>16)] ^",
            f"            {t}[5][byte(xored>>8)] ^ {t}[4][byte(xored)] ^",
            f"            {t}[3][byte(b47>>24)] ^ {t}[2][byte(b47>>16)] ^",
            f"            {t}[1][byte(b47>>8)] ^ {t}[0][byte(b47)]",
            "        data = data[8:]",
            "    }",
            "    for _, b := range data {",
            "        top := byte(crc >> 24)",
            f"        crc = {t}[0][top^b] ^ (crc << 8)",
            "    }",
        ]
    # w == 64
    if refin:
        return [
            "    for len(data) >= 8 {",
            "        b := uint64(data[0]) | uint64(data[1])<<8 | uint64(data[2])<<16 | uint64(data[3])<<24 |",
            "            uint64(data[4])<<32 | uint64(data[5])<<40 | uint64(data[6])<<48 | uint64(data[7])<<56",
            "        xored := crc ^ b",
            f"        crc = {t}[7][byte(xored)] ^ {t}[6][byte(xored>>8)] ^",
            f"            {t}[5][byte(xored>>16)] ^ {t}[4][byte(xored>>24)] ^",
            f"            {t}[3][byte(xored>>32)] ^ {t}[2][byte(xored>>40)] ^",
            f"            {t}[1][byte(xored>>48)] ^ {t}[0][byte(xored>>56)]",
            "        data = data[8:]",
            "    }",
            "    for _, b := range data {",
            f"        crc = {t}[0][byte(crc)^b] ^ (crc >> 8)",
            "    }",
        ]
    # Non-reflected w=64.  Same index convention as w=32.
    return [
        "    for len(data) >= 8 {",
        "        b := uint64(data[0])<<56 | uint64(data[1])<<48 | uint64(data[2])<<40 | uint64(data[3])<<32 |",
        "            uint64(data[4])<<24 | uint64(data[5])<<16 | uint64(data[6])<<8 | uint64(data[7])",
        "        xored := crc ^ b",
        f"        crc = {t}[7][byte(xored>>56)] ^ {t}[6][byte(xored>>48)] ^",
        f"            {t}[5][byte(xored>>40)] ^ {t}[4][byte(xored>>32)] ^",
        f"            {t}[3][byte(xored>>24)] ^ {t}[2][byte(xored>>16)] ^",
        f"            {t}[1][byte(xored>>8)] ^ {t}[0][byte(xored)]",
        "        data = data[8:]",
        "    }",
        "    for _, b := range data {",
        "        top := byte(crc >> 56)",
        f"        crc = {t}[0][top^b] ^ (crc << 8)",
        "    }",
    ]


def _update_loop_go(
    w: int,
    poly: int,
    refin: bool,
    mask: str,
    gtype: str,
    table: bool,
    fname: str,
) -> list[str]:
    """Emit the per-byte main-loop lines for the update function."""
    t = f"_{fname}_table"
    if table:
        if w == 8:
            # 8-bit: table lookup IS the algorithm (no shifts, and
            # ``uint8 << 8`` would overflow at compile time in Go's
            # constant evaluator anyway).
            return [
                "    for _, b := range data {",
                f"        crc = {t}[crc^b]",
                "    }",
            ]
        if refin:
            return [
                "    for _, b := range data {",
                f"        crc = {t}[{gtype}(byte(crc)^b)] ^ (crc >> 8)",
                "    }",
            ]
        return [
            "    for _, b := range data {",
            f"        crc = {t}[byte(crc>>{w - 8})^b] ^ ((crc << 8) & {mask})",
            "    }",
        ]
    if refin:
        ref_poly = _reflect(poly, w)
        return [
            "    for _, b := range data {",
            f"        crc ^= {gtype}(b)",
            "        for i := 0; i < 8; i++ {",
            "            if crc&1 != 0 {",
            f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)}",
            "            } else {",
            "                crc >>= 1",
            "            }",
            "        }",
            "    }",
        ]
    return [
        "    for _, b := range data {",
        f"        crc ^= {gtype}(b) << {w - 8}",
        "        for i := 0; i < 8; i++ {",
        f"            if crc&{_hex(1 << (w - 1), w)} != 0 {{",
        f"                crc = (crc << 1) ^ {_hex(poly, w)}",
        "            } else {",
        "                crc <<= 1",
        "            }",
        f"            crc &= {mask}",
        "        }",
        "    }",
    ]


def _self_test_go(fname, check, width, style, docs) -> list[str]:
    """Emit a Go self-test function returning true on success."""
    return [
        *style.doc_block(docs["self_test"]),
        f"func {fname}_self_test() bool {{",
        f'    return {fname}([]byte("123456789")) == {_hex(check, width)}',
        f"}}",
    ]


def combine_go(outputs: list[str], stem: str | None = None) -> str:
    """Combine several Go outputs into one ``package crc`` file.

    Go allows exactly one ``package`` clause per file, so the first
    output is kept intact (banner + ``package crc`` + body) and only the
    body (everything after ``package crc``) of the rest is appended.
    Per-symbol table names (``_<sym>_table``) keep the merged package
    collision-free.

    Args:
        outputs: Individual :func:`generate_go` results, one per algorithm.
        stem: Unused; present for signature parity with the C combiner.

    Returns:
        One valid ``package crc`` Go source string with all algorithms.

    Examples:
        >>> a = generate_go("crc32")
        >>> b = generate_go("crc16-modbus")
        >>> combine_go([a, b]).count("package crc")
        1
    """
    del stem
    first = outputs[0]
    rest = [o.partition("package crc")[2] for o in outputs[1:]]
    return first.rstrip("\n") + "\n" + "".join(rest)


def generate_go(
    name: str,
    symbol: str | None = None,
    variant: Literal["bitwise", "table", "slice8"] = "bitwise",
    comment_style: str = "plain",
) -> str | None:
    """Look up a CRC algorithm by name and generate Go source for it.

    Thin wrapper around :func:`generate_go_from_entry`; use the latter
    directly when generating from a custom (non-catalogue) algorithm
    spec.
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_go_from_entry(
        name, algo, symbol=symbol, variant=variant, comment_style=comment_style,
    )


def generate_go_from_entry(
    name: str,
    algo: AlgorithmInfo,
    symbol: str | None = None,
    variant: Literal["bitwise", "table", "slice8"] = "bitwise",
    comment_style: str = "plain",
) -> str:
    """Generate a Go source file from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments and as the default
            function-name source).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).
        variant: Implementation shape -- ``"bitwise"`` (default),
            ``"table"`` (256-entry lookup), or ``"slice8"`` (8 tables;
            requires ``algo.width`` to be 32 or 64; ``ValueError``
            otherwise).

    Returns:
        Go source code string declaring ``package crc``.
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
    mask = _mask(w)

    if slice8 and w not in (32, 64):
        raise ValueError(
            f"variant='slice8' requires width=32 or width=64 (got width={w}). "
            "Slice-by-8 is a high-throughput optimization that only "
            "makes sense at those widths; smaller CRCs would need a "
            "different chunking scheme."
        )

    if w <= 8:
        gtype = "uint8"
    elif w <= 16:
        gtype = "uint16"
    elif w <= 32:
        gtype = "uint32"
    else:
        gtype = "uint64"

    # Pre-loaded init state for streaming entry.
    init_state = _reflect(init, w) if refin else init

    style = comment_style_for("go", comment_style)
    meta = AlgoMeta(
        name=name, desc=desc, width=w, poly=poly, init=init, refin=refin,
        refout=refout, xorout=xorout, check=check, variant=variant,
    )
    usage = UsageExample(
        streaming=(
            f"s := {fname}_init()",
            f"s = {fname}_update(s, chunk)  // over each chunk of the message",
            f"crc := {fname}_finalize(s)",
        ),
        oneshot=f"{fname}(data)",
        selftest=f"{fname}_self_test()",
        selftest_returns="returns true on success",
    )
    docs = standard_doc_blocks(
        fname, state_type=gtype,
        data_params=(DocParam("data", "the message bytes."),),
        selftest_returns="true",
        refin=refin, refout=refout, xorout=xorout,
    )

    lines: list[str] = []
    lines += style.file_header(meta, usage)
    lines.append(f"")
    lines.append(f"package crc")
    lines.append(f"")

    if slice8:
        slice_tables = _build_slice8_tables(w, poly, refin)
        lines.append(_format_slice8_tables_go(slice_tables, w, gtype, fname))
        lines.append("")
    elif table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_go(tbl, w, gtype, fname))
        lines.append("")

    # ----- <fname>_init() -----
    lines += style.doc_block(docs["init"])
    lines.append(f"func {fname}_init() {gtype} {{")
    lines.append(f"    return {_hex(init_state, w)}")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines += style.doc_block(docs["update"])
    lines.append(f"func {fname}_update(state {gtype}, data []byte) {gtype} {{")
    lines.append(f"    crc := state")
    if slice8:
        lines.extend(_update_loop_go_slice8(w, refin, gtype, fname))
    else:
        lines.extend(_update_loop_go(w, poly, refin, mask, gtype, table, fname))
    lines.append(f"    return crc")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines += style.doc_block(docs["finalize"])
    lines.append(f"func {fname}_finalize(state {gtype}) {gtype} {{")
    if refout != refin:
        lines.append(f"    // reflect output (refout != refin)")
        lines.append(f"    var reflected {gtype} = 0")
        lines.append(f"    for k := 0; k < {w}; k++ {{")
        lines.append(f"        reflected |= ((state >> k) & 1) << ({w - 1} - k)")
        lines.append(f"    }}")
        lines.append(f"    state = reflected")
    if xorout:
        lines.append(f"    return state ^ {_hex(xorout, w)}")
    else:
        lines.append(f"    return state")
    lines.append(f"}}")
    lines.append("")

    # ----- one-shot wrapper -----
    lines += style.doc_block(docs["oneshot"])
    lines.append(f"func {fname}(data []byte) {gtype} {{")
    lines.append(
        f"    return {fname}_finalize({fname}_update({fname}_init(), data))"
    )
    lines.append(f"}}")
    lines.append("")

    # ----- self-test -----
    lines.extend(_self_test_go(fname, check, w, style, docs))

    return "\n".join(lines)
