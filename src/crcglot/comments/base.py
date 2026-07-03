"""Render engine, the `plain` style, and the shared pre-render body builders.

A :class:`CommentStyle` turns the structured model into text in one language's
comment syntax.  The base class *is* the ``plain`` rendering; doc-tool styles
subclass it and override ``file_header`` / ``doc_block``.  The ``_*_body``
helpers assemble comment text **before** the language's delimiters are applied
by :func:`_render`, so styles share wording and diverge only on syntax.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from crcglot._helpers import _hex

from .model import AlgoMeta, DocBlock, ProvInfo, UsageExample


# ── comment syntax per language (for the `plain` style) ──────────────────────


@dataclass(frozen=True)
class _Syntax:
    """How to render a `plain` comment block in one language.

    ``kind`` is ``"line"`` (prefix each line), ``"block"`` (``/* ... */``),
    or ``"docstring"`` (Python triple-quote).
    """

    kind: str
    prefix: str = ""
    open: str = "/*"
    cont: str = " *"
    close: str = " */"
    #: For ``line`` kind, the prefix used for the *file header* when it
    #: must differ from per-item docs.  Rust per-item docs are ``///``
    #: (rustdoc would bind a ``///`` file banner to the first item, and a
    #: blank line after it would warn), so the header uses plain ``//``.
    header_prefix: str = ""


_PLAIN_SYNTAX: dict[str, _Syntax] = {
    "c":          _Syntax("block"),
    "csharp":     _Syntax("line", prefix="//"),
    "go":         _Syntax("line", prefix="//"),
    "java":       _Syntax("line", prefix="//"),
    "python":     _Syntax("docstring"),
    "rust":       _Syntax("line", prefix="///", header_prefix="//"),
    "typescript": _Syntax("line", prefix="//"),
    "verilog":    _Syntax("line", prefix="//"),
    "vhdl":       _Syntax("line", prefix="--"),
}

#: The shared ``/** ... */`` block syntax used by doxygen, javadoc and jsdoc.
_BLOCKDOC_SYNTAX = _Syntax("block", open="/**")


def _render(syntax: _Syntax, body: list[str], indent: int) -> list[str]:
    """Render comment ``body`` lines into ``syntax`` at ``indent`` spaces."""
    pad = " " * indent
    if syntax.kind == "line":
        return [
            f"{pad}{syntax.prefix} {ln}".rstrip() if ln else f"{pad}{syntax.prefix}"
            for ln in body
        ]
    if syntax.kind == "docstring":
        if not body:
            return [f'{pad}"""', f'{pad}"""']
        out = [f'{pad}"""{body[0]}'.rstrip()]
        out += [f"{pad}{ln}".rstrip() if ln else "" for ln in body[1:]]
        out.append(f'{pad}"""')
        return out
    # block
    if not body:
        return [f"{pad}{syntax.open}{syntax.close}"]
    out = [f"{pad}{syntax.open} {body[0]}".rstrip()]
    out += [
        f"{pad}{syntax.cont} {ln}".rstrip() if ln else f"{pad}{syntax.cont}"
        for ln in body[1:]
    ]
    out.append(f"{pad}{syntax.close}")
    return out


# ── shared pre-render body builders ──────────────────────────────────────────


def _param_summary(meta: AlgoMeta) -> str:
    """The one-line Rocksoft parameter summary, shared by every style."""
    refl = f"refin={str(meta.refin).lower()}, refout={str(meta.refout).lower()}"
    return (
        f"width={meta.width}, poly={_hex(meta.poly, meta.width)}, "
        f"init={_hex(meta.init, meta.width)}, {refl}, "
        f"xorout={_hex(meta.xorout, meta.width)}"
    )


def _prov_block_lines(prov: ProvInfo) -> list[str]:
    """The always-on provenance block (pre-render), shared by every style.

    Renders the reconstruction parameters as column-aligned ``key: value``
    lines under a single lead, with the producing crcglot ``version`` first so a
    reader knows which release to regenerate with.  All values are constrained
    tokens, so the block is safe in any comment syntax.

    Examples:
        >>> p = ProvInfo("0.21.0", "crc16-xmodem", "c", "table",
        ...              "plain", "crc16_xmodem", "snake")
        >>> _prov_block_lines(p)[0]
        'Reproduce with crcglot:'
        >>> _prov_block_lines(p)[1]
        '    version:   0.21.0'
    """
    fields = (
        ("version", prov.version),
        ("algorithm", prov.algorithm),
        ("target", prov.target),
        ("variant", prov.variant),
        ("comment", prov.comment),
        ("symbol", prov.symbol),
        ("naming", prov.naming),
    )
    width = max(len(key) + 1 for key, _ in fields)  # +1 for the colon
    return ["Reproduce with crcglot:"] + [
        f"    {key + ':':<{width}} {value}" for key, value in fields
    ]


