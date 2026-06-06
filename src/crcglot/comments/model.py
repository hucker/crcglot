"""Structured documentation model shared by every comment style.

The generators describe their output as data -- algorithm parameters
(:class:`AlgoMeta`), a usage example (:class:`UsageExample`), and a doc block
per function (:class:`DocBlock`) -- and a style renders it.  The invariant
prose (what the five functions' docs *say*) is authored once in
:func:`standard_doc_blocks`, so every language reads identically in spirit and
a new style reuses the wording verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlgoMeta:
    """Algorithm parameters, for the file-header overview."""

    name: str
    desc: str
    width: int
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int
    check: int
    variant: str


@dataclass(frozen=True)
class UsageExample:
    """Language-specific usage snippets for the file header.

    ``streaming`` is a list of code lines (no comment prefix) showing the
    init -> update -> finalize sequence; ``oneshot`` / ``selftest`` are the
    call expressions; ``caveats`` are extra notes (signed-int, one-byte
    update, ...).
    """

    streaming: tuple[str, ...]
    oneshot: str
    selftest: str
    selftest_returns: str
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocParam:
    name: str
    text: str


@dataclass(frozen=True)
class DocBlock:
    """Documentation for one generated function.

    ``symbol`` is the function's own emitted name (e.g. ``crc32_update``).
    Most styles ignore it; godoc needs it because Go's convention is that a
    doc comment opens with the identifier it documents.
    """

    summary: str
    params: tuple[DocParam, ...] = ()
    returns: str | None = None
    notes: tuple[str, ...] = ()
    symbol: str = ""


# The invariant wording, authored once.  Generators supply only the
# language-specific param names / notes via ``standard_doc_blocks``.
_CALL_ORDER = "Call init -> update (any number of times) -> finalize."


def _finalize_summary(refin: bool, refout: bool, xorout: int) -> str:
    """Describe what ``finalize`` actually does for these parameters.

    The body emitted by every generator reflects only ``if refout != refin``
    and XORs only ``if xorout != 0``, so a single hardcoded summary mislabels
    the common cases (no catalogue algorithm reflects in finalize, and 47 of
    72 have ``xorout == 0``, making finalize a no-op).  This selects wording
    matching the four possible shapes.

    Args:
        refin: Whether input bytes are reflected.
        refout: Whether the output is reflected.
        xorout: The final XOR mask (``0`` means no final XOR).

    Returns:
        The summary line for the ``finalize`` doc block.

    Examples:
        >>> _finalize_summary(refin=True, refout=True, xorout=0)
        'Return the finished CRC; this algorithm applies no final transform.'
        >>> _finalize_summary(refin=True, refout=True, xorout=0xFFFFFFFF)
        'Apply the final XOR to produce the CRC.'
    """
    reflects = refout != refin
    xors = xorout != 0
    if reflects and xors:
        return "Reflect the CRC and apply the final XOR to produce the result."
    if reflects:
        return "Reflect the CRC to produce the final result."
    if xors:
        return "Apply the final XOR to produce the CRC."
    return "Return the finished CRC; this algorithm applies no final transform."


def standard_doc_blocks(
    fname: str,
    *,
    state_type: str,
    data_params: tuple[DocParam, ...],
    selftest_returns: str,
    refin: bool,
    refout: bool,
    xorout: int,
    extra_notes: dict[str, tuple[str, ...]] | None = None,
    oneshot_params: tuple[DocParam, ...] | None = None,
) -> dict[str, DocBlock]:
    """Build the five standard :class:`DocBlock`s for a generated algorithm.

    Args:
        fname: The emitted function-name stem (e.g. ``crc32``).
        state_type: How to name the running-CRC type in prose (e.g.
            ``"uint32_t"``, ``"int"``).
        data_params: The non-``state`` parameters of ``update``, in order
            (e.g. C ``(data, len)``; Verilog ``(byte_in,)``).
        selftest_returns: e.g. ``"0 on success, 1 on failure"`` /
            ``"true on success"``.
        refin: Whether input bytes are reflected -- selects the ``finalize``
            summary wording (see :func:`_finalize_summary`).
        refout: Whether the output is reflected.
        xorout: The final XOR mask (``0`` means finalize applies no XOR).
        extra_notes: Optional per-function extra ``notes`` keyed by
            ``init|update|finalize|oneshot|self_test``.
        oneshot_params: The one-shot's parameters when they differ from
            ``data_params`` (Verilog: ``update`` takes one byte but the
            one-shot takes the whole array).  Defaults to ``data_params``.

    Returns:
        Dict keyed by ``init|update|finalize|oneshot|self_test``.
    """
    notes = extra_notes or {}
    if oneshot_params is None:
        oneshot_params = data_params
    state_param = DocParam(
        "state", f"running {state_type} state (from init or a prior update)."
    )
    return {
        "init": DocBlock(
            summary="Return the initial CRC state to begin a computation.",
            returns=f"the starting {state_type} state.",
            notes=notes.get("init", ()),
            symbol=f"{fname}_init",
        ),
        "update": DocBlock(
            summary="Fold input into the running CRC state and return the new state.",
            params=(state_param,) + data_params,
            returns=f"the updated {state_type} state (not yet finalized).",
            notes=(_CALL_ORDER,) + notes.get("update", ()),
            symbol=f"{fname}_update",
        ),
        "finalize": DocBlock(
            summary=_finalize_summary(refin, refout, xorout),
            params=(DocParam("state", f"accumulated {state_type} state from update."),),
            returns="the finished CRC value.",
            notes=("Do not feed the finalized value back into update.",)
            + notes.get("finalize", ()),
            symbol=f"{fname}_finalize",
        ),
        "oneshot": DocBlock(
            summary="One-shot convenience: init + a single update + finalize.",
            params=oneshot_params,
            returns="the finished CRC value.",
            notes=notes.get("oneshot", ()),
            symbol=fname,
        ),
        "self_test": DocBlock(
            summary="Self-test the implementation against the reveng catalogue.",
            returns=f"{selftest_returns} iff the CRC of \"123456789\" matches "
            "the embedded check value.",
            notes=(
                "Run once on your target toolchain -- it is the cheapest way "
                "to catch a compiler / endianness / width mismatch before "
                "trusting the output.",
            )
            + notes.get("self_test", ()),
            symbol=f"{fname}_self_test",
        ),
    }
