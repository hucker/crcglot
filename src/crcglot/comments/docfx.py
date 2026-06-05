"""C# XML documentation comments (``/// <summary> <param> <returns>``).

C#-only and the odd one out of the doc-tool styles: not a ``/** */`` block but
line-prefixed ``///`` carrying XML tags -- the Microsoft-idiomatic form,
consumed by docfx, Visual Studio, and Sandcastle.
"""

from __future__ import annotations

from .base import CommentStyle, _Syntax, _render
from .model import DocBlock

_DOCFX_SYNTAX = _Syntax("line", prefix="///")


def _xml_escape(text: str) -> str:
    """Escape the characters that would make XML doc content ill-formed.

    Only ``&`` and ``<`` must be escaped in element content; a bare ``>``
    (as in our ``init -> update`` prose) is well-formed XML, so it is left
    readable rather than turned into ``&gt;``.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;")


def _docfx_block_body(block: DocBlock) -> list[str]:
    """A function doc as C# XML tags (``<summary>`` / ``<param>`` / ...)."""
    body = ["<summary>", _xml_escape(block.summary), "</summary>"]
    for p in block.params:
        body.append(f'<param name="{p.name}">{_xml_escape(p.text)}</param>')
    if block.returns:
        body.append(f"<returns>{_xml_escape(block.returns)}</returns>")
    if block.notes:
        body.append("<remarks>")
        body += [_xml_escape(n) for n in block.notes]
        body.append("</remarks>")
    return body


class DocfxStyle(CommentStyle):
    """C# XML doc comments (``/// <summary> <param> <returns>``).  C#-only.

    The file header stays a plain ``//`` banner -- an XML doc comment before
    the ``using`` directives would warn CS1587 ("not on a valid language
    element") -- while the per-member ``<summary>`` docs sit directly above
    each method, where the C# compiler binds them to the declaration.
    """

    name = "docfx"
    label = "DocFX"
    description = "C# XML doc comments (/// <summary> <param> <returns>)"
    languages = frozenset({"csharp"})

    def doc_block(self, block: DocBlock, indent: int = 0) -> list[str]:
        return _render(_DOCFX_SYNTAX, _docfx_block_body(block), indent)