def _generated_by_line(meta: AlgoMeta) -> str:
    """The header's one-line origin statement, scoped to what is true.

    A catalogue algorithm really is from reveng and independently verified.
    A custom polynomial is neither -- its check value is computed by crcglot
    itself -- so claiming "from reveng/<name> -- a verified reference
    implementation" there would be false on both counts.
    """
    if meta.custom:
        return (
            "Generated by crcglot from custom parameters (not a reveng "
            "catalogue entry; see the self-test note on verification)."
        )
    return (
        f"Generated by crcglot from reveng/{meta.name} -- a verified "
        "reference implementation."
    )


def _header_body(meta: AlgoMeta, usage: UsageExample) -> list[str]:
    """Assemble the file-header text (pre-render) -- the standard structure
    shared by every language and style."""
    params = _param_summary(meta)
    body = [
        f"{meta.name} -- {meta.desc}",
        f"Parameters: {params}.",
        f"check: {usage.oneshot} over \"123456789\" == {_hex(meta.check, meta.width)}",
        "",
        "Streaming (init -> update -> finalize); update may be called any",
        "number of times over successive chunks of the message:",
        "",
    ]
    body += ["    " + ln for ln in usage.streaming]
    body += [
        "",
        f"One-shot: {usage.oneshot}",
        f"Verify:   {usage.selftest} {usage.selftest_returns}; run it once on your",
        "          toolchain to catch a compiler / endianness mismatch.",
    ]
    for caveat in usage.caveats:
        body += ["", caveat]
    body += [
        "",
        _generated_by_line(meta),
    ]
    if meta.provenance is not None:
        body += _prov_block_lines(meta.provenance)
    return body


def _block_body(block: DocBlock) -> list[str]:
    """Assemble a function doc block's text (pre-render), plain style."""
    body = [block.summary]
    if block.params:
        body.append("")
        for p in block.params:
            body.append(f"{p.name}: {p.text}")
    if block.returns:
        body.append("")
        body.append(f"Returns {block.returns}")
    for note in block.notes:
        body.append("")
        body.append(note)
    return body


def _tagged_block_body(
    block: DocBlock, *, param_sep: str, returns_tag: str,
) -> list[str]:
    """A ``/** ... */`` doc body using ``@param`` / ``@return`` block tags.

    ``param_sep`` joins a parameter's name and description (JavaDoc uses a
    space, TSDoc a `` - `` hyphen); ``returns_tag`` is ``return`` (JavaDoc) or
    ``returns`` (JSDoc).  Free-form notes precede the block tags, as the
    JavaDoc/JSDoc convention puts the description before the ``@`` tags.
    """
    body = [block.summary]
    for note in block.notes:
        body += ["", note]
    if block.params:
        body.append("")
        for p in block.params:
            body.append(f"@param {p.name}{param_sep}{p.text}")
    if block.returns:
        body.append(f"@{returns_tag} {block.returns}")
    return body


# ── the style base + `plain` ─────────────────────────────────────────────────


class CommentStyle:
    """Base + `plain` rendering.  Subclasses override to inject doc-tool markup.

    Each concrete style is **self-describing**: it declares its own ``name``
    and the ``languages`` it renders correctly for.  The registry derives the
    whole (language, style) compatibility matrix from these, so no higher-level
    code (CLI, MCP) has to hardcode which styles fit which language.
    """

    #: Style code, e.g. ``"doxygen"`` -- the machine-readable name passed to
    #: the generator / CLI / MCP.  Set on each concrete subclass.
    name: str = ""
    #: Human-readable label for a UI dropdown, e.g. ``"Doxygen"``.
    label: str = ""
    #: One-line human description for UI tooltips / help text.
    description: str = ""
    #: Languages this style renders correctly for.  Set on each subclass.
    languages: frozenset[str] = frozenset()

    def __init__(self, language: str) -> None:
        self.language = language
        self.syntax = _PLAIN_SYNTAX[language]

    def file_header(self, meta: AlgoMeta, usage: UsageExample) -> list[str]:
        """Render the top-of-file overview + usage example."""
        syntax = self.syntax
        if syntax.kind == "line" and syntax.header_prefix:
            syntax = replace(syntax, prefix=syntax.header_prefix)
        return _render(syntax, _header_body(meta, usage), indent=0)

    def doc_block(self, block: DocBlock, indent: int = 0) -> list[str]:
        """Render a function's doc comment at ``indent`` spaces."""
        return _render(self.syntax, _block_body(block), indent)


class PlainStyle(CommentStyle):
    """Human-readable comments in the native syntax, no doc-tool markup."""

    name = "plain"
    label = "Plain"
    description = "Human-readable comments in the language's native syntax"
    languages = frozenset(_PLAIN_SYNTAX)  # every language
