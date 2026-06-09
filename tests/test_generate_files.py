"""Tests for the named-file generation surface (``crcglot.generate_files`` /
``LanguageInfo.generate_files``).

crcglot owns every filename / in-code-naming decision so a CLI / MCP / UI can
"configure once, read finished files out".  These tests pin that contract:
the filename(s) each target produces, the lockstep between a file and the class
named inside it (Java's class *must* equal the file; C# conventionally does),
the cased ``name=`` override, the verbatim ``symbol=`` escape hatch, and the
independent ``file_stem`` knob.

Generation correctness (the CRC math) is covered by ``test_<lang>_gen.py``; here
we assert naming + structure only.
"""

from __future__ import annotations

import pytest

from crcglot import LANGUAGES, AlgorithmInfo, GeneratedFile, generate_files
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


class TestFileStemIndependent:
    """``file_stem`` names the file independently of the in-code identifier."""

    def test_symbol_functions_file_stem_filename(self):
        # symbol drives the function; file_stem drives the filename.
        f = generate_files("rust", "crc32", symbol="explicit", file_stem="outname")[0]
        assert f.filename == "outname.rs", f.filename
        assert "fn explicit_update(" in f.content, "functions follow symbol="


class TestBundle:
    """Several algorithms bundle into one named file."""

    def test_rust_bundle_default_name(self):
        files = generate_files("rust", ["crc32", "crc8"])
        assert [f.filename for f in files] == ["crcglot.rs"], files
        body = files[0].content
        assert "crc32_update(" in body and "crc8_update(" in body, "both present"

    def test_c_bundle_is_pair(self):
        files = generate_files("c", ["crc32", "crc8"])
        assert [f.filename for f in files] == ["crcglot.h", "crcglot.c"], files


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


class TestErrors:
    """The guard rails."""

    def test_algorithm_and_custom_mutually_exclusive(self):
        with pytest.raises(ValueError, match="exactly one"):
            generate_files("rust", "crc32", custom=ALGORITHMS["crc32"])

    def test_name_with_bundle_rejected(self):
        with pytest.raises(ValueError, match="renames one CRC"):
            generate_files("rust", ["crc32", "crc8"], name="x")

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
