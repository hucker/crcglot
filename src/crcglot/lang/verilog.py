"""Verilog CRC generator (SystemVerilog 2012 dialect).

Emits a single ``.sv`` file containing a ``package`` with five
functions:

  - ``<fname>_init`` -- return the starting state ([W-1:0])
  - ``<fname>_update(state, byte_in)`` -- feed one byte, return new state
  - ``<fname>_finalize(state)`` -- apply output reflection + xorout
  - ``<fname>(data)`` -- one-shot wrapper over a ``byte data[]``
    dynamic array
  - ``<fname>_self_test`` -- returns ``1'b1`` iff the algorithm
    reproduces the reveng catalogue check value for ``"123456789"``

Wrapped in ``package <fname>_pkg ... endpackage`` so a testbench can
``import <fname>_pkg::*;`` and call the functions directly.  Compiled
under ``iverilog -g2012`` (SystemVerilog 2012); the same construct
works under Verilator and modern commercial simulators.

Scope note: like the VHDL target, this is a simulator-friendly
reference implementation -- pure combinational functions over packed
bit vectors.  Synthesizable pipelined RTL (always_ff blocks, a real
hardware datapath) is a future enhancement; the function-in-package
form is the right shape for "verified reference implementation",
not a drop-in synthesizable IP block.

Verified at build time by ``tests.test_verilog_gen
.TestGeneratedVerilogExecutes`` (one-shot path via iverilog + vvp).
"""

# ruff: noqa: F541  - f-strings without placeholders used for code alignment

from __future__ import annotations

from typing import Literal

from crcglot._helpers import _func_name, _variant_to_flags, resolve_variant, crc_function_names
from crcglot.catalogue import ALGORITHMS, AlgorithmInfo, _reflect
from crcglot.comments import (
    AlgoMeta,
    DocParam,
    UsageExample,
    build_prov,
    comment_style_for,
    standard_doc_blocks,
)


def _sv_lit(value: int, width: int) -> str:
    """Format an integer constant as a SystemVerilog sized hex literal."""
    hex_w = (width + 3) // 4
    return f"{width}'h{value:0{hex_w}X}"


def _self_test_sv(names, check, width, style, docs) -> list[str]:
    """Emit a SystemVerilog self_test function returning 1'b1 on success.

    The reveng check input ``"123456789"`` is hardcoded as a byte
    array literal; the function returns the comparison result so a
    testbench can do ``assert (<fname>_self_test());`` or use it in
    any boolean context.
    """
    return [
        f"",
        *style.doc_block(docs["self_test"], indent=4),
        f"    function automatic bit {names['self_test']}();",
        f"        byte unsigned data[] = '{{",
        f"            8'h31, 8'h32, 8'h33, 8'h34, 8'h35,",
        f"            8'h36, 8'h37, 8'h38, 8'h39",
        f"        }};",
        f"        return ({names['oneshot']}(data) == {_sv_lit(check, width)});",
        f"    endfunction",
    ]


def generate_verilog(
    name: str,
    symbol: str | None = None,
    variant: Literal["auto", "bitwise"] = "auto",
    comment_style: str = "plain",
    naming: str = "snake",
) -> str | None:
    """Look up a CRC algorithm by name and generate a Verilog package.

    Thin wrapper around :func:`generate_verilog_from_entry`; use the
    latter directly for custom (non-catalogue) algorithm specs.
    """
    algo = ALGORITHMS.get(name)
    if algo is None:
        return None
    return generate_verilog_from_entry(
        name, algo, symbol=symbol, variant=variant,
        comment_style=comment_style, naming=naming,
    )


