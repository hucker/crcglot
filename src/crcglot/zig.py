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
    _build_table,
    _func_name,
    _hex,
    _mask,
)
from crcglot.catalogue import CRC_CATALOGUE, _reflect


def _zig_type(width: int) -> str:
    """Pick the Zig unsigned integer type for the algorithm width."""
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
    lines = [f"const crc_table = [256]{ztype}{{"]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append("};")
    return "\n".join(lines)


def _update_loop_zig(
    w: int, poly: int, refin: bool, mask: str, ztype: str, table: bool,
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
    if table:
        if w == 8:
            return [
                "    for (data) |b| {",
                "        crc = crc_table[@as(usize, crc ^ b)];",
                "    }",
            ]
        if refin:
            return [
                "    for (data) |b| {",
                f"        crc = crc_table[@as(usize, @as(u8, @truncate(crc)) ^ b)] ^ (crc >> 8);",
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
            f"        crc = crc_table[@as(usize, @as(u8, @truncate(crc >> {w - 8})) ^ b)] ^ ((crc & {keep_mask}) << 8);",
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
    name: str, table: bool = False, symbol: str | None = None,
) -> str | None:
    """Look up a CRC algorithm by name and generate Zig source for it.

    Thin wrapper around :func:`generate_zig_from_entry`; use the latter
    directly when generating from a custom (non-catalogue) algorithm
    spec.
    """
    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        return None
    return generate_zig_from_entry(name, entry, table=table, symbol=symbol)


def generate_zig_from_entry(
    name: str,
    entry: dict,
    table: bool = False,
    symbol: str | None = None,
) -> str:
    """Generate a Zig source file from a catalogue-shaped entry dict.

    Args:
        name: Algorithm name (used in comments and as the default
            function-name source).
        entry: Catalogue dict with ``width`` / ``poly`` / ``init`` /
            ``refin`` / ``refout`` / ``xorout`` / ``check`` (required)
            and ``desc`` (optional).
        table: If True, emit the table-driven implementation.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).

    Returns:
        Zig source code string.
    """
    w = entry["width"]
    poly = entry["poly"]
    init = entry["init"]
    refin = entry["refin"]
    refout = entry["refout"]
    xorout = entry["xorout"]
    check = entry["check"]
    desc = entry.get("desc", "")
    fname = symbol if symbol else _func_name(name)
    ztype = _zig_type(w)
    mask = _mask(w)

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

    if table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_zig(tbl, w, ztype))
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
    lines.extend(_update_loop_zig(w, poly, refin, mask, ztype, table))
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
