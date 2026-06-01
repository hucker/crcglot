"""Cross-cutting tests that span multiple target languages.

Tests in this file are organized by API surface rather than by target
language:

* ``TestLanguageMetadata`` -- the ``LANGUAGES`` registry (every target
  present, extensions correct, variants subset valid, generator
  callables wired).

* ``TestAlgorithmMetadata`` -- the ``ALGORITHMS`` typed view of the
  reveng catalogue (every entry has a well-formed AlgorithmInfo).

* ``TestGenerators`` -- every generator accepts both reflected
  (refin=True) and normal (refin=False) algorithms.

* ``TestCustomCrcChainAgainstRevengTruth`` -- the custom-params path
  (``generate_<lang>_from_entry(name, algo, ...)``) verified against
  HARDCODED reveng check values rather than engine-derived ones, so a
  regression in either the engine OR the generators surfaces.

* ``TestSymbolOverride`` -- the ``symbol=`` keyword renames the emitted
  function across header / declarations / definitions.

* ``TestGenerateFromEntryAcceptsSyntheticEntry`` -- the
  custom-params path accepts entries for algorithms not in any
  catalogue.

* ``TestSliceBy8GeneratorAPI`` -- structural surface of the slice8
  parameter (emits 8 tables, rejects narrow widths, not exposed in
  Python / VHDL).  Execution-correctness lives in test_c_gen.py and
  test_rust_gen.py.

Zero toolchain calls -- all assertions run in-process.
"""

from __future__ import annotations

import pytest

from crcglot import (
    ALGORITHMS,
    LANGUAGES,
    AlgorithmInfo,
    LanguageInfo,
    generate_c,
    generate_c_from_entry,
    generate_python,
    generate_python_from_entry,
    generate_rust,
    generate_vhdl,
)
from crcglot.catalogue import _generic_crc_python, generic_crc


# Reveng-derived canonical check values for the algorithms used in
# the round-trip tests below.  These are HARDCODED on purpose: they
# come from the reveng CRC catalogue
# (https://reveng.sourceforge.io/crc-catalogue/all.htm) and serve as
# external ground truth for the entire chain.  Deriving them from
# ``CRC_CATALOGUE`` or ``generic_crc`` instead would make the tests
# circular -- the catalogue's check field IS populated by the same
# engine, so the tests would assert ``engine(x) == engine(x)`` and
# pass even if the engine were silently wrong.  By hardcoding from
# the external source, a regression in either the engine OR the
# generators surfaces as a real failure.
_REVENG_CHECK_VALUES = {
    "crc16-modbus":  (16, 0x8005,     0xFFFF,     True,  True,  0x0000,     0x4B37),
    "crc16-xmodem":  (16, 0x1021,     0x0000,     False, False, 0x0000,     0x31C3),
    "crc16-ibm-3740": (16, 0x1021,    0xFFFF,     False, False, 0x0000,     0x29B1),
    "crc32":         (32, 0x04C11DB7, 0xFFFFFFFF, True,  True,  0xFFFFFFFF, 0xCBF43926),
    "crc32-bzip2":   (32, 0x04C11DB7, 0xFFFFFFFF, False, False, 0xFFFFFFFF, 0xFC891918),
    "crc8":          (8,  0x07,       0x00,       False, False, 0x00,       0xF4),
    "crc8-maxim":    (8,  0x31,       0x00,       True,  True,  0x00,       0xA1),
    "crc64-xz":      (64, 0x42F0E1EBA9EA3693, 0xFFFFFFFFFFFFFFFF,
                      True, True, 0xFFFFFFFFFFFFFFFF, 0x995DC9BBDF1939FA),
}


