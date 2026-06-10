"""Tests for the named-file generation surface (``crcglot.generate_files`` /
``LanguageInfo.generate_files``).

crcglot owns every filename / in-code-naming decision so a CLI / MCP / UI can
"configure once, read finished files out".  These tests pin that contract:
the filename(s) each target produces, the lockstep between a file and the class
named inside it (Java's class *must* equal the file; C# conventionally does),
the cased ``name=`` knob (the one that sets file + identifier, single or
bundle), and the verbatim ``symbol=`` escape hatch.

Generation correctness (the CRC math) is covered by ``test_<lang>_gen.py``; here
we assert naming + structure only.
"""

from __future__ import annotations

import pytest

from crcglot import (
    LANGUAGES,
    AlgorithmInfo,
    GeneratedFile,
    default_stem,
    generate_files,
)
from crcglot.catalogue import ALGORITHMS


class TestFilenameContract:
    """The filename(s) and extension(s) each language emits for one algorithm."""

    # (language, expected filenames) for crc16-xmodem.
    _EXPECTED = {
        "c": ["crc16_xmodem.h", "crc16_xmodem.c"],
        "csharp": ["Crc16Xmodem.cs"],
        "go": ["crc16_xmodem.go"],
        "java": ["Crc16Xmodem.java"],
        "python": ["crc16_xmodem.py"],
        "rust": ["crc16_xmodem.rs"],
        "typescript": ["crc16_xmodem.ts"],
        "verilog": ["crc16_xmodem.sv"],
        "vhdl": ["crc16_xmodem.vhd"],
    }

    @pytest.mark.parametrize("language", sorted(_EXPECTED))
    def test_filenames_per_language(self, language):
        # Act
        files = generate_files(language, "crc16-xmodem")

        # Assert
        actual = [f.filename for f in files]
        expected = self._EXPECTED[language]
        assert actual == expected, f"{language}: {actual} != {expected}"
        for f in files:
            assert isinstance(f, GeneratedFile), "returns GeneratedFile records"
            assert f.content.strip(), f"{language}: empty content for {f.filename}"

    def test_c_pair_is_role_labelled(self):
        # Act
        files = generate_files("c", "crc16-xmodem")

        # Assert -- the two C files are labelled for a UI.
        roles = {f.role: f.filename for f in files}
        assert roles == {"header": "crc16_xmodem.h", "source": "crc16_xmodem.c"}, (
            f"C header/source roles wrong: {roles}"
        )


class TestLockstep:
    """For class-named files (Java hard, C# by convention), the in-code class
    declaration must equal the filename stem -- the property that makes the
    output drop-in correct."""

    @pytest.mark.parametrize(
        "language,decl",
        [
            ("java", "public final class {stem}"),
            ("csharp", "public static class {stem}"),
        ],
    )
    def test_class_name_equals_filename_stem(self, language, decl):
        # Act
        f = generate_files(language, "crc16-xmodem")[0]

        # Assert -- filename stem (drop the extension) appears as the class.
        stem = f.filename.rsplit(".", 1)[0]
        assert decl.format(stem=stem) in f.content, (
            f"{language}: class != filename stem {stem!r}"
        )


class TestNameOverrideCased:
    """``name=`` replaces the algorithm name and is cased per target."""

    def test_rust_snake(self):
        f = generate_files("rust", "crc32", name="my-widget")[0]
        assert f.filename == "my_widget.rs", f.filename
        assert "fn my_widget_update(" in f.content, "rust functions are snake-cased"

    def test_java_pascal_class_camel_methods(self):
        f = generate_files("java", "crc32", name="my-widget")[0]
        assert f.filename == "MyWidget.java", f.filename
        assert "public final class MyWidget" in f.content, "class is PascalCase"
        assert "myWidgetUpdate(" in f.content, "methods are camelCase (Java default)"

    def test_csharp_pascal(self):
        f = generate_files("csharp", "crc32", name="my-widget")[0]
        assert f.filename == "MyWidget.cs", f.filename
        assert "public static class MyWidget" in f.content, "class is PascalCase"

    def test_name_is_cased_not_verbatim(self):
        # The whole point: name= flows through the casing machinery, unlike the
        # verbatim symbol= escape hatch.
        f = generate_files("go", "crc32", name="my-widget")[0]
        assert "MyWidgetUpdate(" in f.content, "Go default is PascalCase"


