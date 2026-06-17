"""Rust CRC generator.

Emits a complete ``.rs`` file with five module-level functions:

  - ``<fname>_init() -> rtype`` -- return the starting state
  - ``<fname>_update(state, data) -> rtype`` -- feed bytes, return new state
  - ``<fname>_finalize(state) -> rtype`` -- apply output reflection + xorout
  - ``<fname>(data) -> rtype`` -- one-shot wrapper (init + update + finalize)
  - ``<fname>_self_test() -> bool`` -- true iff the generated CRC
    reproduces its independent reference values

The self-test is a plain ``pub fn``, not a ``#[cfg(test)]`` block, so
callers can wire it into a boot self-check or a release-build startup
assertion -- not only into ``cargo test``.  This matches the other six
targets and the README's recommendation to verify in-environment.

The streaming primitives (init / update / finalize) let callers
compute a CRC over data that arrives in chunks (large files, network
streams) without buffering everything.  The one-shot wrapper preserves
the simple API for the common case.

Verified at build time by ``tests.test_rust_gen
.TestGeneratedRustExecutes`` (one-shot path: compile with an injected
``main()`` that calls ``_self_test()`` and exits 0 iff it returns
``true``) and ``TestGeneratedRustStreaming`` (streaming splittability
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
    _mask,
    _variant_to_flags,
    resolve_variant,
    crc_function_names,
)
from crcglot._vectors import goldens_for
from crcglot.catalogue import ALGORITHMS, AlgorithmInfo, _reflect
from crcglot.comments import (
    AlgoMeta,
    DocParam,
    UsageExample,
    build_prov,
    comment_style_for,
    standard_doc_blocks,
)


def _format_table_rust(table: list[int], width: int, rtype: str) -> str:
    """Format a lookup table as a Rust ``const`` array."""
    hex_w = (width + 3) // 4
    lines = [f"const CRC_TABLE: [{rtype}; 256] = ["]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append("];")
    return "\n".join(lines)


def _format_slice8_tables_rust(
    tables: list[list[int]], width: int, rtype: str,
) -> str:
    """Format the 8 slice-by-8 tables as a Rust 2D ``const`` array."""
    hex_w = (width + 3) // 4
    lines = [f"const CRC_SLICE_TABLES: [[{rtype}; 256]; 8] = ["]
    for t_idx, table in enumerate(tables):
        lines.append(f"    // T{t_idx}")
        lines.append(f"    [")
        for row in range(0, 256, 8):
            vals = ", ".join(
                f"0x{table[i]:0{hex_w}X}"
                for i in range(row, min(row + 8, 256))
            )
            lines.append(f"        {vals},")
        lines.append(f"    ],")
    lines.append("];")
    return "\n".join(lines)


def _update_loop_rust_slice8(w: int, refin: bool) -> list[str]:
    """Emit the per-8-byte slice-by-8 main loop + byte-by-byte tail.

    Variable ``crc`` (mutable, of the appropriate width type) is assumed
    to already hold the incoming state.  Processes ``data[0..n-1]`` 8
    bytes at a time via 8 chained table lookups (the slice tables),
    then falls back to single-byte table-driven via
    ``CRC_SLICE_TABLES[0]`` for any 1-7 trailing bytes.

    Only valid for w == 32 or w == 64 (the only widths where slice-by-8
    has a meaningful "fits in a u64 chunk" equivalent).
    """
    if w == 32:
        if refin:
            # Reflected: input loaded little-endian, low byte of state
            # XOR'd with first 4 input bytes, table indices walk from
            # least-significant byte upward (T7..T0).
            return [
                "    let n = data.len();",
                "    let mut i: usize = 0;",
                "    while i + 8 <= n {",
                "        let b03 = data[i] as u32"
                " | (data[i+1] as u32) << 8"
                " | (data[i+2] as u32) << 16"
                " | (data[i+3] as u32) << 24;",
                "        let b47 = data[i+4] as u32"
                " | (data[i+5] as u32) << 8"
                " | (data[i+6] as u32) << 16"
                " | (data[i+7] as u32) << 24;",
                "        let xored = crc ^ b03;",
                "        crc = CRC_SLICE_TABLES[7][ (xored        & 0xFF) as usize]"
                " ^ CRC_SLICE_TABLES[6][((xored >>  8) & 0xFF) as usize]",
                "            ^ CRC_SLICE_TABLES[5][((xored >> 16) & 0xFF) as usize]"
                " ^ CRC_SLICE_TABLES[4][((xored >> 24) & 0xFF) as usize]",
                "            ^ CRC_SLICE_TABLES[3][ (b47          & 0xFF) as usize]"
                " ^ CRC_SLICE_TABLES[2][((b47   >>  8) & 0xFF) as usize]",
                "            ^ CRC_SLICE_TABLES[1][((b47   >> 16) & 0xFF) as usize]"
                " ^ CRC_SLICE_TABLES[0][((b47   >> 24) & 0xFF) as usize];",
                "        i += 8;",
                "    }",
                "    while i < n {",
                "        crc = CRC_SLICE_TABLES[0]"
                "[((crc ^ data[i] as u32) & 0xFF) as usize] ^ (crc >> 8);",
                "        i += 1;",
                "    }",
            ]
        # Non-reflected w=32: load big-endian, state's top XOR'd with
        # first 4 input bytes' top.  Table-index convention: byte at
        # position k in the chunk uses T[7-k] (k=0 is "most delayed",
        # i.e. the byte that has the most zero-bytes processed after
        # it to reach the end of the chunk).
        return [
            "    let n = data.len();",
            "    let mut i: usize = 0;",
            "    while i + 8 <= n {",
            "        let b03 = (data[i] as u32) << 24"
            " | (data[i+1] as u32) << 16"
            " | (data[i+2] as u32) << 8"
            " | data[i+3] as u32;",
            "        let b47 = (data[i+4] as u32) << 24"
            " | (data[i+5] as u32) << 16"
            " | (data[i+6] as u32) << 8"
            " | data[i+7] as u32;",
            "        let xored = crc ^ b03;",
            "        crc = CRC_SLICE_TABLES[7][((xored >> 24) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[6][((xored >> 16) & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[5][((xored >>  8) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[4][ (xored        & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[3][((b47   >> 24) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[2][((b47   >> 16) & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[1][((b47   >>  8) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[0][ (b47          & 0xFF) as usize];",
            "        i += 8;",
            "    }",
            "    while i < n {",
            "        let top = crc >> 24;",
            "        crc = CRC_SLICE_TABLES[0]"
            "[((top ^ data[i] as u32) & 0xFF) as usize] ^ (crc << 8);",
            "        i += 1;",
            "    }",
        ]
    # w == 64
    if refin:
        return [
            "    let n = data.len();",
            "    let mut i: usize = 0;",
            "    while i + 8 <= n {",
            "        let b = data[i] as u64"
            " | (data[i+1] as u64) << 8"
            " | (data[i+2] as u64) << 16"
            " | (data[i+3] as u64) << 24",
            "              | (data[i+4] as u64) << 32"
            " | (data[i+5] as u64) << 40"
            " | (data[i+6] as u64) << 48"
            " | (data[i+7] as u64) << 56;",
            "        let xored = crc ^ b;",
            "        crc = CRC_SLICE_TABLES[7][ (xored        & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[6][((xored >>  8) & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[5][((xored >> 16) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[4][((xored >> 24) & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[3][((xored >> 32) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[2][((xored >> 40) & 0xFF) as usize]",
            "            ^ CRC_SLICE_TABLES[1][((xored >> 48) & 0xFF) as usize]"
            " ^ CRC_SLICE_TABLES[0][((xored >> 56) & 0xFF) as usize];",
            "        i += 8;",
            "    }",
            "    while i < n {",
            "        crc = CRC_SLICE_TABLES[0]"
            "[((crc ^ data[i] as u64) & 0xFF) as usize] ^ (crc >> 8);",
            "        i += 1;",
            "    }",
        ]
    # Non-reflected w=64.  Same index convention as w=32: byte at
    # position k uses T[7-k].
    return [
        "    let n = data.len();",
        "    let mut i: usize = 0;",
        "    while i + 8 <= n {",
        "        let b = (data[i] as u64) << 56"
        " | (data[i+1] as u64) << 48"
        " | (data[i+2] as u64) << 40"
        " | (data[i+3] as u64) << 32",
        "              | (data[i+4] as u64) << 24"
        " | (data[i+5] as u64) << 16"
        " | (data[i+6] as u64) << 8"
        " | data[i+7] as u64;",
        "        let xored = crc ^ b;",
        "        crc = CRC_SLICE_TABLES[7][((xored >> 56) & 0xFF) as usize]"
        " ^ CRC_SLICE_TABLES[6][((xored >> 48) & 0xFF) as usize]",
        "            ^ CRC_SLICE_TABLES[5][((xored >> 40) & 0xFF) as usize]"
        " ^ CRC_SLICE_TABLES[4][((xored >> 32) & 0xFF) as usize]",
        "            ^ CRC_SLICE_TABLES[3][((xored >> 24) & 0xFF) as usize]"
        " ^ CRC_SLICE_TABLES[2][((xored >> 16) & 0xFF) as usize]",
        "            ^ CRC_SLICE_TABLES[1][((xored >>  8) & 0xFF) as usize]"
        " ^ CRC_SLICE_TABLES[0][ (xored        & 0xFF) as usize];",
        "        i += 8;",
        "    }",
        "    while i < n {",
        "        let top = crc >> 56;",
        "        crc = CRC_SLICE_TABLES[0]"
        "[((top ^ data[i] as u64) & 0xFF) as usize] ^ (crc << 8);",
        "        i += 1;",
        "    }",
    ]


def _update_loop_rust(
    w: int,
    poly: int,
    refin: bool,
    mask: str,
    rtype: str,
    table: bool,
) -> list[str]:
    """Emit the per-byte main-loop lines for the update function.

    Variable ``crc`` (of type ``rtype``) is assumed to already hold
    the incoming state; this returns only the for-loop that consumes
    ``data`` and updates ``crc`` in place.
    """
    if table:
        if w == 8:
            # For 8-bit CRC, table lookup IS the complete algorithm --
            # no shifts or masks needed.  Rust rejects ``u8 << 8`` as
            # arithmetic_overflow (correctly -- u8 has 8 bits), so the
            # generic shift-and-xor formula below would fail to compile
            # for w=8.  C silently widens the operand via integer
            # promotion and produces the same result, but emitting the
            # simplified form is cleaner output for both languages.
            return [
                "    for &byte in data {",
                "        crc = CRC_TABLE[(crc ^ byte) as usize];",
                "    }",
            ]
        if refin:
            return [
                "    for &byte in data {",
                f"        crc = CRC_TABLE[(crc ^ byte as {rtype}) as usize & 0xFF] ^ (crc >> 8);",
                "    }",
            ]
        return [
            "    for &byte in data {",
            f"        crc = CRC_TABLE[((crc >> {w - 8}) ^ byte as {rtype}) as usize & 0xFF] ^ (crc << 8) & {mask};",
            "    }",
        ]
    if refin:
        ref_poly = _reflect(poly, w)
        return [
            "    for &byte in data {",
            f"        crc ^= byte as {rtype};",
            "        for _ in 0..8 {",
            "            if crc & 1 != 0 {",
            f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)};",
            "            } else {",
            "                crc >>= 1;",
            "            }",
            "        }",
            "    }",
        ]
    if w < 8:
        # Sub-byte non-reflected: bit-by-bit, MSB first.  The byte-aligned
        # ``byte << (w - 8)`` fold is a negative shift for width < 8, which
        # Rust rejects at compile time (arithmetic_overflow).
        return [
            "    for &byte in data {",
            "        for j in (0..8).rev() {",
            f"            let bit = ((byte >> j) & 1) as {rtype};",
            f"            if (((crc >> {w - 1}) & 1) ^ bit) != 0 {{",
            f"                crc = ((crc << 1) ^ {_hex(poly, w)}) & {mask};",
            "            } else {",
            f"                crc = (crc << 1) & {mask};",
            "            }",
            "        }",
            "    }",
        ]
    return [
        "    for &byte in data {",
        f"        crc ^= (byte as {rtype}) << {w - 8};",
        "        for _ in 0..8 {",
        f"            if crc & {_hex(1 << (w - 1), w)} != 0 {{",
        f"                crc = (crc << 1) ^ {_hex(poly, w)};",
        "            } else {",
        "                crc <<= 1;",
        "            }",
        f"            crc &= {mask};",
        "        }",
        "    }",
    ]


def _self_test_rust(names, check, width, rtype, style, docs, goldens) -> str:
    """Emit a Rust ``pub fn <fname>_self_test() -> bool``.

    Returns true iff the generated CRC reproduces independent reference
    values.  Callable from any build configuration -- debug, release,
    embedded -- so downstream consumers can wire it into a boot
    self-check or a startup assertion, not just ``cargo test``.  Matches
    the convention of every other target (C returns 0/1; Go / C# /
    TypeScript / Python / Verilog / VHDL return bool).

    For a catalogue algorithm ``goldens`` carries four independent
    reference CRCs; the two large inputs are reproduced with
    byte-at-a-time loops (no embedded array).  A custom polynomial
    (``goldens is None``) falls back to the single ``check`` assertion.
    """
    n = names

    def lit(value: int) -> str:
        return f"{_hex(value, width)}_{rtype}"

    if goldens is None:
        lines = [
            "",
            *style.doc_block(docs["self_test"]),
            f"pub fn {n['self_test']}() -> bool {{",
            f'    {n["oneshot"]}(b"123456789") == {lit(check)}',
            "}",
        ]
        return "\n".join(lines)
    g = goldens
    lines = [
        "",
        *style.doc_block(docs["self_test"]),
        f"pub fn {n['self_test']}() -> bool {{",
        f'    if {n["oneshot"]}(b"") != {lit(g["empty"])} {{',
        "        return false;",
        "    }",
        f'    if {n["oneshot"]}(b"123456789") != {lit(g["check"])} {{',
        "        return false;",
        "    }",
        f"    let mut s = {n['init']}();",
        "    for i in 0u32..256 {",
        f"        s = {n['update']}(s, &[i as u8]);",
        "    }",
        f"    if {n['finalize']}(s) != {lit(g['all_bytes'])} {{",
        "        return false;",
        "    }",
        f"    s = {n['init']}();",
        "    for i in 0u32..1024 {",
        f"        s = {n['update']}(s, &[((i * 167 + 13) & 0xFF) as u8]);",
        "    }",
        f"    if {n['finalize']}(s) != {lit(g['binary_1k'])} {{",
        "        return false;",
        "    }",
        "    true",
        "}",
    ]
    return "\n".join(lines)


def generate_rust(
    name: str,
    symbol: str | None = None,
    variant: Literal["auto", "bitwise", "table", "slice8"] = "auto",
    comment_style: str = "plain",
    naming: str = "snake",
) -> str | None:
    """Look up a CRC algorithm by name and generate Rust source for it.

    Thin wrapper around :func:`generate_rust_from_entry`; use the
    latter directly when generating from a custom (non-catalogue)
    algorithm spec.
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_rust_from_entry(
        name, algo, symbol=symbol, variant=variant,
        comment_style=comment_style, naming=naming,
    )


