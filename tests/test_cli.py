"""Tests for the crcglot CLI.

Strategy: call ``crcglot.cli.main(argv)`` directly with an explicit
argv list -- no subprocess.  ``capsys`` captures stdout/stderr;
``monkeypatch.chdir(tmp_path)`` isolates filesystem writes for the
``file=STEM`` output path.

Coverage targets the public surface (subcommands, options, error
paths, both output modes) plus the small helpers (_parse_int,
_parse_bool, _parse_kv_tokens) which are unit-tested directly because
their behavior is the contract for every codegen invocation.  Filename
/ identifier sanitizing now lives in crcglot (LanguageInfo.validate_symbol),
exercised here and in test_generate_files.py.

All tests are fast (no toolchain calls); the CRC generators are
already execution-verified in test_{c,rust,vhdl,python}_gen.py.
"""

from __future__ import annotations

import json

import pytest

from crcglot import LANGUAGES
from crcglot.cli import (
    _parse_bool,
    _parse_int,
    _parse_kv_tokens,
    build_parser,
    main,
)


# ─────────────────────────────────────────────────────────────────────
# Helper functions -- unit tested directly.
# ─────────────────────────────────────────────────────────────────────


class TestParseInt:
    """``_parse_int`` accepts hex (0x...) or decimal; case-insensitive on prefix."""

    @pytest.mark.parametrize("value,expected", [
        ("0", 0),
        ("42", 42),
        ("0xFF", 255),
        ("0xff", 255),
        ("0X10", 16),
        ("  0x1234  ", 0x1234),
        ("65535", 65535),
    ])
    def test_valid(self, value, expected):
        assert _parse_int(value) == expected

    @pytest.mark.parametrize("value", ["", "abc", "0xZZ", "12.5", "0x"])
    def test_invalid_raises(self, value):
        with pytest.raises(ValueError):
            _parse_int(value)


class TestParseBool:
    """``_parse_bool`` is permissive: true/false/1/0/yes/no/on/off, any case."""

    @pytest.mark.parametrize("value", [
        "true", "True", "TRUE", "1", "yes", "YES", "on", "ON", "  true  ",
    ])
    def test_truthy(self, value):
        assert _parse_bool(value) is True

    @pytest.mark.parametrize("value", [
        "false", "False", "FALSE", "0", "no", "NO", "off", "OFF", "  false  ",
    ])
    def test_falsy(self, value):
        assert _parse_bool(value) is False

    @pytest.mark.parametrize("value", ["maybe", "", "2", "y", "n"])
    def test_invalid_raises(self, value):
        with pytest.raises(ValueError, match="expected true/false"):
            _parse_bool(value)


class TestValidateSymbol:
    """``LanguageInfo.validate_symbol`` sanitizes a stem to a valid identifier
    base (basename, ``-`` / ``.`` -> ``_``).  crcglot owns this now -- the CLI's
    private ``_symbol_from_stem`` moved here.  Strict (filename == class)
    targets additionally reject a stem that can't be a legal class name."""

    @pytest.mark.parametrize("stem,expected", [
        ("mycrc", "mycrc"),
        ("my-crc", "my_crc"),
        ("crc16.modbus", "crc16_modbus"),
        ("path/to/my-file", "my_file"),
        ("a-b.c-d", "a_b_c_d"),
    ])
    def test_basenames_and_substitutions(self, stem, expected):
        assert LANGUAGES["rust"].validate_symbol(stem) == expected

    def test_pascal_target_rejects_illegal_class(self):
        # Java's file == class, so a stem that PascalCases to an illegal
        # identifier (leading digit) is rejected up front.
        with pytest.raises(ValueError, match="not a legal"):
            LANGUAGES["java"].validate_symbol("3bad")


