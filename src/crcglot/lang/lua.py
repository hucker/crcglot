"""Lua CRC generator.

Emits a complete ``.lua`` module -- ``local M = {} ... return M`` --
with five public functions on the module table:

  - ``M.<fname>_init()`` -- return the starting state
  - ``M.<fname>_update(state, data)`` -- feed a byte string, return new state
  - ``M.<fname>_finalize(state)`` -- apply output reflection + xorout
  - ``M.<fname>(data)`` -- one-shot wrapper (init + update + finalize)
  - ``M.<fname>_self_test()`` -- true iff the generated CRC reproduces
    its independent reference values

Consumption is the standard Lua module idiom::

    local crc = dofile("mycrc.lua")   -- or require("mycrc")
    print(("%X"):format(crc.crc32("123456789")))

**Requires Lua 5.3 or newer** (stated in the generated header): the
emitted code uses native 64-bit integers and the bitwise operators
(``&``, ``|``, binary ``~`` for XOR, ``<<``, ``>>``).  Lua 5.1 / 5.2 /
LuaJIT have neither -- their numbers are doubles, whose 53-bit mantissa
cannot even represent a CRC-64 -- so they are deliberately out of scope
rather than half-supported.

Lua's integer semantics make this the simplest emit of any target:

* One integer type (64-bit) serves every CRC width; explicit masks
  hold the state to ``width`` bits, and no mask is needed at width 64
  (shifted-out bits are discarded, not trapped).
* ``>>`` is a logical shift (zero-fill) even when the 64-bit pattern
  has the high bit set, and shift counts >= 64 are defined (yield 0).
* Hex literals above ``0x7FFF...`` wrap to negative integers, but the
  bit pattern is preserved and ``==`` compares patterns, so embedded
  check constants work unmodified at width 64.
* Lookup tables use an explicit ``[0] =`` first entry, so the update
  loop indexes bytes directly with no ``+ 1`` arithmetic.

Variants: ``bitwise`` and ``table`` only.  Slice-by-8 is excluded for
the same measured reason as Python: interpreter per-operation overhead
absorbs the multi-table win.

Verified at build time by ``tests.test_lua_gen`` (structural checks
plus the ``lua_batch`` single-run execution tier; per-algorithm
isolation classes behind ``--exhaustive``).
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


def _format_table_lua(table: list[int], width: int) -> str:
    """Format a lookup table as a Lua array with an explicit ``[0]`` entry."""
    hex_w = (width + 3) // 4
    lines = [f"local CRC_TABLE = {{"]
    for row in range(0, 256, 8):
        vals = ", ".join(
            ("[0] = " if i == 0 else "") + f"0x{table[i]:0{hex_w}X}"
            for i in range(row, min(row + 8, 256))
        )
        lines.append(f"    {vals},")
    lines.append("}")
    return "\n".join(lines)


def _masked(expr: str, w: int, mask: str) -> str:
    """Apply the width mask, except at 64 where shifted-out bits vanish."""
    if w == 64:
        return expr
    return f"({expr}) & {mask}"


def _update_loop_lua(
    w: int,
    poly: int,
    refin: bool,
    mask: str,
    table: bool,
) -> list[str]:
    """Emit the per-byte main-loop lines for the update function.

    Variable ``crc`` is assumed to already hold the incoming state.
    All arithmetic runs in Lua's single 64-bit integer type; the mask
    re-narrows to ``w`` bits wherever a shift could carry past them
    (never needed at width 64).
    """
    t = "CRC_TABLE"
    if table:
        if w == 8:
            # Table lookup IS the complete algorithm at width 8.
            return [
                "    for i = 1, #data do",
                f"        crc = {t}[crc ~ data:byte(i)]",
                "    end",
            ]
        if refin:
            return [
                "    for i = 1, #data do",
                f"        crc = {t}[(crc ~ data:byte(i)) & 0xFF] ~ (crc >> 8)",
                "    end",
            ]
        shifted = _masked("crc << 8", w, mask)
        return [
            "    for i = 1, #data do",
            f"        crc = {t}[((crc >> {w - 8}) ~ data:byte(i)) & 0xFF] ~ {shifted}",
            "    end",
        ]
    if refin:
        # Reflected bit-by-bit works for every width, sub-byte included:
        # the whole input byte lands in the low bits and shifts down
        # through the polynomial taps (same generic loop as Rust / Zig).
        ref_poly = _reflect(poly, w)
        return [
            "    for i = 1, #data do",
            "        crc = crc ~ data:byte(i)",
            "        for _ = 1, 8 do",
            "            if crc & 1 ~= 0 then",
            f"                crc = (crc >> 1) ~ {_hex(ref_poly, w)}",
            "            else",
            "                crc = crc >> 1",
            "            end",
            "        end",
            "    end",
        ]
    if w < 8:
        # Sub-byte non-reflected: bit-by-bit, MSB first.  The byte-aligned
        # ``b << (w - 8)`` fold would be a negative shift below width 8.
        stepped = _masked(f"(crc << 1) ~ {_hex(poly, w)}", w, mask)
        shifted = _masked("crc << 1", w, mask)
        return [
            "    for i = 1, #data do",
            "        local b = data:byte(i)",
            "        for j = 7, 0, -1 do",
            f"            if (((crc >> {w - 1}) & 1) ~ ((b >> j) & 1)) ~= 0 then",
            f"                crc = {stepped}",
            "            else",
            f"                crc = {shifted}",
            "            end",
            "        end",
            "    end",
        ]
    # Non-reflected bit-by-bit, w >= 8.
    align_in = "data:byte(i)" if w == 8 else f"(data:byte(i) << {w - 8})"
    top_bit = _hex(1 << (w - 1), w)
    stepped = _masked(f"(crc << 1) ~ {_hex(poly, w)}", w, mask)
    shifted = _masked("crc << 1", w, mask)
    return [
        "    for i = 1, #data do",
        f"        crc = crc ~ {align_in}",
        "        for _ = 1, 8 do",
        f"            if crc & {top_bit} ~= 0 then",
        f"                crc = {stepped}",
        "            else",
        f"                crc = {shifted}",
        "            end",
        "        end",
        "    end",
    ]


def _self_test_lua(names, check, width, style, docs, goldens) -> str:
    """Emit ``function M.<fname>_self_test()`` returning true on success.

    For a catalogue algorithm ``goldens`` carries four independent
    reference CRCs; the two large inputs are reproduced with
    byte-at-a-time loops (no embedded array).  A custom polynomial
    (``goldens is None``) falls back to the single ``check`` assertion.
    """
    n = names
    lines: list[str] = ["", *style.doc_block(docs["self_test"])]
    if goldens is None:
        lines += [
            f"function M.{n['self_test']}()",
            f'    return M.{n["oneshot"]}("123456789") == {_hex(check, width)}',
            "end",
        ]
        return "\n".join(lines)
    g = goldens
    lines += [
        f"function M.{n['self_test']}()",
        f'    if M.{n["oneshot"]}("") ~= {_hex(g["empty"], width)} then return false end',
        f'    if M.{n["oneshot"]}("123456789") ~= {_hex(g["check"], width)} then return false end',
        f"    local s = M.{n['init']}()",
        "    for i = 0, 255 do",
        f"        s = M.{n['update']}(s, string.char(i))",
        "    end",
        f"    if M.{n['finalize']}(s) ~= {_hex(g['all_bytes'], width)} then return false end",
        f"    s = M.{n['init']}()",
        "    for i = 0, 1023 do",
        f"        s = M.{n['update']}(s, string.char((i * 167 + 13) & 0xFF))",
        "    end",
        f"    if M.{n['finalize']}(s) ~= {_hex(g['binary_1k'], width)} then return false end",
        "    return true",
        "end",
    ]
    return "\n".join(lines)


def combine_lua(outputs: list[str], stem: str | None = None) -> str:
    """Combine several single-module generator outputs into one module.

    Each output opens with ``local M = {}`` and closes with ``return M``;
    a concatenation would redeclare the table and return early.  Keep the
    first opener and the final return, and strip them from every other
    output.  Per-symbol table locals keep the merged module collision-free.

    Args:
        outputs: Individual ``generate_lua`` results, one per algorithm.
        stem: Unused; present for signature parity with ``combine_c``.

    Returns:
        One valid Lua module exposing every algorithm's functions.
    """
    del stem  # only C's combiner needs the output stem
    opener = "local M = {}"
    footer = "return M"
    parts: list[str] = []
    for idx, out in enumerate(outputs):
        body = out
        if idx > 0:
            body = body.replace(opener, "", 1)
        # Strip the trailing return from all but the last output.
        if idx < len(outputs) - 1:
            head, sep, _tail = body.rpartition(footer)
            body = head if sep else body
        parts.append(body.strip("\n"))
    return "\n\n".join(parts) + "\n"


def generate_lua(
    name: str,
    symbol: str | None = None,
    variant: Literal["auto", "bitwise", "table"] = "auto",
    comment_style: str = "plain",
    naming: str = "snake",
) -> str | None:
    """Look up a CRC algorithm by name and generate Lua source for it.

    Thin wrapper around :func:`generate_lua_from_entry`; use the
    latter directly when generating from a custom (non-catalogue)
    algorithm spec.
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_lua_from_entry(
        name, algo, symbol=symbol, variant=variant,
        comment_style=comment_style, naming=naming,
    )