class TestLanguageMetadata:
    """The ``LANGUAGES`` registry exposes one ``LanguageInfo`` per
    target with file extensions, variant support, and generator
    callables wired correctly.
    """

    def test_all_languages_present(self):
        # Assert
        assert set(LANGUAGES.keys()) == {
            "c", "csharp", "go", "python", "rust",
            "typescript", "verilog", "vhdl",
        }, (
            "expected c / csharp / go / python / rust / typescript / "
            "verilog / vhdl in LANGUAGES"
        )

    @pytest.mark.parametrize(
        "code",
        [
            "c", "csharp", "go", "python", "rust",
            "typescript", "verilog", "vhdl",
        ],
    )
    def test_entry_is_languageinfo_with_callables(self, code):
        # Act
        info = LANGUAGES[code]

        # Assert
        assert isinstance(info, LanguageInfo), f"{code}: expected LanguageInfo"
        assert info.code == code, f"{code}: info.code mismatch ({info.code!r})"
        assert callable(info.generator), f"{code}: generator not callable"
        assert callable(info.generator_from_entry), (
            f"{code}: generator_from_entry not callable"
        )

    def test_c_has_two_extensions_others_one(self):
        # Assert -- C is the only target that emits two files (.h + .c).
        assert LANGUAGES["c"].extensions == (".h", ".c"), (
            "C extension tuple is (.h, .c)"
        )
        for code, info in LANGUAGES.items():
            if code == "c":
                continue
            assert len(info.extensions) == 1, (
                f"{code}: expected single-element extension tuple, "
                f"got {info.extensions!r}"
            )

    @pytest.mark.parametrize(
        "code,expected_ext",
        [
            ("csharp", ".cs"),
            ("go", ".go"),
            ("python", ".py"),
            ("rust", ".rs"),
            ("typescript", ".ts"),
            ("verilog", ".sv"),
            ("vhdl", ".vhd"),
        ],
    )
    def test_extension_per_language(self, code, expected_ext):
        # Assert
        assert LANGUAGES[code].extensions == (expected_ext,), (
            f"{code}: extension mismatch"
        )

    def test_variants_are_valid_subset(self):
        valid = {"bitwise", "table", "slice8"}
        for code, info in LANGUAGES.items():
            assert info.variants <= valid, (
                f"{code}: variants {info.variants} not in {valid}"
            )
            assert "bitwise" in info.variants, (
                f"{code}: every language must support bitwise"
            )

    def test_slice8_supported_on_compiled_languages(self):
        # Assert -- every compiled software target supports slice8.
        # Python is the holdout (CPython per-int overhead measurably
        # negates the speedup); VHDL and Verilog are simulator
        # references (bitwise only).
        slice8_langs = {
            code for code, info in LANGUAGES.items() if "slice8" in info.variants
        }
        assert slice8_langs == {
            "c", "csharp", "go", "rust", "typescript",
        }, (
            "slice8 is supported on c / csharp / go / rust / typescript; "
            f"got {sorted(slice8_langs)}"
        )

    def test_hardware_targets_are_bitwise_only(self):
        # Assert -- both HDL generators emit bit-by-bit only (no table
        # / slice8); these are simulator-reference, not synthesizable
        # high-throughput RTL.
        for hdl in ("vhdl", "verilog"):
            assert LANGUAGES[hdl].variants == frozenset({"bitwise"}), (
                f"{hdl} generator must be bit-by-bit only"
            )

    def test_python_excludes_slice8(self):
        # Assert -- Python's per-int overhead eats the slice8 speedup
        # (measured ~0.79x); the variant is intentionally absent.
        assert "slice8" not in LANGUAGES["python"].variants

    def test_every_entry_has_emoji_and_display_name(self):
        # Assert -- the v0.7.0 display-metadata fields are populated
        # for every target.  Emoji is at least one codepoint; display
        # name is a non-empty human-readable string.
        for code, info in LANGUAGES.items():
            assert isinstance(info.emoji, str) and info.emoji, (
                f"{code}: emoji must be a non-empty string"
            )
            assert isinstance(info.display_name, str) and info.display_name, (
                f"{code}: display_name must be a non-empty string"
            )

    @pytest.mark.parametrize(
        "code,expected_display",
        [
            ("c", "C / C++"),
            ("csharp", "C#"),
            ("go", "Go"),
            ("python", "Python"),
            ("rust", "Rust"),
            ("typescript", "TypeScript"),
            ("verilog", "Verilog"),
            ("vhdl", "VHDL"),
        ],
    )
    def test_display_name_per_language(self, code, expected_display):
        # Assert
        actual = LANGUAGES[code].display_name
        expected = expected_display
        assert actual == expected, (
            f"{code}: display_name mismatch (got {actual!r}, "
            f"expected {expected!r})"
        )

    def test_emojis_are_distinct(self):
        # Assert -- no two targets share an emoji (catches copy-paste
        # bugs when adding a new language).
        emojis = [info.emoji for info in LANGUAGES.values()]
        assert len(set(emojis)) == len(emojis), (
            f"duplicate emoji in LANGUAGES: {emojis}"
        )


