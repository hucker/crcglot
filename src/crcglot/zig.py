"""Zig CRC generator.

Emits a complete ``.zig`` file with five public functions:

  - ``<fname>_init() u{8,16,32,64}`` -- return the starting state
  - ``<fname>_update(state, data) ...`` -- feed bytes, return new state
  - ``<fname>_finalize(state) ...`` -- apply output reflection + xorout
  - ``<fname>(data) ...`` -- one-shot wrapper (init + update + finalize)
  - ``<fname>_self_test() bool`` -- True iff the algorithm reproduces
    the reveng catalogue's canonical check value

Zig's strict integer semantics shape the emit:

* ``u8 << 1`` of ``0x80`` is undefined behaviour (overflow); use the
  wrapping operator ``<<%`` whenever a shift could exceed the bit
  width.  We then mask explicitly to restore the algorithmic value.
* No implicit integer widening: shifting a ``u8`` left by ``w - 8``
  to align it with the state requires ``@as(uW, b)`` first.
* Array indexing requires ``usize``; the table-driven variant widens
  the byte index via ``@as(usize, ...)``.

The streaming primitives let callers compute a CRC over data that
arrives in chunks (file streams, network buffers, sensor data on a
microcontroller running Zig firmware) without buffering everything.
``_self_test()`` is callable from a Zig ``test`` block, a startup
self-check on bare-metal firmware, or anywhere -- no runtime
dependencies beyond the language's integer primitives.

Verified at build time by ``tests.test_zig_gen.TestGenerateZig``
(structural) and ``TestGeneratedZigExecutes`` (shells out to
``zig run``; slow-marked, skipped without ``zig`` on PATH).
"""

# ruff: noqa: F541  - f-strings without placeholders used for code alignment

from __future__ import annotations

from crcglot._helpers import (
    _build_slice8_tables,
    _build_table,
    _func_name,
    _hex,
    _mask,
)
from crcglot.catalogue import ALGORITHMS, AlgorithmInfo, _reflect


def _zig_type(width: int) -> str:
    """Pick the Zig unsigned integer type for the algorithm width."""
    if width <= 8:
        return "u8"
    if width <= 16:
        return "u16"
    if width <= 32:
        return "u32"
    return "u64"


def _format_table_zig(
    table: list[int], width: int, ztype: str, fname: str,
) -> str:
    """Format a lookup table as a Zig ``const`` array literal.

    Name prefixed with ``fname`` so multiple generated CRCs can live
    in one compilation unit without collisions.
    """
    hex_w = (width + 3) // 4
    lines = [f"const {fname}_table = [256]{ztype}{{"]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append("};")
    return "\n".join(lines)


def _format_slice8_tables_zig(
    tables: list[list[int]], width: int, ztype: str, fname: str,
) -> str:
    """Format the 8 slice-by-8 tables as a Zig 2D ``const`` array literal.

    Name prefixed with ``fname`` so multiple generated CRCs can live
    in one compilation unit without collisions.
    """
    hex_w = (width + 3) // 4
    lines = [f"const {fname}_sliceTables = [8][256]{ztype}{{"]
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


def _update_loop_zig_slice8(
    w: int, refin: bool, ztype: str, fname: str,
) -> list[str]:
    """Emit the per-8-byte slice-by-8 main loop + byte-by-byte tail for Zig.

    Variable ``crc`` (of type ``ztype``) is assumed to already hold the
    incoming state.  Walks ``data`` 8 bytes at a time via 8 chained
    table lookups, then falls back to single-byte via T0 for any 1-7
    trailing bytes.  Only valid for w == 32 or w == 64.

    Zig specifics:
    - No ``<<%`` operator; shifts that might overflow are sidestepped
      by masking before shifting, same trick as the bit-by-bit path.
    - Array indices must be ``usize`` -- byte values are widened via
      ``@as(usize, x)``.
    - ``@as(u8, @truncate(crc))`` narrows the state to its low byte.
    """
    t = f"{fname}_sliceTables"
    if w == 32:
        # ``crc << 8`` in the tail (non-reflected w=32) needs the
        # mask-before-shift trick because Zig rejects shifts that
        # would overflow uW.  See _update_loop_zig for the same idiom.
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
    w: int, poly: int, refin: bool, mask: str, ztype: str, table: bool,
    fname: str,
) -> list[str]:
    """Emit the per-byte main-loop lines for the update function.

    Zig is strict about shift overflow: ``crc << 1`` on a ``uW`` whose
    top bit is set is illegal behaviour (panic in debug, undefined in
    release) because the result would not fit in ``uW``.  Zig has no
    C-style wrapping shift operator -- ``<<%`` does not exist.  We
    sidestep this by **masking before shifting** so the shift result
    always fits, instead of masking after.  Mathematically equivalent
    to C's wrapping shift on unsigned types.
    """
    t = f"{fname}_table"
    if table:
        if w == 8:
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
        # Non-reflected w > 8.  The C equivalent is
        # ``(crc << 8) & mask`` -- shift left by 8 and drop the byte
        # that fell off the top.  Equivalent in unsigned arithmetic
        # to masking the bottom (w - 8) bits first and shifting them
        # up by 8: result max is ``(2^(w-8) - 1) * 256``, which fits
        # in uW.  This avoids the shift-overflow that ``crc << 8`` on
        # the full state would trigger in Zig's safe mode.
        keep_mask = _hex((1 << (w - 8)) - 1, w)
        return [
            "    for (data) |b| {",
            f"        crc = {t}[@as(usize, @as(u8, @truncate(crc >> {w - 8})) ^ b)] ^ ((crc & {keep_mask}) << 8);",
            "    }",
        ]
    if refin:
        ref_poly = _reflect(poly, w)
        widened_b = "b" if w == 8 else f"@as({ztype}, b)"
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
    # Non-reflected bit-by-bit.  Two-arm structure of the loop:
    #
    #   * Top-bit-clear branch: ``crc << 1`` is safe (the high bit
    #     becomes the new top bit, max result is 2^w - 2).
    #   * Top-bit-set branch: we'd overflow on a raw ``crc << 1``, so
    #     we mask off the top bit first; the masked-and-shifted value
    #     plus the XOR with ``poly`` is equivalent to the C version's
    #     ``(crc << 1) ^ poly`` after the implicit overflow drop.
    if w == 8:
        align_in = "b"
    else:
        align_in = f"(@as({ztype}, b) << {w - 8})"
    top_bit = _hex(1 << (w - 1), w)
    low_mask = _hex((1 << (w - 1)) - 1, w)
    _ = mask  # see method docstring -- pre-shift masking removes need for it
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


