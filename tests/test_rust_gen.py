"""Tests for the Rust CRC code generator.

Two layers:

* **Structural** (fast, always run) -- ``TestGenerateRust`` and the
  ``TestGenerateRust*Variants`` classes check the shape of
  ``generate_rust(...)`` output: function signature, type width,
  ``<fname>_self_test()`` callable, check value embedded, the
  table-driven and slice-by-8 update-loop variants, and the
  ``refout != refin`` finalize reflection branch (reachable only via
  ``generate_rust_from_entry`` since no catalogue entry has them
  unequal).  These tests assert on emitted Rust syntax -- execution
  correctness is the slow tests' job.

* **Execution-verified** (marked ``slow``, skipped without rustc) --
  shells out to ``rustc`` to compile and run the generated code,
  asserting against the reveng canonical check value for every
  algorithm in the catalogue.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Literal

import pytest

from crcglot import (
    ALGORITHMS,
    AlgorithmInfo,
    generate_rust,
    generate_rust_from_entry,
)


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
        n for n, a in ALGORITHMS.items() if a.width in (32, 64)
    )


# ─────────────────────────────────────────────────────────────────────
# Structural tests -- fast, no toolchain needed.
# ─────────────────────────────────────────────────────────────────────


class TestGenerateRust:
    """generate_rust returns a single .rs source string.

    Includes a ``pub fn <fname>_self_test() -> bool`` at the bottom --
    a plain runtime-callable function so downstream consumers can wire
    it into a boot self-check or startup assertion, not just
    ``cargo test``.  See the execution-verified tests below for the
    parameterized compile + run verification.
    """

    def test_generates_code(self):
        # Act
        code = generate_rust("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "fn crc16_modbus" in code, "function name"
        assert "u16" in code, "correct type"
        assert "0x4B37" in code, "check value"
        assert "pub fn crc16_modbus_self_test() -> bool" in code, (
            "runtime-callable self_test emitted"
        )
        assert "#[cfg(test)]" not in code, (
            "no cfg(test) gate -- self_test must compile into release builds"
        )

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

    def test_crc64_uses_u64(self):
        # Act
        code = generate_rust("crc64-xz")

        # Assert -- exercises the w > 32 type-selection branch.
        assert code is not None, "generator returned code"
        assert "u64" in code, "CRC-64 should use u64"
        assert "fn crc64_xz" in code, "function name"


class TestGenerateRustTableVariants:
    """Table-driven update-loop variants emit different inner loops:

    * w == 8: simplified ``CRCGLOT_TABLE_<SYM>[(crc ^ byte) as usize]``
      (Rust rejects ``u8 << 8`` so the generic shift form would fail to
      compile).
    * w > 8, refin=True: right-shift form (``>> 8`` after table xor).
    * w > 8, refin=False: left-shift form (``(crc >> {w-8}) ^ byte``).

    All three are reveng-verified at runtime by the slow exec tests
    below; these structural tests catch shape regressions without
    needing rustc.
    """

    def test_table_emits_const_array(self):
        # Act
        code = generate_rust("crc16-modbus", variant='table')

        # Assert -- the table-format helper output (lines 41-51 in rust.py).
        assert code is not None
        assert "const CRCGLOT_TABLE_CRC16_MODBUS: [u16; 256] = [" in code, (
            "per-symbol table declaration with correct type and length"
        )

    def test_table_w8_uses_simplified_loop(self):
        """w=8 table loop has no shifts -- the table lookup IS the
        complete step."""
        # Act
        code = generate_rust("crc8", variant='table')

        # Assert
        assert code is not None
        assert "crc = CRCGLOT_TABLE_CRC8[(crc ^ byte) as usize];" in code, (
            "w=8 table loop is the simplified form"
        )
        # The generic refin=False shift form (`crc >> 0` is nonsense for
        # u8) must NOT appear -- if it did, rustc would reject u8 << 8.
        assert "(crc << 8)" not in code, (
            "w=8 must not emit the wider-CRC shift form"
        )

    def test_table_reflected_uses_right_shift(self):
        """w>8 reflected: ``CRCGLOT_TABLE_<SYM>[(crc ^ byte as u16) as usize
        & 0xFF] ^ (crc >> 8)``."""
        # Act -- crc16-modbus is refin=True.
        code = generate_rust("crc16-modbus", variant='table')

        # Assert
        assert code is not None
        assert "^ (crc >> 8)" in code, "reflected table loop right-shifts"

    def test_table_normal_uses_left_shift(self):
        """w>8 non-reflected: ``CRCGLOT_TABLE_<SYM>[((crc >> {w-8}) ^ byte
        as u16) as usize & 0xFF] ^ (crc << 8) & {mask}``."""
        # Act -- crc16-xmodem is refin=False.
        code = generate_rust("crc16-xmodem", variant='table')

        # Assert
        assert code is not None
        assert "(crc >> 8)" in code, (
            "non-reflected w=16 right-shifts by w-8=8 for table index"
        )
        assert "(crc << 8) & 0xFFFF" in code, (
            "non-reflected w=16 left-shifts result, masked to width"
        )


class TestGenerateRustSliceBy8Variants:
    """Slice-by-8 emits four distinct update-loop variants depending on
    (width, refin).  Each loads chunks of 8 bytes in either little-endian
    (refin=True) or big-endian (refin=False) order before chaining
    through ``CRCGLOT_SLICE_<SYM>[7..0]``.

    The TestSliceBy8GeneratorAPI tests in test_catalogue.py only cover
    the crc32 (w=32, refin=True) variant; these add the remaining three.
    """

    def test_slice8_w32_reflected(self):
        # Act -- crc32 is w=32 refin=True (the canonical reflected case).
        code = generate_rust("crc32", variant='slice8')

        # Assert -- little-endian byte loading; LSB-first table indexing.
        assert code is not None
        assert "let b03 = data[i] as u32 | (data[i+1] as u32) << 8" in code, (
            "reflected w=32 loads bytes little-endian"
        )
        # Tail loop also right-shifts.
        assert "^ (crc >> 8);" in code, "reflected tail right-shifts"

    def test_slice8_w32_normal(self):
        # Act -- crc32-bzip2 is w=32 refin=False (non-reflected).
        code = generate_rust("crc32-bzip2", variant='slice8')

        # Assert -- big-endian byte loading; top-byte-first xor.
        assert code is not None
        assert "let b03 = (data[i] as u32) << 24" in code, (
            "non-reflected w=32 loads bytes big-endian"
        )
        assert "let top = crc >> 24;" in code, (
            "non-reflected tail extracts top byte before xor"
        )
        assert "^ (crc << 8);" in code, "non-reflected tail left-shifts"

    def test_slice8_w64_reflected(self):
        # Act -- crc64-xz is w=64 refin=True.
        code = generate_rust("crc64-xz", variant='slice8')

        # Assert -- 64-bit little-endian load + 8-byte table chain.
        assert code is not None
        assert "(data[i+7] as u64) << 56" in code, (
            "reflected w=64 loads all 8 bytes little-endian to u64"
        )
        assert "^ (crc >> 8);" in code, "reflected w=64 tail right-shifts"

    def test_slice8_w64_normal(self):
        # Act -- crc64-ecma-182 is w=64 refin=False.
        code = generate_rust("crc64-ecma-182", variant='slice8')

        # Assert -- 64-bit big-endian load + tail extracts top byte.
        assert code is not None
        assert "(data[i] as u64) << 56" in code, (
            "non-reflected w=64 loads top byte first"
        )
        assert "let top = crc >> 56;" in code, (
            "non-reflected w=64 tail extracts top byte"
        )
        assert "^ (crc << 8);" in code, "non-reflected w=64 tail left-shifts"


class TestGenerateRustFromEntryReflectionPaths:
    """``generate_rust_from_entry`` accepts entries where
    ``refin != refout`` -- a configuration absent from the reveng
    catalogue but valid Rocksoft/Williams.  This triggers the finalize
    function's reflection block (rust.py:435-440), which loops over the
    state bits and rebuilds them in reversed order.

    Catalogue entries always have refin == refout, so this branch is
    unreachable via ``generate_rust(name)`` alone -- only via a
    synthetic entry.  The test here is structural; runtime correctness
    of mixed reflection is covered by the from_entry exec tests in
    test_catalogue.py.
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
        code = generate_rust_from_entry("synth_mixed", algo)

        # Assert -- the reflection block appears in finalize.
        assert "// reflect output (refout != refin)" in code, (
            "comment marks the mixed-reflection branch"
        )
        assert "let mut reflected: u16 = 0;" in code, (
            "reflection accumulator declared at u16"
        )
        assert "for k in 0..16 {" in code, "loop over all 16 state bits"
        assert "reflected |= ((state >> k) & 1) << (15 - k);" in code, (
            "bit-reverse formula"
        )
        assert "let state = reflected;" in code, (
            "rebinds state to the reversed value for subsequent xorout"
        )


