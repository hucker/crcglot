"""Tests for the Zig CRC code generator.

Two layers:

* **Structural** (fast, always run) -- shape checks on the emitted
  source: camelCase default function names, Zig container types
  matching the algorithm width, per-symbol table consts, the
  ``SelfTest`` function plus the Zig-native ``test`` declaration,
  the ``@bitReverse`` finalize branch for ``refout != refin``.

* **Execution-verified** (marked ``slow``, skipped without ``zig``) --
  the ``zig_batch`` single-build tier compiles the whole catalogue x
  variant matrix in ONE ``zig run`` (Debug mode, so Zig's shift and
  overflow safety checks are live during verification); per-algorithm
  isolation classes are behind ``--exhaustive``.  The batch driver
  prints results via ``std.debug.print`` (stderr) because that is the
  one printing API that has survived Zig's std churn -- 0.15 removed
  ``std.io`` entirely; the generated code itself imports nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from typing import Literal

import pytest

from crcglot import (
    ALGORITHMS,
    LANGUAGES,
    AlgorithmInfo,
    generate_zig,
    generate_zig_from_entry,
)
from crcglot._helpers import crc_function_names


HAS_ZIG = shutil.which("zig") is not None


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


def _zig_state_type(width: int) -> str:
    """Pick the Zig container type to match what generate_zig uses."""
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


class TestGenerateZig:
    """generate_zig returns a single .zig source string of pub fns.

    Zig's file-as-module model means the file needs no package clause,
    class container, or header pair: the pub fns ARE the module surface
    (``@import("crc32.zig")`` returns the file's namespace).
    """

    def test_generates_code(self):
        # Act
        code = generate_zig("crc16-modbus")

        # Assert -- camelCase is the Zig default naming
        assert code is not None, "generator returned code"
        names = crc_function_names(_func_name("crc16-modbus"), "camel")
        assert f"pub fn {names['oneshot']}(" in code, "one-shot function name"
        assert "u16" in code, "correct state type"
        assert "0x4B37" in code, "check value embedded"
        assert f"pub fn {names['self_test']}() bool" in code, "self-test emitted"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_zig("nonexistent") is None, (
            "unknown algorithm should return None"
        )

    def test_default_names_are_camel_case(self):
        # Act
        code = generate_zig("crc16-modbus")

        # Assert -- the Zig style guide's function convention
        assert code is not None, "generator returned code"
        assert "pub fn crc16ModbusUpdate(" in code, "camelCase update name"
        assert "pub fn crc16ModbusSelfTest() bool" in code, (
            "camelCase self-test name"
        )

    def test_snake_naming_available(self):
        # Act
        code = generate_zig("crc16-modbus", naming="snake")

        # Assert
        assert code is not None, "generator returned code"
        assert "pub fn crc16_modbus_update(" in code, "snake_case update name"

    def test_crc8_uses_u8(self):
        # Act
        code = generate_zig("crc8")

        # Assert
        assert code is not None, "generator returned code"
        assert ") u8 {" in code, "CRC-8 should use u8"

    def test_crc32_uses_u32(self):
        # Act
        code = generate_zig("crc32")

        # Assert
        assert code is not None, "generator returned code"
        assert ") u32 {" in code, "CRC-32 should use u32"

    def test_crc64_uses_u64(self):
        # Act
        code = generate_zig("crc64-xz")

        # Assert
        assert code is not None, "generator returned code"
        assert ") u64 {" in code, "CRC-64 should use u64"

    def test_sub_byte_width_uses_u8_container(self):
        # Act
        code = generate_zig("crc5-usb")

        # Assert -- sub-byte widths ride the u8 container with a mask
        assert code is not None, "generator returned code"
        assert ") u8 {" in code, "CRC-5 should use the u8 container"

    def test_symbol_override(self):
        # Act
        code = generate_zig("crc32", symbol="my_crc32")

        # Assert -- an explicit symbol is emitted verbatim (snake-joined)
        assert code is not None, "generator returned code"
        assert "pub fn my_crc32(" in code, "symbol override applied"
        assert "pub fn my_crc32_self_test() bool" in code, (
            "self-test uses the overridden symbol"
        )

    def test_table_emits_per_symbol_table_const(self):
        # Act
        code = generate_zig("crc32", variant="table")

        # Assert -- table const is symbol-namespaced so multiple generated
        # CRCs can coexist in one file without collision.
        assert code is not None, "generator returned code"
        assert "const crcglot_table_crc32 = [256]u32{" in code, (
            "table-driven variant emits the per-symbol lookup table"
        )

    def test_slice8_emits_eight_tables(self):
        # Act
        code = generate_zig("crc32", variant="slice8")

        # Assert
        assert code is not None, "generator returned code"
        assert "const crcglot_slice_crc32 = [8][256]u32{" in code, (
            "slice-by-8 variant emits the 2D table"
        )
        # Sanity: all 8 sub-table headers should be present.
        for i in range(8):
            assert f"// T{i}" in code, f"slice-by-8 missing T{i} comment"

    @pytest.mark.parametrize("algo", ["crc8", "crc16-modbus"])
    def test_slice8_rejects_narrow_widths(self, algo):
        # Act + Assert
        with pytest.raises(ValueError, match="variant=.slice8. requires width"):
            generate_zig(algo, variant="slice8")

    def test_sub_byte_table_request_degrades_to_bitwise(self):
        # Act -- table below width 8 would emit a negative shift; the
        # generator degrades to bitwise instead (see variants_for_width).
        code = generate_zig("crc5-usb", variant="table")

        # Assert
        assert code is not None, "generator returned code"
        assert "crcglot_table_" not in code, (
            "sub-byte table request must degrade to bitwise (no table const)"
        )

    def test_emits_zig_native_test_declaration(self):
        # Act
        code = generate_zig("crc32")

        # Assert -- ``zig test crc32.zig`` must verify with no harness;
        # the decl is stripped from normal builds so it costs nothing.
        assert code is not None, "generator returned code"
        assert 'test "crc32 self-test" {' in code, (
            "Zig-native test declaration present"
        )
        assert "error.SelfTestFailed" in code, (
            "test declaration reports failure via an error"
        )

    def test_emits_no_imports(self):
        # Act
        code = generate_zig("crc32", variant="slice8")

        # Assert -- import-free output is the churn-resistance property:
        # 0.15 removed std.io wholesale, but language primitives held.
        assert code is not None, "generator returned code"
        assert "@import" not in code, "generated Zig must not import anything"

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_all_catalogue_entries_compile_shape(self, name):
        # Act
        code = generate_zig(name)

        # Assert -- structural only; execution tests verify behaviour
        assert code is not None, f"generate_zig({name!r}) returned code"
        names = crc_function_names(_func_name(name), "camel")
        assert f"pub fn {names['oneshot']}(" in code, (
            f"{name}: one-shot function present"
        )
        assert f"pub fn {names['self_test']}() bool" in code, (
            f"{name}: self-test present"
        )


class TestGenerateZigFromEntryRefoutBranch:
    """The ``refout != refin`` finalize uses ``@bitReverse`` with a
    comptime headroom shift -- reverse over the container, shift right
    to re-align to ``width`` bits.  crc12-umts covers one direction at
    execution time (via the batch); the synthetic entry here pins the
    emitted shape for both.
    """

    def test_refout_differs_from_refin_emits_bit_reverse(self):
        # Arrange -- synthetic entry with refout != refin, w < container
        algo = AlgorithmInfo(
            width=12, poly=0x80F, init=0x000,
            refin=False, refout=True, xorout=0x000,
            check=0x000, desc="synthetic refout!=refin probe",
            source="custom",
        )

        # Act
        code = generate_zig_from_entry("synthetic_refout", algo)

        # Assert -- reversal over u16 then >> 4 re-aligns to 12 bits
        assert "reflect output (refout != refin)" in code, (
            "reflection comment present"
        )
        assert "@bitReverse(state) >> 4" in code, (
            "container reversal with the headroom shift"
        )

    def test_full_width_reflection_needs_no_shift(self):
        # Arrange -- w == container width, so headroom is zero
        algo = AlgorithmInfo(
            width=16, poly=0x1021, init=0x0000,
            refin=False, refout=True, xorout=0x0000,
            check=0x0000, desc="synthetic full-width probe",
            source="custom",
        )

        # Act
        code = generate_zig_from_entry("synthetic_full", algo)

        # Assert
        assert "@bitReverse(state);" in code, (
            "full-width reversal emitted without a shift"
        )


_EXIT_CODE_LABEL = {
    0: "(all checks passed)",
    1: "SelfTest failed (one-shot check value wrong)",
    2: "split-at-4 streamed result wrong",
    3: "empty-chunk-first streamed result wrong",
    4: "empty-chunk-last streamed result wrong",
}


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_ZIG, reason="zig toolchain not on PATH")
class TestGeneratedZigExecutes:
    """Shell out to ``zig run`` to compile and execute the generated
    code.  The runner checks four things in one compiled binary:

      1. ``SelfTest()``          -- four inputs vs independent references
      2. split-at-4 streaming    -- init / update("1234") /
                                    update("56789") / finalize
      3. empty-chunk-first       -- init / update("") /
                                    update("123456789") / finalize
      4. empty-chunk-last        -- init / update("123456789") /
                                    update("") / finalize

    Distinct exit codes (1..4) let a failure point to which pattern
    broke; 0 means every pattern matched the catalogue check value.
    ``zig run`` builds Debug, so Zig's shift/overflow safety checks
    are live -- an illegal shift panics instead of wrapping silently.
    """

    @pytest.mark.parametrize("variant", ["bitwise", "table"])
    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_oneshot_and_streaming(self, name, variant, tmp_path):
        # Arrange
        algo = ALGORITHMS[name]
        expected = hex(algo.check)
        code = generate_zig(name, variant=variant)
        assert code is not None, f"generate_zig({name!r}) returned code"
        # No symbol= override, so the generated functions use the Zig
        # default camelCase naming -- the harness call sites must match.
        names = crc_function_names(_func_name(name), "camel")
        runner = textwrap.dedent(f"""
            const std = @import("std");

            pub fn main() void {{
                if (!{names['self_test']}()) std.process.exit(1);
                var s = {names['init']}();
                s = {names['update']}(s, "1234");
                s = {names['update']}(s, "56789");
                if ({names['finalize']}(s) != {expected}) std.process.exit(2);
                s = {names['init']}();
                s = {names['update']}(s, "");
                s = {names['update']}(s, "123456789");
                if ({names['finalize']}(s) != {expected}) std.process.exit(3);
                s = {names['init']}();
                s = {names['update']}(s, "123456789");
                s = {names['update']}(s, "");
                if ({names['finalize']}(s) != {expected}) std.process.exit(4);
            }}
        """)
        src_path = tmp_path / "main.zig"
        src_path.write_text(code + runner, encoding="utf-8")

        # Act
        result = subprocess.run(
            ["zig", "run", str(src_path)],
            capture_output=True, text=True, timeout=120, cwd=tmp_path,
        )

        # Assert
        label = _EXIT_CODE_LABEL.get(
            result.returncode, "(compile or runtime error)"
        )
        assert result.returncode == 0, (
            f"{name} (variant={variant}): zig run exited "
            f"{result.returncode} {label}; stderr={result.stderr!r}"
        )


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_ZIG, reason="zig toolchain not on PATH")
class TestGeneratedZigSliceBy8Executes:
    """Slice-by-8 equivalence with bit-by-bit in generated Zig.

    Generate both forms under disjoint symbol names, concatenate into
    one file (Zig top-level decls coexist; tables are per-symbol),
    assert equal output across a range of input lengths.  Since the
    bit-by-bit form is already reveng-verified, agreement checks
    slice-by-8 without a second oracle.
    """

    @pytest.mark.parametrize("name", _slice8_algos())
    def test_slice8_matches_bitbybit(self, name, tmp_path):
        # Arrange -- two whole generated files concatenate cleanly; the
        # in-between header comments are plain `//` lines, legal anywhere.
        bb_sym = f"{_func_name(name)}_bb"
        s8_sym = f"{_func_name(name)}_s8"
        bb_code = generate_zig(name, symbol=bb_sym, variant="bitwise")
        s8_code = generate_zig(name, symbol=s8_sym, variant="slice8")
        assert bb_code is not None, f"generate_zig({name!r}) returned None"
        assert s8_code is not None, (
            f"generate_zig({name!r}, variant='slice8') returned None"
        )
        lengths_csv = ", ".join(str(n) for n in _SLICE8_INPUT_LENGTHS)
        runner = textwrap.dedent(f"""
            const std = @import("std");

            pub fn main() void {{
                var buf: [256]u8 = undefined;
                var k: usize = 0;
                while (k < 256) : (k += 1) buf[k] = @truncate(k);
                const lengths = [_]usize{{ {lengths_csv} }};
                var i: usize = 0;
                while (i < lengths.len) : (i += 1) {{
                    const n = lengths[i];
                    if ({bb_sym}(buf[0..n]) != {s8_sym}(buf[0..n])) {{
                        std.process.exit(@intCast(i + 1));
                    }}
                }}
            }}
        """)
        src_path = tmp_path / "main.zig"
        src_path.write_text(
            bb_code + "\n\n" + s8_code + runner, encoding="utf-8"
        )

        # Act
        result = subprocess.run(
            ["zig", "run", str(src_path)],
            capture_output=True, text=True, timeout=180, cwd=tmp_path,
        )

        # Assert -- exit 0 means slice-by-8 == bit-by-bit at every length;
        # a nonzero code is 1 + the index of the disagreeing length.
        assert result.returncode == 0, (
            f"{name}: zig run exited {result.returncode} "
            f"(1 + length index); stderr={result.stderr!r}"
        )


# ─────────────────────────────────────────────────────────────────────
# Batch execution -- whole catalogue x every variant merged into one
# .zig file and built + run in ONE zig invocation instead of one per
# case.  DEFAULT path; the per-algorithm classes above are kept behind
# ``exhaustive`` for isolation.  Zig top-level decls need no package
# munging (unlike Go) -- whole generated files concatenate as-is.  Full
# rationale incl. the mandatory ``xdist_group`` pin is in CLAUDE.md,
# "Execution tests: batch vs exhaustive".
# ─────────────────────────────────────────────────────────────────────

_ZigVariant = Literal["bitwise", "table", "slice8"]
_ZIG_VARIANT_TAG: dict[_ZigVariant, str] = {"bitwise": "b", "table": "t", "slice8": "s8"}


def _zig_batch_cases() -> list[tuple[str, _ZigVariant]]:
    """(name, variant) for every algorithm x supported Zig variant."""
    cases: list[tuple[str, _ZigVariant]] = []
    for name in sorted(ALGORITHMS.keys()):
        w = ALGORITHMS[name].width
        variants: list[_ZigVariant] = ["bitwise"]
        if w >= 8:
            variants.append("table")
        if w in (32, 64):
            variants.append("slice8")
        for v in variants:
            cases.append((name, v))
    return cases


def _zig_batch_driver_case(name: str, variant: _ZigVariant) -> str:
    """One Zig block: <sym>_self_test() + split-streaming + byte-at-a-time
    checks, printing ``<name>/<variant> PASS|FAIL:<phase>`` via
    ``std.debug.print`` (stderr -- the API that survived std's churn)."""
    sym = f"{_func_name(name)}_{_ZIG_VARIANT_TAG[variant]}"
    lit = hex(ALGORITHMS[name].check)
    tag = f"{name}/{variant}"
    # Each case in its own block so s/s2/i stay case-scoped.
    return (
        "    {\n"
        f"        if (!{sym}_self_test()) {{\n"
        f'            std.debug.print("{tag} FAIL:oneshot\\n", .{{}});\n'
        "        } else {\n"
        f"            var s = {sym}_init();\n"
        f"            var s2 = {sym}_init();\n"
        '            const full = "123456789";\n'
        f"            s = {sym}_update(s, full[0..4]);\n"
        f"            s = {sym}_update(s, full[4..9]);\n"
        "            var i: usize = 0;\n"
        "            while (i < 9) : (i += 1) {\n"
        f"                s2 = {sym}_update(s2, full[i .. i + 1]);\n"
        "            }\n"
        f"            if ({sym}_finalize(s) != {lit}) {{\n"
        f'                std.debug.print("{tag} FAIL:streaming\\n", .{{}});\n'
        f"            }} else if ({sym}_finalize(s2) != {lit}) {{\n"
        f'                std.debug.print("{tag} FAIL:bytewise\\n", .{{}});\n'
        "            } else {\n"
        f'                std.debug.print("{tag} PASS\\n", .{{}});\n'
        "            }\n"
        "        }\n"
        "    }"
    )


@pytest.fixture(scope="session")
def zig_batch_results(tmp_path_factory) -> dict[str, str]:
    """Generate every (algorithm, variant) under a unique symbol, merge
    into one .zig file, build + run once, return ``{"name/variant": ...}``.

    Results are parsed from STDERR: ``std.debug.print`` writes there,
    and it is the printing API Zig's std has kept stable (0.15 removed
    ``std.io``).  A failed build never reaches parsing -- the fixture
    fails on the nonzero exit first.
    """
    if not HAS_ZIG:
        return {}
    cases = _zig_batch_cases()
    bodies, driver = [], []
    for name, variant in cases:
        sym = f"{_func_name(name)}_{_ZIG_VARIANT_TAG[variant]}"
        code = generate_zig(name, symbol=sym, variant=variant)
        assert code is not None, f"generate_zig({name!r}) returned None"
        bodies.append(code)
        driver.append(_zig_batch_driver_case(name, variant))
    src = (
        'const std = @import("std");\n\n'
        + "\n\n".join(bodies)
        + "\n\npub fn main() void {\n"
        + "\n".join(driver)
        + "\n}\n"
    )
    d = tmp_path_factory.mktemp("zig_batch")
    main_zig = d / "main.zig"
    main_zig.write_text(src, encoding="utf-8")
    proc = subprocess.run(
        ["zig", "run", str(main_zig)],
        capture_output=True, text=True, timeout=600, cwd=d,
    )
    if proc.returncode != 0:
        pytest.fail(
            "Zig batch failed to build/run (a collision or codegen error):\n"
            + proc.stderr[:3000]
        )
    results: dict[str, str] = {}
    for line in proc.stderr.splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_ZIG, reason="zig toolchain not on PATH")
# One xdist worker so the session-scoped zig build runs once, not per worker.
# See CLAUDE.md "Execution tests: batch vs exhaustive".
@pytest.mark.xdist_group("zig_batch")
@pytest.mark.parametrize("name,variant", _zig_batch_cases())
def test_zig_batch_execution(name, variant, zig_batch_results):
    # Assert -- the single-build driver reported PASS for this case.
    key = f"{name}/{variant}"
    actual = zig_batch_results.get(key)
    assert actual == "PASS", (
        f"{key}: expected PASS, got {actual!r} "
        f"(missing => absent from the one-shot batch run's output)"
    )


_MULTI_ALGOS = ["crc32", "crc16-modbus", "crc8"]


@pytest.mark.slow
@pytest.mark.skipif(not HAS_ZIG, reason="zig toolchain not on PATH")
@pytest.mark.xdist_group("zig_multi")
def test_zig_combined_multi_algorithm_compiles_and_runs(tmp_path):
    """The CLI's multi-algorithm bundle (combine_concat) must produce one
    valid .zig file whose self-tests all pass."""
    # Arrange -- combine several algorithms exactly as the CLI does.
    outputs = []
    for name in _MULTI_ALGOS:
        out = generate_zig(name)
        assert out is not None, f"generate_zig({name!r}) returned None"
        outputs.append(out)
    combined = LANGUAGES["zig"].combiner(outputs, None)
    # generate_zig(n) has no symbol= override, so the self-tests use the
    # Zig default camelCase names; the call sites must match.
    checks = "\n".join(
        f"    if (!{crc_function_names(_func_name(n), 'camel')['self_test']}()) "
        f"std.process.exit({i + 1});"
        for i, n in enumerate(_MULTI_ALGOS)
    )
    src = (
        'const std = @import("std");\n\n'
        + combined
        + "\n\npub fn main() void {\n"
        + checks
        + "\n}\n"
    )
    (tmp_path / "main.zig").write_text(src, encoding="utf-8")

    # Act
    result = subprocess.run(
        ["zig", "run", str(tmp_path / "main.zig")],
        capture_output=True, text=True, timeout=120, cwd=tmp_path,
    )

    # Assert -- 0 means every bundled algorithm's self-test passed.
    assert result.returncode == 0, (
        f"combined file failed (rc {result.returncode}); "
        f"stderr={result.stderr!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# Asymmetric custom execution -- see the matching section in test_c_gen.py:
# refin != refout in the direction crc12-umts does not cover, plus the
# reflect+XOR finalize, compiled and graded against two-oracle values.
# ─────────────────────────────────────────────────────────────────────

_ASYM_IDS = ["refin-only", "refout-only-xor"]


@pytest.fixture(scope="session")
def zig_asymmetric_results(asymmetric_oracle_cases, tmp_path_factory) -> dict[str, str]:
    """Build both refin != refout customs plus an oracle-literal driver as
    one .zig file, run once, return ``{label: "PASS"|"FAIL"}``."""
    if not HAS_ZIG:
        return {}
    bodies, driver = [], []
    for label, algo, oracle in asymmetric_oracle_cases:
        sym = "asym_" + label.replace("-", "_")
        code = generate_zig_from_entry(sym, algo, symbol=sym)
        assert code is not None, f"generate_zig_from_entry({label!r}) returned None"
        bodies.append(code)
        driver.append(
            "    {\n"
            f"        var s = {sym}_init();\n"
            f'        s = {sym}_update(s, "123456789");\n'
            f"        if ({sym}_finalize(s) != {hex(oracle)}) {{\n"
            f'            std.debug.print("{label} FAIL\\n", .{{}});\n'
            "        } else {\n"
            f'            std.debug.print("{label} PASS\\n", .{{}});\n'
            "        }\n"
            "    }"
        )
    src = (
        'const std = @import("std");\n\n'
        + "\n\n".join(bodies)
        + "\n\npub fn main() void {\n"
        + "\n".join(driver)
        + "\n}\n"
    )
    d = tmp_path_factory.mktemp("zig_asym")
    main_zig = d / "main.zig"
    main_zig.write_text(src, encoding="utf-8")
    proc = subprocess.run(
        ["zig", "run", str(main_zig)],
        capture_output=True, text=True, timeout=300, cwd=d,
    )
    if proc.returncode != 0:
        pytest.fail(
            "asymmetric custom Zig failed to build/run:\n" + proc.stderr[:3000]
        )
    results: dict[str, str] = {}
    for line in proc.stderr.splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_ZIG, reason="zig toolchain not on PATH")
@pytest.mark.xdist_group("zig_batch")
class TestAsymmetricCustomExecution:
    """Compiled Zig for ``refin != refout`` customs must reproduce the value
    two independent oracles agreed on -- the asymmetry direction and the
    reflect+XOR finalize that no catalogue algorithm reaches."""

    @pytest.mark.parametrize("idx", [0, 1], ids=_ASYM_IDS)
    def test_generated_code_matches_oracle(
        self, idx, asymmetric_oracle_cases, zig_asymmetric_results
    ):
        # Assert -- the single-build driver reported PASS for this custom.
        label, _algo, oracle = asymmetric_oracle_cases[idx]
        actual = zig_asymmetric_results.get(label)
        assert actual == "PASS", (
            f"{label}: compiled Zig disagreed with the two-oracle value "
            f"0x{oracle:X} (got {actual!r}; missing => absent from driver output)"
        )
