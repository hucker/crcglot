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


@pytest.mark.slow
@pytest.mark.skipif(not HAS_GO, reason="go toolchain not on PATH")
class TestGeneratedGoExecutes:
    """Shell out to ``go run`` to compile and execute the generated
    code, asserting it reproduces the reveng catalogue's check value.

    Same pattern as the C / Rust execution tests.  Synthesizes a
    main.go runner per algorithm that calls _self_test() and exits
    0 on success.
    """

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_self_test_passes(self, name, table, tmp_path):
        # Arrange
        code = generate_go(name, table=table)
        assert code is not None, f"generate_go({name!r}) returned code"
        fname = _func_name(name)
        # Replace `package crc` with `package main` + `import "os"`
        # immediately after, then append a main() that exits 0 if
        # self_test passes, 1 otherwise.  Go requires imports to come
        # before any other declarations, so we inject the import at
        # the package boundary rather than appending it at the end.
        code = code.replace(
            "package crc",
            'package main\n\nimport "os"',
            1,
        )
        runner = textwrap.dedent(f"""
            func main() {{
                if {fname}_self_test() {{
                    os.Exit(0)
                }}
                os.Exit(1)
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
        assert result.returncode == 0, (
            f"{name} (table={table}): go run exited "
            f"{result.returncode}; stderr={result.stderr!r}"
        )
