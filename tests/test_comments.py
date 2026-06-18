"""Tests for the pluggable comment-style system (``crcglot.comments``).

These are *structural* tests over the generated text: they assert that the
``plain`` style emits the documentation a reference-implementation consumer
needs (a file header with a copy-paste streaming example, the per-function
doc blocks, the special-consideration notes) and -- critically -- that the
emitted comments are *syntactically* well formed (balanced ``/* */`` for the
block-comment languages).  Whether the documented code actually *compiles*
is covered by the per-language execution suites; here we only guard the
comment layer those suites would not specifically diagnose.
"""

from __future__ import annotations

import ast

import pytest

from crcglot import ALGORITHMS, LANGUAGES
from crcglot._helpers import _func_name, crc_function_names
from crcglot.comments import (
    COMMENT_STYLES,
    comment_style_for,
    comment_styles_for_language,
    languages_for_style,
    style_info,
    styles_for_language,
)

# Languages whose ``plain`` comment is a ``/* ... */`` block (the only ones
# where a stray ``*/`` in a description or example would break compilation).
_BLOCK_COMMENT_LANGS = ("c",)


def _names(lang: str, name: str = "crc32") -> dict[str, str]:
    """The five emitted function names under ``lang``'s default convention.

    Lets the header/identifier assertions stay correct as each language's
    idiomatic naming default differs (snake / camel / pascal).  C# is the
    exception: it wraps each algorithm in its own class, so its methods are
    role-only (``Compute`` / ``Init`` / ...) -- the class, not the method,
    carries the algorithm name.
    """
    naming = LANGUAGES[lang].default_naming
    if lang == "csharp":
        from crcglot.lang.csharp import _cs_method_names
        return _cs_method_names(naming)
    return crc_function_names(_func_name(name), naming)


def _source(lang: str, name: str = "crc32", variant: str = "bitwise") -> str:
    """Generate ``name`` for ``lang`` and return one flat source string.

    C returns a ``(header, source)`` pair; everything else a single string.
    Joining the pair keeps the per-language assertions uniform.
    """
    out = LANGUAGES[lang].generator(name, variant=variant)
    assert out is not None, f"{lang}: generator returned None for {name!r}"
    return "\n".join(out) if isinstance(out, tuple) else out


# ── registry / validation ────────────────────────────────────────────────


@pytest.mark.parametrize("lang", sorted(LANGUAGES))
def test_plain_valid_for_every_language(lang: str) -> None:
    """``plain`` resolves for all nine targets (it is the universal style)."""
    # Act
    style = comment_style_for(lang, "plain")

    # Assert
    actual = style.language
    assert actual == lang, f"{lang}: comment_style_for set language={actual!r}"


def test_unknown_style_raises() -> None:
    """An unregistered style name is rejected as 'unknown'."""
    # Act / Assert
    with pytest.raises(ValueError, match="unknown comment style"):
        comment_style_for("rust", "doesnotexist")


def test_style_not_valid_for_language_raises() -> None:
    """A real style applied to the wrong language explains where it applies."""
    # Act / Assert -- rustdoc is a Rust-only style, so Go must be rejected.
    with pytest.raises(ValueError, match="not valid for language 'go'"):
        comment_style_for("go", "rustdoc")


def test_every_registered_style_resolves_on_its_language() -> None:
    """All eight styles are implemented and resolve on a language they target."""
    # Arrange -- each style mapped to one language it is registered for.
    style_lang = {
        "plain": "c", "doxygen": "c", "google": "python", "numpy": "python",
        "rest": "python", "rustdoc": "rust", "godoc": "go", "javadoc": "java",
        "jsdoc": "typescript", "docfx": "csharp",
    }

    # Assert -- the map covers exactly the registry, and each pair resolves.
    actual_styles = set(style_lang)
    expected_styles = set(COMMENT_STYLES)
    assert actual_styles == expected_styles, "test map must cover every style"
    for style, lang in style_lang.items():
        resolved = comment_style_for(lang, style).language
        assert resolved == lang, f"{style} must resolve for {lang}"


