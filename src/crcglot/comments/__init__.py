"""Pluggable comment / documentation styles for generated CRC source.

The generators describe their output structurally -- algorithm parameters
(:class:`AlgoMeta`), a language-specific usage example (:class:`UsageExample`),
and a doc block per function (:class:`DocBlock`) -- and a ``CommentStyle``
renders that into the chosen comment style.  The prose (what the comments
*say*) lives once in :mod:`crcglot.comments.model`, so every language reads
identically in spirit and a new style reuses it verbatim.

`plain` is the default: professional, human-readable comments in each
language's native comment syntax, with no documentation-tool markup.  Each
doc-tool style renders the same structured input into a tool's conventions and
is registered for the languages that tool understands:

    doxygen  -> C / C# / Java   (``/** @brief @param */``)
    google   -> Python          (``Args:`` / ``Returns:`` docstrings)
    numpy    -> Python          (numpydoc underlined ``Parameters`` / ``Returns``)
    rest     -> Python          (reST / Sphinx ``:param:`` field lists)
    rustdoc  -> Rust            (``///`` with ``# Arguments`` Markdown)
    godoc    -> Go              (``//`` docs opening with the identifier)
    javadoc  -> Java            (``/** @param @return */``)
    jsdoc    -> TypeScript      (TSDoc ``/** @param x - ... @returns */``)
    docfx    -> C#              (``/// <summary> <param> <returns>`` XML)

Module layout: :mod:`~crcglot.comments.model` (data + shared prose),
:mod:`~crcglot.comments.base` (render engine + ``plain`` + shared body
builders), one module per doc-tool style (``doxygen``, ``google``, ...), and
:mod:`~crcglot.comments.registry` (the style map + validation).  A new style is
a ``CommentStyle`` subclass in its own module plus a registry entry -- the
generators do not change.  ``comment_style_for(language, style)`` validates the
(language, style) pair.
"""

from __future__ import annotations

from .model import (
    DEFAULT_SELFTEST_INPUTS_NOTE,
    HDL_SELFTEST_INPUTS_NOTE,
    AlgoMeta,
    DocBlock,
    DocParam,
    ProvInfo,
    UsageExample,
    build_prov,
    standard_doc_blocks,
)
from .registry import (
    COMMENT_STYLES,
    StyleInfo,
    comment_style_for,
    comment_styles_for_language,
    languages_for_style,
    style_info,
    styles_for_language,
)

__all__ = [
    "COMMENT_STYLES",
    "DEFAULT_SELFTEST_INPUTS_NOTE",
    "HDL_SELFTEST_INPUTS_NOTE",
    "AlgoMeta",
    "DocBlock",
    "DocParam",
    "ProvInfo",
    "StyleInfo",
    "UsageExample",
    "build_prov",
    "comment_style_for",
    "comment_styles_for_language",
    "languages_for_style",
    "standard_doc_blocks",
    "style_info",
    "styles_for_language",
]
