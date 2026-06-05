"""NumPy-style Python docstrings (numpydoc: underlined Parameters / Returns).

Python-only.  The convention ubiquitous in scientific Python (numpy, scipy,
pandas, scikit-learn).  Overrides only ``doc_block`` -- the module header is
the standard overview docstring inherited from the base.  Per-parameter types
are omitted (the generated signatures already carry them, which is idiomatic
numpydoc when annotations are present).
"""

from __future__ import annotations

from .base import CommentStyle, _render
from .model import DocBlock


def _section(title: str) -> list[str]:
    """A numpydoc section header underlined with dashes of matching length."""
    return [title, "-" * len(title)]


def _numpy_block_body(block: DocBlock) -> list[str]:
    """A function docstring in NumPy style (underlined Parameters / Returns)."""
    body = [block.summary]
    if block.params:
        body += ["", *_section("Parameters")]
        for p in block.params:
            body.append(p.name)
            body.append(f"    {p.text}")
    if block.returns:
        body += ["", *_section("Returns"), f"    {block.returns}"]
    if block.notes:
        body += ["", *_section("Notes"), *block.notes]
    return body


class NumpyStyle(CommentStyle):
    """NumPy-style (numpydoc) Python docstrings.  Python-only."""

    name = "numpy"
    label = "NumPy"
    description = "NumPy (numpydoc) docstrings, underlined Parameters / Returns"
    languages = frozenset({"python"})

    def doc_block(self, block: DocBlock, indent: int = 0) -> list[str]:
        return _render(self.syntax, _numpy_block_body(block), indent)