def test_generator_propagates_invalid_style() -> None:
    """The (lang, style) check fires through the generator entry point too."""
    # Act / Assert -- the CLI/MCP rely on this ValueError to return rc 2
    # (docfx is C#-only, so requesting it for C must be rejected).
    with pytest.raises(ValueError, match="not valid for language 'c'"):
        LANGUAGES["c"].generator("crc32", comment_style="docfx")


# ── self-describing styles + the compatibility query API ─────────────────


def test_styles_are_self_describing() -> None:
    """Every style declares its own name + non-empty languages.

    This is the single source of truth the matrix is derived from, so the
    invariant must hold: a style's ``name`` matches its registry key and its
    ``languages`` are a subset of the known targets.
    """
    # Arrange
    known_langs = set(LANGUAGES)

    # Act / Assert -- resolve each style on one of its languages and inspect.
    for style in COMMENT_STYLES:
        langs = languages_for_style(style)
        assert langs, f"{style} must declare at least one language"
        assert langs <= known_langs, f"{style} targets unknown languages: {langs}"
        instance = comment_style_for(next(iter(langs)), style)
        assert instance.name == style, (
            f"style {style!r} resolved to a class whose name is {instance.name!r}"
        )


def test_styles_for_language_is_derived_not_hardcoded() -> None:
    """styles_for_language returns exactly the styles whose languages include it."""
    # Act
    py = styles_for_language("python")
    c = styles_for_language("c")
    go = styles_for_language("go")

    # Assert -- plain is universal; doc-tool styles land only where declared.
    assert py == ("plain", "google", "numpy", "rest"), f"python styles: {py}"
    assert "doxygen" in c and "docfx" not in c, f"c styles: {c}"
    assert "doxygen" not in go and "godoc" in go, f"go styles: {go}"
    for lang in LANGUAGES:
        assert "plain" in styles_for_language(lang), f"plain missing for {lang}"


def test_query_api_round_trips_with_resolution() -> None:
    """styles_for_language and comment_style_for agree on every (lang,style)."""
    # Act / Assert -- a style is listed for a language iff it resolves there.
    for lang in LANGUAGES:
        listed = set(styles_for_language(lang))
        for style in COMMENT_STYLES:
            if style in listed:
                assert comment_style_for(lang, style).language == lang, (
                    f"{style} listed for {lang} but failed to resolve"
                )
            else:
                with pytest.raises(ValueError, match="not valid for language"):
                    comment_style_for(lang, style)


def test_languages_for_style_unknown_raises() -> None:
    """An unknown style name is a KeyError from languages_for_style."""
    # Act / Assert
    with pytest.raises(KeyError):
        languages_for_style("nope")


def test_style_info_carries_machine_and_human_names() -> None:
    """style_info exposes name (machine) + label/description (human) for UIs."""
    # Act
    info = style_info("google")

    # Assert -- distinct machine vs human names, plus a description + languages.
    assert info.name == "google", "machine-readable name"
    assert info.label == "Google", "human-readable label"
    assert info.description, "must carry a one-line description"
    assert "python" in info.languages, "must report where it applies"


def test_every_style_has_label_and_description() -> None:
    """No style may ship without a UI label + description (dropdown needs both)."""
    # Act / Assert
    for style in COMMENT_STYLES:
        info = style_info(style)
        assert info.label, f"{style} is missing a label"
        assert info.description, f"{style} is missing a description"


def test_comment_styles_for_language_is_ui_ready() -> None:
    """comment_styles_for_language returns dropdown-ready records in order."""
    # Act
    c = comment_styles_for_language("c")
    py = comment_styles_for_language("python")

    # Assert -- names match the lightweight query, and each record is complete.
    assert [s.name for s in c] == list(styles_for_language("c")), "c order/names"
    assert [s.name for s in py] == ["plain", "google", "numpy", "rest"], "py names"
    for s in c + py:
        assert s.name and s.label and s.description, f"incomplete record: {s}"


# Doxygen natively parses the /** @brief */ syntax for these brace languages.
_DOXYGEN_LANGS = ("c", "csharp", "java")