class TestParseKvTokens:
    """``_parse_kv_tokens`` splits CLI tokens into recognized key=value
    pairs vs bare positional tokens.  Only keys in the allowlist
    (width/poly/init/refin/refout/xorout/name/desc/file/symbol) are
    captured as kv; everything else stays bare."""

    def test_only_known_keys_are_kv(self):
        kv, bare = _parse_kv_tokens(
            ["width=16", "poly=0x8005", "crc32", "unknown=val"],
        )
        assert kv == {"width": "16", "poly": "0x8005"}
        assert bare == ["crc32", "unknown=val"]

    def test_file_and_symbol_are_kv(self):
        kv, bare = _parse_kv_tokens(["file=out", "symbol=my_func", "crc32"])
        assert kv == {"file": "out", "symbol": "my_func"}
        assert bare == ["crc32"]

    def test_empty_tokens_yields_empty(self):
        kv, bare = _parse_kv_tokens([])
        assert kv == {}
        assert bare == []

    def test_value_can_contain_equals(self):
        # desc=foo=bar -- split on first '='
        kv, bare = _parse_kv_tokens(["desc=foo=bar"])
        assert kv == {"desc": "foo=bar"}
        assert bare == []


# ─────────────────────────────────────────────────────────────────────
# `crcglot list`
# ─────────────────────────────────────────────────────────────────────