def generate_rust_from_entry(
    name: str,
    algo: AlgorithmInfo,
    symbol: str | None = None,
    variant: Literal["auto", "bitwise", "table", "slice8"] = "auto",
    comment_style: str = "plain",
    naming: str = "snake",
    stem: str | None = None,
) -> str:
    """Generate Rust source from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).
        stem: Optional identifier-base override (cased per ``naming``,
            unlike the verbatim ``symbol``); ``name`` still labels the code.
        variant: Implementation shape -- ``"auto"`` (default -- fastest valid), ``"bitwise"``,
            ``"table"`` (256-entry lookup), or ``"slice8"`` (8 tables;
            requires ``algo.width`` to be 32 or 64; ``ValueError``
            otherwise).

    Returns:
        Rust source code string.
    """
    resolved = resolve_variant("rust", algo.width, variant)
    table, slice8 = _variant_to_flags(resolved)
    w = algo.width
    if w < 8 and table:
        # Sub-byte CRCs are bit-by-bit only (see variants_for_width); a stray
        # table request degrades to bitwise rather than emitting code Rust
        # rejects (``u8 >> 8`` is a compile-time overflow).
        table = False
    poly = algo.poly
    init = algo.init
    refin = algo.refin
    refout = algo.refout
    xorout = algo.xorout
    check = algo.check
    desc = algo.desc
    from crcglot.targets import naming_convention_for

    naming = naming_convention_for("rust", naming)
    base = symbol if symbol else _func_name(stem if stem is not None else name)
    names = crc_function_names(base, naming, is_override=symbol is not None)
    mask = _mask(w)

    if w <= 8:
        rtype = "u8"
    elif w <= 16:
        rtype = "u16"
    elif w <= 32:
        rtype = "u32"
    else:
        rtype = "u64"

    if slice8 and w not in (32, 64):
        raise ValueError(
            f"variant='slice8' requires width=32 or width=64 (got width={w}). "
            "Slice-by-8 is a high-throughput optimization that only "
            "makes sense at those widths; smaller CRCs would need a "
            "different chunking scheme."
        )

    # Pre-loaded init state for streaming entry.
    init_state = _reflect(init, w) if refin else init

    style = comment_style_for("rust", comment_style)
    provenance = build_prov(
        algo_source=algo.source, algorithm=name, target="rust",
        variant=resolved, comment=comment_style, symbol=base, naming=naming,
    )
    meta = AlgoMeta(
        name=name, desc=desc, width=w, poly=poly, init=init, refin=refin,
        refout=refout, xorout=xorout, check=check, variant=variant,
        provenance=provenance,
    )
    usage = UsageExample(
        streaming=(
            f"let s = {names['init']}();",
            f"let s = {names['update']}(s, chunk);  // over each chunk",
            f"let crc = {names['finalize']}(s);",
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
        names, state_type=rtype,
        data_params=(DocParam("data", "the message bytes."),),
        selftest_returns="true",
        refin=refin, refout=refout, xorout=xorout,
    )

    lines: list[str] = []
    if slice8:
        slice_tables = _build_slice8_tables(w, poly, refin)
        lines.append(_format_slice8_tables_rust(slice_tables, w, rtype))
        lines.append("")
    elif table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_rust(tbl, w, rtype))
        lines.append("")
    lines += style.file_header(meta, usage)
    lines.append("")

    # ``pub fn`` (not plain ``fn``) so the same file works equally well
    # as a standalone crate (where ``fn`` would suffice) and as a module
    # included into a parent crate via ``include!`` / ``mod`` (where
    # the caller needs the symbol to cross the mod boundary).  Plain
    # ``fn`` is private-to-mod by default in Rust.

    # ----- <fname>_init() -----
    lines += style.doc_block(docs["init"])
    lines.append(f"pub fn {names['init']}() -> {rtype} {{")
    lines.append(f"    {_hex(init_state, w)}")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines += style.doc_block(docs["update"])
    lines.append(
        f"pub fn {names['update']}(state: {rtype}, data: &[u8]) -> {rtype} {{"
    )
    lines.append(f"    let mut crc: {rtype} = state;")
    if slice8:
        lines.extend(_update_loop_rust_slice8(w, refin))
    else:
        lines.extend(_update_loop_rust(w, poly, refin, mask, rtype, table))
    lines.append(f"    crc")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines += style.doc_block(docs["finalize"])
    lines.append(f"pub fn {names['finalize']}(state: {rtype}) -> {rtype} {{")
    if refout != refin:
        lines.append(f"    // reflect output (refout != refin)")
        lines.append(f"    let mut reflected: {rtype} = 0;")
        lines.append(f"    for k in 0..{w} {{")
        lines.append(f"        reflected |= ((state >> k) & 1) << ({w - 1} - k);")
        lines.append(f"    }}")
        lines.append(f"    let state = reflected;")
    if xorout:
        lines.append(f"    state ^ {_hex(xorout, w)}")
    else:
        lines.append(f"    state")
    lines.append(f"}}")
    lines.append("")

    # ----- one-shot wrapper -----
    lines += style.doc_block(docs["oneshot"])
    lines.append(f"pub fn {names['oneshot']}(data: &[u8]) -> {rtype} {{")
    lines.append(
        f"    {names['finalize']}({names['update']}({names['init']}(), data))"
    )
    lines.append(f"}}")
    lines.append(
        _self_test_rust(names, check, w, rtype, style, docs, goldens_for(algo))
    )

    module = "\n".join(lines)
    # Namespace the lookup-table consts per symbol so several generated
    # modules (different algorithms, or one algorithm in multiple variants)
    # can live in one crate/module without colliding.  The emitters use the
    # fixed placeholders ``CRC_TABLE`` / ``CRC_SLICE_TABLES``; rewrite them
    # to the SCREAMING_SNAKE ``CRCGLOT_TABLE_<SYMBOL>`` /
    # ``CRCGLOT_SLICE_<SYMBOL>`` here.  Slice first; ``CRC_TABLE`` is not a
    # substring of ``CRC_SLICE_TABLES``.
    sym = base.upper()
    module = module.replace("CRC_SLICE_TABLES", f"CRCGLOT_SLICE_{sym}")
    module = module.replace("CRC_TABLE", f"CRCGLOT_TABLE_{sym}")
    return module