@pytest.mark.parametrize("lang", _DOXYGEN_LANGS)
def test_doxygen_emits_tagged_docs(lang: str) -> None:
    """The doxygen style renders @brief/@param/@return -- no generator change.

    Demonstrates the seam: the SAME generator, a different style argument,
    yields Doxygen markup.  The generator passes structured DocBlocks; the
    style decides the syntax.  One DoxygenStyle serves all three languages.
    """
    # Act
    out = LANGUAGES[lang].generator(
        "crc32", variant="table", comment_style="doxygen"
    )
    src = "\n".join(out) if isinstance(out, tuple) else out

    # Assert -- the doc-tool tags appear and the blocks stay balanced.
    assert "@file" in src, f"{lang}: doxygen header must carry @file"
    assert "@brief" in src, f"{lang}: doxygen must tag summaries with @brief"
    assert "@param state" in src, f"{lang}: doxygen must emit @param for update"
    assert "@return" in src, f"{lang}: doxygen must emit @return"
    # Count every block-open ``/*`` (``/**`` is a superset spelling of it, and
    # C's ``#endif /* GUARD */`` is a plain block too) against every ``*/``.
    actual_open = src.count("/*")
    actual_close = src.count("*/")
    assert actual_open == actual_close, (
        f"{lang}: doxygen block comments must balance "
        f"({actual_open} '/*' vs {actual_close} '*/')"
    )


def test_plain_does_not_leak_doxygen_tags() -> None:
    """The default plain style must never emit doxygen markup."""
    # Act
    header, _ = LANGUAGES["c"].generator("crc32")  # type: ignore[misc]

    # Assert
    assert "@brief" not in header, "plain style must not emit doxygen tags"


def test_doxygen_rejected_for_non_brace_languages() -> None:
    """doxygen applies to C / C# / Java; other targets are rejected."""
    # Act / Assert -- Rust/Go have their own (reserved) doc styles.
    for lang in ("rust", "go", "typescript", "python", "verilog", "vhdl"):
        with pytest.raises(ValueError, match="not valid for language"):
            comment_style_for(lang, "doxygen")


# ── google-style Python docstrings ───────────────────────────────────────


def test_google_emits_napoleon_sections() -> None:
    """The google style renders Args:/Returns:/Note: sections -- no py change."""
    # Act
    src = LANGUAGES["python"].generator(
        "crc32", variant="table", comment_style="google"
    )

    # Assert -- the Napoleon section headers appear, and a param is listed
    # under Args (indented one level deeper than the header).
    assert "Args:" in src, "google must emit an Args: section"
    assert "Returns:" in src, "google must emit a Returns: section"
    assert "Note:" in src, "google must emit a Note: section"
    assert "        state: running int state" in src, (
        "google must list params indented under Args:"
    )


def test_google_python_is_valid_and_executes() -> None:
    """Google-style output stays syntactically valid, runnable Python."""
    # Arrange
    src = LANGUAGES["python"].generator(
        "crc32", variant="table", comment_style="google"
    )

    # Act -- parse + run the embedded self-test.
    ast.parse(src)
    ns: dict = {}
    exec(src, ns)  # noqa: S102 - generated code under test
    self_test = ns["crc32_self_test"]

    # Assert
    actual = self_test()
    assert actual is True, "google-style crc32 self_test must pass"


def test_google_is_python_only() -> None:
    """google is registered for Python; other targets are rejected."""
    # Act / Assert
    for lang in ("c", "rust", "go", "java", "csharp", "typescript"):
        with pytest.raises(ValueError, match="not valid for language"):
            comment_style_for(lang, "google")


def test_numpy_emits_underlined_sections() -> None:
    """numpydoc renders underlined Parameters/Returns sections, valid Python."""
    # Act
    src = LANGUAGES["python"].generator(
        "crc32", variant="table", comment_style="numpy"
    )

    # Assert -- section header underlined with matching-length dashes.
    assert "Parameters\n    ----------" in src, "numpy needs an underlined Parameters"
    assert "Returns\n    -------" in src, "numpy needs an underlined Returns"
    ast.parse(src)  # still valid Python


