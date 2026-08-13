"""Zig CRC generator.

Emits a complete ``.zig`` file with five public functions:

  - ``<fname>Init() u{8,16,32,64}`` -- return the starting state
  - ``<fname>Update(state, data) ...`` -- feed bytes, return new state
  - ``<fname>Finalize(state) ...`` -- apply output reflection + xorout
  - ``<fname>(data) ...`` -- one-shot wrapper (init + update + finalize)
  - ``<fname>SelfTest() bool`` -- true iff the generated CRC
    reproduces its independent reference values

plus a Zig-native ``test`` declaration wrapping the self-test, so
``zig test <file>.zig`` verifies the module with no harness; normal
builds strip test declarations, and the runtime-callable ``SelfTest``
stays available for boot checks (the same both-worlds lesson Rust's
generator learned when its ``#[cfg(test)]`` block proved invisible
outside ``cargo test``).

The emitted code imports nothing, not even ``std`` -- only language
primitives.  That makes the functions pure, so they also run at
compile time (``comptime``), and it keeps the output clear of Zig's
fastest-moving surface: the 0.13 -> 0.16 releases removed ``std.io``
entirely while the operators and builtins this module emits (``@as``,
``@truncate``, ``@bitReverse``) were untouched.

Zig's strict integer semantics shape the emit:

* ``crc << 1`` on a ``uW`` whose top bit is set is illegal behaviour
  (safety panic in Debug).  Zig has no C-style wrapping shift, so the
  non-reflected paths **mask before shifting** -- the shift result
  always fits, equivalent to C's wrapping shift on unsigned types.
* No implicit integer widening: aligning a byte with the state
  requires ``@as(uW, b)`` first.
* Array indexing requires ``usize``; table lookups widen the byte
  index via ``@as(usize, ...)``.

Verified at build time by ``tests.test_zig_gen`` (structural checks
plus the ``zig_batch`` single-build execution tier; per-algorithm
isolation classes behind ``--exhaustive``).
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


def _zig_type(width: int) -> str:
    """Pick the Zig unsigned container type for the algorithm width.

    Zig has arbitrary-width integers (``u5``, ``u24``), but the byte-size
    containers keep shifts and table indexing free of ``Log2Int`` cast
    gymnastics and match every other generator's shape; explicit masks
    hold the state to ``width`` bits, same as C / Rust / Go.
    """
    if width <= 8:
        return "u8"
    if width <= 16:
        return "u16"
    if width <= 32:
        return "u32"
    return "u64"


def _format_table_zig(table: list[int], width: int, ztype: str) -> str:
    """Format a lookup table as a Zig ``const`` array literal."""
    hex_w = (width + 3) // 4
    lines = [f"const CRC_TABLE = [256]{ztype}{{"]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append("};")
    return "\n".join(lines)


def _format_slice8_tables_zig(
    tables: list[list[int]], width: int, ztype: str,
) -> str:
    """Format the 8 slice-by-8 tables as a Zig 2D ``const`` array literal."""
    hex_w = (width + 3) // 4
    lines = [f"const CRC_SLICE_TABLES = [8][256]{ztype}{{"]
    for t_idx, table in enumerate(tables):
        lines.append(f"    // T{t_idx}")
        lines.append(f"    [256]{ztype}{{")
        for row in range(0, 256, 8):
            vals = ", ".join(
                f"0x{table[i]:0{hex_w}X}"
                for i in range(row, min(row + 8, 256))
            )
            lines.append(f"        {vals},")
        lines.append(f"    }},")
    lines.append("};")
    return "\n".join(lines)


def _update_loop_zig_slice8(w: int, refin: bool) -> list[str]:
    """Emit the per-8-byte slice-by-8 main loop + byte-by-byte tail.

    Variable ``crc`` (of the container type) is assumed to already hold
    the incoming state.  Walks ``data`` 8 bytes at a time via 8 chained
    table lookups, then falls back to single-byte via T0 for any 1-7
    trailing bytes.  Only valid for w == 32 or w == 64.

    The non-reflected tails use the mask-before-shift idiom (see
    :func:`_update_loop_zig`); byte extraction is
    ``@as(u8, @truncate(...))`` widened to ``usize`` for indexing.
    """
    t = "CRC_SLICE_TABLES"
    if w == 32:
        keep_24 = "0xFFFFFF"
        if refin:
            return [
                "    var d: []const u8 = data;",
                "    while (d.len >= 8) {",
                "        const b03: u32 = @as(u32, d[0]) | @as(u32, d[1]) << 8"
                " | @as(u32, d[2]) << 16 | @as(u32, d[3]) << 24;",
                "        const b47: u32 = @as(u32, d[4]) | @as(u32, d[5]) << 8"
                " | @as(u32, d[6]) << 16 | @as(u32, d[7]) << 24;",
                "        const xored: u32 = crc ^ b03;",
                f"        crc = {t}[7][@as(usize, @as(u8, @truncate(xored)))]"
                f" ^ {t}[6][@as(usize, @as(u8, @truncate(xored >> 8)))]",
                f"            ^ {t}[5][@as(usize, @as(u8, @truncate(xored >> 16)))]"
                f" ^ {t}[4][@as(usize, @as(u8, @truncate(xored >> 24)))]",
                f"            ^ {t}[3][@as(usize, @as(u8, @truncate(b47)))]"
                f" ^ {t}[2][@as(usize, @as(u8, @truncate(b47 >> 8)))]",
                f"            ^ {t}[1][@as(usize, @as(u8, @truncate(b47 >> 16)))]"
                f" ^ {t}[0][@as(usize, @as(u8, @truncate(b47 >> 24)))];",
                "        d = d[8..];",
                "    }",
                "    for (d) |b| {",
                f"        crc = {t}[0][@as(usize, @as(u8, @truncate(crc)) ^ b)]"
                " ^ (crc >> 8);",
                "    }",
            ]
        return [
            "    var d: []const u8 = data;",
            "    while (d.len >= 8) {",
            "        const b03: u32 = @as(u32, d[0]) << 24 | @as(u32, d[1]) << 16"
            " | @as(u32, d[2]) << 8 | @as(u32, d[3]);",
            "        const b47: u32 = @as(u32, d[4]) << 24 | @as(u32, d[5]) << 16"
            " | @as(u32, d[6]) << 8 | @as(u32, d[7]);",
            "        const xored: u32 = crc ^ b03;",
            f"        crc = {t}[7][@as(usize, @as(u8, @truncate(xored >> 24)))]"
            f" ^ {t}[6][@as(usize, @as(u8, @truncate(xored >> 16)))]",
            f"            ^ {t}[5][@as(usize, @as(u8, @truncate(xored >> 8)))]"
            f" ^ {t}[4][@as(usize, @as(u8, @truncate(xored)))]",
            f"            ^ {t}[3][@as(usize, @as(u8, @truncate(b47 >> 24)))]"
            f" ^ {t}[2][@as(usize, @as(u8, @truncate(b47 >> 16)))]",
            f"            ^ {t}[1][@as(usize, @as(u8, @truncate(b47 >> 8)))]"
            f" ^ {t}[0][@as(usize, @as(u8, @truncate(b47)))];",
            "        d = d[8..];",
            "    }",
            "    for (d) |b| {",
            "        const top: u8 = @as(u8, @truncate(crc >> 24));",
            f"        crc = {t}[0][@as(usize, top ^ b)]"
            f" ^ ((crc & {keep_24}) << 8);",
            "    }",
        ]
    # w == 64.
    keep_56 = "0xFFFFFFFFFFFFFF"
    if refin:
        return [
            "    var d: []const u8 = data;",
            "    while (d.len >= 8) {",
            "        const b: u64 = @as(u64, d[0]) | @as(u64, d[1]) << 8"
            " | @as(u64, d[2]) << 16 | @as(u64, d[3]) << 24",
            "            | @as(u64, d[4]) << 32 | @as(u64, d[5]) << 40"
            " | @as(u64, d[6]) << 48 | @as(u64, d[7]) << 56;",
            "        const xored: u64 = crc ^ b;",
            f"        crc = {t}[7][@as(usize, @as(u8, @truncate(xored)))]"
            f" ^ {t}[6][@as(usize, @as(u8, @truncate(xored >> 8)))]",
            f"            ^ {t}[5][@as(usize, @as(u8, @truncate(xored >> 16)))]"
            f" ^ {t}[4][@as(usize, @as(u8, @truncate(xored >> 24)))]",
            f"            ^ {t}[3][@as(usize, @as(u8, @truncate(xored >> 32)))]"
            f" ^ {t}[2][@as(usize, @as(u8, @truncate(xored >> 40)))]",
            f"            ^ {t}[1][@as(usize, @as(u8, @truncate(xored >> 48)))]"
            f" ^ {t}[0][@as(usize, @as(u8, @truncate(xored >> 56)))];",
            "        d = d[8..];",
            "    }",
            "    for (d) |b| {",
            f"        crc = {t}[0][@as(usize, @as(u8, @truncate(crc)) ^ b)]"
            " ^ (crc >> 8);",
            "    }",
        ]
    return [
        "    var d: []const u8 = data;",
        "    while (d.len >= 8) {",
        "        const b: u64 = @as(u64, d[0]) << 56 | @as(u64, d[1]) << 48"
        " | @as(u64, d[2]) << 40 | @as(u64, d[3]) << 32",
        "            | @as(u64, d[4]) << 24 | @as(u64, d[5]) << 16"
        " | @as(u64, d[6]) << 8 | @as(u64, d[7]);",
        "        const xored: u64 = crc ^ b;",
        f"        crc = {t}[7][@as(usize, @as(u8, @truncate(xored >> 56)))]"
        f" ^ {t}[6][@as(usize, @as(u8, @truncate(xored >> 48)))]",
        f"            ^ {t}[5][@as(usize, @as(u8, @truncate(xored >> 40)))]"
        f" ^ {t}[4][@as(usize, @as(u8, @truncate(xored >> 32)))]",
        f"            ^ {t}[3][@as(usize, @as(u8, @truncate(xored >> 24)))]"
        f" ^ {t}[2][@as(usize, @as(u8, @truncate(xored >> 16)))]",
        f"            ^ {t}[1][@as(usize, @as(u8, @truncate(xored >> 8)))]"
        f" ^ {t}[0][@as(usize, @as(u8, @truncate(xored)))];",
        "        d = d[8..];",
        "    }",
        "    for (d) |b| {",
        "        const top: u8 = @as(u8, @truncate(crc >> 56));",
        f"        crc = {t}[0][@as(usize, top ^ b)]"
        f" ^ ((crc & {keep_56}) << 8);",
        "    }",
    ]


def _update_loop_zig(
    w: int,
    poly: int,
    refin: bool,
    mask: str,
    ztype: str,
    table: bool,
) -> list[str]:
    """Emit the per-byte main-loop lines for the update function.

    Zig is strict about shift overflow: ``crc << 1`` on a ``uW`` whose
    top bit is set is illegal behaviour (safety panic in Debug builds).
    Zig has no C-style wrapping shift operator, so the non-reflected
    paths **mask before shifting** -- the shift result always fits,
    which is mathematically equivalent to C's wrapping shift on
    unsigned types.  Sub-byte widths get headroom for free from the
    ``u8`` container; the mask holds the state to ``w`` bits.
    """
    t = "CRC_TABLE"
    if table:
        if w == 8:
            # Table lookup IS the complete algorithm at width 8 -- no
            # shifts or masks needed (same simplification as Rust / C).
            return [
                "    for (data) |b| {",
                f"        crc = {t}[@as(usize, crc ^ b)];",
                "    }",
            ]
        if refin:
            return [
                "    for (data) |b| {",
                f"        crc = {t}[@as(usize, @as(u8, @truncate(crc)) ^ b)] ^ (crc >> 8);",
                "    }",
            ]
        # Non-reflected w > 8.  C's ``(crc << 8) & mask`` becomes
        # mask-the-bottom-then-shift: ``(crc & keep) << 8`` where keep
        # holds the low (w - 8) bits, so the result always fits the
        # container and never trips Zig's shift-overflow safety check.
        keep_mask = _hex((1 << (w - 8)) - 1, w)
        return [
            "    for (data) |b| {",
            f"        crc = {t}[@as(usize, @as(u8, @truncate(crc >> {w - 8})) ^ b)] ^ ((crc & {keep_mask}) << 8);",
            "    }",
        ]
    if refin:
        # Reflected bit-by-bit works for every width, sub-byte included:
        # the whole input byte lands in the low bits and shifts down
        # through the polynomial taps (same generic loop as Rust).
        ref_poly = _reflect(poly, w)
        widened_b = "b" if ztype == "u8" else f"@as({ztype}, b)"
        return [
            "    for (data) |b| {",
            f"        crc ^= {widened_b};",
            "        var i: u32 = 0;",
            "        while (i < 8) : (i += 1) {",
            "            if (crc & 1 != 0) {",
            f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)};",
            "            } else {",
            "                crc >>= 1;",
            "            }",
            "        }",
            "    }",
        ]
    if w < 8:
        # Sub-byte non-reflected: bit-by-bit, MSB first.  The byte-aligned
        # ``@as(u8, b) << (w - 8)`` fold would be a negative shift below
        # width 8.  The u8 container has headroom above ``w`` bits, so a
        # plain ``crc << 1`` cannot overflow; the mask re-narrows to w.
        return [
            "    for (data) |b| {",
            "        var j: u32 = 8;",
            "        while (j > 0) : (j -= 1) {",
            "            const bit: u8 = (b >> @as(u3, @intCast(j - 1))) & 1;",
            f"            if ((((crc >> {w - 1}) & 1) ^ bit) != 0) {{",
            f"                crc = ((crc << 1) ^ {_hex(poly, w)}) & {mask};",
            "            } else {",
            f"                crc = (crc << 1) & {mask};",
            "            }",
            "        }",
            "    }",
        ]
    # Non-reflected bit-by-bit, w >= 8.  Two-arm loop:
    #   * top-bit-clear: ``crc << 1`` is safe (result stays under 2^w).
    #   * top-bit-set: mask the top bit off first, then shift + XOR the
    #     polynomial -- equivalent to C's wrapping ``(crc << 1) ^ poly``.
    align_in = "b" if w == 8 else f"(@as({ztype}, b) << {w - 8})"
    top_bit = _hex(1 << (w - 1), w)
    low_mask = _hex((1 << (w - 1)) - 1, w)
    del mask  # pre-shift masking keeps every result in range without it
    return [
        "    for (data) |b| {",
        f"        crc ^= {align_in};",
        "        var i: u32 = 0;",
        "        while (i < 8) : (i += 1) {",
        f"            if (crc & {top_bit} != 0) {{",
        f"                crc = ((crc & {low_mask}) << 1) ^ {_hex(poly, w)};",
        "            } else {",
        "                crc = crc << 1;",
        "            }",
        "        }",
        "    }",
    ]


def _self_test_zig(names, check, width, ztype, style, docs, goldens) -> str:
    """Emit ``pub fn <fname>SelfTest() bool`` plus a ``test`` declaration.

    The ``pub fn`` is callable from any build -- a boot self-check on
    bare-metal firmware, a startup assertion -- and the trailing
    Zig-native ``test`` block makes ``zig test <file>.zig`` verify the
    module with zero harness code.  Test declarations are stripped from
    non-test builds, so shipping both costs nothing at runtime.

    For a catalogue algorithm ``goldens`` carries four independent
    reference CRCs; the two large inputs are reproduced with
    byte-at-a-time loops (no embedded array).  A custom polynomial
    (``goldens is None``) falls back to the single ``check`` assertion.
    """
    n = names
    lines: list[str] = ["", *style.doc_block(docs["self_test"])]
    if goldens is None:
        lines += [
            f"pub fn {n['self_test']}() bool {{",
            f'    return {n["oneshot"]}("123456789") == {_hex(check, width)};',
            "}",
        ]
    else:
        g = goldens
        lines += [
            f"pub fn {n['self_test']}() bool {{",
            f'    if ({n["oneshot"]}("") != {_hex(g["empty"], width)}) return false;',
            f'    if ({n["oneshot"]}("123456789") != {_hex(g["check"], width)}) return false;',
            f"    var s = {n['init']}();",
            "    var i: u32 = 0;",
            "    while (i < 256) : (i += 1) {",
            f"        s = {n['update']}(s, &[_]u8{{@as(u8, @truncate(i))}});",
            "    }",
            f"    if ({n['finalize']}(s) != {_hex(g['all_bytes'], width)}) return false;",
            f"    s = {n['init']}();",
            "    i = 0;",
            "    while (i < 1024) : (i += 1) {",
            f"        s = {n['update']}(s, &[_]u8{{@as(u8, @truncate(i * 167 + 13))}});",
            "    }",
            f"    if ({n['finalize']}(s) != {_hex(g['binary_1k'], width)}) return false;",
            "    return true;",
            "}",
        ]
    lines += [
        "",
        "// `zig test <file>.zig` runs this; normal builds strip it.",
        f'test "{n["oneshot"]} self-test" {{',
        f"    if (!{n['self_test']}()) return error.SelfTestFailed;",
        "}",
    ]
    del ztype  # literals coerce from comptime_int; no suffix needed
    return "\n".join(lines)


def generate_zig(
    name: str,
    symbol: str | None = None,
    variant: Literal["auto", "bitwise", "table", "slice8"] = "auto",
    comment_style: str = "plain",
    naming: str = "camel",
) -> str | None:
    """Look up a CRC algorithm by name and generate Zig source for it.

    Thin wrapper around :func:`generate_zig_from_entry`; use the
    latter directly when generating from a custom (non-catalogue)
    algorithm spec.
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_zig_from_entry(
        name, algo, symbol=symbol, variant=variant,
        comment_style=comment_style, naming=naming,
    )