class TestSymbolVerbatim:
    """``symbol=`` emits the identifier verbatim (the escape hatch)."""

    def test_rust_symbol_unchanged(self):
        f = generate_files("rust", "crc32", symbol="myCheck")[0]
        assert "fn myCheck_update(" in f.content, "symbol kept verbatim, not re-cased"
        assert f.filename == "myCheck.rs", f.filename


class TestNameSymbolDivergence:
    """``name=`` + ``symbol=`` lets the filename and the in-code identifier
    differ: the file follows ``name``, the functions follow ``symbol``."""

    def test_name_files_symbol_functions(self):
        # name drives the filename; symbol drives the (verbatim) function.
        f = generate_files("rust", "crc32", name="outname", symbol="explicit")[0]
        assert f.filename == "outname.rs", f.filename
        assert "fn explicit_update(" in f.content, "functions follow symbol="


class TestBundle:
    """Several algorithms bundle into one named file."""

    def test_rust_bundle_default_name(self):
        files = generate_files("rust", ["crc32", "crc8"])
        assert [f.filename for f in files] == ["crc_bundle.rs"], files
        body = files[0].content
        assert "crc32_update(" in body and "crc8_update(" in body, "both present"

    def test_c_bundle_is_pair(self):
        files = generate_files("c", ["crc32", "crc8"])
        assert [f.filename for f in files] == ["crc_bundle.h", "crc_bundle.c"], files

    def test_bundle_named(self):
        # name= now names a bundle's file / module; each member keeps its fn.
        files = generate_files("rust", ["crc32", "crc8"], name="checks")
        assert [f.filename for f in files] == ["checks.rs"], files
        body = files[0].content
        assert "crc32_update(" in body and "crc8_update(" in body, "both present"


class TestCustom:
    """A custom AlgorithmInfo generates the same way as a catalogue entry."""

    def test_custom_named(self):
        algo = AlgorithmInfo(
            width=16, poly=0x8005, init=0, refin=False, refout=False,
            xorout=0, check=ALGORITHMS["crc16-arc"].check,
            desc="custom", source="custom",
        )
        f = generate_files("rust", custom=algo, name="my-proto")[0]
        assert f.filename == "my_proto.rs", f.filename
        assert "fn my_proto_update(" in f.content, "custom functions follow name="


class TestValidateSymbol:
    """``LanguageInfo.validate_symbol`` is the UI pre-check."""

    def test_sanitizes_to_identifier(self):
        assert LANGUAGES["rust"].validate_symbol("my-crc.v2") == "my_crc_v2"

    def test_pascal_target_rejects_leading_digit(self):
        with pytest.raises(ValueError, match="not a legal"):
            LANGUAGES["java"].validate_symbol("3crc")


class TestFormatFilename:
    """``LanguageInfo.format_filename`` cases a stem to the target's filename
    convention -- and must agree with the file ``generate_files`` actually
    writes."""

    @pytest.mark.parametrize(
        "language,stem,expected",
        [
            ("rust", "crc_bundle", "crc_bundle"),        # snake: as-is
            ("c", "my-crc.v2", "my_crc_v2"),             # snake: - / . -> _
            ("python", "crc32", "crc32"),
            ("csharp", "crc-bundle", "CrcBundle"),       # pascal: cased
            ("java", "crc32", "Crc32"),
        ],
    )
    def test_cases_per_target(self, language, stem, expected):
        # Act
        actual = LANGUAGES[language].format_filename(stem)

        # Assert
        assert actual == expected, f"{language}.format_filename({stem!r})"

    @pytest.mark.parametrize("language", sorted(LANGUAGES))
    def test_matches_generated_filename(self, language):
        # The contract: format_filename(stem) is exactly the basename
        # generate_files(name=stem) emits -- so a UI's preview can't lie.
        stem = "my-crc.v2"

        # Act
        previewed = LANGUAGES[language].format_filename(stem)
        generated_stem = generate_files(
            language, "crc32", name=stem
        )[0].filename.rsplit(".", 1)[0]

        # Assert
        assert previewed == generated_stem, (
            f"{language}: preview {previewed!r} != generated {generated_stem!r}"
        )

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_returned_unchanged(self, blank):
        # Total: empty / whitespace passes through (the caller guards empty).
        actual = LANGUAGES["java"].format_filename(blank)
        assert actual == blank, f"blank {blank!r} should pass through unchanged"

    def test_idempotent_on_snake_and_single_word_pascal(self):
        # Snake casing and single-word PascalCase are fixed points, so a
        # value re-fed through stays stable.
        once_rust = LANGUAGES["rust"].format_filename("crc32")
        twice_rust = LANGUAGES["rust"].format_filename(once_rust)
        assert twice_rust == once_rust, "snake target is idempotent"

        once_java = LANGUAGES["java"].format_filename("crc32")
        twice_java = LANGUAGES["java"].format_filename(once_java)
        assert twice_java == once_java, "single-word pascal is idempotent"