def test_rest_emits_param_field_lists() -> None:
    """reST renders :param:/:returns: field lists, valid Python."""
    # Act
    src = LANGUAGES["python"].generator(
        "crc32", variant="table", comment_style="rest"
    )

    # Assert
    assert ":param state: running int state" in src, "rest needs :param: fields"
    assert ":returns: the updated int state" in src, "rest needs a :returns: field"
    ast.parse(src)


def test_numpy_and_rest_are_python_only() -> None:
    """numpy and rest are registered for Python; other targets are rejected."""
    # Act / Assert
    for style in ("numpy", "rest"):
        for lang in ("c", "rust", "go", "java", "csharp", "typescript"):
            with pytest.raises(ValueError, match="not valid for language"):
                comment_style_for(lang, style)


# ── rustdoc (Rust) and godoc (Go) ────────────────────────────────────────


def test_rustdoc_emits_markdown_sections() -> None:
    """rustdoc renders /// item docs with # Arguments / # Returns Markdown."""
    # Act
    src = LANGUAGES["rust"].generator(
        "crc32", variant="table", comment_style="rustdoc"
    )

    # Assert -- Markdown section headers + backtick-quoted param bullets, all
    # inside outer (``///``) doc comments.
    assert "/// # Arguments" in src, "rustdoc must emit a # Arguments section"
    assert "/// # Returns" in src, "rustdoc must emit a # Returns section"
    assert "/// * `state` - running u32 state" in src, (
        "rustdoc must list params as backtick-quoted bullets"
    )


def test_rustdoc_is_rust_only() -> None:
    """rustdoc is registered for Rust; other targets are rejected."""
    # Act / Assert
    for lang in ("c", "go", "python", "java", "csharp"):
        with pytest.raises(ValueError, match="not valid for language"):
            comment_style_for(lang, "rustdoc")


def test_godoc_doc_opens_with_identifier() -> None:
    """godoc docs open with the declared name (Go's golint convention)."""
    # Act
    src = LANGUAGES["go"].generator(
        "crc32", variant="table", comment_style="godoc"
    )

    # Assert -- the doc comment opens with the (Pascal, by Go default) name.
    go = _names("go")
    assert f"// {go['update']} fold input into" in src, (
        "godoc doc must open with the function identifier"
    )
    assert f"// {go['init']} return the initial" in src, (
        "godoc doc must open with the function identifier"
    )


def test_godoc_is_go_only() -> None:
    """godoc is registered for Go; other targets are rejected."""
    # Act / Assert
    for lang in ("c", "rust", "python", "java", "csharp", "typescript"):
        with pytest.raises(ValueError, match="not valid for language"):
            comment_style_for(lang, "godoc")


# ── javadoc (Java) and jsdoc / TSDoc (TypeScript) ────────────────────────


def test_javadoc_emits_param_return_no_brief() -> None:
    """JavaDoc uses @param/@return and -- unlike doxygen -- no @brief/@file."""
    # Act
    src = LANGUAGES["java"].generator(
        "crc32", variant="table", comment_style="javadoc"
    )

    # Assert
    assert "@param state running int state" in src, "javadoc needs @param"
    assert "@return the updated int state" in src, "javadoc needs @return"
    assert "@brief" not in src, "javadoc must not use doxygen's @brief"
    assert "@file" not in src, "javadoc must not use doxygen's @file"
    assert src.count("/*") == src.count("*/"), "javadoc blocks must balance"


def test_jsdoc_uses_tsdoc_hyphen_and_returns() -> None:
    """TSDoc JSDoc uses '@param name - desc' and '@returns', no {type}."""
    # Act
    src = LANGUAGES["typescript"].generator(
        "crc32", variant="table", comment_style="jsdoc"
    )

    # Assert
    assert "@param state - running number state" in src, (
        "jsdoc must use the TSDoc ' - ' parameter separator"
    )
    assert "@returns the updated number state" in src, "jsdoc needs @returns"
    assert "{number}" not in src, "TSDoc omits {type} (the signature has it)"
    assert src.count("/*") == src.count("*/"), "jsdoc blocks must balance"