# ─────────────────────────────────────────────────────────────────────
# Execution-verified tests -- compile and run via rustc.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_RUSTC, reason="rustc not in PATH")
class TestGeneratedRustExecutes:
    """Compile each generated .rs file with an injected main(), then run.

    The generator emits ``pub fn <fname>_self_test() -> bool``; we
    append a ``main()`` that calls it and exits 0 iff it returns true.
    This exercises exactly the path a downstream consumer would use
    (release-build runtime call, not ``cargo test``), so a regression
    that only manifests outside ``cfg(test)`` would surface here.
    """

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_check_value_matches_reveng(self, name, tmp_path):
        # Arrange
        code = generate_rust(name)
        assert code is not None, f"generate_rust({name!r}) returned None"
        fname = _func_name(name)

        main_src = (
            f"\nfn main() {{\n"
            f"    if !{fname}_self_test() {{ std::process::exit(1); }}\n"
            f"}}\n"
        )
        src = tmp_path / f"{fname}.rs"
        src.write_text(code + main_src)
        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile + run (no --test; we have our own main())
        compile_result = subprocess.run(
            ["rustc", "--edition=2021", "-A", "warnings",
             "-o", str(binary), str(src)],
            capture_output=True,
            cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name}: rustc failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )

        run_result = subprocess.run([str(binary)], capture_output=True, cwd=tmp_path)

        # Assert
        assert run_result.returncode == 0, (
            f"{name}: _self_test() returned false "
            f"(binary exit {run_result.returncode})"
        )

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_table_driven_check_value_matches_reveng(self, name, tmp_path):
        """Same as above but with variant='table'."""
        # Arrange
        code = generate_rust(name, variant='table')
        assert code is not None, (
            f"generate_rust({name!r}, variant='table') returned None"
        )
        fname = _func_name(name)

        main_src = (
            f"\nfn main() {{\n"
            f"    if !{fname}_self_test() {{ std::process::exit(1); }}\n"
            f"}}\n"
        )
        src = tmp_path / f"{fname}.rs"
        src.write_text(code + main_src)
        binary = tmp_path / ("run.exe" if shutil.which("cmd") else "run")

        # Act -- compile + run
        compile_result = subprocess.run(
            ["rustc", "--edition=2021", "-A", "warnings",
             "-o", str(binary), str(src)],
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
            f"{name} (table): _self_test() returned false "
            f"(binary exit {run_result.returncode})"
        )


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_RUSTC, reason="rustc not in PATH")
class TestGeneratedRustStreaming:
    """Verify the Rust streaming triple satisfies the splittability invariant."""

    @pytest.mark.parametrize("variant", ["bitwise", "table"])
    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_split_streaming_matches_check(self, name, variant, tmp_path):
        # Arrange
        algo = ALGORITHMS[name]
        expected = algo.check
        code = generate_rust(name, variant=variant)
        assert code is not None, f"generate_rust({name!r}) returned None"
        fname = _func_name(name)
        rtype = _rust_state_type(algo.width)

        # Append a main() that exercises the streaming patterns.  The
        # generated _self_test() is just a plain pub fn now, so rustc
        # without --test compiles the whole file fine alongside our
        # injected main().
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
            f"{name} (variant={variant}): rustc failed: "
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
        s8_code = generate_rust(name, variant='slice8', symbol=s8_sym)
        rtype = _rust_state_type(ALGORITHMS[name].width)

        bb_path = tmp_path / f"{bb_sym}.rs"
        s8_path = tmp_path / f"{s8_sym}.rs"
        bb_path.write_text(bb_code)
        s8_path.write_text(s8_code)

        # Each file's table is now per-symbol (CRCGLOT_TABLE_<SYM> /
        # CRCGLOT_SLICE_<SYM>), so they no longer collide -- but we keep the
        # separate-``mod`` framing to also exercise that the generated module
        # compiles cleanly when nested under an outer module.
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