class TestListCommand:
    def test_list_all(self, capsys):
        rc = main(["list"])
        out, _err = capsys.readouterr()
        assert rc == 0
        # At least crc32 and crc16-modbus should appear.
        assert "crc32" in out
        assert "crc16-modbus" in out
        assert "width=" in out

    def test_list_with_glob(self, capsys):
        rc = main(["list", "crc16-*"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "crc16-modbus" in out
        assert "crc16-xmodem" in out
        # crc32 should NOT match crc16-*
        for line in out.splitlines():
            algo = line.strip().split()[0] if line.strip() else ""
            if algo:
                assert algo.startswith("crc16-"), (
                    f"glob 'crc16-*' leaked non-matching {algo!r}"
                )

    def test_list_no_match_returns_1(self, capsys):
        rc = main(["list", "nonexistent-*"])
        _out, err = capsys.readouterr()
        assert rc == 1
        assert "No algorithms match" in err

    def test_list_json_contains_full_params(self, capsys):
        rc = main(["list", "crc32", "--json"])
        out, _err = capsys.readouterr()
        assert rc == 0
        payload = json.loads(out)
        assert payload["count"] == 1
        actual = payload["algorithms"][0]
        assert actual["name"] == "crc32"
        assert actual["width"] == 32
        assert actual["poly_hex"] == "0x04C11DB7"
        assert actual["check_hex"] == "0xCBF43926"

    def test_list_json_respects_glob_filter(self, capsys):
        rc = main(["list", "crc16-*", "--json"])
        out, _err = capsys.readouterr()
        assert rc == 0
        payload = json.loads(out)
        names = [entry["name"] for entry in payload["algorithms"]]
        assert names, "expected at least one crc16-* algorithm"
        assert all(name.startswith("crc16-") for name in names)


# ─────────────────────────────────────────────────────────────────────
# `crcglot info`
# ─────────────────────────────────────────────────────────────────────


class TestInfoCommand:
    def test_info_known_algorithm(self, capsys):
        rc = main(["info", "crc32"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "crc32" in out
        assert "width:" in out
        assert "poly:" in out
        assert "0x04C11DB7" in out
        assert "check:" in out

    def test_info_includes_desc_when_present(self, capsys):
        # crc16-modbus has a desc in the catalogue.
        rc = main(["info", "crc16-modbus"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "desc:" in out

    def test_info_unknown_algorithm_returns_1(self, capsys):
        rc = main(["info", "totally-fake-crc"])
        _out, err = capsys.readouterr()
        assert rc == 1
        assert "Unknown algorithm" in err


# ─────────────────────────────────────────────────────────────────────
# `crcglot compute`
# ─────────────────────────────────────────────────────────────────────


class TestComputeCommand:
    def test_compute_defaults_to_hex(self, capsys):
        rc = main(["compute", "crc32", "123456789"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert out.strip() == "0xCBF43926"

    def test_compute_dec_override(self, capsys):
        rc = main(["compute", "crc32", "123456789", "--dec"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert out.strip() == "3421780262"


# ─────────────────────────────────────────────────────────────────────
# Codegen -- stdout path.
# ─────────────────────────────────────────────────────────────────────


class TestCodegenStdout:
    """Without file=STEM, generator output goes to stdout.  C emits
    header + source separated by a blank line; the rest emit a single
    body."""

    def test_python_stdout(self, capsys):
        rc = main(["python", "crc16-modbus"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "def crc16_modbus(" in out

    def test_rust_stdout(self, capsys):
        rc = main(["rust", "crc16-modbus"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "fn crc16_modbus" in out
        assert "u16" in out

    def test_vhdl_stdout(self, capsys):
        rc = main(["vhdl", "crc16-modbus"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "package crc16_modbus_pkg" in out

    def test_c_stdout_emits_header_and_source(self, capsys):
        rc = main(["c", "crc16-modbus"])
        out, _err = capsys.readouterr()
        assert rc == 0
        # Both header bits (extern "C") and source bits (#include) present.
        assert "extern \"C\"" in out
        assert "#include \"crc16_modbus.h\"" in out

    def test_algorithm_name_is_case_insensitive(self, capsys):
        """The codegen path lowercases the algorithm name before lookup."""
        rc = main(["python", "CRC32"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "def crc32(" in out


# ─────────────────────────────────────────────────────────────────────
# Codegen -- file= output path.
# ─────────────────────────────────────────────────────────────────────


class TestCodegenFile:
    """With file=STEM, generators write files (relative to cwd) and
    print 'Wrote <path>' for each.  C writes .h + .c; others one file."""

    def test_python_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        rc = main(["python", "crc16-modbus", "file=mycrc"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert (tmp_path / "mycrc.py").exists()
        # file=mycrc derives symbol from stem -> def mycrc(...).
        assert "def mycrc(" in (tmp_path / "mycrc.py").read_text()
        assert "Wrote" in out and "mycrc.py" in out

    def test_rust_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        rc = main(["rust", "crc16-modbus", "file=mycrc"])
        assert rc == 0
        assert (tmp_path / "mycrc.rs").exists()
        out, _err = capsys.readouterr()
        assert "mycrc.rs" in out

    def test_vhdl_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        rc = main(["vhdl", "crc16-modbus", "file=mycrc"])
        assert rc == 0
        assert (tmp_path / "mycrc.vhd").exists()

    def test_c_file_writes_both_h_and_c(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        rc = main(["c", "crc16-modbus", "file=mycrc"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert (tmp_path / "mycrc.h").exists()
        assert (tmp_path / "mycrc.c").exists()
        # Wrote line appears for each file.
        assert out.count("Wrote") == 2

    def test_file_with_dash_in_stem(self, tmp_path, monkeypatch):
        """file= is sanitized to a valid identifier (- and . become _) for
        BOTH the filename and the in-code symbol, so they match (a dashed
        Python file name wouldn't be importable as a module anyway)."""
        monkeypatch.chdir(tmp_path)
        rc = main(["python", "crc16-modbus", "file=my-crc"])
        assert rc == 0
        body = (tmp_path / "my_crc.py").read_text()
        assert "def my_crc(" in body

    def test_empty_file_value_returns_2(self, capsys):
        rc = main(["python", "crc16-modbus", "file="])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "file= requires a value" in err

    def test_empty_symbol_value_returns_2(self, capsys):
        rc = main(["python", "crc16-modbus", "symbol="])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "symbol= requires a value" in err


# ─────────────────────────────────────────────────────────────────────
# Codegen -- multiple algorithms bundled into one output file.
# ─────────────────────────────────────────────────────────────────────


class TestCodegenMultiAlgorithm:
    """Passing >1 catalogue name emits every algorithm into one file (or
    one .h/.c pair for C).  Per-symbol table names make the bundle
    collision-free; single-algorithm output is unchanged."""

    def test_python_bundle_one_file_all_symbols(self, tmp_path, monkeypatch, capsys):
        # Arrange / Act
        monkeypatch.chdir(tmp_path)
        rc = main(["python", "crc32", "crc16-modbus", "crc8", "file=multi"])
        out, _err = capsys.readouterr()

        # Assert -- one file with all three functions.
        body = (tmp_path / "multi.py").read_text()
        assert rc == 0, "bundle exits 0"
        for fn in ("def crc32(", "def crc16_modbus(", "def crc8("):
            assert fn in body, f"{fn} present in bundle"
        actual_files = sorted(p.name for p in tmp_path.iterdir())
        expected_files = ["multi.py"]
        assert actual_files == expected_files, "exactly one output file"

    def test_c_bundle_combined_header_and_source(self, tmp_path, monkeypatch, capsys):
        # Act
        monkeypatch.chdir(tmp_path)
        rc = main(["c", "crc32", "crc16-modbus", "crc8", "file=multi"])
        _out, _err = capsys.readouterr()

        # Assert -- one .h + one .c; combined .c includes ONLY the merged
        # header (each source's own per-symbol include was rewritten away).
        assert rc == 0, "bundle exits 0"
        source = (tmp_path / "multi.c").read_text()
        header = (tmp_path / "multi.h").read_text()
        actual_includes = source.count('#include "')
        assert actual_includes == 1, "exactly one quoted include in combined .c"
        assert '#include "multi.h"' in source, "combined .c includes the merged header"
        for guard in ("CRC32_H", "CRC16_MODBUS_H", "CRC8_H"):
            assert f"#ifndef {guard}" in header, f"{guard} guard present"

    def test_go_bundle_one_package_clause(self, tmp_path, monkeypatch, capsys):
        # Act
        monkeypatch.chdir(tmp_path)
        rc = main(["go", "crc32", "crc16-modbus", "file=multi"])
        _out, _err = capsys.readouterr()

        # Assert -- Go allows exactly one package clause per file.
        body = (tmp_path / "multi.go").read_text()
        actual_pkg = body.count("package crc")
        assert rc == 0, "bundle exits 0"
        assert actual_pkg == 1, "exactly one package clause"

    def test_csharp_bundle_one_using_directive(self, tmp_path, monkeypatch, capsys):
        # Act
        monkeypatch.chdir(tmp_path)
        rc = main(["csharp", "crc32", "crc16-modbus", "crc8", "file=multi"])
        _out, _err = capsys.readouterr()

        # Assert -- one hoisted `using System;` (a second, following a type,
        # would not compile) and a distinct class per algorithm.
        body = (tmp_path / "multi.cs").read_text()
        actual_using = body.count("using System;")
        actual_classes = body.count("public static class")
        assert rc == 0, "bundle exits 0"
        assert actual_using == 1, "exactly one using directive"
        assert actual_classes == 3, "one class per algorithm"

    def test_duplicate_names_deduped(self, capsys):
        # Act -- the same algorithm twice collapses to one copy.  (No file=,
        # so the symbol stays the algorithm name rather than a stem rename.)
        rc = main(["python", "crc32", "crc32"])
        out, _err = capsys.readouterr()

        # Assert
        actual_defs = out.count("def crc32(")
        assert rc == 0, "dedup exits 0"
        assert actual_defs == 1, "duplicate name emitted once"

    def test_bundle_to_stdout(self, capsys):
        # Act -- no file= bundles to stdout.
        rc = main(["rust", "crc32", "crc16-modbus"])
        out, _err = capsys.readouterr()

        # Assert
        assert rc == 0, "stdout bundle exits 0"
        assert "fn crc32(" in out and "fn crc16_modbus(" in out, "both functions"

    def test_symbol_with_multiple_is_error(self, capsys):
        # Act
        rc = main(["c", "crc32", "crc16-modbus", "symbol=foo"])
        _out, err = capsys.readouterr()

        # Assert
        assert rc == 2, "symbol= with >1 algorithm is rejected"
        assert "symbol=" in err and "single function" in err, "explains why"

    def test_unknown_name_in_bundle_fails_fast(self, tmp_path, monkeypatch, capsys):
        # Act -- one bad name aborts the whole bundle, nothing written.
        monkeypatch.chdir(tmp_path)
        rc = main(["c", "crc32", "bogus", "crc8", "file=multi"])
        _out, err = capsys.readouterr()

        # Assert
        assert rc == 2, "unknown name in list exits 2"
        assert "bogus" in err, "names the offending algorithm"
        actual_files = list(tmp_path.iterdir())
        assert actual_files == [], "nothing written on error"


# ─────────────────────────────────────────────────────────────────────
# Codegen -- options (--table, --slice8, mutual exclusion, fallbacks).
# ─────────────────────────────────────────────────────────────────────


class TestCodegenOptions:
    def test_table_python(self, capsys):
        rc = main(["python", "crc16-modbus", "--table"])
        out, _err = capsys.readouterr()
        assert rc == 0
        # Table-driven Python embeds a per-symbol module-level table.
        assert "_crcglot_table_crc16_modbus = (" in out

    def test_table_c(self, capsys):
        rc = main(["c", "crc16-modbus", "--table"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "crcglot_table_crc16_modbus[256]" in out

    def test_slice8_c_crc32(self, capsys):
        rc = main(["c", "crc32", "--slice8"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "crcglot_slice_crc32[8][256]" in out

    def test_slice8_rust_crc32(self, capsys):
        rc = main(["rust", "crc32", "--slice8"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "CRCGLOT_SLICE_CRC32: [[u32; 256]; 8]" in out

    def test_slice8_and_table_mutually_exclusive(self, capsys):
        rc = main(["c", "crc32", "--slice8", "--table"])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "mutually exclusive" in err

    def test_slice8_python_falls_back_to_table(self, capsys):
        """Python's slice8 is slower in CPython, so the CLI silently
        downgrades it to --table and warns on stderr."""
        rc = main(["python", "crc32", "--slice8"])
        out, err = capsys.readouterr()
        assert rc == 0
        assert "slower than --table" in err
        # Table-driven output emitted (per-symbol module-level table).
        assert "_crcglot_table_crc32 = (" in out

    def test_slice8_narrow_width_returns_2(self, capsys):
        """generate_c('crc8', variant="slice8") raises ValueError; CLI
        catches and converts to exit code 2."""
        rc = main(["c", "crc8", "--slice8"])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "variant='slice8' requires width" in err


class TestGenerationAdvisories:
    """The CLI surfaces faster-path advisories on stderr -- stdout stays a
    clean source file (pipe-safe)."""

    def test_crc32_hints_the_stdlib_on_stderr(self, capsys):
        rc = main(["rust", "crc32"])
        _out, err = capsys.readouterr()
        assert rc == 0
        assert "crc32fast" in err, "rust crc32 should hint the crc32fast crate"

    def test_non_crc32_is_silent(self, capsys):
        rc = main(["c", "crc16-modbus"])
        _out, err = capsys.readouterr()
        assert rc == 0
        assert err == "", "no advisory for a non-crc32 algorithm"

    def test_python_target_warns_to_use_the_package(self, capsys):
        rc = main(["python", "crc8"])
        _out, err = capsys.readouterr()
        assert rc == 0
        assert "package itself" in err, "python target -> 'use the crcglot package'"

    def test_advisory_never_pollutes_stdout(self, capsys):
        main(["rust", "crc32"])
        out, _err = capsys.readouterr()
        assert "Faster CRC-32" not in out, "advisory must stay off stdout"

    def test_custom_crc32_equivalent_is_advised(self, capsys):
        # The CLI has the full params, so a custom CRC that IS crc32 still warns.
        rc = main([
            "c", "--custom", "width=32", "poly=0x04C11DB7", "init=0xFFFFFFFF",
            "refin=true", "refout=true", "xorout=0xFFFFFFFF",
        ])
        _out, err = capsys.readouterr()
        assert rc == 0
        assert "Faster CRC-32" in err, "custom crc32-equivalent should still advise"


class TestCodegenIntentFlags:
    """``--small`` / ``--fast`` are the intent front door: the user says
    what they want and crcglot picks the implementation for the
    (language, width).  ``--table`` / ``--slice8`` remain expert overrides."""

    def test_fast_c_crc32_picks_slice8(self, capsys):
        # width 32 + a slice8-capable language -> slice-by-8.
        rc = main(["c", "crc32", "--fast"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "crcglot_slice_crc32[8][256]" in out, "fast crc32 should be slice-by-8"

    def test_fast_rust_crc64_picks_slice8(self, capsys):
        # width 64 also gets slice-by-8.
        rc = main(["rust", "crc64-xz", "--fast"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "CRCGLOT_SLICE_CRC64_XZ" in out, "fast crc64 should be slice-by-8"

    def test_fast_narrow_width_picks_table(self, capsys):
        # width 16 can't use slice-by-8, so --fast falls to table-driven --
        # and must NOT error (unlike an explicit --slice8 on width 16).
        rc = main(["c", "crc16-modbus", "--fast"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "crcglot_table_crc16_modbus[256]" in out, "fast crc16 should be table-driven"
        assert "crcglot_slice" not in out, "width 16 has no slice-by-8"

    def test_fast_python_picks_table(self, capsys):
        # Python lists no slice8 variant, so --fast is table-driven without a
        # fallback note (--fast never asks for an unsupported variant).  stderr
        # still carries the always-on Python-runtime advisory, which is separate.
        rc = main(["python", "crc32", "--fast"])
        out, err = capsys.readouterr()
        assert rc == 0
        assert "_crcglot_table_crc32 = (" in out, "fast python should be table-driven"
        assert "slower than --table" not in err, "no --slice8 fallback note for --fast"

    def test_small_is_bit_by_bit(self, capsys):
        rc = main(["c", "crc32", "--small"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "crcglot_table" not in out and "crcglot_slice" not in out, (
            "--small should be bit-by-bit, no tables"
        )

    def test_fast_matches_default(self, capsys):
        # The default is now the fastest variant: no flag must be byte-identical
        # to --fast (it flipped from --small in the fast-by-default change).
        rc1 = main(["c", "crc32", "--fast"])
        fast_out, _ = capsys.readouterr()
        rc2 = main(["c", "crc32"])
        default_out, _ = capsys.readouterr()
        assert rc1 == 0 and rc2 == 0
        assert fast_out == default_out, "no flag must equal --fast (fastest is the default)"

    def test_default_is_no_longer_bit_by_bit(self, capsys):
        # Document the flip: a width-32 CRC defaults to a table/slice impl now,
        # not bit-by-bit. --small remains the explicit opt-in to smallest.
        rc = main(["c", "crc32"])
        default_out, _ = capsys.readouterr()
        assert rc == 0
        actual_has_table = "crcglot_table" in default_out or "crcglot_slice" in default_out
        assert actual_has_table, "default should now be fast (table/slice), not bit-by-bit"

    @pytest.mark.parametrize(
        "flags",
        [
            ["--small", "--fast"],
            ["--small", "--table"],
            ["--fast", "--slice8"],
            ["--fast", "--table"],
            ["--small", "--fast", "--table", "--slice8"],
        ],
    )
    def test_selectors_mutually_exclusive(self, flags, capsys):
        rc = main(["c", "crc32", *flags])
        _out, err = capsys.readouterr()
        assert rc == 2, f"{flags} should be rejected"
        assert "mutually exclusive" in err

    def test_fast_custom_width32_picks_slice8(self, capsys):
        # --fast resolves off the custom width= token too.
        rc = main([
            "c", "--custom", "--fast",
            "width=32", "poly=0x04C11DB7", "init=0xFFFFFFFF",
            "refin=true", "refout=true", "xorout=0xFFFFFFFF",
        ])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "crcglot_slice_crc_custom[8][256]" in out


class TestCodegenSymbolOverride:
    def test_symbol_overrides_function_name(self, capsys):
        rc = main(["python", "crc16-modbus", "symbol=my_check"])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "def my_check(" in out
        assert "def crc16_modbus(" not in out

    def test_symbol_default_from_file_stem(self, tmp_path, monkeypatch):
        """When file= is given and symbol= is not, symbol is derived
        from the file stem."""
        monkeypatch.chdir(tmp_path)
        rc = main(["python", "crc16-modbus", "file=renamed"])
        assert rc == 0
        body = (tmp_path / "renamed.py").read_text()
        assert "def renamed(" in body


class TestCodegenNaming:
    def test_go_defaults_to_pascal(self, capsys):
        """Go emits PascalCase methods out of the box (no --naming)."""
        # Act
        rc = main(["go", "crc16-modbus"])
        out, _err = capsys.readouterr()

        # Assert
        assert rc == 0, "go generation succeeds"
        assert "func Crc16ModbusUpdate(" in out, "default Go naming is PascalCase"
        assert "func crc16_modbus_update(" not in out, "no snake methods"

    def test_naming_flag_overrides_default(self, capsys):
        """``--naming camel`` re-cases the public functions."""
        # Act -- C defaults to snake but offers camel.
        rc = main(["c", "crc16-modbus", "--naming", "camel"])
        out, _err = capsys.readouterr()

        # Assert
        assert rc == 0, "c generation with --naming succeeds"
        assert "crc16ModbusUpdate(" in out, "--naming camel cases the functions"

    def test_unsupported_naming_rejected_by_argparse(self, capsys):
        """A convention a language doesn't offer is rejected (exit 2)."""
        # Act / Assert -- Rust is snake-only; argparse rejects the choice.
        with pytest.raises(SystemExit) as exc:
            main(["rust", "crc32", "--naming", "pascal"])
        assert exc.value.code == 2, "argparse rejects an invalid choice with code 2"
        _out, err = capsys.readouterr()
        assert "--naming" in err, "the error names the offending argument"


# ─────────────────────────────────────────────────────────────────────
# Codegen -- catalogue lookup errors.
# ─────────────────────────────────────────────────────────────────────


class TestCodegenCatalogueErrors:
    def test_missing_algorithm_name_returns_2(self, capsys):
        rc = main(["python"])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "usage:" in err

    def test_unknown_algorithm_returns_2(self, capsys):
        rc = main(["python", "totally-fake-crc"])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "unknown algorithm" in err
        assert "crcglot list" in err


# ─────────────────────────────────────────────────────────────────────
# Codegen -- --custom Rocksoft/Williams parameters.
# ─────────────────────────────────────────────────────────────────────


class TestCodegenCustom:
    """The --custom path takes raw Rocksoft/Williams params instead of
    looking up a name in the catalogue.  Required: width=, poly=.
    Optional: init=, refin=, refout=, xorout=, name=, desc=."""

    def test_custom_python_minimal(self, capsys):
        # Minimal valid invocation -- defaults init=0, refin/refout=false,
        # xorout=0, name=crc_custom.
        rc = main([
            "python", "--custom",
            "width=16", "poly=0x8005",
        ])
        out, _err = capsys.readouterr()
        assert rc == 0
        # Default name when no name= and no file=.
        assert "def crc_custom(" in out

    def test_custom_python_with_all_params(self, tmp_path, monkeypatch, capsys):
        """Mirror crc16-modbus via --custom -- the generated code should
        compute the canonical 0x4B37 check on b'123456789'."""
        monkeypatch.chdir(tmp_path)
        rc = main([
            "python", "--custom",
            "width=16", "poly=0x8005", "init=0xFFFF",
            "refin=true", "refout=true", "xorout=0x0000",
            "name=mymodbus", "desc=mirror of crc16-modbus",
            "file=mymodbus",
        ])
        assert rc == 0
        body = (tmp_path / "mymodbus.py").read_text()
        ns: dict = {}
        exec(body, ns)
        # mymodbus stem -> mymodbus symbol (no - or . to sanitize).
        assert ns["mymodbus"](b"123456789") == 0x4B37

    def test_custom_c_writes_header_and_source(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        rc = main([
            "c", "--custom",
            "width=32", "poly=0x04C11DB7", "init=0xFFFFFFFF",
            "refin=true", "refout=true", "xorout=0xFFFFFFFF",
            "file=mycrc",
        ])
        assert rc == 0
        assert (tmp_path / "mycrc.h").exists()
        assert (tmp_path / "mycrc.c").exists()

    def test_custom_missing_width_returns_2(self, capsys):
        rc = main(["c", "--custom", "poly=0x8005"])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "width=N and poly=X" in err

    def test_custom_with_algorithm_name_returns_2(self, tmp_path, monkeypatch, capsys):
        # Act -- --custom builds one CRC from params; a stray catalogue name
        # is rejected (not silently dropped) since custom makes one function.
        monkeypatch.chdir(tmp_path)
        rc = main(["c", "--custom", "width=16", "poly=0x8005", "crc32", "file=x"])
        _out, err = capsys.readouterr()

        # Assert
        assert rc == 2, "--custom with an algorithm name exits 2"
        assert "crc32" in err, "names the offending token"
        actual_files = list(tmp_path.iterdir())
        assert actual_files == [], "nothing written on error"

    def test_custom_missing_poly_returns_2(self, capsys):
        rc = main(["c", "--custom", "width=16"])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "width=N and poly=X" in err

    def test_custom_bad_int_returns_2(self, capsys):
        rc = main(["c", "--custom", "width=notanumber", "poly=0x8005"])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "custom CRC param" in err

    def test_custom_bad_bool_returns_2(self, capsys):
        rc = main([
            "c", "--custom",
            "width=16", "poly=0x8005", "refin=maybe",
        ])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "custom CRC param" in err

    def test_custom_unsupported_width_returns_2(self, capsys):
        rc = main([
            "c", "--custom",
            "width=12", "poly=0x8005",
        ])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "width must be 8, 16, 32, or 64" in err

    def test_custom_slice8_narrow_width_returns_2(self, capsys):
        """Slice-by-8 on a width-16 custom CRC should bubble up the
        generator's ValueError as exit code 2."""
        rc = main([
            "c", "--custom", "--slice8",
            "width=16", "poly=0x8005",
        ])
        _out, err = capsys.readouterr()
        assert rc == 2
        assert "variant='slice8' requires width" in err

    def test_custom_symbol_overrides_file_derived_name(self, tmp_path, monkeypatch):
        """symbol= wins for the function while file= sets the filename -- the
        file/identifier divergence escape hatch (name= + symbol=)."""
        monkeypatch.chdir(tmp_path)
        rc = main([
            "python", "--custom",
            "width=16", "poly=0x8005",
            "file=outname", "symbol=explicit_sym",
        ])
        assert rc == 0
        body = (tmp_path / "outname.py").read_text()
        assert "def explicit_sym(" in body, "function follows symbol="

    def test_name_and_file_conflict_rejected(self, tmp_path, monkeypatch):
        """name= and file= both name the output (the one knob), so giving both
        with different values is rejected -- use file= + symbol= to diverge."""
        monkeypatch.chdir(tmp_path)
        rc = main([
            "python", "--custom",
            "width=16", "poly=0x8005",
            "name=mycrc", "file=from_file",
        ])
        assert rc == 2, "conflicting name= and file= should be rejected"

    def test_custom_symbol_from_name_when_no_file(self, capsys):
        """No file= and no symbol=: symbol falls back to the custom name=."""
        rc = main([
            "python", "--custom",
            "width=16", "poly=0x8005",
            "name=from_name",
        ])
        out, _err = capsys.readouterr()
        assert rc == 0
        assert "def from_name(" in out


# ─────────────────────────────────────────────────────────────────────
# Top-level main() -- argparse-driven error paths.
# ─────────────────────────────────────────────────────────────────────


class TestMain:
    def test_no_args_exits_via_argparse(self, capsys):
        """argparse with required=True on subparsers SystemExits 2 on
        missing command -- main() never returns in that case."""
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 2

    def test_unknown_subcommand_exits_via_argparse(self):
        """Unknown subcommand -> argparse SystemExit 2."""
        with pytest.raises(SystemExit) as exc:
            main(["bogus-command"])
        assert exc.value.code == 2

    def test_build_parser_returns_parser(self):
        """``build_parser`` is the public seam for embedding the CLI;
        verify it returns something argparse-shaped."""
        parser = build_parser()
        assert hasattr(parser, "parse_args")
        assert parser.prog == "crcglot"


class TestChecksumCommand:
    """``crcglot checksum`` identifies a non-CRC checksum; ``detect`` prints it
    as a hint when no CRC matches."""

    def _lrc_frame_hex(self) -> str:
        from crcglot._trailers import _lrc8

        d = b"123456789"
        return (d + bytes([_lrc8(d)])).hex()

    def test_checksum_command_identifies_lrc(self, capsys):
        # Act
        rc = main(["identify", "--hex", self._lrc_frame_hex()])
        out, _err = capsys.readouterr()
        # Assert
        assert rc == 0, "checksum command should match an LRC frame"
        assert "lrc8" in out, f"expected lrc8 in stdout, got {out!r}"

    def test_detect_prints_trailer_hint_on_no_match(self, capsys):
        # Act
        rc = main(["detect", "--hex", self._lrc_frame_hex()])
        _out, err = capsys.readouterr()
        # Assert
        assert rc == 1, "no catalogue CRC matches this LRC frame"
        assert "lrc8" in err, f"detect should print the checksum hint, got {err!r}"
