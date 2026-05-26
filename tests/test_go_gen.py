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

import pytest

from crcglot import CRC_CATALOGUE, generate_go, generate_go_from_entry


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
        code = generate_go("crc32", table=True)

        # Assert
        assert code is not None, "generator returned code"
        assert "var _crcTable = [256]uint32{" in code, (
            "table-driven variant emits the lookup table"
        )

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
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
        entry = {
            "width": 16,
            "poly": 0x1021,
            "init": 0x0000,
            "refin": False,
            "refout": True,
            "xorout": 0x0000,
            "check": 0x0000,
            "desc": "synthetic refout!=refin probe",
        }

        # Act
        code = generate_go_from_entry("synthetic_refout", entry)

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

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_oneshot_and_streaming(self, name, table, tmp_path):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        gtype = _go_state_type(entry["width"])
        code = generate_go(name, table=table)
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
            f"{name} (table={table}): go run exited "
            f"{result.returncode} {label}; stderr={result.stderr!r}"
        )