# ─────────────────────────────────────────────────────────────────────
# Batch execution -- whole catalogue x every variant concatenated into one
# crate, compiled + run in ONE rustc invocation instead of one per case.
# DEFAULT path; the per-algorithm classes above are kept behind
# ``exhaustive`` for isolation.  The single crate is also the coexistence
# proof: every top-level item (fns and CRCGLOT_TABLE_<SYM> consts) is unique
# so it compiles.  Full rationale incl. the mandatory ``xdist_group`` pin is
# in CLAUDE.md, "Execution tests: batch vs exhaustive".
# ─────────────────────────────────────────────────────────────────────

_RsVariant = Literal["bitwise", "table", "slice8"]
_RS_VARIANT_TAG: dict[_RsVariant, str] = {"bitwise": "b", "table": "t", "slice8": "s8"}


def _rust_batch_cases() -> list[tuple[str, _RsVariant]]:
    """(name, variant) for every algorithm x supported Rust variant."""
    cases: list[tuple[str, _RsVariant]] = []
    for name in sorted(ALGORITHMS.keys()):
        variants: list[_RsVariant] = ["bitwise", "table"]
        if ALGORITHMS[name].width in (32, 64):
            variants.append("slice8")
        for v in variants:
            cases.append((name, v))
    return cases


