"""Doxygen ``/** @brief @param @return */`` markup for C / C# / Java.

Demonstrates the seam: DoxygenStyle overrides only the two hook methods,
consuming the same structured :class:`AlgoMeta` / :class:`DocBlock` the
generators already build -- no generator changes.
"""

from __future__ import annotations

from crcglot._helpers import _hex

from .base import (
    _BLOCKDOC_SYNTAX,
    CommentStyle,
    _generated_by_line,
    _param_summary,
    _prov_block_lines,
    _render,
)
from .model import AlgoMeta, DocBlock, UsageExample


def _doxy_block_body(block: DocBlock) -> list[str]:
    """A function doc block in Doxygen tags (``@brief`` / ``@param`` / ...)."""
    body = [f"@brief {block.summary}"]
    if block.params:
        body.append("")
        for p in block.params:
            body.append(f"@param {p.name} {p.text}")
    if block.returns:
        body.append(f"@return {block.returns}")
    for note in block.notes:
        body.append(f"@note {note}")
    return body


def _doxy_header_body(meta: AlgoMeta, usage: UsageExample) -> list[str]:
    """The file overview as a Doxygen ``@file`` block with an ``@code`` example."""
    body = [
        "@file",
        f"@brief {meta.name} -- {meta.desc}",
        "",
        f"Parameters: {_param_summary(meta)}.",
        f"check: {usage.oneshot} over \"123456789\" == "
        f"{_hex(meta.check, meta.width)}",
        "",
        "Streaming -- call init, then update over each chunk, then finalize:",
        "@code",
    ]
    body += ["    " + ln for ln in usage.streaming]
    body += [
        "@endcode",
        "",
        f"One-shot: {usage.oneshot}",
        f"@note Verify with {usage.selftest} ({usage.selftest_returns}); run it "
        "once on your toolchain to catch a compiler / endianness mismatch.",
    ]
    for caveat in usage.caveats:
        body += ["", f"@note {caveat}"]
    body += [
        "",
        f"@note {_generated_by_line(meta)}",
    ]
    if meta.provenance is not None:
        body += _prov_block_lines(meta.provenance)
    return body


class DoxygenStyle(CommentStyle):
    """Doxygen ``/** @brief ... @param ... */`` markup for C / C++.

    Overrides the two hook methods only; the structured inputs are
    identical to :class:`PlainStyle`, so no generator changes are needed.
    """

    name = "doxygen"
    label = "Doxygen"
    description = "Doxygen /** @brief @param @return */ markup"
    languages = frozenset({"c", "csharp", "java"})

    def file_header(self, meta: AlgoMeta, usage: UsageExample) -> list[str]:
        return _render(_BLOCKDOC_SYNTAX, _doxy_header_body(meta, usage), indent=0)

    def doc_block(self, block: DocBlock, indent: int = 0) -> list[str]:
        return _render(_BLOCKDOC_SYNTAX, _doxy_block_body(block), indent)
