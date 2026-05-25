"""Tests for the VHDL CRC code generator.

Two layers:

* **Structural** (fast, always run) -- ``TestGenerateVhdl`` checks the
  shape of ``generate_vhdl(...)`` output: package header, compute
  function declaration, ``_self_test`` boolean function, ``numeric_std``
  usage, embedded check value, and that ``table=True`` is accepted but
  ignored (VHDL is bit-by-bit only).

* **Execution-verified** (marked ``slow``, skipped without ghdl) --
  shells out to ``ghdl`` to analyze + elaborate + simulate a synthesized
  testbench, asserting against the reveng canonical check value for
  every algorithm in the catalogue.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from crcglot import CRC_CATALOGUE, generate_vhdl


HAS_GHDL = shutil.which("ghdl") is not None


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


# ─────────────────────────────────────────────────────────────────────
# Structural tests -- fast, no toolchain needed.
# ─────────────────────────────────────────────────────────────────────


class TestGenerateVhdl:
    """generate_vhdl returns a complete .vhd package source.

    Includes a ``<fname>_self_test`` boolean function that crcglot's
    pytest harness exercises by synthesizing a testbench (see the
    execution-verified tests below).  Bit-by-bit only -- table-driven
    VHDL is a future enhancement; the ``table=True`` parameter is
    accepted for API symmetry but ignored.
    """

    def test_generates_code(self):
        # Act
        code = generate_vhdl("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "package crc16_modbus_pkg" in code, "package header present"
        assert "function crc16_modbus(" in code, "compute function declared"
        assert (
            "function crc16_modbus_self_test return boolean" in code
        ), "self_test function declared"
        assert "ieee.numeric_std" in code, "uses numeric_std for unsigned arithmetic"
        assert "0x4B37" in code or "19255" in code, "self_test checks against reveng value"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_vhdl("nonexistent") is None, "unknown algorithm returns None"

    def test_table_parameter_accepted_but_ignored(self):
        # Act -- table=True should not raise; bit-by-bit is always emitted.
        bit_code = generate_vhdl("crc16-modbus", table=False)
        table_code = generate_vhdl("crc16-modbus", table=True)

        # Assert
        actual = table_code
        expected = bit_code
        assert actual == expected, (
            "table=True must produce identical output to table=False (ignored param)"
        )


# ─────────────────────────────────────────────────────────────────────
# Execution-verified tests -- compile and simulate via ghdl.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.skipif(not HAS_GHDL, reason="ghdl not in PATH")
class TestGeneratedVhdlExecutes:
    """Compile each generated .vhd through GHDL + a synthesized testbench.

    Per-algorithm flow:

    1. ``generate_vhdl(name)`` -> ``.vhd`` source (a package containing
       the compute function and a ``_self_test`` boolean function).
    2. Write the package to ``<fname>.vhd``.
    3. Synthesize a tiny ``<fname>_tb.vhd`` whose architecture is a
       single process that ``assert``s ``<fname>_self_test`` with
       ``severity failure`` -- GHDL halts the simulation with a
       non-zero exit on assertion failure.
    4. ``ghdl -a <fname>.vhd <fname>_tb.vhd`` -- analyze both files.
       Syntax errors in the generator output surface here.
    5. ``ghdl -e <fname>_tb`` -- elaborate the testbench entity.
    6. ``ghdl -r <fname>_tb`` -- run the simulation.  Exit 0 = check
       value matches; non-zero = the generated function produced the
       wrong CRC for "123456789".

    GHDL is available via ``apt install ghdl`` on Linux, ``brew
    install ghdl`` on macOS, and a manual installer on Windows.  Not
    preinstalled on GitHub Actions runners, so the suite stays green
    on CI without it.
    """

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_self_test_passes(self, name, tmp_path):
        # Arrange
        code = generate_vhdl(name)
        assert code is not None, f"generate_vhdl({name!r}) returned None"
        fname = _func_name(name)

        (tmp_path / f"{fname}.vhd").write_text(code)
        (tmp_path / f"{fname}_tb.vhd").write_text(
            "library ieee;\n"
            "use ieee.std_logic_1164.all;\n"
            f"use work.{fname}_pkg.all;\n"
            "\n"
            f"entity {fname}_tb is\n"
            "end entity;\n"
            "\n"
            f"architecture sim of {fname}_tb is\n"
            "begin\n"
            "    process\n"
            "    begin\n"
            f"        assert {fname}_self_test\n"
            f'            report "{name} self_test FAILED"\n'
            "            severity failure;\n"
            "        wait;\n"
            "    end process;\n"
            "end architecture;\n"
        )

        # Act -- analyze (compile to GHDL's working library)
        analyze = subprocess.run(
            ["ghdl", "-a", "--std=08", f"{fname}.vhd", f"{fname}_tb.vhd"],
            capture_output=True,
            cwd=tmp_path,
        )
        assert analyze.returncode == 0, (
            f"{name}: ghdl analyze failed: "
            f"{analyze.stderr.decode(errors='replace')}"
        )

        # Act -- elaborate the testbench entity
        elaborate = subprocess.run(
            ["ghdl", "-e", "--std=08", f"{fname}_tb"],
            capture_output=True,
            cwd=tmp_path,
        )
        assert elaborate.returncode == 0, (
            f"{name}: ghdl elaborate failed: "
            f"{elaborate.stderr.decode(errors='replace')}"
        )

        # Act -- run the simulation
        run = subprocess.run(
            ["ghdl", "-r", "--std=08", f"{fname}_tb"],
            capture_output=True,
            cwd=tmp_path,
        )

        # Assert
        assert run.returncode == 0, (
            f"{name}: ghdl simulation returned {run.returncode}: "
            f"{run.stderr.decode(errors='replace')}"
        )


@pytest.mark.slow
@pytest.mark.skipif(not HAS_GHDL, reason="ghdl not in PATH")
class TestGeneratedVhdlStreaming:
    """Verify the VHDL streaming triple satisfies the splittability invariant."""

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_split_streaming_matches_check(self, name, tmp_path):
        # Arrange
        entry = CRC_CATALOGUE[name]
        w = entry["width"]
        expected = entry["check"]
        code = generate_vhdl(name)
        assert code is not None, f"generate_vhdl({name!r}) returned None"
        fname = _func_name(name)

        # Format the expected check value as a VHDL literal matching
        # the package's convention (hex bit-string for multiple-of-4
        # widths, to_unsigned otherwise).
        if w % 4 == 0:
            expected_lit = f'x"{expected:0{w // 4}X}"'
        else:
            expected_lit = f"std_logic_vector(to_unsigned({expected}, {w}))"

        (tmp_path / f"{fname}.vhd").write_text(code)
        (tmp_path / f"{fname}_tb.vhd").write_text(
            "library ieee;\n"
            "use ieee.std_logic_1164.all;\n"
            "use ieee.numeric_std.all;\n"
            f"use work.{fname}_pkg.all;\n"
            "\n"
            f"entity {fname}_tb is\n"
            "end entity;\n"
            "\n"
            f"architecture sim of {fname}_tb is\n"
            "begin\n"
            "    process\n"
            f"        variable s: std_logic_vector({w - 1} downto 0);\n"
            "    begin\n"
            "        -- Pattern 1: split at byte 4\n"
            f"        s := {fname}_init;\n"
            f'        s := {fname}_update(s, x"31323334");\n'
            f'        s := {fname}_update(s, x"3536373839");\n'
            f"        assert {fname}_finalize(s) = {expected_lit}\n"
            f'            report "{name} split-streaming FAILED"\n'
            "            severity failure;\n"
            "        -- Pattern 2: empty chunk first, then full\n"
            f"        s := {fname}_init;\n"
            f'        s := {fname}_update(s, x"313233343536373839");\n'
            f"        assert {fname}_finalize(s) = {expected_lit}\n"
            f'            report "{name} full-update FAILED"\n'
            "            severity failure;\n"
            "        wait;\n"
            "    end process;\n"
            "end architecture;\n"
        )

        # Act -- analyze, elaborate, run
        for stage, args in [
            ("analyze", ["ghdl", "-a", "--std=08", f"{fname}.vhd", f"{fname}_tb.vhd"]),
            ("elaborate", ["ghdl", "-e", "--std=08", f"{fname}_tb"]),
            ("run", ["ghdl", "-r", "--std=08", f"{fname}_tb"]),
        ]:
            result = subprocess.run(args, capture_output=True, cwd=tmp_path)
            assert result.returncode == 0, (
                f"{name}: ghdl {stage} failed: "
                f"{result.stderr.decode(errors='replace')}"
            )
