"""TSDoc-flavored JSDoc ``/** ... @param x - ... @returns ... */``.

TypeScript-only.  The other JavaDoc-family member: omits ``{type}``
annotations -- the signature already carries the types -- and uses the TSDoc
`` - `` parameter separator and ``@returns`` tag, via the shared
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


class JsdocStyle(CommentStyle):
    """TSDoc-flavored JSDoc ``/** ... @param x - ... @returns ... */``.

    TypeScript-only.  Omits ``{type}`` annotations -- the signature already
    carries the types -- and uses the TSDoc `` - `` parameter separator.
    """

    name = "jsdoc"
    label = "JSDoc"
    description = "TSDoc /** @param x - ... @returns */ comments"
    languages = frozenset({"typescript"})

    def file_header(self, meta: AlgoMeta, usage: UsageExample) -> list[str]:
        return _render(_BLOCKDOC_SYNTAX, _header_body(meta, usage), indent=0)

    def doc_block(self, block: DocBlock, indent: int = 0) -> list[str]:
        body = _tagged_block_body(block, param_sep=" - ", returns_tag="returns")
        return _render(_BLOCKDOC_SYNTAX, body, indent)