def test_javadoc_jsdoc_language_restrictions() -> None:
    """javadoc is Java-only; jsdoc is TypeScript-only."""
    # Act / Assert
    for lang in ("c", "csharp", "rust", "go", "python", "typescript"):
        with pytest.raises(ValueError, match="not valid for language"):
            comment_style_for(lang, "javadoc")
    for lang in ("c", "csharp", "rust", "go", "python", "java"):
        with pytest.raises(ValueError, match="not valid for language"):
            comment_style_for(lang, "jsdoc")


# ── docfx (C# XML documentation comments) ────────────────────────────────


def test_docfx_emits_xml_doc_tags() -> None:
    """docfx renders /// <summary> <param name=...> <returns> XML for C#."""
    # Act
    src = LANGUAGES["csharp"].generator(
        "crc32", variant="table", comment_style="docfx"
    )

    # Assert -- line-prefixed (///) XML tags, params keyed by name.
    assert "/// <summary>" in src, "docfx must open a <summary>"
    assert '/// <param name="state">running uint state' in src, (
        "docfx must emit <param name=...> elements"
    )
    assert "/// <returns>the updated uint state" in src, "docfx needs <returns>"
    assert "/** " not in src, "docfx uses /// XML, not a /** */ block"


def test_docfx_escapes_xml_metacharacters() -> None:
    """A '<' or '&' in doc text is escaped so the XML stays well-formed.

    No catalogue prose contains those today, but the renderer must not be the
    weak link if one ever does -- a bare '<' would break the XML doc parse.
    """
    # Arrange -- exercise the escaper directly on adversarial text.
    from crcglot.comments.docfx import _xml_escape  # noqa: PLC0415 - local to test

    # Act
    actual = _xml_escape("a < b && c")
    expected = "a &lt; b &amp;&amp; c"

    # Assert
    assert actual == expected, f"_xml_escape gave {actual!r}"


def test_docfx_is_csharp_only() -> None:
    """docfx is registered for C#; other targets are rejected."""
    # Act / Assert
    for lang in ("c", "java", "rust", "go", "python", "typescript"):
        with pytest.raises(ValueError, match="not valid for language"):
            comment_style_for(lang, "docfx")


def test_comment_style_default_is_plain() -> None:
    """Omitting comment_style yields the same bytes as an explicit plain."""
    # Act -- hold variant constant (bitwise, matching _source) so this isolates
    # comment_style; the default variant is "auto"/fastest, a separate axis.
    implicit = _source("rust")
    explicit = "\n".join(  # rust returns a str, but stay uniform
        s if isinstance(s, str) else "\n".join(s)
        for s in [
            LANGUAGES["rust"].generator("crc32", variant="bitwise", comment_style="plain")
        ]
    )

    # Assert
    assert implicit == explicit, "default comment_style must equal 'plain'"


# ── per-language plain structure ─────────────────────────────────────────


@pytest.mark.parametrize("lang", sorted(LANGUAGES))
def test_header_carries_provenance_and_check(lang: str) -> None:
    """Every file header names its reveng source and the check value."""
    # Act
    src = _source(lang)

    # Assert -- provenance line and the check: line both present.
    assert "from reveng/crc32" in src, f"{lang}: header missing reveng provenance"
    assert "check:" in src, f"{lang}: header missing check: line"


@pytest.mark.parametrize("lang", sorted(LANGUAGES))
def test_header_shows_streaming_triple(lang: str) -> None:
    """The copy-paste example names all three streaming functions.

    This is the whole point of the feature: a consumer must learn the
    ``init -> update -> finalize`` contract from the comments, not the tests.
    """
    # Act
    src = _source(lang)

    # Assert -- names use the language's idiomatic naming default.
    names = _names(lang)
    for role in ("init", "update", "finalize"):
        assert names[role] in src, f"{lang}: streaming example missing {names[role]}"


