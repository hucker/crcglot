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
trio (e.g. ``symbol=Crc32`` for Go-idiomatic exported names).

Verified at build time by ``tests.test_go_gen.TestGenerateGo``
(structural) and ``TestGeneratedGoExecutes`` (compile + run via
``go run``; slow-marked, skipped without ``go`` on PATH).
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


def _format_table_go(table: list[int], width: int, gtype: str) -> str:
    """Format a lookup table as a Go ``var`` array literal."""
    hex_w = (width + 3) // 4
    lines = [f"var _crcTable = [256]{gtype}{{"]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append("}")
    return "\n".join(lines)


def _update_loop_go(
    w: int, poly: int, refin: bool, mask: str, gtype: str, table: bool,
) -> list[str]:
    """Emit the per-byte main-loop lines for the update function."""
    if table:
        if w == 8:
            # 8-bit: table lookup IS the algorithm (no shifts, and
            # ``uint8 << 8`` would overflow at compile time in Go's
            # constant evaluator anyway).
            return [
                "    for _, b := range data {",
                "        crc = _crcTable[crc^b]",
                "    }",
            ]
        if refin:
            return [
                "    for _, b := range data {",
                f"        crc = _crcTable[{gtype}(byte(crc)^b)] ^ (crc >> 8)",
                "    }",
            ]
        return [
            "    for _, b := range data {",
            f"        crc = _crcTable[byte(crc>>{w - 8})^b] ^ ((crc << 8) & {mask})",
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


def _self_test_go(fname: str, check: int, width: int) -> list[str]:
    """Emit a Go self-test function returning true on success."""
    return [
        f"func {fname}_self_test() bool {{",
        f'    return {fname}([]byte("123456789")) == {_hex(check, width)}',
        f"}}",
    ]


def generate_go(
    name: str, table: bool = False, symbol: str | None = None,
) -> str | None:
    """Look up a CRC algorithm by name and generate Go source for it.

    Thin wrapper around :func:`generate_go_from_entry`; use the latter
    directly when generating from a custom (non-catalogue) algorithm
    spec.
    """
    entry = CRC_CATALOGUE.get(name)
    if entry is None:
        return None
    return generate_go_from_entry(name, entry, table=table, symbol=symbol)


def generate_go_from_entry(
    name: str,
    entry: dict,
    table: bool = False,
    symbol: str | None = None,
) -> str:
    """Generate a Go source file from a catalogue-shaped entry dict.

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
        Go source code string declaring ``package crc``.
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
    mask = _mask(w)

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

    lines: list[str] = []
    lines.append(f"// {fname}.go -- generated by crcglot from reveng/{name}")
    lines.append(f"// {desc}")
    lines.append(f'// check: {fname}([]byte("123456789")) == {_hex(check, w)}')
    lines.append(f"//")
    lines.append(f"// Streaming: init -> update (any number of times) -> finalize.")
    lines.append(f"// One-shot:  call {fname}(data).")
    lines.append(f"// Verify:    call {fname}_self_test() (returns true on success).")
    lines.append(f"")
    lines.append(f"package crc")
    lines.append(f"")

    if table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_go(tbl, w, gtype))
        lines.append("")

    # ----- <fname>_init() -----
    lines.append(f"func {fname}_init() {gtype} {{")
    lines.append(f"    return {_hex(init_state, w)}")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines.append(f"func {fname}_update(state {gtype}, data []byte) {gtype} {{")
    lines.append(f"    crc := state")
    lines.extend(_update_loop_go(w, poly, refin, mask, gtype, table))
    lines.append(f"    return crc")
    lines.append(f"}}")
    lines.append("")

    # ----- <fname>_finalize(state) -----
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
    lines.append(f"func {fname}(data []byte) {gtype} {{")
    lines.append(
        f"    return {fname}_finalize({fname}_update({fname}_init(), data))"
    )
    lines.append(f"}}")
    lines.append("")

    # ----- self-test -----
    lines.extend(_self_test_go(fname, check, w))

    return "\n".join(lines)
