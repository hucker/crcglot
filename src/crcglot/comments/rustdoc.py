"""rustdoc ``///`` item docs with Markdown sections (``# Arguments`` / ...).

Rust-only.  Overrides only ``doc_block`` -- the file header is identical to
plain (rust's ``//`` banner); the per-item ``///`` placement is what rustc
expects.
"""

from __future__ import annotations

from .base import CommentStyle, _render
from .model import DocBlock


def _rustdoc_block_body(block: DocBlock) -> list[str]:
    """A function doc in rustdoc Markdown (``# Arguments`` / ``# Returns``)."""
    body = [block.summary]
    if block.params:
        body += ["", "# Arguments", ""]
        for p in block.params:
            body.append(f"* `{p.name}` - {p.text}")
    if block.returns:
        ret = block.returns[0].upper() + block.returns[1:]
        body += ["", "# Returns", "", ret]
    for note in block.notes:
        body += ["", note]
    return body


class RustdocStyle(CommentStyle):
    """rustdoc ``///`` item docs with Markdown sections.  Rust-only."""

    name = "rustdoc"
    label = "Rustdoc"
    description = "rustdoc /// docs with Markdown (# Arguments / # Returns)"
    languages = frozenset({"rust"})

    def doc_block(self, block: DocBlock, indent: int = 0) -> list[str]:
        return _render(self.syntax, _rustdoc_block_body(block), indent)
