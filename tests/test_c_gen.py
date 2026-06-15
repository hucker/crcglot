"""Tests for the C CRC code generator.

Two layers:

* **Structural** (fast, always run) -- ``TestGenerateC`` checks the
  shape of ``generate_c(...)`` output: ``(header, source)`` tuple,
  ``extern "C"`` guard, correct integer types, declared functions.
  These catch "the generator emits the wrong shape" but not "the
  generated code computes the wrong CRC."

* **Execution-verified** (marked ``slow``, skipped without gcc) --
  ``TestGeneratedC*`` classes shell out to ``gcc`` to compile and run
  the generated code, asserting against the reveng canonical check
  value for every algorithm in the catalogue.

The ``slow`` marker keeps the fast iteration loop (``pytest -m 'not
slow'``) free of subprocess spawns.  The ``HAS_GCC`` skipif keeps the
suite green on machines without a C toolchain.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Literal

import pytest

from crcglot import (
    ALGORITHMS,
    LANGUAGES,
    AlgorithmInfo,
    generate_c,
    generate_c_from_entry,
)


HAS_GCC = shutil.which("gcc") is not None


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


def _c_state_type(width: int) -> str:
    """Pick the C state type to match what generate_c uses internally."""
    if width <= 8:
        return "uint8_t"
    if width <= 16:
        return "uint16_t"
    if width <= 32:
        return "uint32_t"
    return "uint64_t"


# Input lengths spanning degenerate, sub-chunk, exact-chunk, mixed.
_SLICE8_INPUT_LENGTHS = (0, 1, 7, 8, 9, 15, 16, 100)


def _slice8_algos() -> list[str]:
    """Catalogue algorithms eligible for slice-by-8 (width 32 or 64)."""
    return sorted(
        n for n, a in ALGORITHMS.items() if a.width in (32, 64)
    )


# ─────────────────────────────────────────────────────────────────────
# Structural tests -- fast, no toolchain needed.
# ─────────────────────────────────────────────────────────────────────


class TestGenerateC:
    """generate_c returns a (header, source) pair of complete files.

    The header has the standard ``extern "C"`` guard for C++ interop;
    the source ``#include``s the header and emits a ``_self_test()``
    function callers can invoke for runtime verification.  See the
    execution-verified tests below for the compile + run verification
    that pins correctness for every algorithm.
    """

    def test_generates_pair(self):
        # Act
        result = generate_c("crc16-modbus")

        # Assert -- tuple shape and basic content
        assert result is not None, "generator returned a pair"
        header, source = result
        assert "extern \"C\"" in header, "header has extern \"C\" guard for C++ interop"
        assert "uint16_t crc16_modbus(" in header, "header declares the function"
        assert "int crc16_modbus_self_test(" in header, "header declares self_test"
        assert "#include \"crc16_modbus.h\"" in source, "source includes its header"
        assert "crc16_modbus_self_test" in source, "source defines self_test"
        assert "0x4B37" in source, "self_test asserts the canonical check value"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_c("nonexistent") is None, "unknown algorithm should return None"

    def test_crc8_uses_uint8(self):
        # Act
        result = generate_c("crc8")

        # Assert
        assert result is not None, "generator returned a pair"
        _header, source = result
        assert "uint8_t" in source, "CRC-8 should use uint8_t"

    def test_crc32_uses_uint32(self):
        # Act
        result = generate_c("crc32")

        # Assert
        assert result is not None, "generator returned a pair"
        _header, source = result
        assert "uint32_t" in source, "CRC-32 should use uint32_t"

    def test_crc64_uses_uint64(self):
        # Act -- exercises the w > 32 type-selection branch.
        result = generate_c("crc64-xz")

        # Assert
        assert result is not None, "generator returned a pair"
        header, source = result
        assert "uint64_t" in source, "CRC-64 should use uint64_t"
        assert "uint64_t crc64_xz(" in header, "header declares u64-return func"


class TestCProvenanceRecord:
    """Every C pair carries a linkable ``const crcglot_provenance_t`` record.

    Declared (``extern``) in the header, defined in the source, behind a
    ``CRCGLOT_NO_PROVENANCE`` guard so a toolchain without section GC can
    compile it out.  It is a *public* symbol, so it never trips
    ``-Wunused-const-variable``; a linker with ``--gc-sections`` drops it when
    unused.  It carries the ``tool_version`` that the comment block omits.
    """

    def _pair(self, name: str, **kw) -> tuple[str, str]:
        """``generate_c`` pair with the ``None`` (unknown algorithm) narrowed."""
        out = generate_c(name, **kw)
        assert out is not None, f"generate_c({name!r}) returned None"
        return out

    def _prov_value(self, source: str, field: str) -> str:
        """The string assigned to ``.field`` in the const initializer."""
        line = next(ln for ln in source.splitlines() if f".{field}" in ln)
        return line.split('"')[1]

    def test_header_declares_extern_record(self):
        # Act
        header, _ = self._pair("crc16-xmodem")

        # Assert
        assert "} crcglot_provenance_t;" in header, "header defines the record type"
        actual = "extern const crcglot_provenance_t crc16_xmodem_provenance;"
        assert actual in header, "header declares the per-symbol extern record"

    def test_source_defines_const_record_with_params(self):
        # Act
        _, source = self._pair("crc16-xmodem", variant="table")

        # Assert -- the resolved generation parameters are in the const.
        assert "const crcglot_provenance_t crc16_xmodem_provenance = {" in source, (
            "source defines the const record"
        )
        actual = {
            "algorithm": self._prov_value(source, "algorithm"),
            "target": self._prov_value(source, "target"),
            "variant": self._prov_value(source, "variant"),
        }
        expected = {"algorithm": "crc16-xmodem", "target": "c", "variant": "table"}
        assert actual == expected, f"record params {actual} != {expected}"

    def test_record_carries_tool_version(self):
        """The volatile version lives here (not in the comment block)."""
        # Act
        _, source = self._pair("crc16-xmodem")

        # Assert -- a non-empty version string is assigned.
        actual = self._prov_value(source, "tool_version")
        assert actual, "const record must carry a non-empty tool_version"

    def test_record_is_macro_guarded_both_sides(self):
        # Act
        header, source = self._pair("crc16-xmodem")

        # Assert
        assert "#ifndef CRCGLOT_NO_PROVENANCE" in header, "header guards the decl"
        assert "#ifndef CRCGLOT_NO_PROVENANCE" in source, "source guards the def"

    def test_record_variant_is_canonical(self):
        """``auto`` on crc32 resolves to slice-by-8; the record shows it."""
        # Act
        _, source = self._pair("crc32", variant="auto")

        # Assert
        actual = self._prov_value(source, "variant")
        expected = "slice8"
        assert actual == expected, f"record variant {actual!r} != {expected!r}"

    def test_record_labels_custom_polynomial(self):
        # Arrange
        from crcglot.catalogue import custom_algorithm

        cust = custom_algorithm(width=16, poly=0x1021, desc="a custom crc")

        # Act
        from crcglot.lang.c import generate_c_from_entry

        _, source = generate_c_from_entry("mycrc", cust)

        # Assert
        actual = self._prov_value(source, "algorithm")
        expected = "custom"
        assert actual == expected, f"custom record algorithm {actual!r} != {expected!r}"


@pytest.mark.slow
@pytest.mark.skipif(not HAS_GCC, reason="gcc not in PATH")
def test_c_provenance_record_compiles_both_ways(tmp_path):
    """The const record compiles clean under ``-Werror`` (public symbol, so no
    unused-const warning), is readable at runtime, and is removed entirely by
    ``-DCRCGLOT_NO_PROVENANCE`` -- the escape hatch for a no-GC toolchain.
    """
    # Arrange
    pair = generate_c("crc16-xmodem", variant="table")
    assert pair is not None, "generate_c returned None"
    header, source = pair
    fname = "crc16_xmodem"
    (tmp_path / f"{fname}.h").write_text(header)
    (tmp_path / f"{fname}.c").write_text(source)
    (tmp_path / "ref.c").write_text(
        f'#include "{fname}.h"\n'
        "int main(void) {\n"
        f"    if ({fname}_self_test() != 0) return 1;\n"
        f"    return {fname}_provenance.tool_version[0] == 0;\n"
        "}\n"
    )
    (tmp_path / "noref.c").write_text(
        f'#include "{fname}.h"\n'
        f"int main(void) {{ return {fname}_self_test(); }}\n"
    )
    base = ["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror",
            "-Wunused-const-variable"]

    # Act / Assert -- default build references the record and runs.
    on_bin = tmp_path / "on"
    on = subprocess.run(
        [*base, "-o", str(on_bin), str(tmp_path / f"{fname}.c"),
         str(tmp_path / "ref.c")],
        capture_output=True, cwd=tmp_path,
    )
    assert on.returncode == 0, (
        f"default build failed: {on.stderr.decode(errors='replace')}"
    )
    assert subprocess.run([str(on_bin)], cwd=tmp_path).returncode == 0, (
        "runtime read of the provenance record failed"
    )

    # Act / Assert -- macro-off build compiles the record out and still runs.
    off_bin = tmp_path / "off"
    off = subprocess.run(
        [*base, "-DCRCGLOT_NO_PROVENANCE", "-o", str(off_bin),
         str(tmp_path / f"{fname}.c"), str(tmp_path / "noref.c")],
        capture_output=True, cwd=tmp_path,
    )
    assert off.returncode == 0, (
        f"-DCRCGLOT_NO_PROVENANCE build failed: "
        f"{off.stderr.decode(errors='replace')}"
    )
    assert subprocess.run([str(off_bin)], cwd=tmp_path).returncode == 0, (
        "macro-off build did not run"
    )


class TestGenerateCTableVariants:
    """Table-driven update-loop variants emit different inner loops:

    * refin=True: ``crcglot_table_<sym>[(crc ^ data[i]) & 0xFF] ^ (crc >> 8)``
    * refin=False: ``crcglot_table_<sym>[((crc >> {w-8}) ^ data[i]) & 0xFF] ^
      ((crc << 8) & {mask})`` -- fully parenthesized so gcc's
      ``-Wparentheses`` (in ``-Wall``) doesn't complain about
      ``a ^ b & c`` precedence ambiguity.

    Reflected (refin=True) is already exercised by the CLI test
    ``test_table_c`` (crc16-modbus); this class adds the non-reflected
    branch and pins both inner loops structurally.  Runtime correctness
    is the slow exec tests' job.

    Unlike Rust, C has no w=8 simplified branch -- C's implicit integer
    promotion to int silently handles ``uint8_t << 8`` so the generic
    shift form compiles fine for all widths.
    """

    def test_table_reflected_uses_right_shift(self):
        # Act -- crc16-modbus is refin=True.
        result = generate_c("crc16-modbus", variant='table')
        assert result is not None
        _header, source = result

        # Assert
        assert (
            "crc = crcglot_table_crc16_modbus[(crc ^ data[i]) & 0xFFU] ^ (crc >> 8);"
            in source
        ), "reflected table loop right-shifts and uses simple xor index"

    def test_table_normal_uses_left_shift(self):
        # Act -- crc16-xmodem is refin=False; covers c.py:244.
        result = generate_c("crc16-xmodem", variant='table')
        assert result is not None
        _header, source = result

        # Assert -- fully parenthesized for -Wall -Werror survival.
        assert (
            "crc = crcglot_table_crc16_xmodem[((crc >> 8) ^ data[i]) & 0xFFU]"
            " ^ ((crc << 8) & 0xFFFFU);" in source
        ), "non-reflected w=16 table loop is the fully-parenthesized form"


class TestGenerateCSliceBy8Variants:
    """Slice-by-8 emits four distinct update-loop variants depending on
    (width, refin).  Each loads chunks of 8 bytes in either little-endian
    (refin=True) or big-endian (refin=False) order before chaining
    through ``crcglot_slice_<sym>[7..0]``.

    The TestSliceBy8GeneratorAPI test in test_catalogue.py covers the
    crc32 (w=32, refin=True) variant; these add the other three to
    pin emitted structure without needing gcc.
    """

    def test_slice8_w32_normal(self):
        # Act -- crc32-bzip2 is w=32 refin=False.
        result = generate_c("crc32-bzip2", variant='slice8')
        assert result is not None
        _header, source = result

        # Assert -- big-endian byte loading; top-byte-first xor in tail.
        assert "(uint32_t)data[0] << 24" in source, (
            "non-reflected w=32 loads bytes big-endian"
        )
        assert "uint32_t top = crc >> 24;" in source, (
            "non-reflected tail extracts top byte before xor"
        )
        assert "^ (crc << 8);" in source, "non-reflected tail left-shifts"

    def test_slice8_w64_reflected(self):
        # Act -- crc64-xz is w=64 refin=True.
        result = generate_c("crc64-xz", variant='slice8')
        assert result is not None
        _header, source = result

        # Assert -- 64-bit little-endian load + LSB-first table indexing.
        assert "(uint64_t)data[7] << 56" in source, (
            "reflected w=64 loads all 8 bytes little-endian to u64"
        )
        # Tail right-shifts state by 8 each iteration.
        assert (
            "crc = crcglot_slice_crc64_xz[0][(crc ^ *data) & 0xFFU] ^ (crc >> 8);"
            in source
        ), "reflected w=64 tail right-shifts (counted loop, no in-expression ++)"

    def test_slice8_w64_normal(self):
        # Act -- crc64-ecma-182 is w=64 refin=False.
        result = generate_c("crc64-ecma-182", variant='slice8')
        assert result is not None
        _header, source = result

        # Assert -- 64-bit big-endian load + tail extracts top byte.
        assert "(uint64_t)data[0] << 56" in source, (
            "non-reflected w=64 loads top byte first"
        )
        assert "uint64_t top = crc >> 56;" in source, (
            "non-reflected w=64 tail extracts top byte"
        )
        assert "^ (crc << 8);" in source, "non-reflected w=64 tail left-shifts"


class TestGenerateCFromEntryReflectionPaths:
    """``generate_c_from_entry`` accepts entries where
    ``refin != refout`` -- a configuration absent from the reveng
    catalogue but valid Rocksoft/Williams.  This triggers the finalize
    function's reflection block (c.py:470-475), which loops over the
    state bits and rebuilds them in reversed order.

    Catalogue entries always have refin == refout, so this branch is
    unreachable via ``generate_c(name)`` alone -- only via a synthetic
    entry.  Runtime correctness of mixed reflection is covered by the
    from_entry exec tests in test_catalogue.py; this is the structural
    pin.
    """

    def test_refout_differs_from_refin_emits_reflection_block(self):
        # Arrange -- CRC-16 with refin=False, refout=True (synthetic).
        algo = AlgorithmInfo(
            width=16, poly=0x1021, init=0xFFFF,
            refin=False, refout=True, xorout=0x0000,
            check=0xDEAD, desc="synthetic mixed-reflection",
            source="custom",
        )

        # Act
        _header, source = generate_c_from_entry(
            "synth_mixed", algo, symbol="synth_mixed",
        )

        # Assert -- the reflection block appears in finalize.
        assert "/* reflect output (refout != refin) */" in source, (
            "comment marks the mixed-reflection branch"
        )
        assert "uint16_t reflected = 0U;" in source, (
            "reflection accumulator declared at uint16_t"
        )
        assert "for (int k = 0; k < 16; k++) {" in source, (
            "loop over all 16 state bits, braced"
        )
        assert "reflected |= ((state >> k) & 1U) << (15 - k);" in source, (
            "bit-reverse formula"
        )
        assert "state = reflected;" in source, (
            "rebinds state to the reversed value for subsequent xorout"
        )


# ─────────────────────────────────────────────────────────────────────
# Execution-verified tests -- compile and run via gcc.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_GCC, reason="gcc not in PATH")
class TestGeneratedCExecutes:
    """Compile each generated C pair + a synthesized runner, then run.

    Per-algorithm flow:

    1. ``generate_c(name)`` -> ``(header, source)`` (a ``.h`` + ``.c`` pair)
    2. Write both to a fresh tmp dir under their conventional names
       (``<fname>.h`` and ``<fname>.c``).
    3. Synthesize a tiny ``runner.c`` that ``#include``s the header
       and has a ``main()`` that returns the result of the generated
       ``<fname>_self_test()``.
    4. ``gcc <fname>.c runner.c -o run`` -- compile-error caught
       here is a generator bug (syntactically broken C).
    5. Execute the binary -- nonzero exit means the generated function
       produced the wrong CRC for ``"123456789"``.

    The whole loop runs in ~80 ms per algorithm (~5 s for all 64) on
    a modern laptop with gcc on PATH.
    """

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_self_test_returns_zero(self, name, tmp_path):
        # Arrange
        result = generate_c(name)
        assert result is not None, f"generate_c({name!r}) returned None"
        header, source = result
        fname = _func_name(name)

        (tmp_path / f"{fname}.h").write_text(header)
        (tmp_path / f"{fname}.c").write_text(source)
        (tmp_path / "runner.c").write_text(
            f'#include "{fname}.h"\n'
            f"int main(void) {{ return {fname}_self_test(); }}\n"
        )

        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile
        compile_result = subprocess.run(
            [
                "gcc",
                "-std=c99",
                "-Wall",
                "-Werror",
                "-o", str(binary),
                str(tmp_path / f"{fname}.c"),
                str(tmp_path / "runner.c"),
            ],
            capture_output=True,
            cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name}: gcc failed: {compile_result.stderr.decode(errors='replace')}"
        )

        # Act -- run
        run_result = subprocess.run([str(binary)], cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name}: self_test returned {run_result.returncode} "
            f"(expected 0 == check value matches reveng)"
        )

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_table_driven_self_test_returns_zero(self, name, tmp_path):
        """Same as above but with variant='table' (table-driven implementation)."""
        # Arrange
        result = generate_c(name, variant='table')
        assert result is not None, f"generate_c({name!r}, variant='table') returned None"
        header, source = result
        fname = _func_name(name)

        (tmp_path / f"{fname}.h").write_text(header)
        (tmp_path / f"{fname}.c").write_text(source)
        (tmp_path / "runner.c").write_text(
            f'#include "{fname}.h"\n'
            f"int main(void) {{ return {fname}_self_test(); }}\n"
        )

        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile + run
        compile_result = subprocess.run(
            [
                "gcc",
                "-std=c99",
                "-Wall",
                "-Werror",
                "-o", str(binary),
                str(tmp_path / f"{fname}.c"),
                str(tmp_path / "runner.c"),
            ],
            capture_output=True,
            cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name} (table): gcc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )
        run_result = subprocess.run([str(binary)], cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name} (table): self_test returned {run_result.returncode}"
        )


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_GCC, reason="gcc not in PATH")
class TestGeneratedCStreaming:
    """Verify the C streaming triple (init / update / finalize) satisfies
    the splittability invariant against the reveng check value."""

    @pytest.mark.parametrize("variant", ["bitwise", "table"])
    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_split_streaming_matches_check(self, name, variant, tmp_path):
        # Arrange
        algo = ALGORITHMS[name]
        expected = algo.check
        result = generate_c(name, variant=variant)
        assert result is not None, f"generate_c({name!r}) returned None"
        header, source = result
        fname = _func_name(name)
        ctype = _c_state_type(algo.width)

        (tmp_path / f"{fname}.h").write_text(header)
        (tmp_path / f"{fname}.c").write_text(source)
        (tmp_path / "runner.c").write_text(
            f'#include "{fname}.h"\n'
            f"int main(void) {{\n"
            f"    /* Pattern 1: split at byte 4 */\n"
            f"    {ctype} s1 = {fname}_init();\n"
            f'    s1 = {fname}_update(s1, (const uint8_t *)"1234", 4);\n'
            f'    s1 = {fname}_update(s1, (const uint8_t *)"56789", 5);\n'
            f"    if ({fname}_finalize(s1) != 0x{expected:X}) return 1;\n"
            f"    /* Pattern 2: empty chunk first */\n"
            f"    {ctype} s2 = {fname}_init();\n"
            f'    s2 = {fname}_update(s2, (const uint8_t *)"", 0);\n'
            f'    s2 = {fname}_update(s2, (const uint8_t *)"123456789", 9);\n'
            f"    if ({fname}_finalize(s2) != 0x{expected:X}) return 2;\n"
            f"    /* Pattern 3: empty chunk last */\n"
            f"    {ctype} s3 = {fname}_init();\n"
            f'    s3 = {fname}_update(s3, (const uint8_t *)"123456789", 9);\n'
            f'    s3 = {fname}_update(s3, (const uint8_t *)"", 0);\n'
            f"    if ({fname}_finalize(s3) != 0x{expected:X}) return 3;\n"
            f"    return 0;\n"
            f"}}\n"
        )

        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act
        compile_result = subprocess.run(
            [
                "gcc",
                "-std=c99", "-Wall", "-Werror",
                "-o", str(binary),
                str(tmp_path / f"{fname}.c"),
                str(tmp_path / "runner.c"),
            ],
            capture_output=True, cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name} (variant={variant}): gcc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )

        run_result = subprocess.run([str(binary)], cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name} (variant={variant}): streaming returned "
            f"{run_result.returncode} (1=split, 2=empty-first, 3=empty-last)"
        )


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_GCC, reason="gcc not in PATH")
class TestGeneratedCSliceBy8Executes:
    """Slice-by-8 equivalence with bit-by-bit in generated C.

    Slice-by-8 is a high-throughput CRC optimization (8 tables, 8 bytes
    per iteration -- 5-10x faster than plain table-driven on large
    buffers).  Verification strategy: generate BOTH bit-by-bit and
    slice-by-8 in C under different symbol names, compile both into the
    same runner, and assert they produce identical output across a
    range of input lengths.  Since the bit-by-bit form is already
    reveng-verified above, equivalence proves slice-by-8 is correct.

    Input lengths chosen to exercise the 8-byte main loop AND the 1-7
    byte tail loop: 0 (degenerate), 1 (pure tail), 7 (just under one
    chunk), 8 (exactly one chunk), 9 (one chunk + 1-byte tail), 15
    (just under two chunks), 16 (exactly two chunks), 100 (12 chunks +
    4-byte tail).  Input data is the cyclic byte sequence 0x00..0xFF
    to avoid all-zero / all-one patterns that might mask indexing bugs.

    Limited to CRC-32 and CRC-64 algorithms; slice-by-8 only makes
    sense at those widths (validated by the variant='slice8' ValueError in
    the generators).
    """

    @pytest.mark.parametrize("name", _slice8_algos())
    def test_slice8_matches_bitbybit(self, name, tmp_path):
        # Arrange -- generate two C pairs with disjoint symbol names so
        # they can link in the same runner.
        bb_sym = f"{_func_name(name)}_bb"
        s8_sym = f"{_func_name(name)}_s8"
        bb_result = generate_c(name, symbol=bb_sym)
        s8_result = generate_c(name, variant='slice8', symbol=s8_sym)
        assert bb_result is not None, f"generate_c({name!r}) returned None"
        assert s8_result is not None, (
            f"generate_c({name!r}, variant='slice8') returned None"
        )
        bb_header, bb_source = bb_result
        s8_header, s8_source = s8_result
        ctype = _c_state_type(ALGORITHMS[name].width)

        (tmp_path / f"{bb_sym}.h").write_text(bb_header)
        (tmp_path / f"{bb_sym}.c").write_text(bb_source)
        (tmp_path / f"{s8_sym}.h").write_text(s8_header)
        (tmp_path / f"{s8_sym}.c").write_text(s8_source)

        lengths_csv = ", ".join(str(n) for n in _SLICE8_INPUT_LENGTHS)
        runner_src = (
            f'#include "{bb_sym}.h"\n'
            f'#include "{s8_sym}.h"\n'
            f"#include <stdio.h>\n"
            f"int main(void) {{\n"
            f"    static uint8_t buf[256];\n"
            f"    for (int k = 0; k < 256; k++) buf[k] = (uint8_t)k;\n"
            f"    size_t lengths[] = {{ {lengths_csv} }};\n"
            f"    size_t nlen = sizeof(lengths) / sizeof(lengths[0]);\n"
            f"    for (size_t li = 0; li < nlen; li++) {{\n"
            f"        size_t n = lengths[li];\n"
            f"        {ctype} bb = {bb_sym}(buf, n);\n"
            f"        {ctype} s8 = {s8_sym}(buf, n);\n"
            f"        if (bb != s8) {{\n"
            f'            fprintf(stderr, "len=%zu bb=0x%llx s8=0x%llx\\n",\n'
            f"                    n, (unsigned long long)bb,\n"
            f"                    (unsigned long long)s8);\n"
            f"            return (int)(li + 1);\n"
            f"        }}\n"
            f"    }}\n"
            f"    return 0;\n"
            f"}}\n"
        )
        (tmp_path / "runner.c").write_text(runner_src)

        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile + run
        compile_result = subprocess.run(
            [
                "gcc",
                "-std=c99", "-Wall", "-Werror",
                "-o", str(binary),
                str(tmp_path / f"{bb_sym}.c"),
                str(tmp_path / f"{s8_sym}.c"),
                str(tmp_path / "runner.c"),
            ],
            capture_output=True, cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name}: gcc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )
        run_result = subprocess.run(
            [str(binary)], capture_output=True, cwd=tmp_path,
        )

        # Assert -- exit 0 means slice-by-8 == bit-by-bit at every length;
        # nonzero index identifies which length disagreed.
        assert run_result.returncode == 0, (
            f"{name}: slice8 != bit-by-bit at length "
            f"{_SLICE8_INPUT_LENGTHS[run_result.returncode - 1]}: "
            f"{run_result.stderr.decode(errors='replace')}"
        )


# ─────────────────────────────────────────────────────────────────────
# Batch execution -- whole catalogue x every variant in ONE gcc link +
# run, instead of one gcc per case.  DEFAULT path; the per-algorithm
# classes above are kept behind ``exhaustive`` for isolation.  The single
# multi-TU link is also the coexistence proof: it only links because the
# tables are per-symbol (``crcglot_table_<sym>``).  Full rationale (session
# fixture, parametrized lookup, the mandatory ``xdist_group`` pin) is in
# CLAUDE.md, "Execution tests: batch vs exhaustive".
# ─────────────────────────────────────────────────────────────────────

_CVariant = Literal["bitwise", "table", "slice8"]
_C_VARIANT_TAG: dict[_CVariant, str] = {"bitwise": "b", "table": "t", "slice8": "s8"}


def _c_batch_cases() -> list[tuple[str, _CVariant]]:
    """(name, variant) for every algorithm x supported C variant."""
    cases: list[tuple[str, _CVariant]] = []
    for name in sorted(ALGORITHMS.keys()):
        variants: list[_CVariant] = ["bitwise", "table"]
        if ALGORITHMS[name].width in (32, 64):
            variants.append("slice8")
        for v in variants:
            cases.append((name, v))
    return cases


def _c_batch_driver_case(name: str, variant: _CVariant) -> str:
    """One C block: <sym>_self_test() + split-streaming check, printing
    ``<name>/<variant> PASS|FAIL:<phase>``."""
    sym = f"{_func_name(name)}_{_C_VARIANT_TAG[variant]}"
    algo = ALGORITHMS[name]
    ctype = _c_state_type(algo.width)
    lit = f"({ctype})0x{algo.check:X}ULL"
    tag = f"{name}/{variant}"
    return (
        "    {\n"
        f'        if ({sym}_self_test() != 0) {{ printf("{tag} FAIL:oneshot\\n"); }}\n'
        "        else {\n"
        f"            {ctype} s = {sym}_init();\n"
        f"            s = {sym}_update(s, FULL, 4);\n"
        f"            s = {sym}_update(s, FULL + 4, 5);\n"
        f'            if ({sym}_finalize(s) != {lit}) printf("{tag} FAIL:streaming\\n");\n'
        f'            else printf("{tag} PASS\\n");\n'
        "        }\n"
        "    }"
    )


@pytest.fixture(scope="session")
def c_batch_results(tmp_path_factory) -> dict[str, str]:
    """Generate every (algorithm, variant) under a unique symbol, link them
    into one gcc binary, run once, return ``{"name/variant": result}``."""
    if not HAS_GCC:
        return {}
    cases = _c_batch_cases()
    d = tmp_path_factory.mktemp("c_batch")
    includes, csrcs, driver = [], [], []
    for name, variant in cases:
        sym = f"{_func_name(name)}_{_C_VARIANT_TAG[variant]}"
        result = generate_c(name, symbol=sym, variant=variant)
        assert result is not None, f"generate_c({name!r}) returned None"
        header, source = result
        (d / f"{sym}.h").write_text(header)
        (d / f"{sym}.c").write_text(source)
        includes.append(f'#include "{sym}.h"')
        csrcs.append(f"{sym}.c")
        driver.append(_c_batch_driver_case(name, variant))
    runner = (
        "#include <stdio.h>\n#include <stdint.h>\n#include <stddef.h>\n"
        + "\n".join(includes)
        + "\nstatic const uint8_t FULL[9] = {0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39};\n"
        + "int main(void) {\n"
        + "\n".join(driver)
        + "\n    return 0;\n}\n"
    )
    (d / "runner.c").write_text(runner)
    binary = d / "run.exe"
    comp = subprocess.run(
        ["gcc", "-std=c99", "-O1", "-w", "-o", str(binary),
         *[str(d / c) for c in csrcs], str(d / "runner.c")],
        capture_output=True, cwd=d,
    )
    if comp.returncode != 0:
        pytest.fail(
            "C batch failed to compile/link (a collision or codegen error):\n"
            + comp.stderr.decode(errors="replace")[:3000]
        )
    run = subprocess.run([str(binary)], capture_output=True, cwd=d)
    results: dict[str, str] = {}
    for line in run.stdout.decode(errors="replace").splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_GCC, reason="gcc not in PATH")
# One xdist worker so the session-scoped gcc link runs once, not per worker.
# See CLAUDE.md "Execution tests: batch vs exhaustive".
@pytest.mark.xdist_group("c_batch")
@pytest.mark.parametrize("name,variant", _c_batch_cases())
def test_c_batch_execution(name, variant, c_batch_results):
    # Assert -- the single-build driver reported PASS for this case.
    key = f"{name}/{variant}"
    actual = c_batch_results.get(key)
    assert actual == "PASS", (
        f"{key}: expected PASS, got {actual!r} "
        f"(missing => absent from the one-shot batch run's output)"
    )


_MULTI_ALGOS = ["crc32", "crc16-modbus", "crc8"]


@pytest.mark.slow
@pytest.mark.skipif(not HAS_GCC, reason="gcc not in PATH")
@pytest.mark.xdist_group("c_multi")
def test_c_combined_multi_algorithm_compiles_and_runs(tmp_path):
    """The CLI's multi-algorithm bundle (combine_c) must produce a .h/.c
    pair that compiles as one TU and whose self_tests all pass -- proving
    the header merge + self-include rewrite + per-symbol tables."""
    # Arrange -- combine several algorithms exactly as the CLI does.
    outputs = []
    for name in _MULTI_ALGOS:
        out = generate_c(name)
        assert out is not None, f"generate_c({name!r}) returned None"
        outputs.append(out)
    header, source = LANGUAGES["c"].combiner(outputs, "bundle")
    (tmp_path / "bundle.h").write_text(header)
    (tmp_path / "bundle.c").write_text(source)
    calls = "\n".join(
        f"    if ({_func_name(n)}_self_test() != 0) return {i + 1};"
        for i, n in enumerate(_MULTI_ALGOS)
    )
    (tmp_path / "runner.c").write_text(
        '#include "bundle.h"\nint main(void) {\n' + calls + "\n    return 0;\n}\n"
    )
    binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

    # Act
    compile_result = subprocess.run(
        ["gcc", "-std=c99", "-Wall", "-Werror", "-o", str(binary),
         str(tmp_path / "bundle.c"), str(tmp_path / "runner.c")],
        capture_output=True, cwd=tmp_path,
    )
    assert compile_result.returncode == 0, (
        f"combined .h/.c failed to compile: "
        f"{compile_result.stderr.decode(errors='replace')}"
    )
    run_result = subprocess.run([str(binary)], cwd=tmp_path)

    # Assert -- 0 means every bundled algorithm's self_test passed.
    assert run_result.returncode == 0, (
        f"bundled self_test #{run_result.returncode} "
        f"({_MULTI_ALGOS[run_result.returncode - 1]}) failed"
    )