@pytest.mark.parametrize("lang", sorted(LANGUAGES))
def test_header_mentions_oneshot_and_selftest(lang: str) -> None:
    """The header documents the one-shot call and the self-test."""
    # Act
    src = _source(lang)

    # Assert
    assert "One-shot:" in src, f"{lang}: header missing One-shot: line"
    assert "Verify:" in src, f"{lang}: header missing Verify: line"
    assert _names(lang)["self_test"] in src, (
        f"{lang}: header missing self-test reference"
    )


@pytest.mark.parametrize("lang", sorted(LANGUAGES))
def test_each_function_is_documented(lang: str) -> None:
    """The four invariant doc-block summaries each appear in the output."""
    # Arrange -- the invariant summaries authored once in comments.py.
    # ``finalize`` is deliberately excluded: its summary is parameter-aware
    # (see test_finalize_summary_*), not a fixed string.
    summaries = (
        "Return the initial CRC state",
        "Fold input into the running CRC state",
        "One-shot convenience",
        "Self-test the implementation",
    )

    # Act
    src = _source(lang)

    # Assert
    for summary in summaries:
        assert summary in src, f"{lang}: missing doc block summary {summary!r}"


@pytest.mark.parametrize("lang", sorted(LANGUAGES))
def test_finalize_summary_tracks_xorout(lang: str) -> None:
    """finalize's summary reflects whether the algorithm has a final XOR.

    The body XORs only when ``xorout != 0``; the summary must agree.  crc32
    has ``xorout=0xFFFFFFFF`` (final XOR) while crc16-modbus has ``xorout=0``
    (a ``return state`` no-op), so the two must read differently.
    """
    # Act
    xor_src = _source(lang, "crc32")
    noop_src = _source(lang, "crc16-modbus")

    # Assert -- xor-only algorithm names the XOR, no-op algorithm denies it.
    assert "Apply the final XOR to produce the CRC." in xor_src, (
        f"{lang}: crc32 finalize must document the final XOR"
    )
    assert "applies no final transform" not in xor_src, (
        f"{lang}: crc32 finalize must not claim it is a no-op"
    )
    assert "this algorithm applies no final transform" in noop_src, (
        f"{lang}: crc16-modbus finalize must document the no-op"
    )
    assert "final XOR" not in noop_src, (
        f"{lang}: crc16-modbus finalize must not claim a final XOR"
    )


def test_finalize_summary_reflect_case() -> None:
    """A ``refin != refout`` algorithm documents output reflection in finalize.

    No catalogue entry hits this (all have ``refin == refout``); a custom spec
    is the only way to reach the reflect branch, so it is exercised directly.
    """
    # Arrange -- a custom spec with mismatched reflection and a final XOR;
    # generate_python_from_entry is the seam for non-catalogue algorithms.
    from crcglot.lang.python import generate_python_from_entry
    from crcglot.catalogue import AlgorithmInfo

    algo = AlgorithmInfo(
        width=8, poly=0x07, init=0x00, refin=True, refout=False,
        xorout=0x55, check=0x00, desc="reflect-case probe", source="custom",
    )

    # Act
    src = generate_python_from_entry("odd", algo)

    # Assert
    expected = "Reflect the CRC and apply the final XOR to produce the result."
    assert expected in src, "mismatched refin/refout must document reflection"


def test_finalize_summary_helper_covers_all_shapes() -> None:
    """The helper maps each (reflects, xors) combination to distinct wording."""
    # Arrange / Act -- all four shapes; reflects := refout != refin.
    from crcglot.comments.model import _finalize_summary

    actual = {
        "reflect+xor": _finalize_summary(refin=True, refout=False, xorout=0x55),
        "reflect": _finalize_summary(refin=True, refout=False, xorout=0),
        "xor": _finalize_summary(refin=True, refout=True, xorout=0x55),
        "noop": _finalize_summary(refin=True, refout=True, xorout=0),
    }
    expected = {
        "reflect+xor": "Reflect the CRC and apply the final XOR to produce the result.",
        "reflect": "Reflect the CRC to produce the final result.",
        "xor": "Apply the final XOR to produce the CRC.",
        "noop": "Return the finished CRC; this algorithm applies no final transform.",
    }

    # Assert
    assert actual == expected, f"finalize summary wording mismatch: {actual}"


