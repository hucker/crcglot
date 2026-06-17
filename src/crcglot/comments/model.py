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
class ProvInfo:
    """The resolved generation parameters, for the provenance block.

    ``version`` is the crcglot release that produced the file (``crcglot
    .__version__``); the rest are already-constrained values derived from the
    request (catalogue name, target / variant / comment enums, an identifier
    symbol), so the block is reconstruction-complete and carries no
    comment-injection risk.  ``variant`` is the canonical resolved name
    (``bitwise`` / ``table`` / ``slice8``), never the raw flag spelling or
    ``auto``.

    The version is included deliberately: generators change between releases
    (a fixed reflection bug, a new variant), so without it a reader cannot tell
    which crcglot emitted a given file or whether regenerating would change it.
    The cost is that bumping crcglot re-diffs the block in every generated file
    and EXAMPLES cell, which is the truthful record that the producing version
    moved.
    """

    version: str
    algorithm: str
    target: str
    variant: str
    comment: str
    symbol: str
    naming: str


def _tool_version() -> str:
    """The installed crcglot version, for the provenance block.

    Read lazily (function-local import) so building the model never triggers a
    package-import cycle, and resolved through ``crcglot.__version__`` so there
    is a single source of truth shared with ``crcglot version``.
    """
    import crcglot

    return crcglot.__version__


def build_prov(
    *,
    algo_source: str,
    algorithm: str,
    target: str,
    variant: str,
    comment: str,
    symbol: str,
    naming: str,
    version: str | None = None,
) -> ProvInfo:
    """Build the :class:`ProvInfo` for a generation.

    Centralizes the ``"custom"`` algorithm label for non-catalogue polynomials
    so every generator shares it.  Provenance is always built (the block is
    always on).

    Args:
        algo_source: The algorithm's ``source`` field (``"custom"`` for a
            custom polynomial, else a catalogue provenance string).
        algorithm: The catalogue name, used unless ``algo_source`` is custom.
        target: The target language code.
        variant: The canonical resolved variant (``bitwise`` / ``table`` /
            ``slice8``), never the raw flag or ``"auto"``.
        comment: The comment style.
        symbol: The resolved function symbol base.
        naming: The resolved naming convention.
        version: The crcglot version to stamp; defaults to the installed
            ``crcglot.__version__``.  Pass an explicit value only to pin the
            stamp (e.g. in a test).

    Returns:
        A populated :class:`ProvInfo`.
    """
    return ProvInfo(
        version=version if version is not None else _tool_version(),
        algorithm="custom" if algo_source == "custom" else algorithm,
        target=target,
        variant=variant,
        comment=comment,
        symbol=symbol,
        naming=naming,
    )


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
    #: Resolved generation provenance, emitted as a block when ``--prov`` /
    #: ``prov=True`` is requested; ``None`` (the default) emits nothing, so
    #: existing output is byte-unchanged.
    provenance: ProvInfo | None = None


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
    names: dict[str, str],
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
        names: The emitted identifier for each role, keyed
            ``oneshot|init|update|finalize|self_test`` (already cased per the
            target's naming convention -- see
            :func:`crcglot._helpers.crc_function_names`).
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
            symbol=names["init"],
        ),
        "update": DocBlock(
            summary="Fold input into the running CRC state and return the new state.",
            params=(state_param,) + data_params,
            returns=f"the updated {state_type} state (not yet finalized).",
            notes=(_CALL_ORDER,) + notes.get("update", ()),
            symbol=names["update"],
        ),
        "finalize": DocBlock(
            summary=_finalize_summary(refin, refout, xorout),
            params=(DocParam("state", f"accumulated {state_type} state from update."),),
            returns="the finished CRC value.",
            notes=("Do not feed the finalized value back into update.",)
            + notes.get("finalize", ()),
            symbol=names["finalize"],
        ),
        "oneshot": DocBlock(
            summary="One-shot convenience: init + a single update + finalize.",
            params=oneshot_params,
            returns="the finished CRC value.",
            notes=notes.get("oneshot", ()),
            symbol=names["oneshot"],
        ),
        "self_test": DocBlock(
            summary="Self-test the implementation against independent reference CRCs.",
            returns=f"{selftest_returns} iff the generated CRC reproduces every "
            "embedded reference value.",
            notes=(
                "Catalogue algorithms check four fixed inputs (the empty string, "
                '"123456789", all 256 byte values, and a 1 KiB pattern); the two '
                "large inputs are regenerated with a byte-at-a-time loop, so no "
                "big array is embedded.  The references come from two independent "
                "engines that had to agree.",
                "Run once on your target toolchain -- it is the cheapest way "
                "to catch a compiler / endianness / width mismatch before "
                "trusting the output.",
            )
            + notes.get("self_test", ()),
            symbol=names["self_test"],
        ),
    }
