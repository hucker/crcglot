"""godoc ``//`` docs that open with the declared identifier.  Go-only.

Overrides only ``doc_block``; the file header is identical to plain (go's
``//`` banner).  The defining idiom is Go's convention that a doc comment
opens with the name it documents ("Name does X").
"""

from __future__ import annotations

from .base import CommentStyle, _block_body, _render
from .model import DocBlock


def _godoc_block_body(block: DocBlock) -> list[str]:
    """A function doc in godoc style: opens with the identifier, then prose.

    Reuses the plain body and rewrites the summary line to ``<name> <verb>...``
    -- Go's ``golint`` convention ("comment should be of the form 'Name ...'").
    """
    body = list(_block_body(block))
    if block.symbol and body:
        lead = block.summary
        lead = lead[0].lower() + lead[1:] if lead else lead
        body[0] = f"{block.symbol} {lead}"
    return body


class GodocStyle(CommentStyle):
    """godoc ``//`` docs that open with the declared identifier.  Go-only."""

    name = "godoc"
    label = "Godoc"
    description = "godoc // comments that open with the identifier"
    languages = frozenset({"go"})

    def doc_block(self, block: DocBlock, indent: int = 0) -> list[str]:
        return _render(self.syntax, _godoc_block_body(block), indent)