def _self_test_zig(fname: str, check: int, width: int) -> list[str]:
    """Emit a Zig self-test function returning true on success."""
    return [
        f"pub fn {fname}_self_test() bool {{",
        f'    return {fname}("123456789") == {_hex(check, width)};',
        f"}}",
    ]


def generate_zig(
    name: str,
    table: bool = False,
    symbol: str | None = None,
    slice8: bool = False,
) -> str | None:
    """Look up a CRC algorithm by name and generate Zig source for it.

    Thin wrapper around :func:`generate_zig_from_entry`; use the latter
    directly when generating from a custom (non-catalogue) algorithm
    spec.
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_zig_from_entry(
        name, algo, table=table, symbol=symbol, slice8=slice8,
    )


def generate_zig_from_entry(
    name: str,
    algo: AlgorithmInfo,
    table: bool = False,
    symbol: str | None = None,
    slice8: bool = False,
) -> str:
    """Generate a Zig source file from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments and as the default
            function-name source).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        table: If True, emit the table-driven implementation.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).
        slice8: If True, emit the slice-by-8 implementation (8 tables,
            ~5-10x throughput vs plain table-driven for CRC-32 /
            CRC-64 on large buffers).  Only valid for width 32 or 64;
            ``ValueError`` otherwise.

    Returns:
        Zig source code string.
    """
    w = algo.width
    poly = algo.poly
    init = algo.init
    refin = algo.refin
    refout = algo.refout
    xorout = algo.xorout
    check = algo.check
    desc = algo.desc
    fname = symbol if symbol else _func_name(name)
    ztype = _zig_type(w)
    mask = _mask(w)

    if slice8 and w not in (32, 64):
        raise ValueError(
            f"slice8=True requires width=32 or width=64 (got width={w}). "
            "Slice-by-8 is a high-throughput optimization that only "
            "makes sense at those widths; smaller CRCs would need a "
            "different chunking scheme."
        )

    init_state = _reflect(init, w) if refin else init

    lines: list[str] = []
    lines.append(f"// {fname}.zig -- generated by crcglot from reveng/{name}")
    lines.append(f"// {desc}")
    lines.append(f'// check: {fname}("123456789") == {_hex(check, w)}')
    lines.append(f"//")
    lines.append(f"// Streaming: init -> update (any number of times) -> finalize.")
    lines.append(f"// One-shot:  call {fname}(data).")
    lines.append(f"// Verify:    call {fname}_self_test() (returns true on success).")
    lines.append(f"")

    if slice8:
        slice_tables = _build_slice8_tables(w, poly, refin)
        lines.append(_format_slice8_tables_zig(slice_tables, w, ztype, fname))
        lines.append("")
    elif table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_zig(tbl, w, ztype, fname))
        lines.append("")

    # ----- <fname>_init() -----
    lines.append(f"pub fn {fname}_init() {ztype} {{")
    lines.append(f"    return {_hex(init_state, w)};")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines.append(
        f"pub fn {fname}_update(state: {ztype}, data: []const u8) {ztype} {{"
    )
    lines.append(f"    var crc: {ztype} = state;")
    if slice8:
        lines.extend(_update_loop_zig_slice8(w, refin, ztype, fname))
    else:
        lines.extend(_update_loop_zig(w, poly, refin, mask, ztype, table, fname))
    lines.append(f"    return crc;")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines.append(f"pub fn {fname}_finalize(state: {ztype}) {ztype} {{")
    if refout != refin:
        lines.append(f"    // reflect output (refout != refin)")
        lines.append(f"    var reflected: {ztype} = 0;")
        lines.append(f"    var k: u32 = 0;")
        lines.append(f"    while (k < {w}) : (k += 1) {{")
        shamt = f"@as(u{max(w.bit_length(), 1)}, @intCast({w - 1} - k))"
        lines.append(
            f"        reflected |= "
            f"(@as({ztype}, (state >> @as(u{max(w.bit_length(), 1)}, @intCast(k))) & 1)) << "
            f"{shamt};"
        )
        lines.append(f"    }}")
        lines.append(f"    var s: {ztype} = reflected;")
        if xorout:
            lines.append(f"    return s ^ {_hex(xorout, w)};")
        else:
            lines.append(f"    return s;")
    elif xorout:
        lines.append(f"    return state ^ {_hex(xorout, w)};")
    else:
        lines.append(f"    return state;")
    lines.append(f"}}")
    lines.append("")

    # ----- one-shot wrapper -----
    lines.append(f"pub fn {fname}(data: []const u8) {ztype} {{")
    lines.append(
        f"    return {fname}_finalize({fname}_update({fname}_init(), data));"
    )
    lines.append(f"}}")
    lines.append("")

    # ----- self-test -----
    lines.extend(_self_test_zig(fname, check, w))

    return "\n".join(lines)
