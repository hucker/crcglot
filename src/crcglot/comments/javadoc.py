"""Standard JavaDoc ``/** ... @param ... @return ... */``.  Java-only.

A member of the JavaDoc ``/** ... */`` family.  Unlike doxygen it carries no
``@brief`` / ``@file`` -- the summary IS the first sentence -- via the shared
:func:`_tagged_block_body` helper.
"""

from __future__ import annotations

from .base import (
    _BLOCKDOC_SYNTAX,
    CommentStyle,
    _header_body,
    _render,
    _tagged_block_body,
)
from .model import AlgoMeta, DocBlock, UsageExample


class JavadocStyle(CommentStyle):
    """Standard JavaDoc ``/** ... @param ... @return ... */``.  Java-only."""

    name = "javadoc"
    label = "Javadoc"
    description = "JavaDoc /** @param @return */ comments"
    languages = frozenset({"java"})

    def file_header(self, meta: AlgoMeta, usage: UsageExample) -> list[str]:
        return _render(_BLOCKDOC_SYNTAX, _header_body(meta, usage), indent=0)

    def doc_block(self, block: DocBlock, indent: int = 0) -> list[str]:
        body = _tagged_block_body(block, param_sep=" ", returns_tag="return")
        return _render(_BLOCKDOC_SYNTAX, body, indent)
