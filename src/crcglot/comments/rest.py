"""reStructuredText / Sphinx field-list Python docstrings (``:param:``).

Python-only.  The native Sphinx field-list convention (``:param:`` /
``:returns:``), common in Sphinx-documented codebases.  Overrides only
``doc_block``; the module header is the standard overview docstring inherited
from the base.
"""

from __future__ import annotations

from .base import CommentStyle, _render
from .model import DocBlock


def _rest_block_body(block: DocBlock) -> list[str]:
    """A function docstring as reST field lists (``:param:`` / ``:returns:``)."""
    body = [block.summary]
    if block.params or block.returns:
        body.append("")
    for p in block.params:
        body.append(f":param {p.name}: {p.text}")
    if block.returns:
        body.append(f":returns: {block.returns}")
    for note in block.notes:
        body += ["", ".. note::", f"   {note}"]
    return body


class RestStyle(CommentStyle):
    """reStructuredText / Sphinx field-list docstrings.  Python-only."""

    name = "rest"
    label = "reStructuredText"
    description = "Sphinx field-list docstrings (:param: / :returns:)"
    languages = frozenset({"python"})

    def doc_block(self, block: DocBlock, indent: int = 0) -> list[str]:
        return _render(self.syntax, _rest_block_body(block), indent)