class TestVariantsForWidth:
    """``LanguageInfo.variants_for_width(width)`` filters the variant
    set by the algorithm width.  The only width-dependent rule today
    is "slice8 only at width 32 or 64"; surfacing it on the registry
    keeps the magic numbers out of downstream consumer code.
    """

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("c", ("bitwise", "table", "slice8")),
            ("csharp", ("bitwise", "table", "slice8")),
            ("go", ("bitwise", "table", "slice8")),
            ("rust", ("bitwise", "table", "slice8")),
            ("typescript", ("bitwise", "table", "slice8")),
            ("python", ("bitwise", "table")),
            ("verilog", ("bitwise",)),
            ("vhdl", ("bitwise",)),
        ],
    )
    def test_full_set_at_width_32(self, code, expected):
        # Act
        actual = LANGUAGES[code].variants_for_width(32)
        # Assert
        assert actual == expected, (
            f"{code}@32: variants_for_width mismatch (got {actual}, "
            f"expected {expected})"
        )

    @pytest.mark.parametrize(
        "code,expected",
        [
            ("c", ("bitwise", "table", "slice8")),
            ("csharp", ("bitwise", "table", "slice8")),
            ("go", ("bitwise", "table", "slice8")),
            ("rust", ("bitwise", "table", "slice8")),
            ("typescript", ("bitwise", "table", "slice8")),
            ("python", ("bitwise", "table")),
        ],
    )
    def test_slice8_present_at_width_64(self, code, expected):
        # Assert -- width 64 still admits slice8 on languages that ship it.
        actual = LANGUAGES[code].variants_for_width(64)
        assert actual == expected, (
            f"{code}@64: variants_for_width mismatch (got {actual}, "
            f"expected {expected})"
        )

    @pytest.mark.parametrize("width", [8, 16, 12, 24])
    def test_slice8_filtered_at_narrow_widths(self, width):
        # Assert -- slice-by-8 is meaningless below width 32; the
        # generator would raise.  variants_for_width drops it so
        # callers don't even offer the option.
        for code in ("c", "csharp", "go", "rust", "typescript"):
            actual = LANGUAGES[code].variants_for_width(width)
            assert "slice8" not in actual, (
                f"{code}@{width}: slice8 should be filtered out, got {actual}"
            )

    def test_canonical_ordering(self):
        # Assert -- the returned tuple follows VARIANT_ORDER so UIs see
        # bitwise first, then table, then slice8.
        from crcglot import VARIANT_ORDER

        c32 = LANGUAGES["c"].variants_for_width(32)
        assert c32 == VARIANT_ORDER, (
            f"c@32: expected canonical order {VARIANT_ORDER}, got {c32}"
        )

    def test_bitwise_only_languages_ignore_width(self):
        # Assert -- VHDL / Verilog return ("bitwise",) at any width.
        for width in (8, 16, 32, 64):
            for code in ("vhdl", "verilog"):
                actual = LANGUAGES[code].variants_for_width(width)
                assert actual == ("bitwise",), (
                    f"{code}@{width}: expected ('bitwise',), got {actual}"
                )

    def test_consumer_integration_shape(self):
        # Arrange -- the consumer's wrapper code becomes a one-liner:
        # ``list(LANGUAGES[code].variants_for_width(width))``.
        # Exercise that end-to-end.
        for code in LANGUAGES:
            for width in (8, 16, 32, 64):
                variants_list = list(LANGUAGES[code].variants_for_width(width))
                # Assert each is a valid variant name
                for v in variants_list:
                    assert v in {"bitwise", "table", "slice8"}, (
                        f"{code}@{width}: unexpected variant {v!r}"
                    )