def _rust_batch_driver_case(name: str, variant: _RsVariant) -> str:
    """One Rust block: <sym>_self_test() + split-streaming check, printing
    ``<name>/<variant> PASS|FAIL:<phase>``."""
    sym = f"{_func_name(name)}_{_RS_VARIANT_TAG[variant]}"
    algo = ALGORITHMS[name]
    rtype = _rust_state_type(algo.width)
    lit = f"0x{algo.check:X}{rtype}"
    tag = f"{name}/{variant}"
    return (
        "    {\n"
        f"        if !{sym}_self_test() {{ println!(\"{tag} FAIL:oneshot\"); }}\n"
        "        else {\n"
        "            let full: [u8; 9] = [0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39];\n"
        f"            let mut s = {sym}_init();\n"
        f"            s = {sym}_update(s, &full[0..4]);\n"
        f"            s = {sym}_update(s, &full[4..9]);\n"
        f"            if {sym}_finalize(s) != {lit} {{ println!(\"{tag} FAIL:streaming\"); }}\n"
        f"            else {{ println!(\"{tag} PASS\"); }}\n"
        "        }\n"
        "    }"
    )


@pytest.fixture(scope="session")
def rust_batch_results(tmp_path_factory) -> dict[str, str]:
    """Generate every (algorithm, variant) under a unique symbol into one
    crate, compile + run once, return ``{"name/variant": result}``."""
    if not HAS_RUSTC:
        return {}
    cases = _rust_batch_cases()
    bodies, driver = [], []
    for name, variant in cases:
        sym = f"{_func_name(name)}_{_RS_VARIANT_TAG[variant]}"
        bodies.append(generate_rust(name, symbol=sym, variant=variant))
        driver.append(_rust_batch_driver_case(name, variant))
    src = (
        "\n\n".join(bodies)
        + "\n\nfn main() {\n"
        + "\n".join(driver)
        + "\n}\n"
    )
    d = tmp_path_factory.mktemp("rust_batch")
    main_rs = d / "main.rs"
    main_rs.write_text(src)
    binary = d / "run.exe"
    # No -O: we verify correctness, not speed, and optimizing one giant
    # crate of the whole catalogue is dramatically slower (minutes vs
    # seconds) than an unoptimized build.  The per-algorithm exhaustive
    # tests likewise compile without -O.
    comp = subprocess.run(
        ["rustc", "--edition=2021", "-A", "warnings",
         "-o", str(binary), str(main_rs)],
        capture_output=True, cwd=d,
    )
    if comp.returncode != 0:
        pytest.fail(
            "Rust batch failed to compile (a collision or codegen error):\n"
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
@pytest.mark.skipif(not HAS_RUSTC, reason="rustc not in PATH")
# One xdist worker so the session-scoped rustc build runs once, not per
# worker.  See CLAUDE.md "Execution tests: batch vs exhaustive".
@pytest.mark.xdist_group("rust_batch")
@pytest.mark.parametrize("name,variant", _rust_batch_cases())
def test_rust_batch_execution(name, variant, rust_batch_results):
    # Assert -- the single-build driver reported PASS for this case.
    key = f"{name}/{variant}"
    actual = rust_batch_results.get(key)
    assert actual == "PASS", (
        f"{key}: expected PASS, got {actual!r} "
        f"(missing => absent from the one-shot batch run's output)"
    )