def generate_verilog_from_entry(
    name: str,
    algo: AlgorithmInfo,
    symbol: str | None = None,
    variant: Literal["auto", "bitwise"] = "auto",
    comment_style: str = "plain",
    naming: str = "snake",
    stem: str | None = None,
) -> str:
    """Generate a SystemVerilog package from an :class:`AlgorithmInfo`.

    Args:
        name: Algorithm name (used in comments).
        algo: Algorithm parameters as a typed :class:`AlgorithmInfo`.
        symbol: Optional override for the generated function name
            (default: ``_func_name(name)``).  Package name derives
            from the symbol.
        stem: Optional identifier-base override (cased per ``naming``,
            unlike the verbatim ``symbol``); ``name`` still labels the code.
        variant: ``"auto"`` / ``"bitwise"`` (both bit-by-bit; the only
            shape this generator emits) -- accepted for API symmetry with
            the other generators.  Passing ``"table"`` or ``"slice8"``
            raises ``ValueError`` (table-driven Verilog deferred, same
            scope decision as VHDL).

    Returns:
        SystemVerilog source string.
    """
    resolved = resolve_variant("verilog", algo.width, variant)
    _variant_to_flags(resolved, allow_table=False, allow_slice8=False)
    w = algo.width
    poly = algo.poly
    init = algo.init
    refin = algo.refin
    refout = algo.refout
    xorout = algo.xorout
    check = algo.check
    desc = algo.desc
    from crcglot.targets import naming_convention_for

    naming = naming_convention_for("verilog", naming)
    base = symbol if symbol else _func_name(stem if stem is not None else name)
    names = crc_function_names(base, naming, is_override=symbol is not None)
    pkg = f"{base}_pkg"

    if refin:
        init_state = _reflect(init, w)
        poly_val = _reflect(poly, w)
    else:
        init_state = init
        poly_val = poly

    style = comment_style_for("verilog", comment_style)
    provenance = build_prov(
        algo_source=algo.source, algorithm=name, target="verilog",
        variant=resolved, comment=comment_style, symbol=base, naming=naming,
    )
    meta = AlgoMeta(
        name=name, desc=desc, width=w, poly=poly, init=init, refin=refin,
        refout=refout, xorout=xorout, check=check, variant=variant,
        provenance=provenance,
    )
    usage = UsageExample(
        streaming=(
            f"state = {names['init']}();",
            f"state = {names['update']}(state, byte_in);  // call once per byte",
            f"crc = {names['finalize']}(state);",
        ),
        oneshot=f"{names['oneshot']}(data)",
        selftest=f"{names['self_test']}",
        selftest_returns="returns 1'b1 on success",
        caveats=(
            "update consumes ONE byte per call -- the one-shot wrapper "
            "loops over the array for you.",
            "Simulator reference (verified under ghdl / iverilog), not a "
            "drop-in synthesizable RTL core.",
        ),
    )
    docs = standard_doc_blocks(
        names, state_type=f"{w}-bit",
        data_params=(DocParam("byte_in", "a single input byte (8 bits)."),),
        oneshot_params=(
            DocParam("data", "the packed message as a byte-unsigned array."),
        ),
        selftest_returns="1'b1",
        refin=refin, refout=refout, xorout=xorout,
        extra_notes={
            "update": (
                "Consumes one byte per call -- loop over your message bytes.",
            ),
        },
    )

    lines: list[str] = []
    lines += style.file_header(meta, usage)
    lines += [
        f"",
        f"`ifndef {pkg.upper()}_SV",
        f"`define {pkg.upper()}_SV",
        f"",
        f"package {pkg};",
    ]

    # ---- <fname>_init ----
    lines.append(f"")
    lines += style.doc_block(docs["init"], indent=4)
    lines += [
        f"    function automatic [{w - 1}:0] {names['init']}();",
        f"        {names['init']} = {_sv_lit(init_state, w)};",
        f"    endfunction",
    ]

    # ---- <fname>_update(state, byte_in) ----
    # Single-byte update -- caller loops over data themselves.  This
    # matches the streaming-friendly shape (callers can feed bytes
    # as they arrive without pre-buffering).
    lines.append(f"")
    lines += style.doc_block(docs["update"], indent=4)
    lines += [
        f"    function automatic [{w - 1}:0] {names['update']}("
        f"input [{w - 1}:0] state, input [7:0] byte_in);",
        f"        logic [{w - 1}:0] crc;",
        f"        crc = state;",
    ]
    if refin and w < 8:
        # Sub-byte reflected: bit-by-bit, LSB first.  The byte (8 bits) is
        # wider than the register, so it can't be zero-extended/XORed whole
        # ({(w-8){1'b0}} is a negative replication for width < 8).
        lines += [
            f"        for (int j = 0; j < 8; j++) begin",
            f"            if ((crc[0] ^ byte_in[j]) == 1'b1) begin",
            f"                crc = (crc >> 1) ^ {_sv_lit(poly_val, w)};",
            f"            end else begin",
            f"                crc = crc >> 1;",
            f"            end",
            f"        end",
        ]
    elif refin:
        # Reflected: XOR byte into low byte, then right-shift loop.
        if w == 8:
            # For w=8 the resize is trivial; no shift needed for the XOR.
            lines += [
                f"        crc = crc ^ byte_in;",
            ]
        else:
            lines += [
                f"        crc = crc ^ {{{{{w - 8}{{1'b0}}}}, byte_in}};",
            ]
        lines += [
            f"        for (int j = 0; j < 8; j++) begin",
            f"            if (crc[0] == 1'b1) begin",
            f"                crc = (crc >> 1) ^ {_sv_lit(poly_val, w)};",
            f"            end else begin",
            f"                crc = crc >> 1;",
            f"            end",
            f"        end",
        ]
    elif w < 8:
        # Sub-byte non-reflected: bit-by-bit, MSB first.
        lines += [
            f"        for (int j = 7; j >= 0; j--) begin",
            f"            if ((crc[{w - 1}] ^ byte_in[j]) == 1'b1) begin",
            f"                crc = (crc << 1) ^ {_sv_lit(poly_val, w)};",
            f"            end else begin",
            f"                crc = crc << 1;",
            f"            end",
            f"        end",
        ]
    else:
        # Non-reflected: XOR byte into high byte, then left-shift loop.
        if w == 8:
            lines += [
                f"        crc = crc ^ byte_in;",
            ]
        else:
            lines += [
                f"        crc = crc ^ ({{{{{w - 8}{{1'b0}}}}, byte_in}} << {w - 8});",
            ]
        lines += [
            f"        for (int j = 0; j < 8; j++) begin",
            f"            if (crc[{w - 1}] == 1'b1) begin",
            f"                crc = (crc << 1) ^ {_sv_lit(poly_val, w)};",
            f"            end else begin",
            f"                crc = crc << 1;",
            f"            end",
            f"        end",
        ]
    lines += [
        f"        {names['update']} = crc;",
        f"    endfunction",
    ]

    # ---- <fname>_finalize(state) ----
    lines.append(f"")
    lines += style.doc_block(docs["finalize"], indent=4)
    lines += [
        f"    function automatic [{w - 1}:0] {names['finalize']}("
        f"input [{w - 1}:0] state);",
        f"        logic [{w - 1}:0] crc;",
        f"        crc = state;",
    ]
    if refout != refin:
        lines += [
            f"        // reflect output (refout != refin)",
            f"        begin : reflect",
            f"            logic [{w - 1}:0] reflected;",
            f"            reflected = '0;",
            f"            for (int k = 0; k < {w}; k++)",
            f"                reflected[k] = crc[{w - 1} - k];",
            f"            crc = reflected;",
            f"        end",
        ]
    if xorout:
        lines.append(f"        {names['finalize']} = crc ^ {_sv_lit(xorout, w)};")
    else:
        lines.append(f"        {names['finalize']} = crc;")
    lines.append(f"    endfunction")

    # ---- one-shot ----
    lines.append(f"")
    lines += style.doc_block(docs["oneshot"], indent=4)
    lines += [
        f"    function automatic [{w - 1}:0] {names['oneshot']}("
        f"input byte unsigned data[]);",
        f"        logic [{w - 1}:0] s;",
        f"        s = {names['init']}();",
        f"        foreach (data[i]) begin",
        f"            s = {names['update']}(s, data[i]);",
        f"        end",
        f"        {names['oneshot']} = {names['finalize']}(s);",
        f"    endfunction",
    ]

    # ---- self-test ----
    lines.extend(_self_test_sv(names, check, w, style, docs))

    lines += [
        f"",
        f"endpackage",
        f"",
        f"`endif",
    ]

    return "\n".join(lines)