class TestVariantKwargValidation:
    """The ``variant=`` kwarg replaced the old ``table=`` / ``slice8=``
    boolean pair across all generators.  Cross-cutting validation:
    every supported variant generates; every unsupported variant
    raises ValueError; and the consumer's one-liner pattern works.
    """

    @pytest.mark.parametrize(
        "code", ["c", "csharp", "go", "rust", "typescript"]
    )
    @pytest.mark.parametrize("variant", ["bitwise", "table", "slice8"])
    def test_all_supported_variants_generate_at_width_32(self, code, variant):
        # Act
        result = LANGUAGES[code].generator("crc32", variant=variant)
        # Assert
        assert result is not None, (
            f"{code} variant={variant!r}: generator returned None"
        )

    def test_python_table_variant_generates(self):
        # Arrange / Act
        result = LANGUAGES["python"].generator("crc32", variant="table")
        # Assert
        assert result is not None, (
            "python variant='table': generator returned None"
        )

    def test_unknown_variant_raises_valueerror(self):
        # Act / Assert -- typo / never-shipped variant name.  Routed
        # through the registry's ``Callable`` (no Literal narrowing) so
        # the runtime check fires.
        with pytest.raises(ValueError, match="must be 'bitwise', 'table', or 'slice8'"):
            LANGUAGES["c"].generator("crc32", variant="quadword")

    def test_consumer_integration_one_liner(self):
        # Arrange / Act -- exercise the exact pattern from the
        # consumer's wrapper:
        #   LANGUAGES[lang].generator(name, symbol=symbol, variant=variant)
        for code, info in LANGUAGES.items():
            for variant in info.variants_for_width(32):
                result = info.generator("crc32", symbol=None, variant=variant)
                # Assert
                assert result is not None, (
                    f"{code}/{variant}: consumer-pattern call returned None"
                )


class TestAlgorithmMetadata:
    """The ``ALGORITHMS`` typed view of the catalogue: every entry is
    a well-formed AlgorithmInfo with the canonical reveng check value.
    """

    def test_size_matches_catalogue(self):
        # Assert -- 70 algorithms in the catalogue.
        assert len(ALGORITHMS) == 70, f"expected 70 entries, got {len(ALGORITHMS)}"

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_entry_is_algorithminfo(self, name):
        # Assert
        algo = ALGORITHMS[name]
        assert isinstance(algo, AlgorithmInfo), (
            f"{name}: expected AlgorithmInfo"
        )
        assert algo.name == name, (
            f"{name}: algo.name should match key, got {algo.name!r}"
        )
        assert algo.width in (8, 16, 32, 64), (
            f"{name}: width {algo.width} not in {{8, 16, 32, 64}}"
        )
        assert isinstance(algo.desc, str), (
            f"{name}: desc must be str (possibly empty), got {type(algo.desc)}"
        )

    def test_crc32_matches_reveng(self):
        # Assert
        algo = ALGORITHMS["crc32"]
        assert algo.width == 32, "crc32 width"
        assert algo.check == 0xCBF43926, "crc32 reveng check value"
        assert algo.poly == 0x04C11DB7, "crc32 polynomial"

    def test_crc16_modbus_matches_reveng(self):
        # Assert
        algo = ALGORITHMS["crc16-modbus"]
        assert algo.width == 16, "crc16-modbus width"
        assert algo.check == 0x4B37, "crc16-modbus reveng check value"
        assert algo.refin is True, "crc16-modbus refin"
        assert algo.refout is True, "crc16-modbus refout"

    def test_field_values_fit_width(self):
        # Assert -- every numeric field fits the declared width.
        for name, algo in ALGORITHMS.items():
            mask = (1 << algo.width) - 1
            assert 0 <= algo.poly <= mask, f"{name}: poly overflows width"
            assert 0 <= algo.init <= mask, f"{name}: init overflows width"
            assert 0 <= algo.xorout <= mask, f"{name}: xorout overflows width"
            assert 0 <= algo.check <= mask, f"{name}: check overflows width"


