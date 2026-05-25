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

import pytest

from crcglot import CRC_CATALOGUE, generate_c


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
        n for n, e in CRC_CATALOGUE.items() if e["width"] in (32, 64)
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


# ─────────────────────────────────────────────────────────────────────
# Execution-verified tests -- compile and run via gcc.
# ─────────────────────────────────────────────────────────────────────


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

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
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

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_table_driven_self_test_returns_zero(self, name, tmp_path):
        """Same as above but with table=True (table-driven implementation)."""
        # Arrange
        result = generate_c(name, table=True)
        assert result is not None, f"generate_c({name!r}, table=True) returned None"
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


@pytest.mark.slow
@pytest.mark.skipif(not HAS_GCC, reason="gcc not in PATH")
class TestGeneratedCStreaming:
    """Verify the C streaming triple (init / update / finalize) satisfies
    the splittability invariant against the reveng check value."""

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_split_streaming_matches_check(self, name, table, tmp_path):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        result = generate_c(name, table=table)
        assert result is not None, f"generate_c({name!r}) returned None"
        header, source = result
        fname = _func_name(name)
        ctype = _c_state_type(entry["width"])

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
            f"{name} (table={table}): gcc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )

        run_result = subprocess.run([str(binary)], cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name} (table={table}): streaming returned "
            f"{run_result.returncode} (1=split, 2=empty-first, 3=empty-last)"
        )


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
    sense at those widths (validated by the slice8=True ValueError in
    the generators).
    """

    @pytest.mark.parametrize("name", _slice8_algos())
    def test_slice8_matches_bitbybit(self, name, tmp_path):
        # Arrange -- generate two C pairs with disjoint symbol names so
        # they can link in the same runner.
        bb_sym = f"{_func_name(name)}_bb"
        s8_sym = f"{_func_name(name)}_s8"
        bb_header, bb_source = generate_c(name, symbol=bb_sym)
        s8_header, s8_source = generate_c(name, slice8=True, symbol=s8_sym)
        ctype = _c_state_type(CRC_CATALOGUE[name]["width"])

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
