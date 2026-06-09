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
    resolve_variant,
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


def _format_table_python(table: list[int], width: int) -> str:
    """Format a lookup table as a Python tuple literal named ``_TABLE``.

    The 8-values-per-row layout is wrapped in ``# fmt: off`` / ``# fmt: on``
    so ``ruff format`` (and Black) leave it intact.  Without the guard the
    magic trailing comma forces a 256-line one-element-per-row explosion,
    which makes generated modules awkward to read and review.
    """
    hex_w = (width + 3) // 4
    lines = ["# fmt: off", "_TABLE = ("]
    for row in range(0, 256, 8):
        vals = ", ".join(
            f"0x{table[i]:0{hex_w}X}" for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append(")")
    lines.append("# fmt: on")
    return "\n".join(lines)


def _update_loop_python(
    w: int, poly: int, refin: bool, mask: str, table: bool
) -> list[str]:
    """Emit the per-byte main-loop lines for the update function.

    Returns the ``for byte in data:`` loop (header included), branching on the
    five forms the algorithm can take: table reflected / non-reflected, bitwise
    reflected, sub-byte non-reflected, and byte-wide non-reflected.  The caller
    wraps the result with ``crc = state`` and ``return crc``.  Mirrors
    ``_update_loop_go`` / ``_update_loop_c``.  The ``_TABLE`` placeholder is
    rewritten to the per-symbol name once, at the assembly point in the caller.
    """
    lines = ["    for byte in data:"]
    if table:
        if refin:
            lines.append("        crc = _TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)")
        else:
            # Split the table index into its own statement: inlined, the
            # per-symbol table name (``_crcglot_table_<symbol>``) pushes the
            # update past ruff's 88-column limit and the formatter wraps it.
            # ``(tbl ^ (crc << 8)) & mask`` == ``tbl ^ ((crc << 8) & mask)``
            # because masking distributes over XOR (tbl is already masked).
            lines.append(f"        idx = ((crc >> {w - 8}) ^ byte) & 0xFF")
            lines.append(f"        crc = (_TABLE[idx] ^ (crc << 8)) & {mask}")
    elif refin:
        ref_poly = _reflect(poly, w)
        lines.append("        crc ^= byte")
        lines.append("        for _ in range(8):")
        lines.append("            if crc & 1:")
        lines.append(f"                crc = (crc >> 1) ^ {_hex(ref_poly, w)}")
        lines.append("            else:")
        lines.append("                crc >>= 1")
    elif w < 8:
        # Sub-byte non-reflected: feed each byte bit-by-bit, MSB first.
        # The byte-aligned ``byte << (w - 8)`` fold underflows for width < 8.
        lines.append("        for i in range(7, -1, -1):")
        lines.append("            bit = (byte >> i) & 1")
        lines.append(f"            if ((crc >> {w - 1}) & 1) ^ bit:")
        lines.append(f"                crc = ((crc << 1) ^ {_hex(poly, w)}) & {mask}")
        lines.append("            else:")
        lines.append(f"                crc = (crc << 1) & {mask}")
    else:
        lines.append(f"        crc ^= byte << {w - 8}")
        lines.append("        for _ in range(8):")
        lines.append(f"            if crc & {_hex(1 << (w - 1), w)}:")
        lines.append(f"                crc = (crc << 1) ^ {_hex(poly, w)}")
        lines.append("            else:")
        lines.append("                crc <<= 1")
        lines.append(f"            crc &= {mask}")
    return lines


def _self_test_python(names, check, width, style, docs) -> list[str]:
    """Emit a Python self-test function returning True on success.

    Designed to be called from a downstream test framework
    (``assert <self_test>()`` plays nicely with pytest /
    unittest), a script's startup check, or anywhere the caller
    wants to confirm the generated CRC matches the reveng catalogue
    before trusting its output.
    """
    return [
        f"def {names['self_test']}() -> bool:",
        *style.doc_block(docs["self_test"], indent=4),
        f'    return {names["oneshot"]}(b"123456789") == {_hex(check, width)}',
    ]


def generate_python(
    name: str,
    symbol: str | None = None,
    variant: Literal["auto", "bitwise", "table"] = "auto",
    comment_style: str = "plain",
    naming: str = "snake",
) -> str | None:
    """Look up a CRC algorithm by name and generate Python source for it.

    Thin wrapper around :func:`generate_python_from_entry`; use the
    latter directly when generating from a custom (non-catalogue)
    algorithm spec.

    Args:
        name: Algorithm name from :data:`crcglot.ALGORITHMS`.
        symbol: Optional override for the generated function name
            (default: a sanitized form of ``name``).
        variant: ``"auto"`` (default -- fastest valid) or ``"bitwise"`` or ``"table"`` (256-entry
            lookup, ~10x faster).  No ``"slice8"`` -- Python's per-int
            overhead eats the win.
        comment_style: Documentation style for the generated comments
            (default ``"plain"``); see :func:`crcglot.comments.comment_style_for`.

    Returns:
        Python source code string, or None if algorithm not found.
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_python_from_entry(
        name, algo, symbol=symbol, variant=variant,
        comment_style=comment_style, naming=naming,
    )


def generate_python_from_entry(
    name: str,
    algo: AlgorithmInfo,
    symbol: str | None = None,
    variant: Literal["auto", "bitwise", "table"] = "auto",
    comment_style: str = "plain",
    naming: str = "snake",
) -> str:
    """Generate Python source from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments and as the default
            function-name source).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).
        variant: ``"auto"`` (default -- fastest valid) or ``"bitwise"`` or ``"table"`` (256-entry
            lookup, ~10x faster).  No ``"slice8"``.

    Returns:
        Python source code string.
    """
    resolved = resolve_variant("python", algo.width, variant)
    table, _slice8 = _variant_to_flags(resolved, allow_slice8=False)
    w = algo.width
    if w < 8:
        # Sub-byte CRCs are bit-by-bit only: a 256-entry table to checksum
        # the tiny payloads these run on is pure overhead, and the byte-wise
        # table update has no form for a register narrower than a byte.  The
        # variant matrix advertises bitwise-only for width < 8; a stray
        # table request degrades to bitwise rather than emitting broken code.
        table = False
    poly = algo.poly
    init = algo.init
    refin = algo.refin
    refout = algo.refout
    xorout = algo.xorout
    check = algo.check
    desc = algo.desc
    from crcglot.targets import naming_convention_for

    naming = naming_convention_for("python", naming)
    base = symbol if symbol else _func_name(name)
    names = crc_function_names(base, naming, is_override=symbol is not None)
    mask = _mask(w)

    # Pre-loaded init state: matches the value the main loop expects on
    # entry.  Reflected algorithms enter the loop with the reflection
    # of the textbook init; non-reflected use the textbook init directly.
    # This is what crc_init() returns and what callers pass into update().
    init_state = _reflect(init, w) if refin else init

    style = comment_style_for("python", comment_style)
    meta = AlgoMeta(
        name=name, desc=desc, width=w, poly=poly, init=init, refin=refin,
        refout=refout, xorout=xorout, check=check, variant=variant,
    )
    usage = UsageExample(
        streaming=(
            f"s = {names['init']}()",
            f"s = {names['update']}(s, chunk)  # over each chunk of the message",
            f"crc = {names['finalize']}(s)",
        ),
        oneshot=f"{names['oneshot']}(data)",
        selftest=f"{names['self_test']}()",
        selftest_returns="returns True on success",
    )
    docs = standard_doc_blocks(
        names, state_type="int",
        data_params=(DocParam("data", "the message bytes (a bytes-like object)."),),
        selftest_returns="True",
        refin=refin, refout=refout, xorout=xorout,
    )

    lines: list[str] = []
    lines += style.file_header(meta, usage)

    # Table literal (table-driven variant only).  Two trailing blank lines
    # separate the module docstring (or the table) from the first top-level
    # ``def`` -- the spacing ``ruff format`` enforces, so generated modules
    # are already-formatted and survive a format pass unchanged.
    if table:
        lines.append("")
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_python(tbl, w))
        lines.append("")
        lines.append("")
    else:
        lines.append("")
        lines.append("")

    # ----- <init>() -----
    lines.append(f"def {names['init']}() -> int:")
    lines += style.doc_block(docs["init"], indent=4)
    lines.append(f"    return {_hex(init_state, w)}")
    lines.append("")
    lines.append("")

    # ----- <update>(state, data) -----
    lines.append(f"def {names['update']}(state: int, data: bytes) -> int:")
    lines += style.doc_block(docs["update"], indent=4)
    lines.append(f"    crc = state")
    lines.extend(_update_loop_python(w, poly, refin, mask, table))
    lines.append(f"    return crc")
    lines.append("")
    lines.append("")

    # ----- <finalize>(state) -----
    lines.append(f"def {names['finalize']}(state: int) -> int:")
    lines += style.doc_block(docs["finalize"], indent=4)
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

    # ----- <oneshot>(data) one-shot wrapper -----
    lines.append(f"def {names['oneshot']}(data: bytes) -> int:")
    lines += style.doc_block(docs["oneshot"], indent=4)
    # Intermediate state rather than a nested one-liner: for long-named
    # algorithms the fully-nested call exceeds ruff's 88-column limit and the
    # formatter wraps it.  Stepwise is also closer to the streaming idiom.
    lines.append(f"    state = {names['init']}()")
    lines.append(f"    state = {names['update']}(state, data)")
    lines.append(f"    return {names['finalize']}(state)")
    lines.append("")
    lines.append("")

    # ----- <self_test>() -----
    lines.extend(_self_test_python(names, check, w, style, docs))

    module = "\n".join(lines)
    # Namespace the lookup table per symbol so several generated modules
    # pasted into one file/namespace don't shadow each other's table.  The
    # emitter uses the fixed placeholder ``_TABLE``; rewrite it to
    # ``_crcglot_table_<symbol>`` at this single assembly point.
    module = module.replace("_TABLE", f"_crcglot_table_{base}")
    return module
