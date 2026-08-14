"""Regenerate EXAMPLES.md from the current state of the generators.

Run this script before tagging a release.  It emits a self-contained
``EXAMPLES.md`` built from three tours, each varying exactly one axis:

* every language, same algorithm and same defaults;
* every implementation variant, in one language;
* every documentation style, in the language that owns it.

Crossing those axes (the old language x variant grid) mostly repeated
itself: seeing the same algorithm table-driven in eleven languages
answers the language question eleven times and the variant question once.

Usage:
    uv run python scripts/regenerate_examples.py

Effect:
    Overwrites ``EXAMPLES.md`` at the repo root.

The header prose is hardcoded below.  Edit it here if you want to
change what readers see above the gallery.

Verification: open the file in GitHub's preview / VS Code's preview
and confirm every block expands cleanly.  Or just diff the result --
the only changes between runs should be when generator output
genuinely changes.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Lazy-load to be runnable as a script from anywhere in the repo.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from crcglot import (  # noqa: E402  (sys.path adjusted above)
    ALGORITHMS,
    LANGUAGES,
    VARIANT_ORDER,
    generate_files,
    variant_info,
)
from crcglot.comments import (  # noqa: E402  (sys.path adjusted above)
    style_info,
    styles_for_language,
)


# The language tour.  A 16-bit CRC over a framed serial protocol is the
# case crcglot exists for: the polynomial is fixed by someone else's wire
# format and has to be matched exactly.  Deliberately not crc32, which is
# the one algorithm crcglot itself advises against generating (every
# target's standard library already has it, running ~30x faster on CPU CRC
# instructions).  init and xorout are both 0xFFFF, so the emitted
# ``finalize`` shows real work rather than ``return state``.
_ALGORITHM = "crc16-ibm-sdlc"

# The variant tour.  Slice-by-8 exists only at width 32 / 64, so the one
# section that compares variants needs a wider algorithm than the rest of
# the gallery.  crc32-bzip2 is also non-reflected, making its inner loop
# the other of the two shapes the generators emit.
_VARIANT_ALGORITHM = "crc32-bzip2"
_VARIANT_LANGUAGE = "c"

# The comment-styles tour renders the short bit-by-bit form, so each block
# is mostly the comments being compared rather than a 256-entry table.
_STYLE_VARIANT = "bitwise"


# CLI flag per variant.  No flag means ``--fast``, so bit-by-bit needs
# ``--small`` spelled out: ``crcglot c crc32`` emits slice-by-8.
_VARIANT_FLAG = {
    "bitwise": "--small",
    "table": "--table",
    "slice8": "--slice8",
}

# Code-fence info string per language.  Most just equal the LANGUAGES
# code key; exceptions get an explicit override below.  The human-readable
# label comes from ``LanguageInfo.display_name`` so adding a new language
# requires no edits to this script.
_FENCE_OVERRIDES = {
    "verilog": "systemverilog",  # GitHub Linguist recognizes this label
}


def _fence_for(code: str) -> str:
    return _FENCE_OVERRIDES.get(code, code)


def _algorithm_summary(name: str) -> str:
    """One-line parameter summary, read from the catalogue entry."""
    a = ALGORITHMS[name]
    return (
        f"width={a.width}, poly=0x{a.poly:0{a.width // 4}X}, "
        f"init=0x{a.init:0{a.width // 4}X}, "
        f"refin={str(a.refin).lower()}, refout={str(a.refout).lower()}, "
        f"xorout=0x{a.xorout:0{a.width // 4}X}"
    )


def _header() -> str:
    algo = ALGORITHMS[_ALGORITHM]
    return f"""# crcglot generated code gallery

Every block below is real `crcglot` output.  Reproduce any of it with the command shown above the code.

Three tours, each varying one thing:

- [Every language](#every-language): what crcglot emits for each of the {len(LANGUAGES)} targets, same algorithm and same defaults, so the only difference between blocks is the language.
- [Implementation variants](#implementation-variants): what `--small`, `--table`, and `--slice8` change, all in one language.
- [Documentation styles](#documentation-styles): what `--comment` changes, one block per style.

The language tour uses `{_ALGORITHM}` ({algo.desc}; {_algorithm_summary(_ALGORITHM)}).  \
Matching a 16-bit CRC that some existing wire format already fixed is the job crcglot is for.  \
Every generated file embeds a `_self_test()` that checks itself against the catalogue's canonical value, `crc("123456789") == 0x{algo.check:0{algo.width // 4}X}`.

The variant tour uses `{_VARIANT_ALGORITHM}` instead, because slice-by-8 exists only at width 32 and 64.  \
It is also non-reflected, so its inner loop is the other of the two shapes the generators emit.

Want a different algorithm?  Substitute the name (`crcglot list` for the full catalogue) and re-run the command.

Every block is collapsed by default.  Click a heading to expand.

This file is auto-generated by `scripts/regenerate_examples.py`.  Do not hand-edit; re-run the script instead.
"""


_LANGUAGE_INTRO = f"""## Every language

The same algorithm (`{_ALGORITHM}`), generated with no flags, for every target.  \
No flags means `--fast`, so each block is the fastest implementation that target supports at width 16.  \
Verilog and VHDL are bit-by-bit because that is the only form that makes sense in hardware."""


_VARIANT_INTRO = f"""## Implementation variants

`{_VARIANT_ALGORITHM}` in {LANGUAGES[_VARIANT_LANGUAGE].display_name} three times, so the only difference between blocks is the implementation strategy.  \
Slice-by-8 is why this tour uses a 32-bit algorithm: it exists only at width 32 and 64.  \
`--fast` is the default, and picks the last variant the target supports at the algorithm's width."""


_COMMENT_STYLES_INTRO = f"""## Documentation styles

The `--comment` flag picks the documentation style of the generated code; the algorithm and inner loop are identical.  \
Each style appears once, in a language that uses it, because seeing Doxygen three times says nothing the first block did not.  \
`plain`, professional comments in the language's native syntax, is the default and every target supports it.  \
Blocks below are `{_ALGORITHM}` bit-by-bit so you are reading comments rather than a lookup table."""


def _lang_anchor(code: str) -> str:
    """Stable anchor for a language-tour cell.

    We emit explicit ``<a id="...">`` tags rather than relying on GitHub's
    auto-anchor from the rendered heading -- ``C`` and ``C#`` would collide
    (``#`` is stripped from auto-anchors).
    """
    return f"lang-{code}"


def _variant_anchor(variant: str) -> str:
    return f"variant-{variant}"


def _style_anchor(code: str, style: str) -> str:
    return f"style-{code}-{style}"


def _details_block(
    anchor: str,
    heading: str,
    cmd: str,
    lang: str,
    algorithm: str,
    *,
    subtitle: str | None = None,
    variant: str = "auto",
    comment_style: str = "plain",
) -> str:
    """Render one collapsible ``<details>`` block.

    Generation goes through :func:`crcglot.generate_files`, the same front
    door the CLI uses, so the code shown is what the displayed command
    actually produces -- filenames and in-code class names included.  C emits
    a (header, source) pair; every other target emits one file.  ``subtitle``
    is an optional italic line under the heading.
    """
    files = generate_files(
        lang, algorithm, variant=variant, comment_style=comment_style
    )
    fence = _fence_for(lang)

    parts: list[str] = [
        f'<a id="{anchor}"></a>',
        "",
        "<details>",
        "<summary>",
        "",
        f"### {heading}",
        "",
        "</summary>",
        "",
    ]
    if subtitle:
        parts += [f"_{subtitle}_", ""]
    parts += ["```bash", cmd, "```", ""]
    for f in files:
        # Label only when there is more than one file to tell apart.
        if len(files) > 1:
            parts += [f"**`{f.filename}`**", ""]
        parts += [f"```{fence}", f.content, "```", ""]
    parts += ["</details>", ""]
    return "\n".join(parts)


def _render_language(code: str) -> str:
    """Render one language-tour cell: ``_ALGORITHM`` with no flags.

    The command carries no variant flag, so generation asks for ``"auto"``
    too; the subtitle names what crcglot resolves that to rather than
    restating the rule.
    """
    info = LANGUAGES[code]
    vi = variant_info(info.fastest_variant_for_width(ALGORITHMS[_ALGORITHM].width))

    return _details_block(
        _lang_anchor(code),
        info.display_name,
        f"crcglot {code} {_ALGORITHM}",
        code,
        _ALGORITHM,
        subtitle=f"{vi.label}: {vi.description}",
    )


def _render_variant(variant: str) -> str:
    """Render one variant-tour cell: ``_VARIANT_ALGORITHM`` in one language."""
    vi = variant_info(variant)

    return _details_block(
        _variant_anchor(variant),
        vi.label,
        f"crcglot {_VARIANT_LANGUAGE} {_VARIANT_ALGORITHM} "
        f"{_VARIANT_FLAG[variant]}",
        _VARIANT_LANGUAGE,
        _VARIANT_ALGORITHM,
        subtitle=vi.description,
        variant=variant,
    )


def _render_style(code: str, style: str) -> str:
    """Render one style-tour cell: ``_ALGORITHM`` bit-by-bit in one style."""
    si = style_info(style)

    return _details_block(
        _style_anchor(code, style),
        f"{si.label} ({LANGUAGES[code].display_name})",
        f"crcglot {code} {_ALGORITHM} "
        f"{_VARIANT_FLAG[_STYLE_VARIANT]} --comment {style}",
        code,
        _ALGORITHM,
        subtitle=si.description,
        variant=_STYLE_VARIANT,
        comment_style=style,
    )


def _style_owners() -> dict[str, list[str]]:
    """Map each language to the styles it is the showcase for.

    Ownership is derived rather than tabulated: the first language (by code)
    that supports a style owns it.  That lands every single-language style on
    its own language (rustdoc on Rust, godoc on Go, docfx on C#) and the
    shared ones (`plain`, `doxygen`) on C, with no map to maintain when a
    target gains a style.
    """
    owner: dict[str, list[str]] = {c: [] for c in LANGUAGES}
    claimed: set[str] = set()
    for code in sorted(LANGUAGES):
        for style in styles_for_language(code):
            if style not in claimed:
                claimed.add(style)
                owner[code].append(style)
    return owner


def _quick_links(owners: dict[str, list[str]]) -> str:
    """Build the Quick links TOC: one line per tour."""
    langs = " · ".join(
        f"[{LANGUAGES[c].display_name}](#{_lang_anchor(c)})"
        for c in sorted(LANGUAGES)
    )
    variants = " · ".join(
        f"[{variant_info(v).label}](#{_variant_anchor(v)})"
        for v in VARIANT_ORDER
    )
    styles = " · ".join(
        f"[{style_info(s).label}](#{_style_anchor(c, s)})"
        for c in sorted(owners)
        for s in owners[c]
    )
    return "\n".join([
        "## Quick links", "",
        f"- **Every language**: {langs}",
        f"- **Implementation variants**: {variants}",
        f"- **Documentation styles**: {styles}",
        "",
    ])


def render() -> str:
    """Build the whole of EXAMPLES.md as a string.

    Separate from :func:`main` so a test can compare the committed file
    against a fresh render without writing to the repo.
    """
    owners = _style_owners()
    out: list[str] = [_header(), _quick_links(owners)]

    out.append(_LANGUAGE_INTRO + "\n")
    for code in sorted(LANGUAGES):
        out.append(_render_language(code))

    out.append(_VARIANT_INTRO + "\n")
    for variant in VARIANT_ORDER:
        out.append(_render_variant(variant))

    out.append(_COMMENT_STYLES_INTRO + "\n")
    for code in sorted(owners):
        for style in owners[code]:
            out.append(_render_style(code, style))

    return "\n".join(out)


def main() -> None:
    target = Path(__file__).parent.parent / "EXAMPLES.md"
    target.write_text(render(), encoding="utf-8")
    print(f"Wrote {target} ({target.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
