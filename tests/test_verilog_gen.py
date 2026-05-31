"""Tests for the Verilog (SystemVerilog 2012) CRC code generator.

Two layers:

* **Structural** (fast, always run) -- ``TestGenerateVerilog`` checks
  the shape of ``generate_verilog(...)`` output: package header,
  function declarations, ``_self_test`` ``bit`` function, embedded
  check value, ``variant='table'`` rejected with ``ValueError``
  (bit-by-bit only).

* **Execution-verified** (marked ``slow``, skipped without iverilog) --
  shells out to ``iverilog -g2012`` + ``vvp`` to compile and simulate
  a synthesized testbench, asserting against the reveng canonical
  check value for every algorithm in the catalogue.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from crcglot import ALGORITHMS, generate_verilog


HAS_IVERILOG = (
    shutil.which("iverilog") is not None and shutil.which("vvp") is not None
)


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


# ─────────────────────────────────────────────────────────────────────
# Structural tests -- fast, no toolchain needed.
# ─────────────────────────────────────────────────────────────────────


class TestGenerateVerilog:
    """``generate_verilog`` returns a complete SystemVerilog package."""

    def test_generates_code(self):
        # Act
        code = generate_verilog("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "package crc16_modbus_pkg" in code, "package header present"
        assert "function automatic [15:0] crc16_modbus(" in code, (
            "compute function declared"
        )
        assert (
            "function automatic bit crc16_modbus_self_test();" in code
        ), "self_test bit function declared"
        assert "16'h4B37" in code, "self_test checks against reveng value"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_verilog("nonexistent") is None, (
            "unknown algorithm returns None"
        )

    def test_crc8_width(self):
        # Act
        code = generate_verilog("crc8")

        # Assert
        assert code is not None, "generator returned code"
        assert "[7:0]" in code, "8-bit width signal declared"

    def test_crc64_width(self):
        # Act
        code = generate_verilog("crc64-xz")

        # Assert
        assert code is not None, "generator returned code"
        assert "[63:0]" in code, "64-bit width signal declared"

    def test_table_variant_rejected(self):
        # Act / Assert -- Verilog ships bitwise only; variant='table'
        # raises ValueError so callers can't silently get bit-by-bit
        # when they asked for a table-driven implementation.
        with pytest.raises(ValueError, match="variant='table' is not supported"):
            generate_verilog("crc16-modbus", variant="table")  # type: ignore[call-arg]  # ty: ignore[invalid-argument-type]

    def test_symbol_override(self):
        # Act
        code = generate_verilog("crc32", symbol="my_crc")

        # Assert
        assert code is not None, "generator returned code"
        assert "package my_crc_pkg" in code, "package name uses symbol"
        assert "function automatic [31:0] my_crc(" in code, (
            "one-shot uses symbol"
        )
        assert "my_crc_self_test()" in code, "self_test uses symbol"


# ─────────────────────────────────────────────────────────────────────
# Execution-verified tests -- compile + simulate via iverilog / vvp.
# ─────────────────────────────────────────────────────────────────────


def _build_testbench(fname: str, algo_name: str) -> str:
    """Tiny testbench module that asserts the generated self_test."""
    return (
        f"module {fname}_tb;\n"
        f"    import {fname}_pkg::*;\n"
        "    initial begin\n"
        f"        if ({fname}_self_test()) begin\n"
        '            $finish(0);\n'
        "        end else begin\n"
        f'            $display("{algo_name} self_test FAILED");\n'
        '            $finish(1);\n'
        "        end\n"
        "    end\n"
        "endmodule\n"
    )


@pytest.mark.slow
@pytest.mark.skipif(
    not HAS_IVERILOG, reason="iverilog / vvp not in PATH"
)
class TestGeneratedVerilogExecutes:
    """Compile each generated .sv through iverilog + a synthesized testbench.

    Per-algorithm flow:

    1. ``generate_verilog(name)`` -> ``.sv`` source (a SystemVerilog
       package with the compute and ``_self_test`` functions).
    2. Synthesize a tiny ``<fname>_tb.sv`` whose initial block calls
       ``<fname>_self_test()`` and ``$finish(1)`` on failure.
    3. ``iverilog -g2012 -o run.vvp <fname>.sv <fname>_tb.sv``
       -- compile (analyze + elaborate in one pass).
    4. ``vvp run.vvp`` -- simulate.  Exit 0 = check value matches;
       non-zero = the generated function produced the wrong CRC for
       "123456789".
    """

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_self_test_passes(self, name, tmp_path):
        # Arrange
        code = generate_verilog(name)
        assert code is not None, f"generate_verilog({name!r}) returned None"
        fname = _func_name(name)

        (tmp_path / f"{fname}.sv").write_text(code)
        (tmp_path / f"{fname}_tb.sv").write_text(_build_testbench(fname, name))
        vvp_file = tmp_path / f"{fname}.vvp"

        # Act -- compile
        compile_result = subprocess.run(
            [
                "iverilog",
                "-g2012",
                "-o", str(vvp_file),
                f"{fname}.sv",
                f"{fname}_tb.sv",
            ],
            capture_output=True,
            cwd=tmp_path,
        )
        assert compile_result.returncode == 0, (
            f"{name}: iverilog failed: "
            f"{compile_result.stderr.decode(errors='replace')}"
        )

        # Act -- simulate
        run_result = subprocess.run(
            ["vvp", str(vvp_file)],
            capture_output=True,
            cwd=tmp_path,
        )

        # Assert
        assert run_result.returncode == 0, (
            f"{name}: vvp returned {run_result.returncode}: "
            f"{run_result.stdout.decode(errors='replace')}"
            f"{run_result.stderr.decode(errors='replace')}"
        )