class TestFormatNameIdentifier:
    """``format_name(stem, "identifier")`` cases to each language's naming standard."""

    @pytest.mark.parametrize(
        "language,expected",
        [
            ("rust", "crc_bundle"),       # snake default
            ("python", "crc_bundle"),     # snake
            ("go", "CrcBundle"),          # pascal default
            ("csharp", "CrcBundle"),      # pascal
            ("typescript", "crcBundle"),  # camel default
            ("java", "crcBundle"),        # camel
        ],
    )
    def test_identifier_follows_language_naming(self, language, expected):
        # Act
        actual = LANGUAGES[language].format_name("crc-bundle", "identifier")

        # Assert
        assert actual == expected, f"{language} identifier casing"

    @pytest.mark.parametrize("language", sorted(LANGUAGES))
    def test_identifier_matches_generator_oneshot(self, language):
        # The contract: the identifier preview equals the bare (oneshot) name
        # the generator's own naming helper produces -- no parallel casing.
        from crcglot._helpers import crc_function_names

        lang = LANGUAGES[language]

        # Act
        previewed = lang.format_name("crc-bundle", "identifier")
        generated = crc_function_names("crc_bundle", lang.default_naming)["oneshot"]

        # Assert
        assert previewed == generated, (
            f"{language}: preview {previewed!r} != oneshot {generated!r}"
        )

    def test_filename_is_the_default_kind(self):
        # Act
        lang = LANGUAGES["csharp"]

        # Assert -- bare call equals the filename convenience.
        assert lang.format_name("crc-bundle") == lang.format_filename("crc-bundle"), (
            "default kind is filename"
        )

    def test_bad_kind_rejected(self):
        with pytest.raises(ValueError, match="kind must be"):
            LANGUAGES["rust"].format_name("crc32", "klass")


class TestDefaultStem:
    """``default_stem`` is the raw stem crcglot defaults to (single vs bundle)."""

    def test_single_algorithm_name(self):
        actual = default_stem("crc16-xmodem")
        assert actual == "crc16_xmodem", "single algo -> sanitized name"

    def test_one_element_sequence_is_single(self):
        actual = default_stem(["crc32"])
        assert actual == "crc32", "a one-element bundle is still a single CRC"

    def test_bundle_is_crc_bundle(self):
        actual = default_stem(["crc32", "crc8"])
        assert actual == "crc_bundle", "two+ algos -> crc_bundle"

    def test_drives_generated_bundle_filename(self):
        # The contract: default_stem is the stem generate_files writes for a
        # no-override bundle.
        stem = default_stem(["crc32", "crc8"])

        # Act
        f = generate_files("rust", ["crc32", "crc8"])[0]

        # Assert
        assert f.filename == f"{stem}.rs", "bundle filename follows default_stem"


class TestErrors:
    """The guard rails."""

    def test_algorithm_and_custom_mutually_exclusive(self):
        with pytest.raises(ValueError, match="exactly one"):
            generate_files("rust", "crc32", custom=ALGORITHMS["crc32"])

    def test_symbol_with_bundle_rejected(self):
        with pytest.raises(ValueError, match="names one function"):
            generate_files("rust", ["crc32", "crc8"], symbol="x")

    def test_symbol_rejected_for_java(self):
        with pytest.raises(ValueError, match="not used for Java"):
            generate_files("java", "crc32", symbol="x")

    def test_unknown_language(self):
        with pytest.raises(ValueError, match="unknown language"):
            generate_files("cobol", "crc32")

    def test_unknown_algorithm(self):
        with pytest.raises(ValueError, match="unknown algorithm"):
            generate_files("rust", "nope-not-a-crc")
