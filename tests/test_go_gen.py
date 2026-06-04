"""Tests for the Go CRC code generator.

Two layers:

* **Structural** (fast, always run) -- shape checks on the emitted
  source: ``package crc`` declaration, function signatures, Go integer
  types matching the algorithm width, ``_self_test`` block, embedded
  check value, ``refout != refin`` finalize reflection branch
  (reachable only via ``generate_go_from_entry`` since no catalogue
  entry has them unequal).

* **Execution-verified** (marked ``slow``, skipped without ``go``) --
  shells out to ``go run`` to compile and run a synthesized harness
  asserting against the reveng canonical check value for every
  algorithm in the catalogue.  Same pattern as ``test_rust_gen.py``.
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
    generate_go,
    generate_go_from_entry,
)


HAS_GO = shutil.which("go") is not None


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


def _go_state_type(width: int) -> str:
    """Pick the Go state type to match what generate_go uses internally."""
    if width <= 8:
        return "uint8"
    if width <= 16:
        return "uint16"
    if width <= 32:
        return "uint32"
    return "uint64"


# Input lengths spanning degenerate, sub-chunk, exact-chunk, mixed.
_SLICE8_INPUT_LENGTHS = (0, 1, 7, 8, 9, 15, 16, 100)


def _slice8_algos() -> list[str]:
    """Catalogue algorithms eligible for slice-by-8 (width 32 or 64)."""
    return sorted(
        n for n, a in ALGORITHMS.items() if a.width in (32, 64)
    )


class TestGenerateGo:
    """generate_go returns a single .go source string with package crc."""

    def test_generates_code(self):
        # Act
        code = generate_go("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "package crc" in code, "package declaration present"
        assert "func crc16_modbus(" in code, "one-shot function name"
        assert "uint16" in code, "correct state type"
        assert "0x4B37" in code, "check value embedded"
        assert "func crc16_modbus_self_test() bool" in code, "self-test emitted"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_go("nonexistent") is None, (
            "unknown algorithm should return None"
        )

    def test_crc8_uses_uint8(self):
        # Act
        code = generate_go("crc8")

        # Assert
        assert code is not None, "generator returned code"
        assert "uint8" in code, "CRC-8 should use uint8"

    def test_crc32_uses_uint32(self):
        # Act
        code = generate_go("crc32")

        # Assert
        assert code is not None, "generator returned code"
        assert "uint32" in code, "CRC-32 should use uint32"

    def test_crc64_uses_uint64(self):
        # Act
        code = generate_go("crc64-xz")

        # Assert
        assert code is not None, "generator returned code"
        assert "uint64" in code, "CRC-64 should use uint64"

    def test_symbol_override(self):
        # Act
        code = generate_go("crc32", symbol="MyCrc32")

        # Assert
        assert code is not None, "generator returned code"
        assert "func MyCrc32(" in code, "symbol override applied"
        assert "func MyCrc32_self_test() bool" in code, (
            "self-test uses the overridden symbol"
        )

    def test_table_emits_table_constant(self):
        # Act
        code = generate_go("crc32", variant='table')

        # Assert - table variable is fname-prefixed so multiple
        # generated CRCs can coexist in the same Go package without
        # name collision.
        assert code is not None, "generator returned code"
        assert "var _crc32_table = [256]uint32{" in code, (
            "table-driven variant emits the lookup table"
        )

    def test_slice8_emits_eight_tables(self):
        # Act
        code = generate_go("crc32", variant='slice8')

        # Assert
        assert code is not None, "generator returned code"
        assert "var _crc32_sliceTables = [8][256]uint32{" in code, (
            "slice-by-8 variant emits the 2D table"
        )
        # Sanity: all 8 sub-table headers should be present.
        for i in range(8):
            assert f"// T{i}" in code, f"slice-by-8 missing T{i} comment"

    @pytest.mark.parametrize("algo", ["crc8", "crc16-modbus"])
    def test_slice8_rejects_narrow_widths(self, algo):
        # Act + Assert
        with pytest.raises(ValueError, match="variant=.slice8. requires width"):
            generate_go(algo, variant='slice8')

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_all_catalogue_entries_compile_shape(self, name):
        # Act
        code = generate_go(name)

        # Assert - structural only; execution tests verify behaviour
        assert code is not None, f"generate_go({name!r}) returned code"
        fname = _func_name(name)
        assert f"func {fname}(" in code, f"{name}: one-shot function present"
        assert f"func {fname}_self_test() bool" in code, (
            f"{name}: self_test present"
        )


class TestGenerateGoFromEntryRefoutBranch:
    """The ``refout != refin`` finalize-reflection branch is only
    reachable via generate_go_from_entry because no catalogue entry
    has refout differing from refin.  Exercise it explicitly.
    """

    def test_refout_differs_from_refin_emits_reflection(self):
        # Arrange - synthetic entry with refout != refin
        algo = AlgorithmInfo(
            width=16, poly=0x1021, init=0x0000,
            refin=False, refout=True, xorout=0x0000,
            check=0x0000, desc="synthetic refout!=refin probe",
            source="custom",
        )

        # Act
        code = generate_go_from_entry("synthetic_refout", algo)

        # Assert
        assert "reflect output (refout != refin)" in code, (
            "reflection comment present"
        )
        assert "var reflected uint16 = 0" in code, "reflection variable declared"


_EXIT_CODE_LABEL = {
    0: "(all checks passed)",
    1: "_self_test failed (one-shot check value wrong)",
    2: "split-at-4 streamed result wrong",
    3: "empty-chunk-first streamed result wrong",
    4: "empty-chunk-last streamed result wrong",
}


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_GO, reason="go toolchain not on PATH")
class TestGeneratedGoExecutes:
    """Shell out to ``go run`` to compile and execute the generated
    code.  The runner checks four things in one compiled binary:

      1. ``_self_test()``        -- one-shot vs reveng check value
      2. split-at-4 streaming    -- init / update("1234") /
                                    update("56789") / finalize
      3. empty-chunk-first       -- init / update("") /
                                    update("123456789") / finalize
      4. empty-chunk-last        -- init / update("123456789") /
                                    update("") / finalize

    Distinct exit codes (1..4) let a failure point to which pattern
    broke; 0 means every pattern matched the catalogue check value.
    Folding all four into one binary keeps the compile budget the
    same as a one-shot-only test.
    """

    @pytest.mark.parametrize("variant", ["bitwise", "table"])
    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_oneshot_and_streaming(self, name, variant, tmp_path):
        # Arrange
        algo = ALGORITHMS[name]
        expected = algo.check
        gtype = _go_state_type(algo.width)
        code = generate_go(name, variant=variant)
        assert code is not None, f"generate_go({name!r}) returned code"
        fname = _func_name(name)
        code = code.replace(
            "package crc",
            'package main\n\nimport "os"',
            1,
        )
        runner = textwrap.dedent(f"""
            func main() {{
                expected := {gtype}({hex(expected)})
                if !{fname}_self_test() {{
                    os.Exit(1)
                }}
                // split-at-4
                s := {fname}_init()
                s = {fname}_update(s, []byte("1234"))
                s = {fname}_update(s, []byte("56789"))
                if {fname}_finalize(s) != expected {{
                    os.Exit(2)
                }}
                // empty-chunk-first
                s = {fname}_init()
                s = {fname}_update(s, []byte(""))
                s = {fname}_update(s, []byte("123456789"))
                if {fname}_finalize(s) != expected {{
                    os.Exit(3)
                }}
                // empty-chunk-last
                s = {fname}_init()
                s = {fname}_update(s, []byte("123456789"))
                s = {fname}_update(s, []byte(""))
                if {fname}_finalize(s) != expected {{
                    os.Exit(4)
                }}
                os.Exit(0)
            }}
        """)
        src = code + runner
        src_path = tmp_path / "main.go"
        src_path.write_text(src, encoding="utf-8")

        # Act
        result = subprocess.run(
            ["go", "run", str(src_path)],
            capture_output=True, text=True, timeout=30,
        )

        # Assert
        label = _EXIT_CODE_LABEL.get(
            result.returncode, "(compile or runtime error)"
        )
        assert result.returncode == 0, (
            f"{name} (variant={variant}): go run exited "
            f"{result.returncode} {label}; stderr={result.stderr!r}"
        )


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_GO, reason="go toolchain not on PATH")
class TestGeneratedGoSliceBy8Executes:
    """Slice-by-8 equivalence with bit-by-bit in generated Go.

    Strategy mirrors test_c_gen.TestGeneratedCSliceBy8Executes:
    generate both forms under disjoint symbol names, compile both into
    the same ``package main``, assert byte-equal output across a range
    of input lengths.  Since the bit-by-bit form is already
    reveng-verified, equivalence proves slice-by-8 is correct.

    Limited to CRC-32 / CRC-64 algorithms; slice-by-8 only makes sense
    at those widths (validated by the variant='slice8' ValueError in the
    generator).
    """

    @pytest.mark.parametrize("name", _slice8_algos())
    def test_slice8_matches_bitbybit(self, name, tmp_path):
        # Arrange -- generate two .go files with disjoint symbol names.
        bb_sym = f"{_func_name(name)}_bb"
        s8_sym = f"{_func_name(name)}_s8"
        bb_code = generate_go(name, symbol=bb_sym)
        s8_code = generate_go(name, variant='slice8', symbol=s8_sym)
        assert bb_code is not None, f"generate_go({name!r}) returned None"
        assert s8_code is not None, (
            f"generate_go({name!r}, variant='slice8') returned None"
        )

        # Convert the first file's ``package crc`` to ``package main``
        # AND inject ``import "os"`` immediately after, so the import
        # lands before any var/func declarations (Go requires that).
        bb_code = bb_code.replace(
            "package crc",
            'package main\n\nimport "os"',
            1,
        )
        # The second file's package line + comment header collide with
        # the first; keep only the function bodies + tables.  Locate
        # the first ``var`` or ``func`` line and slice from there.
        marker_idx = min(
            (s8_code.find(p) for p in ("\nvar ", "\nfunc ") if s8_code.find(p) >= 0),
            default=-1,
        )
        assert marker_idx > 0, "could not find first var/func in s8 source"
        s8_body = s8_code[marker_idx:]

        gtype = _go_state_type(ALGORITHMS[name].width)
        lengths_csv = ", ".join(str(n) for n in _SLICE8_INPUT_LENGTHS)
        runner = textwrap.dedent(f"""
            func main() {{
                var buf [256]byte
                for k := 0; k < 256; k++ {{ buf[k] = byte(k) }}
                lengths := []int{{ {lengths_csv} }}
                for i, n := range lengths {{
                    var bb {gtype} = {bb_sym}(buf[:n])
                    var s8 {gtype} = {s8_sym}(buf[:n])
                    if bb != s8 {{
                        os.Exit(i + 1)
                    }}
                }}
                os.Exit(0)
            }}
        """)
        src = bb_code + s8_body + runner
        src_path = tmp_path / "main.go"
        src_path.write_text(src, encoding="utf-8")

        # Act
        result = subprocess.run(
            ["go", "run", str(src_path)],
            capture_output=True, text=True, timeout=60,
        )

        # Assert -- exit 0 means slice-by-8 == bit-by-bit at every length;
        # nonzero index identifies which input length disagreed.
        assert result.returncode == 0, (
            f"{name}: go run exited {result.returncode} "
            f"(length index, 0 = ok); stderr={result.stderr!r}"
        )


# ─────────────────────────────────────────────────────────────────────
# Batch execution -- whole catalogue x every variant merged into one
# ``package main`` and built + run in ONE go invocation instead of one per
# case.  DEFAULT path; the per-algorithm classes above are kept behind
# ``exhaustive`` for isolation.  Go tables are already per-symbol
# (``_<sym>_table``), so this needs no Phase-1 change.  Full rationale incl.
# the mandatory ``xdist_group`` pin is in CLAUDE.md, "Execution tests:
# batch vs exhaustive".
# ─────────────────────────────────────────────────────────────────────

_GoVariant = Literal["bitwise", "table", "slice8"]
_GO_VARIANT_TAG: dict[_GoVariant, str] = {"bitwise": "b", "table": "t", "slice8": "s8"}


def _go_batch_cases() -> list[tuple[str, _GoVariant]]:
    """(name, variant) for every algorithm x supported Go variant."""
    cases: list[tuple[str, _GoVariant]] = []
    for name in sorted(ALGORITHMS.keys()):
        variants: list[_GoVariant] = ["bitwise", "table"]
        if ALGORITHMS[name].width in (32, 64):
            variants.append("slice8")
        for v in variants:
            cases.append((name, v))
    return cases


def _go_batch_driver_case(name: str, variant: _GoVariant) -> str:
    """One Go block: <sym>_self_test() + split-streaming check, printing
    ``<name>/<variant> PASS|FAIL:<phase>``."""
    sym = f"{_func_name(name)}_{_GO_VARIANT_TAG[variant]}"
    algo = ALGORITHMS[name]
    gtype = _go_state_type(algo.width)
    lit = f"{gtype}({hex(algo.check)})"
    tag = f"{name}/{variant}"
    return (
        f"\tif !{sym}_self_test() {{\n"
        f'\t\tfmt.Println("{tag} FAIL:oneshot")\n'
        "\t} else {\n"
        f"\t\ts := {sym}_init()\n"
        f'\t\ts = {sym}_update(s, []byte("1234"))\n'
        f'\t\ts = {sym}_update(s, []byte("56789"))\n'
        f"\t\tif {sym}_finalize(s) != {lit} {{\n"
        f'\t\t\tfmt.Println("{tag} FAIL:streaming")\n'
        "\t\t} else {\n"
        f'\t\t\tfmt.Println("{tag} PASS")\n'
        "\t\t}\n"
        "\t}"
    )


@pytest.fixture(scope="session")
def go_batch_results(tmp_path_factory) -> dict[str, str]:
    """Generate every (algorithm, variant) under a unique symbol, merge into
    one ``package main``, build + run once, return ``{"name/variant": ...}``."""
    if not HAS_GO:
        return {}
    cases = _go_batch_cases()
    bodies, driver = [], []
    for name, variant in cases:
        sym = f"{_func_name(name)}_{_GO_VARIANT_TAG[variant]}"
        code = generate_go(name, symbol=sym, variant=variant)
        assert code is not None, f"generate_go({name!r}) returned None"
        # Drop each module's comment header + ``package crc`` line; keep the
        # code after it.  One ``package main`` is prepended for the whole file.
        bodies.append(code.partition("package crc")[2])
        driver.append(_go_batch_driver_case(name, variant))
    src = (
        'package main\n\nimport "fmt"\n'
        + "".join(bodies)
        + "\n\nfunc main() {\n"
        + "\n".join(driver)
        + "\n}\n"
    )
    d = tmp_path_factory.mktemp("go_batch")
    main_go = d / "main.go"
    main_go.write_text(src, encoding="utf-8")
    proc = subprocess.run(
        ["go", "run", str(main_go)],
        capture_output=True, text=True, timeout=300, cwd=d,
    )
    if proc.returncode != 0:
        pytest.fail(
            "Go batch failed to build/run (a collision or codegen error):\n"
            + proc.stderr[:3000]
        )
    results: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_GO, reason="go toolchain not on PATH")
# One xdist worker so the session-scoped go build runs once, not per worker.
# See CLAUDE.md "Execution tests: batch vs exhaustive".
@pytest.mark.xdist_group("go_batch")
@pytest.mark.parametrize("name,variant", _go_batch_cases())
def test_go_batch_execution(name, variant, go_batch_results):
    # Assert -- the single-build driver reported PASS for this case.
    key = f"{name}/{variant}"
    actual = go_batch_results.get(key)
    assert actual == "PASS", (
        f"{key}: expected PASS, got {actual!r} "
        f"(missing => absent from the one-shot batch run's output)"
    )


_MULTI_ALGOS = ["crc32", "crc16-modbus", "crc8"]


@pytest.mark.slow
@pytest.mark.skipif(not HAS_GO, reason="go toolchain not on PATH")
@pytest.mark.xdist_group("go_multi")
def test_go_combined_multi_algorithm_compiles_and_runs(tmp_path):
    """The CLI's multi-algorithm bundle (combine_go) must produce one
    valid `package crc` file whose self_tests all pass."""
    # Arrange -- combine several algorithms exactly as the CLI does, then
    # swap the package clause for an executable main (as the exec tests do).
    outputs = []
    for name in _MULTI_ALGOS:
        out = generate_go(name)
        assert out is not None, f"generate_go({name!r}) returned None"
        outputs.append(out)
    combined = LANGUAGES["go"].combiner(outputs, None)
    assert combined.count("package crc") == 1, "exactly one package clause"
    src = combined.replace("package crc", 'package main\n\nimport "os"', 1)
    src += "\n\nfunc main() {\n" + "\n".join(
        f"\tif !{_func_name(n)}_self_test() {{ os.Exit({i + 1}) }}"
        for i, n in enumerate(_MULTI_ALGOS)
    ) + "\n\tos.Exit(0)\n}\n"
    (tmp_path / "main.go").write_text(src, encoding="utf-8")

    # Act
    result = subprocess.run(
        ["go", "run", str(tmp_path / "main.go")],
        capture_output=True, text=True, timeout=60, cwd=tmp_path,
    )

    # Assert -- 0 means every bundled algorithm's self_test passed.
    assert result.returncode == 0, (
        f"combined package failed (rc {result.returncode}); "
        f"stderr={result.stderr!r}"
    )