def generate_lua_from_entry(
    name: str,
    algo: AlgorithmInfo,
    symbol: str | None = None,
    variant: Literal["auto", "bitwise", "table"] = "auto",
    comment_style: str = "plain",
    naming: str = "snake",
    stem: str | None = None,
) -> str:
    """Generate Lua source from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).
        variant: Implementation shape -- ``"auto"`` (default -- fastest
            valid), ``"bitwise"``, or ``"table"`` (256-entry lookup).
            Slice-by-8 is not offered (interpreter overhead absorbs it,
            same as Python).
        comment_style: Documentation comment style (``"plain"``).
        naming: Function-name convention -- ``"snake"`` (the stdlib's
            style, and the default) or ``"camel"``.
        stem: Optional identifier-base override (cased per ``naming``,
            unlike the verbatim ``symbol``); ``name`` still labels the code.

    Returns:
        Lua source code string (a ``local M = {} ... return M`` module).

    Examples:
        >>> src = generate_lua_from_entry("crc32", ALGORITHMS["crc32"])
        >>> "function M.crc32_update" in src
        True
    """
    resolved = resolve_variant("lua", algo.width, variant)
    table, slice8 = _variant_to_flags(resolved, allow_slice8=False)
    del slice8  # never offered for Lua
    w = algo.width
    if w < 8 and table:
        # Sub-byte CRCs are bit-by-bit only (see variants_for_width); a stray
        # table request degrades to bitwise rather than emitting a negative
        # shift in the table fold.
        table = False
    poly = algo.poly
    init = algo.init
    refin = algo.refin
    refout = algo.refout
    xorout = algo.xorout
    check = algo.check
    desc = algo.desc
    from crcglot.targets import naming_convention_for

    naming = naming_convention_for("lua", naming)
    base = symbol if symbol else _func_name(stem if stem is not None else name)
    names = crc_function_names(base, naming, is_override=symbol is not None)
    mask = _mask(w)

    # Pre-loaded init state for streaming entry.
    init_state = _reflect(init, w) if refin else init

    style = comment_style_for("lua", comment_style)
    provenance = build_prov(
        algo_source=algo.source, algorithm=name, target="lua",
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
            f"local s = crc.{names['init']}()",
            f"s = crc.{names['update']}(s, chunk)  -- over each chunk",
            f"local value = crc.{names['finalize']}(s)",
        ),
        oneshot=f"crc.{names['oneshot']}(data)",
        selftest=f"crc.{names['self_test']}()",
        selftest_returns="returns true on success",
        caveats=(
            'Requires Lua 5.3+ (native 64-bit integers and bitwise '
            'operators); not Lua 5.1 / 5.2 / LuaJIT.',
            'Load with: local crc = dofile("<file>.lua")  '
            '(or require, minus the extension).',
        ),
    )
    docs = standard_doc_blocks(
        names, state_type="integer",
        data_params=(DocParam("data", "the message bytes (a Lua string)."),),
        selftest_returns="true",
        refin=refin, refout=refout, xorout=xorout,
        independent_refs=goldens is not None,
    )

    lines: list[str] = []
    if table:
        tbl = _build_table(w, poly, refin)
        lines.append(_format_table_lua(tbl, w))
        lines.append("")
    lines += style.file_header(meta, usage)
    lines.append("")
    lines.append("local M = {}")
    lines.append("")

    # ----- <fname>_init() -----
    lines += style.doc_block(docs["init"])
    lines.append(f"function M.{names['init']}()")
    lines.append(f"    return {_hex(init_state, w)}")
    lines.append(f"end")
    lines.append("")

    # ----- <fname>_update(state, data) -----
    lines += style.doc_block(docs["update"])
    lines.append(f"function M.{names['update']}(state, data)")
    lines.append(f"    local crc = state")
    lines.extend(_update_loop_lua(w, poly, refin, mask, table))
    lines.append(f"    return crc")
    lines.append(f"end")
    lines.append("")

    # ----- <fname>_finalize(state) -----
    lines += style.doc_block(docs["finalize"])
    lines.append(f"function M.{names['finalize']}(state)")
    if refout != refin:
        lines.append(f"    -- reflect output (refout != refin)")
        lines.append(f"    local reflected = 0")
        lines.append(f"    for k = 0, {w - 1} do")
        lines.append(
            f"        reflected = reflected | (((state >> k) & 1) << ({w - 1} - k))"
        )
        lines.append(f"    end")
        if xorout:
            lines.append(f"    return reflected ~ {_hex(xorout, w)}")
        else:
            lines.append(f"    return reflected")
    elif xorout:
        lines.append(f"    return state ~ {_hex(xorout, w)}")
    else:
        lines.append(f"    return state")
    lines.append(f"end")
    lines.append("")

    # ----- one-shot wrapper -----
    lines += style.doc_block(docs["oneshot"])
    lines.append(f"function M.{names['oneshot']}(data)")
    lines.append(
        f"    return M.{names['finalize']}(M.{names['update']}(M.{names['init']}(), data))"
    )
    lines.append(f"end")
    lines.append(
        _self_test_lua(names, check, w, style, docs, goldens)
    )
    lines.append("")
    lines.append("return M")

    module = "\n".join(lines)
    # Namespace the lookup-table local per symbol so several generated
    # modules (different algorithms, or one algorithm in multiple variants)
    # can be combined into one module without colliding.
    module = module.replace("CRC_TABLE", f"crcglot_table_{base}")
    return module