# ── special considerations ───────────────────────────────────────────────


def test_c_selftest_documents_zero_on_success() -> None:
    """C's self-test returns an int; the docs must state 0 == success."""
    # Act
    src = _source("c")

    # Assert
    assert "returns 0 on success" in src, "C header must document 0-on-success"


def test_java_width32_documents_unsigned_caveat() -> None:
    """Java width-32 results are signed; the docs must point to toUnsignedLong."""
    # Act -- crc32 is width 32, the case that bites Java's signed int.
    src = _source("java")

    # Assert
    assert "Integer.toUnsignedLong" in src, (
        "Java width-32 output must document the unsigned conversion"
    )


def test_verilog_documents_one_byte_update() -> None:
    """Verilog's update takes one byte; the gotcha must be in the comments."""
    # Act
    src = _source("verilog")

    # Assert
    assert "byte per call" in src, (
        "Verilog update must document its one-byte-per-call contract"
    )


@pytest.mark.parametrize("lang", ("verilog", "vhdl"))
def test_hdl_documents_simulator_scope(lang: str) -> None:
    """Both HDLs disclose they are simulator references, not synthesizable RTL."""
    # Act
    src = _source(lang)

    # Assert
    assert "Simulator reference" in src, f"{lang}: missing simulator-scope note"


# ── comment-balance safety ───────────────────────────────────────────────


# Valid (language, name, variant) cells for the block-comment balance check.
# Built explicitly (not via skips) so the run is all-green: slice8 only
# applies at width 32/64, so it is paired only with the wide algorithms.
_BLOCK_BALANCE_CELLS = [
    (lang, name, variant)
    for lang in _BLOCK_COMMENT_LANGS
    for name in ("crc32", "crc8", "crc16-modbus", "crc64-xz")
    for variant in LANGUAGES[lang].variants_for_width(ALGORITHMS[name].width)
]


@pytest.mark.parametrize("lang, name, variant", _BLOCK_BALANCE_CELLS)
def test_block_comments_balanced(lang: str, name: str, variant: str) -> None:
    """Block-comment output never emits an unmatched ``/*`` or ``*/``.

    A stray ``*/`` inside a description or example would prematurely close the
    comment and break compilation -- the one failure mode the execution suite
    might miss if the bad text lands in a rarely-compiled cell.
    """
    # Act
    out = LANGUAGES[lang].generator(name, variant=variant)
    assert out is not None, f"{lang}: generator returned None for {name!r}"
    src = "\n".join(out) if isinstance(out, tuple) else out

    # Assert
    actual_open = src.count("/*")
    actual_close = src.count("*/")
    assert actual_open == actual_close, (
        f"{lang}/{name}/{variant}: unbalanced block comments "
        f"({actual_open} '/*' vs {actual_close} '*/')"
    )


def test_no_catalogue_description_contains_block_marker() -> None:
    """No catalogue ``desc`` carries a ``/*`` or ``*/`` block-comment marker.

    The ``plain`` renderer is line-oriented and passes descriptions through
    verbatim, so a catalogue entry whose prose contained a comment terminator
    would silently unbalance the C header.  Guard the *inputs* so that can
    never happen, rather than trying to escape it at render time.
    """
    # Act / Assert
    offenders = [
        name for name, algo in ALGORITHMS.items()
        if "/*" in algo.desc or "*/" in algo.desc
    ]
    assert offenders == [], (
        f"catalogue descriptions must not contain block-comment markers: "
        f"{offenders}"
    )


# ── reproduce-with-crcglot provenance block ──────────────────────────────
#
# Every file header carries a "Reproduce with crcglot" block of the producing
# version plus the resolved generation parameters (always on, no flag).  It
# flows through the shared `_header_body` (plain / numpy / rest / rustdoc /
# godoc / docfx / jsdoc / javadoc) and the two hand-written header bodies
# (google, doxygen), so it must render in EVERY (language, style) cell with
# balanced delimiters where the style is block-based.  The `version` is stamped
# from `crcglot.__version__` so a reader knows which release to regenerate with;
# C also carries it in the linkable `const` provenance record (see test_c_gen.py).