class TestZlibFastPaths:
    """``generic_crc`` delegates the zlib-computable algorithms (IEEE
    crc32 and crc32-jamcrc) to the hardware-accelerated stdlib
    ``zlib.crc32`` -- they must still return the correct value and
    agree with the reference engine, across input shapes."""

    @pytest.mark.parametrize("name", ["crc32", "crc32-jamcrc"])
    def test_fast_path_matches_reveng_check(self, name):
        # Arrange -- the catalogue params for a zlib-delegated algorithm.
        algo = ALGORITHMS[name]
        args = (
            algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        # Act
        actual = generic_crc(b"123456789", *args)
        # Assert
        assert actual == algo.check, (
            f"{name} fast path gave {actual:#x}, expected {algo.check:#x}"
        )

    @pytest.mark.parametrize("name", ["crc32", "crc32-jamcrc"])
    def test_fast_path_agrees_with_reference_engine(self, name):
        # The zlib-delegated result must equal the pure-Python engine
        # bit-for-bit across input shapes -- proves the delegation
        # (and jamcrc's xorout fix-up) is correct.
        algo = ALGORITHMS[name]
        args = (
            algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        for data in [b"", b"\x00", b"123456789", b"The quick brown fox", bytes(range(256))]:
            assert generic_crc(data, *args) == _generic_crc_python(data, *args), (
                f"{name}: zlib fast path disagrees with reference on "
                f"{len(data)}-byte input"
            )

    def test_jamcrc_is_ieee_with_xorout_flipped(self):
        # JAMCRC == IEEE-32 with xorout=0; since zlib bakes in
        # xorout=0xFFFFFFFF, jamcrc = zlib.crc32(data) ^ 0xFFFFFFFF.
        import zlib
        data = b"123456789"
        algo = ALGORITHMS["crc32-jamcrc"]
        args = (
            algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        assert generic_crc(data, *args) == (zlib.crc32(data) ^ 0xFFFFFFFF)

    def test_matches_stdlib_zlib(self):
        # The delegated path must equal calling zlib directly.
        import zlib
        algo = ALGORITHMS["crc32"]
        args = (
            algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        for data in [b"", b"a", b"123456789", bytes(range(256)) * 10]:
            actual = generic_crc(data, *args)
            assert actual == zlib.crc32(data), (
                f"delegated != zlib.crc32 for {len(data)}-byte input"
            )

    def test_accepts_bytes_like(self):
        # zlib.crc32 takes any buffer; the fast path must too.
        algo = ALGORITHMS["crc32"]
        args = (
            algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        data = b"123456789"
        assert generic_crc(bytearray(data), *args) == 0xCBF43926
        assert generic_crc(memoryview(data), *args) == 0xCBF43926


class TestGenerators:
    """Every generator accepts both reflected (refin=True) and normal
    (refin=False) algorithms via the typed dispatch in LANGUAGES."""

    @pytest.mark.parametrize(
        "lang",
        [
            "c", "csharp", "go", "python", "rust",
            "typescript", "verilog", "vhdl",
        ],
    )
    def test_reflected_algorithm(self, lang):
        """Verify reflected algorithms (refin=True) generate code."""
        # Act - crc16-modbus is reflected
        result = LANGUAGES[lang].generator("crc16-modbus")

        # Assert -- C returns a (header, source) pair; others return a string.
        assert result is not None, f"{lang} generator returned None for reflected algorithm"
        body = "".join(result) if isinstance(result, tuple) else result
        assert len(body) > 100, "non-trivial output"

    @pytest.mark.parametrize(
        "lang",
        [
            "c", "csharp", "go", "python", "rust",
            "typescript", "verilog", "vhdl",
        ],
    )
    def test_normal_algorithm(self, lang):
        """Verify normal algorithms (refin=False) generate code."""
        # Act - crc16-xmodem is normal
        result = LANGUAGES[lang].generator("crc16-xmodem")

        # Assert -- C returns a (header, source) pair; others return a string.
        assert result is not None, f"{lang} generator returned None for normal algorithm"
        body = "".join(result) if isinstance(result, tuple) else result
        assert len(body) > 100, "non-trivial output"


class TestCustomCrcChainAgainstRevengTruth:
    """End-to-end verification of the custom-params path against
    HARDCODED reveng check values (not engine-derived) so a bug in
    either the engine OR the generators is caught for real."""

    @pytest.mark.parametrize("algo_name", sorted(_REVENG_CHECK_VALUES.keys()))
    def test_engine_matches_reveng(self, algo_name):
        """``generic_crc`` with hardcoded params produces the
        reveng-published check value -- proves the engine itself is
        correct independent of catalogue / generator paths."""
        # Arrange
        w, poly, init, refin, refout, xorout, expected = (
            _REVENG_CHECK_VALUES[algo_name]
        )

        # Act
        actual = generic_crc(
            b"123456789", w, poly, init, refin, refout, xorout
        )

        # Assert
        assert actual == expected, (
            f"{algo_name}: generic_crc gave {actual:#x}, "
            f"reveng-canonical is {expected:#x}"
        )

    @pytest.mark.parametrize("algo_name", sorted(_REVENG_CHECK_VALUES.keys()))
    def test_generated_python_matches_reveng_via_custom_params(self, algo_name):
        """The Python generator, fed a synthetic entry built from
        HARDCODED params, produces code whose function returns the
        HARDCODED reveng check.  This is the real test of the
        custom-params path -- if either the entry-dict generator or
        the engine that computed ``check`` is wrong, the test fails."""
        # Arrange -- hardcoded params + hardcoded expected check.
        w, poly, init, refin, refout, xorout, expected = (
            _REVENG_CHECK_VALUES[algo_name]
        )
        algo = AlgorithmInfo(
            name=algo_name,
            width=w, poly=poly, init=init,
            refin=refin, refout=refout, xorout=xorout,
            check=expected, desc=f"hardcoded-canonical for {algo_name}",
        )
        symbol = algo_name.replace("-", "_")

        # Act -- generate code and execute it.
        code = generate_python_from_entry(algo_name, algo, symbol=symbol)
        ns: dict = {}
        exec(code, ns)
        actual = ns[symbol](b"123456789")

        # Assert -- generated function matches the EXTERNAL reveng
        # truth, not the engine's own computation.
        assert actual == expected, (
            f"{algo_name}: generated Python (via from_entry, "
            f"hardcoded reveng params) returned {actual:#x}, "
            f"reveng-canonical is {expected:#x}"
        )

    def test_generate_c_from_entry_header_uses_symbol(self):
        """Structural -- ``symbol=`` renames everything consistently
        across the .h header (declarations, include guard) and the
        .c source.  Value correctness is covered by the parameterized
        round-trip tests above."""
        # Arrange -- crc16-modbus params (any valid CRC works for
        # this structural test).
        w, poly, init, refin, refout, xorout, check = (
            _REVENG_CHECK_VALUES["crc16-modbus"]
        )
        algo = AlgorithmInfo(
            name="my_modbus",
            width=w, poly=poly, init=init,
            refin=refin, refout=refout, xorout=xorout,
            check=check, desc="structural test",
        )

        # Act
        result = generate_c_from_entry(
            "my_modbus", algo, symbol="my_modbus",
        )

        # Assert
        assert result is not None, "generator returned a pair"
        header, source = result
        assert "#ifndef MY_MODBUS_H" in header, (
            "include guard derives from symbol"
        )
        assert "uint16_t my_modbus_init(void)" in header, (
            "header declaration uses symbol"
        )
        assert "uint16_t my_modbus_init(void)" in source, (
            "source definition uses symbol"
        )
        assert '#include "my_modbus.h"' in source, (
            "source #include matches symbol-named header"
        )


class TestSymbolOverride:
    """The symbol= keyword on the generator entry points renames the
    emitted function name across header, declarations, and definitions."""

    def test_explicit_symbol_overrides_algorithm_name(self):
        # Arrange / Act
        code = generate_python("crc16-modbus", symbol="renamed_func")

        # Assert
        assert code is not None
        assert "def renamed_func(" in code, "symbol override renames"
        assert "def crc16_modbus(" not in code, (
            "original algorithm-based name is replaced"
        )

    def test_no_symbol_uses_algorithm_name(self):
        # Arrange / Act -- no symbol override; default = _func_name(name)
        code = generate_python("crc16-modbus")

        # Assert
        assert code is not None
        assert "def crc16_modbus(" in code, (
            "default symbol comes from algorithm name"
        )


class TestGenerateFromEntryAcceptsSyntheticEntry:
    """The generators accept entry dicts for algorithms not in any
    catalogue -- the whole point of the custom-params path."""

    def test_generator_and_engine_agree_on_synthetic_crc(self):
        """For a made-up CRC (no external truth available), assert
        that the GENERATED code computes the SAME value as the
        ENGINE on the same input.  This is a self-consistency check
        between the two implementations of the algorithm, NOT a
        check against an external canonical value -- because for a
        made-up CRC there is no external canonical value to check
        against.

        The reveng-canonical tests above (TestCustomCrcChainAgainstRevengTruth)
        cover external-truth verification.  This test covers the
        complementary property: whatever the engine computes, the
        generated code computes the same thing.  Together they pin
        both halves of the custom-params path.
        """
        # Arrange -- a deliberately weird (but valid) CRC-16 spec
        # that's not in any catalogue.
        width, poly, init = 16, 0x1234, 0xABCD
        refin, refout, xorout = False, False, 0x5678
        engine_result = generic_crc(
            b"123456789", width, poly, init, refin, refout, xorout
        )
        algo = AlgorithmInfo(
            name="madeup",
            width=width, poly=poly, init=init,
            refin=refin, refout=refout, xorout=xorout,
            check=engine_result, desc="Made-up CRC, no reveng truth",
        )

        # Act -- generate Python, exec, run on the check input.
        code = generate_python_from_entry("madeup", algo, symbol="madeup")
        ns: dict = {}
        exec(code, ns)
        generated_result = ns["madeup"](b"123456789")

        # Assert -- generator output matches engine output for the
        # same params and input.  (Both could be wrong in the same
        # way for THIS made-up CRC -- the reveng-canonical tests
        # rule that out for known algorithms.)
        assert generated_result == engine_result, (
            f"generator and engine disagree on synthetic CRC: "
            f"generator={generated_result:#x}, engine={engine_result:#x}"
        )


class TestSliceBy8GeneratorAPI:
    """Structural tests for the slice8 generator parameter.

    Execution-correctness tests live in test_c_gen.py and
    test_rust_gen.py (TestGeneratedCSliceBy8Executes /
    TestGeneratedRustSliceBy8Executes).  These tests verify the API
    surface: returns aren't None, output contains the right markers,
    and out-of-range widths raise cleanly.
    """

    def test_c_slice8_emits_8_tables(self):
        # Arrange + Act
        result = generate_c("crc32", variant='slice8')

        # Assert -- generator returned a (header, source) pair and the
        # source contains the 2D slice-table declaration.
        assert result is not None, "generate_c crc32 variant='slice8' returned None"
        _header, source = result
        assert "crc_slice_tables[8][256]" in source, (
            "C source missing 2D slice-table declaration"
        )

    def test_rust_slice8_emits_8_tables(self):
        # Arrange + Act
        code = generate_rust("crc32", variant='slice8')

        # Assert
        assert code is not None, "generate_rust crc32 variant='slice8' returned None"
        assert "CRC_SLICE_TABLES: [[u32; 256]; 8]" in code, (
            "Rust source missing 2D slice-table declaration"
        )

    @pytest.mark.parametrize("algo", ["crc8", "crc16-modbus"])
    def test_c_slice8_rejects_narrow_widths(self, algo):
        # Act + Assert
        with pytest.raises(ValueError, match="variant=.slice8. requires width"):
            generate_c(algo, variant='slice8')

    @pytest.mark.parametrize("algo", ["crc8", "crc16-modbus"])
    def test_rust_slice8_rejects_narrow_widths(self, algo):
        # Act + Assert
        with pytest.raises(ValueError, match="variant=.slice8. requires width"):
            generate_rust(algo, variant='slice8')

    def test_python_rejects_slice8_variant(self):
        """generate_python intentionally has no slice-by-8 path: Python's
        per-int overhead eats the speedup, so emitting slice-by-8 in
        Python would add code without any throughput benefit.  Asking
        for variant='slice8' raises ValueError."""
        # Act + Assert
        with pytest.raises(ValueError, match="variant='slice8' is not supported"):
            generate_python("crc32", variant='slice8')  # type: ignore[call-arg]  # ty: ignore[invalid-argument-type]

    def test_vhdl_rejects_slice8_variant(self):
        """Same rationale for VHDL: the generator is simulator-focused
        (a reference implementation), not synthesizable hardware where
        throughput optimization would matter.  variant='slice8'
        raises ValueError."""
        # Act + Assert
        with pytest.raises(ValueError, match="variant='slice8' is not supported"):
            generate_vhdl("crc32", variant='slice8')  # type: ignore[call-arg]  # ty: ignore[invalid-argument-type]
