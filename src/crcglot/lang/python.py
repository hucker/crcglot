"""Python CRC generator.

Emits a Python module string containing five module-level functions:

  - ``<fname>_init()``     -- return the starting state
  - ``<fname>_update(state, data)`` -- feed bytes, return new state
  - ``<fname>_finalize(state)`` -- apply output reflection + xorout
  - ``<fname>(data)``      -- one-shot wrapper (init + update + finalize)
  - ``<fname>_self_test()`` -- returns True if the algorithm reproduces
    the reveng catalogue's canonical check value, False otherwise

The streaming primitives (init / update / finalize) let callers
compute a CRC over data that arrives in chunks (large files, network
streams, sensor logs) without buffering everything in memory first.
The one-shot wrapper preserves the simple API for the common case.
``_self_test()`` lets a downstream caller verify that the generated
implementation behaves correctly on their interpreter / Python build
before trusting its output -- callable from pytest, ``unittest``,
boot self-checks, or just ``assert <fname>_self_test()`` in a script.

Verified at build time by :class:`tests.test_crc_codegen
.TestGeneratePython` (one-shot path) and
:class:`tests.test_crc_codegen.TestGeneratedPythonStreaming`
(streaming splittability invariant).
"""

# ruff: noqa: F541  - f-strings without placeholders used for code alignment

from __future__ import annotations

from typing import Literal

from crcglot._helpers import (
    _build_table,
    _func_name,
    _hex,
    _mask,
    _variant_to_flags,
)
from crcglot.catalogue import ALGORITHMS, AlgorithmInfo, _reflect


def _format_table_python(table: list[int], width: int) -> str:
    """Format a lookup table as a Python tuple literal named ``_TABLE``."""
    hex_w = (width + 3) // 4
    lines = ["_TABLE = ("]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append(")")
    return "\n".join(lines)


def _self_test_python(fname: str, check: int, width: int) -> list[str]:
    """Emit a Python self-test function returning True on success.

    Designed to be called from a downstream test framework
    (``assert {fname}_self_test()`` plays nicely with pytest /
    unittest), a script's startup check, or anywhere the caller
    wants to confirm the generated CRC matches the reveng catalogue
    before trusting its output.
    """
    return [
        f"def {fname}_self_test() -> bool:",
        f'    """Return True if {fname} reproduces the reveng check value."""',
        f"    return {fname}(b'123456789') == {_hex(check, width)}",
    ]


def generate_python(
    name: str,
    symbol: str | None = None,
    variant: Literal["bitwise", "table"] = "bitwise",
) -> str | None:
    """Look up a CRC algorithm by name and generate Python source for it.

    Thin wrapper around :func:`generate_python_from_entry`; use the
    latter directly when generating from a custom (non-catalogue)
    algorithm spec.

    Args:
        name: Algorithm name from :data:`crcglot.ALGORITHMS`.
        symbol: Optional override for the generated function name
            (default: a sanitized form of ``name``).
        variant: ``"bitwise"`` (default) or ``"table"`` (256-entry
            lookup, ~10x faster).  No ``"slice8"`` -- Python's per-int
            overhead eats the win.

    Returns:
        Python source code string, or None if algorithm not found.
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_python_from_entry(name, algo, symbol=symbol, variant=variant)


def generate_python_from_entry(
    name: str,
    algo: AlgorithmInfo,
    symbol: str | None = None,
    variant: Literal["bitwise", "table"] = "bitwise",
) -> str:
    """Generate Python source from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments and as the default
            function-name source).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).
        variant: ``"bitwise"`` (default) or ``"table"`` (256-entry
            lookup, ~10x faster).  No ``"slice8"``.

    Returns:
        Python source code string.
    """
    table, _slice8 = _variant_to_flags(variant, allow_slice8=False)
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

    # Pre-loaded init state: matches the value the main loop expects on
    # entry.  Reflected algorithms enter the loop with the reflection
    # of the textbook init; non-reflected use the textbook init directly.
    # This is what crc_init() returns and what callers pass into update().
    init_state = _reflect(init, w) if refin else init

    lines: list[str] = []

    # Table literal (table-driven variant only).
    if table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_python(tbl, w))
        lines.append("")
        lines.append("")

    # ----- <fname>_init() -----
    lines.append(f"def {fname}_init() -> int:")
    lines.append(f'    """Return the initial state for {name} streaming CRC."""')
    lines.append(f"    return {_hex(init_state, w)}")
    lines.append("")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines.append(f"def {fname}_update(state: int, data: bytes) -> int:")
    lines.append(f'    """Feed bytes into {name} state; return updated state."""')
    lines.append(f"    crc = state")
    lines.append(f"    for byte in data:")
    if table:
        if refin:
            lines.append(f"        crc = _TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)")
        else:
            lines.append(
                f"        crc = _TABLE[((crc >> {w - 8}) ^ byte) & 0xFF] ^ (crc << 8) & {mask}"
            )
    elif refin:
        ref_poly = _reflect(poly, w)
        lines.append(f"        crc ^= byte")
        lines.append(f"        for _ in range(8):")
        lines.append(f"            if crc & 1:")
        lines.append(f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)}")
        lines.append(f"            else:")
        lines.append(f"                crc >>= 1")
    else:
        lines.append(f"        crc ^= byte << {w - 8}")
        lines.append(f"        for _ in range(8):")
        lines.append(f"            if crc & {_hex(1 << (w - 1), w)}:")
        lines.append(f"                crc = (crc << 1) ^ {_hex(poly, w)}")
        lines.append(f"            else:")
        lines.append(f"                crc <<= 1")
        lines.append(f"            crc &= {mask}")
    lines.append(f"    return crc")
    lines.append("")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines.append(f"def {fname}_finalize(state: int) -> int:")
    lines.append(
        f'    """Apply output reflection and xorout to finalize {name}."""'
    )
    if refout != refin:
        lines.append(f"    # reflect output (refout != refin)")
        lines.append(
            f"    state = sum(((state >> k) & 1) << ({w - 1} - k) for k in range({w}))"
        )
    if xorout:
        lines.append(f"    return state ^ {_hex(xorout, w)}")
    else:
        lines.append(f"    return state")
    lines.append("")
    lines.append("")

    # ----- <fname>(data) one-shot wrapper -----
    lines.append(f"def {fname}(data: bytes) -> int:")
    lines.append(f'    """{name} - {desc}')
    lines.append(f"")
    lines.append(f"    check: crc(b'123456789') == {_hex(check, w)}")
    lines.append(f"")
    lines.append(
        f"    One-shot wrapper.  For streaming use "
        f"{fname}_init / _update / _finalize."
    )
    lines.append(f'    """')
    lines.append(
        f"    return {fname}_finalize({fname}_update({fname}_init(), data))"
    )
    lines.append("")
    lines.append("")

    # ----- <fname>_self_test() -----
    lines.extend(_self_test_python(fname, check, w))

    return "\n".join(lines)