_PROV_MARKER = "Reproduce with crcglot:"
_PROV_KEYS = (
    "version", "algorithm", "target", "variant", "comment", "symbol", "naming",
)

# Every (language, style) pair, derived from the registry so a new style is
# covered automatically (age-proof, per the project's cruft-audit guidance).
_PROV_STYLE_CELLS = [
    (lang, style)
    for lang in sorted(LANGUAGES)
    for style in styles_for_language(lang)
]


def _prov_source(lang: str, *, style: str, name: str = "crc32",
                 variant: str = "bitwise") -> str:
    """Generate ``name`` for ``lang`` in ``style`` and flatten to one string."""
    out = LANGUAGES[lang].generator(
        name, variant=variant, comment_style=style,
    )
    assert out is not None, f"{lang}/{style}: generator returned None for {name!r}"
    return "\n".join(out) if isinstance(out, tuple) else out


def test_prov_stamps_the_crcglot_version() -> None:
    """The block stamps the producing ``crcglot.__version__`` so a reader knows
    which release emitted the file and can regenerate it with the same one."""
    # Arrange
    import crcglot

    # Act
    src = _prov_source("c", style="plain")

    # Assert -- the block is present and carries a ``version:`` line equal to
    # the installed crcglot version.
    assert _PROV_MARKER in src, "header missing the reproduce-with block"
    line = next(ln for ln in src.splitlines() if "version:" in ln)
    actual = line.split("version:", 1)[1].strip()
    expected = crcglot.__version__
    assert actual == expected, (
        f"block version should be the installed {expected!r}, got {actual!r}"
    )


@pytest.mark.parametrize("lang, style", _PROV_STYLE_CELLS)
def test_prov_block_present_in_every_style(lang: str, style: str) -> None:
    """Every (language, style) cell emits the marker plus all six key lines,
    and block-comment styles stay delimiter-balanced (no injection)."""
    # Act
    src = _prov_source(lang, style=style)

    # Assert -- the block and each reconstruction key are present.
    assert _PROV_MARKER in src, f"{lang}/{style}: reproduce-with block missing"
    for key in _PROV_KEYS:
        assert f"{key}:" in src, f"{lang}/{style}: provenance block missing {key!r}"

    # Assert -- block-comment renderings remain balanced.
    actual_open, actual_close = src.count("/*"), src.count("*/")
    assert actual_open == actual_close, (
        f"{lang}/{style}: provenance block unbalanced the comment "
        f"({actual_open} '/*' vs {actual_close} '*/')"
    )


def test_prov_variant_is_canonical() -> None:
    """The block records the resolved variant, not the raw flag or ``auto``.

    Rust ``crc32`` with ``variant="auto"`` resolves to slice-by-8; the block
    must show ``slice8``, the load-bearing proof we emit ``resolved``.
    """
    # Act
    src = _prov_source("rust", style="plain", name="crc32", variant="auto")

    # Assert
    line = next(ln for ln in src.splitlines() if "variant:" in ln)
    actual = line.split("variant:", 1)[1].strip()
    expected = "slice8"
    assert actual == expected, (
        f"provenance variant should be the resolved {expected!r}, got {actual!r}"
    )


def test_prov_labels_custom_polynomial_as_custom() -> None:
    """A custom (non-catalogue) polynomial is labelled ``algorithm: custom``."""
    # Arrange
    from crcglot.catalogue import custom_algorithm

    cust = custom_algorithm(width=16, poly=0x1021, desc="a custom crc")

    # Act
    files = LANGUAGES["rust"].generate_files(custom=cust)
    src = files[0].content

    # Assert
    line = next(ln for ln in src.splitlines() if "algorithm:" in ln)
    actual = line.split("algorithm:", 1)[1].strip()
    expected = "custom"
    assert actual == expected, (
        f"custom polynomial should be labelled {expected!r}, got {actual!r}"
    )