def generate_zig_from_entry(
    name: str,
    algo: AlgorithmInfo,
    symbol: str | None = None,
    variant: Literal["auto", "bitwise", "table", "slice8"] = "auto",
    comment_style: str = "plain",
    naming: str = "camel",
    stem: str | None = None,
) -> str:
    """Generate Zig source from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).
        variant: Implementation shape -- ``"auto"`` (default -- fastest
            valid), ``"bitwise"``, ``"table"`` (256-entry lookup), or
            ``"slice8"`` (8 tables; requires ``algo.width`` to be 32 or
            64; ``ValueError`` otherwise).
        comment_style: Documentation comment style (``"plain"``).
        naming: Function-name convention -- ``"camel"`` (Zig's style-guide
            default) or ``"snake"``.
        stem: Optional identifier-base override (cased per ``naming``,
            unlike the verbatim ``symbol``); ``name`` still labels the code.

    Returns:
        Zig source code string.

    Examples:
        >>> src = generate_zig_from_entry("crc32", ALGORITHMS["crc32"])
        >>> "pub fn crc32Update" in src
        True
    """
    resolved = resolve_variant("zig", algo.width, variant)
    table, slice8 = _variant_to_flags(resolved)
    w = algo.width
    if w < 8 and table:
        # Sub-byte CRCs are bit-by-bit only (see variants_for_width); a stray
        # table request degrades to bitwise rather than emitting a negative
        # shift Zig rejects at compile time.
        table = False
    poly = algo.poly
    init = algo.init
    refin = algo.refin
    refout = algo.refout
    xorout = algo.xorout
    check = algo.check
    desc = algo.desc
    from crcglot.targets import naming_convention_for

    naming = naming_convention_for("zig", naming)
    base = symbol if symbol else _func_name(stem if stem is not None else name)
    names = crc_function_names(base, naming, is_override=symbol is not None)
    mask = _mask(w)
    ztype = _zig_type(w)

    if slice8 and w not in (32, 64):
        raise ValueError(
            f"variant='slice8' requires width=32 or width=64 (got width={w}). "
            "Slice-by-8 is a high-throughput optimization that only "
            "makes sense at those widths; smaller CRCs would need a "
            "different chunking scheme."
        )

    # Pre-loaded init state for streaming entry.
    init_state = _reflect(init, w) if refin else init

    style = comment_style_for("zig", comment_style)
    provenance = build_prov(
        algo_source=algo.source, algorithm=name, target="zig",
        variant=resolved, comment=comment_style, symbol=base, naming=naming,
    )
    meta = AlgoMeta(
        name=name, desc=desc, width=w, poly=poly, init=init, refin=refin,
        refout=refout, xorout=xorout, check=check, variant=variant,
        provenance=provenance, custom=algo.source == "custom",
    )
    goldens = goldens_for(algo)
    usage = UsageExample(
        streaming=(
            f"var s = {names['init']}();",
            f"s = {names['update']}(s, chunk);  // over each chunk",
            f"const crc = {names['finalize']}(s);",
        ),
        oneshot=f"{names['oneshot']}(data)",
        selftest=f"{names['self_test']}()",
        selftest_returns="returns true on success",
        caveats=(
            ("Import-free and pure: also runs at comptime (raise "
             "@setEvalBranchQuota for long inputs).",)
            + (
                ("Variant: slice-by-8 (8 tables, ~10x throughput vs a "
                 "plain table for large buffers).",)
                if slice8 else ()
            )
        ),
    )
    docs = standard_doc_blocks(
        names, state_type=ztype,
        data_params=(DocParam("data", "the message bytes."),),
        selftest_returns="true",
        refin=refin, refout=refout, xorout=xorout,
        independent_refs=goldens is not None,
    )

    lines: list[str] = []
    if slice8:
        slice_tables = _build_slice8_tables(w, poly, refin)
        lines.append(_format_slice8_tables_zig(slice_tables, w, ztype))
        lines.append("")
    elif table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_zig(tbl, w, ztype))
        lines.append("")
    lines += style.file_header(meta, usage)
    lines.append("")

    # ----- <fname>Init() -----
    lines += style.doc_block(docs["init"])
    lines.append(f"pub fn {names['init']}() {ztype} {{")
    lines.append(f"    return {_hex(init_state, w)};")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>Update(state, data) -----
    lines += style.doc_block(docs["update"])
    lines.append(
        f"pub fn {names['update']}(state: {ztype}, data: []const u8) {ztype} {{"
    )
    lines.append(f"    var crc: {ztype} = state;")
    if slice8:
        lines.extend(_update_loop_zig_slice8(w, refin))
    else:
        lines.extend(_update_loop_zig(w, poly, refin, mask, ztype, table))
    lines.append(f"    return crc;")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>Finalize(state) -----
    lines += style.doc_block(docs["finalize"])
    lines.append(f"pub fn {names['finalize']}(state: {ztype}) {ztype} {{")
    if refout != refin:
        # ``@bitReverse`` reverses over the CONTAINER's bits; shifting
        # right by the headroom re-aligns to a w-bit reflection.  The
        # shift amount is comptime-known, so no Log2Int cast is needed.
        container_bits = int(ztype[1:])
        shift = container_bits - w
        lines.append(f"    // reflect output (refout != refin)")
        if shift:
            lines.append(
                f"    const reflected: {ztype} = @bitReverse(state) >> {shift};"
            )
        else:
            lines.append(f"    const reflected: {ztype} = @bitReverse(state);")
        if xorout:
            lines.append(f"    return reflected ^ {_hex(xorout, w)};")
        else:
            lines.append(f"    return reflected;")
    elif xorout:
        lines.append(f"    return state ^ {_hex(xorout, w)};")
    else:
        lines.append(f"    return state;")
    lines.append(f"}}")
    lines.append("")

    # ----- one-shot wrapper -----
    lines += style.doc_block(docs["oneshot"])
    lines.append(f"pub fn {names['oneshot']}(data: []const u8) {ztype} {{")
    lines.append(
        f"    return {names['finalize']}({names['update']}({names['init']}(), data));"
    )
    lines.append(f"}}")
    lines.append(
        _self_test_zig(names, check, w, ztype, style, docs, goldens)
    )

    module = "\n".join(lines)
    # Namespace the lookup-table consts per symbol so several generated
    # modules (different algorithms, or one algorithm in multiple variants)
    # can live in one file without colliding.  The emitters use the fixed
    # placeholders ``CRC_TABLE`` / ``CRC_SLICE_TABLES``; rewrite them to
    # ``crcglot_table_<symbol>`` / ``crcglot_slice_<symbol>`` here.  Slice
    # first; ``CRC_TABLE`` is not a substring of ``CRC_SLICE_TABLES``.
    module = module.replace("CRC_SLICE_TABLES", f"crcglot_slice_{base}")
    module = module.replace("CRC_TABLE", f"crcglot_table_{base}")
    return module
