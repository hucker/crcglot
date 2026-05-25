"""Tests for the Rust CRC code generator.

Two layers:

* **Structural** (fast, always run) -- ``TestGenerateRust`` checks the
  shape of ``generate_rust(...)`` output: function signature, type
  width, ``#[cfg(test)] mod tests`` block, check value embedded.

* **Execution-verified** (marked ``slow``, skipped without rustc) --
  shells out to ``rustc`` to compile and run the generated code,
  asserting against the reveng canonical check value for every
  algorithm in the catalogue.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from crcglot import CRC_CATALOGUE, generate_rust


HAS_RUSTC = shutil.which("rustc") is not None


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


def _rust_state_type(width: int) -> str:
    """Pick the Rust state type to match what generate_rust uses internally."""
    if width <= 8:
        return "u8"
    if width <= 16:
        return "u16"
    if width <= 32:
        return "u32"
    return "u64"


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


class TestGenerateRust:
    """generate_rust returns a single .rs source string.

    Includes a ``#[cfg(test)] mod tests`` block at the bottom; idiomatic
    Rust testing -- ``cargo test`` discovers it, and crcglot's pytest
    runs it via ``rustc --test``.  See the execution-verified tests
    below for the parameterized compile + run verification.
    """

    def test_generates_code(self):
        # Act
        code = generate_rust("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "fn crc16_modbus" in code, "function name"
        assert "u16" in code, "correct type"
        assert "0x4B37" in code, "check value"
        assert "#[cfg(test)]" in code, "cfg(test) gated test module emitted"
        assert "#[test]" in code, "individual #[test] attribute present"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_rust("nonexistent") is None, "unknown algorithm should return None"

    def test_crc8_uses_u8(self):
        # Act
        code = generate_rust("crc8")

        # Assert
        assert code is not None, "generator returned code"
        assert "u8" in code, "CRC-8 should use u8"

    def test_crc32_uses_u32(self):
        # Act
        code = generate_rust("crc32")

        # Assert
        assert code is not None, "generator returned code"
        assert "u32" in code, "CRC-32 should use u32"


# ─────────────────────────────────────────────────────────────────────
# Execution-verified tests -- compile and run via rustc.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.skipif(not HAS_RUSTC, reason="rustc not in PATH")
class TestGeneratedRustExecutes:
    """Compile each generated .rs file as a test binary, then run.

    The generator embeds a ``#[cfg(test)] mod tests`` block with one
    ``#[test] fn check_value_matches_reveng()`` assertion per file,
    so ``rustc --test file.rs`` produces a binary that runs that
    test on execution.  Nonzero exit = test failed = generator
    produced wrong CRC for ``b"123456789"``.
    """

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_check_value_matches_reveng(self, name, tmp_path):
        # Arrange
        code = generate_rust(name)
        assert code is not None, f"generate_rust({name!r}) returned None"
        fname = _func_name(name)

        src = tmp_path / f"{fname}.rs"
        src.write_text(code)
        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile as a test harness
        compile_result = subprocess.run(
            [
                "rustc",
                "--test",
                "--edition=2021",
                "-o", str(binary),
                str(src),
            ],
            capture_output=True,
            cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name}: rustc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )

        # Act -- run (rustc --test binary returns 0 on all tests pass)
        run_result = subprocess.run([str(binary)], capture_output=True, cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name}: test binary returned {run_result.returncode}: "
            f"{run_result.stdout.decode(errors='replace')}"
        )

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_table_driven_check_value_matches_reveng(self, name, tmp_path):
        """Same as above but with table=True."""
        # Arrange
        code = generate_rust(name, table=True)
        assert code is not None, (
            f"generate_rust({name!r}, table=True) returned None"
        )
        fname = _func_name(name)

        src = tmp_path / f"{fname}.rs"
        src.write_text(code)
        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile + run
        compile_result = subprocess.run(
            [
                "rustc",
                "--test",
                "--edition=2021",
                "-o", str(binary),
                str(src),
            ],
            capture_output=True,
            cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name} (table): rustc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )
        run_result = subprocess.run([str(binary)], capture_output=True, cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name} (table): test binary returned {run_result.returncode}"
        )


@pytest.mark.slow
@pytest.mark.skipif(not HAS_RUSTC, reason="rustc not in PATH")
class TestGeneratedRustStreaming:
    """Verify the Rust streaming triple satisfies the splittability invariant."""

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_split_streaming_matches_check(self, name, table, tmp_path):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        code = generate_rust(name, table=table)
        assert code is not None, f"generate_rust({name!r}) returned None"
        fname = _func_name(name)
        rtype = _rust_state_type(entry["width"])

        # Append a main() that exercises the streaming patterns.  rustc
        # without --test happily compiles a .rs with both fn definitions
        # and main(); the existing #[cfg(test)] mod stays inert.
        main_src = (
            f"\nfn main() {{\n"
            f"    let s1 = {fname}_init();\n"
            f'    let s1 = {fname}_update(s1, b"1234");\n'
            f'    let s1 = {fname}_update(s1, b"56789");\n'
            f"    if {fname}_finalize(s1) != 0x{expected:X}_{rtype} "
            f"{{ std::process::exit(1); }}\n"
            f"    let s2 = {fname}_init();\n"
            f'    let s2 = {fname}_update(s2, b"");\n'
            f'    let s2 = {fname}_update(s2, b"123456789");\n'
            f"    if {fname}_finalize(s2) != 0x{expected:X}_{rtype} "
            f"{{ std::process::exit(2); }}\n"
            f"    let s3 = {fname}_init();\n"
            f'    let s3 = {fname}_update(s3, b"123456789");\n'
            f'    let s3 = {fname}_update(s3, b"");\n'
            f"    if {fname}_finalize(s3) != 0x{expected:X}_{rtype} "
            f"{{ std::process::exit(3); }}\n"
            f"}}\n"
        )

        src = tmp_path / f"{fname}.rs"
        src.write_text(code + main_src)
        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile (NOT --test; we have our own main() now)
        compile_result = subprocess.run(
            ["rustc", "--edition=2021", "-A", "warnings",
             "-o", str(binary), str(src)],
            capture_output=True, cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name} (table={table}): rustc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )

        run_result = subprocess.run([str(binary)], cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name} (table={table}): streaming returned "
            f"{run_result.returncode} (1=split, 2=empty-first, 3=empty-last)"
        )


@pytest.mark.slow
@pytest.mark.skipif(not HAS_RUSTC, reason="rustc not in PATH")
class TestGeneratedRustSliceBy8Executes:
    """Slice-by-8 equivalence with bit-by-bit in generated Rust.

    Limited to CRC-32 and CRC-64 algorithms; slice-by-8 only makes
    sense at those widths.  Strategy mirrors the C version: generate
    both forms under disjoint symbol names, compile into the same
    runner, assert byte-equal output across a range of input lengths.
    """

    @pytest.mark.parametrize("name", _slice8_algos())
    def test_slice8_matches_bitbybit(self, name, tmp_path):
        # Arrange -- generate two .rs files with disjoint symbol names.
        bb_sym = f"{_func_name(name)}_bb"
        s8_sym = f"{_func_name(name)}_s8"
        bb_code = generate_rust(name, symbol=bb_sym)
        s8_code = generate_rust(name, slice8=True, symbol=s8_sym)
        rtype = _rust_state_type(CRC_CATALOGUE[name]["width"])

        bb_path = tmp_path / f"{bb_sym}.rs"
        s8_path = tmp_path / f"{s8_sym}.rs"
        bb_path.write_text(bb_code)
        s8_path.write_text(s8_code)

        # The two files define identically-named CRC_TABLE /
        # CRC_SLICE_TABLES constants but in disjoint modules, so we
        # ``include!`` them into separate mods to avoid name collisions.
        lengths_csv = ", ".join(str(n) for n in _SLICE8_INPUT_LENGTHS)
        runner_src = (
            f'mod bb {{ include!("{bb_sym}.rs"); }}\n'
            f'mod s8 {{ include!("{s8_sym}.rs"); }}\n'
            f"fn main() {{\n"
            f"    let mut buf = [0u8; 256];\n"
            f"    for k in 0..256 {{ buf[k] = k as u8; }}\n"
            f"    let lengths: [usize; {len(_SLICE8_INPUT_LENGTHS)}] = "
            f"[{lengths_csv}];\n"
            f"    for (li, &n) in lengths.iter().enumerate() {{\n"
            f"        let bb: {rtype} = bb::{bb_sym}(&buf[..n]);\n"
            f"        let s8: {rtype} = s8::{s8_sym}(&buf[..n]);\n"
            f"        if bb != s8 {{\n"
            f'            eprintln!("len={{}} bb=0x{{:x}} s8=0x{{:x}}", '
            f"n, bb, s8);\n"
            f"            std::process::exit((li + 1) as i32);\n"
            f"        }}\n"
            f"    }}\n"
            f"}}\n"
        )
        runner_path = tmp_path / "runner.rs"
        runner_path.write_text(runner_src)

        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile + run.  -A warnings silences unused-function
        # / unused-const warnings from the included bit-by-bit + slice-by-8
        # files (each contains a _self_test mod we don't invoke from main).
        compile_result = subprocess.run(
            ["rustc", "--edition=2021", "-A", "warnings",
             "-o", str(binary), str(runner_path)],
            capture_output=True, cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name}: rustc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )
        run_result = subprocess.run(
            [str(binary)], capture_output=True, cwd=tmp_path,
        )

        # Assert -- exit 0 means slice-by-8 == bit-by-bit at every length.
        assert run_result.returncode == 0, (
            f"{name}: slice8 != bit-by-bit at length "
            f"{_SLICE8_INPUT_LENGTHS[run_result.returncode - 1]}: "
            f"{run_result.stderr.decode(errors='replace')}"
        )
