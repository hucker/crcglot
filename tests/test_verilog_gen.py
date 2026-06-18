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

There is deliberately no separate "streaming" test class here (unlike
the software targets and VHDL).  Verilog ships the ``bitwise`` variant
only -- on silicon, bit-by-bit *is* the streaming datapath, not one of
several table/RAM tradeoffs -- and the one-shot wrapper that
``_self_test`` drives already clocks data through ``_update``
incrementally.  A direct init/update/finalize testbench would re-exercise
that same datapath with no extra degree of freedom to get wrong.
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

    @pytest.mark.parametrize(
        "name", ["crc16-modbus", "crc32", "crc8", "crc24-openpgp"]
    )
    def test_self_test_checks_empty_and_check(self, name):
        """The self_test checks the empty input as well as the check string.

        The all-bytes / 1 KiB vectors the software targets use verify the
        byte lookup table; this bitwise package has none, so they are
        dropped.  The empty input is kept because it exercises a distinct
        path -- init then finalize, no update iterations -- that the check
        string does not.
        """
        # Arrange
        from crcglot._vectors import VECTORS
        from crcglot.lang.verilog import _sv_lit
        code = generate_verilog(name)
        assert code is not None, f"generate_verilog({name!r}) returned code"
        algo = ALGORITHMS[name]
        g = VECTORS[name]
        body = code[code.index("function automatic bit"):]
        body = body[: body.index("endfunction")]

        # Assert
        assert g["empty"] != g["check"], (
            f"{name}: fixture sanity -- empty and check goldens must differ"
        )
        assert _sv_lit(g["empty"], algo.width) in body, (
            f"{name}: self_test no longer checks the empty golden"
        )
        assert _sv_lit(g["check"], algo.width) in body, (
            f"{name}: self_test no longer checks the check golden"
        )

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


@pytest.mark.exhaustive
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


# ─────────────────────────────────────────────────────────────────────
# Batch execution -- every algorithm's package (Verilog is bitwise-only)
# compiled with ONE testbench in a single iverilog + vvp run, instead of
# one simulation per algorithm.  DEFAULT path; the per-algorithm class
# above is kept behind ``exhaustive`` for isolation.  No streaming case
# (see the module docstring: bit-by-bit IS the hardware streaming path).
# Full rationale incl. the ``xdist_group`` pin is in CLAUDE.md, "Execution
# tests: batch vs exhaustive".
# ─────────────────────────────────────────────────────────────────────


def _verilog_batch_cases() -> list[tuple[str, str]]:
    """(name, 'bitwise') for every algorithm -- Verilog has one variant."""
    return [(name, "bitwise") for name in sorted(ALGORITHMS.keys())]


@pytest.fixture(scope="session")
def verilog_batch_results(tmp_path_factory) -> dict[str, str]:
    """Compile every algorithm's package + one testbench in a single
    iverilog run, simulate once, return ``{"name/bitwise": result}``."""
    if not HAS_IVERILOG:
        return {}
    d = tmp_path_factory.mktemp("verilog_batch")
    sv_files, imports, checks = [], [], []
    for name, _variant in _verilog_batch_cases():
        sym = f"{_func_name(name)}_b"
        code = generate_verilog(name, symbol=sym)
        assert code is not None, f"generate_verilog({name!r}) returned None"
        (d / f"{sym}.sv").write_text(code)
        sv_files.append(f"{sym}.sv")
        # iverilog rejects a ``pkg::fn()`` call inside an expression, so
        # import each (uniquely-named) package and call it unqualified.
        imports.append(f"    import {sym}_pkg::*;")
        checks.append(
            f"        if ({sym}_self_test()) "
            f'$display("{name}/bitwise PASS");\n'
            f'        else $display("{name}/bitwise FAIL:oneshot");'
        )
    tb = (
        "module batch_tb;\n"
        + "\n".join(imports)
        + "\n    initial begin\n"
        + "\n".join(checks)
        + "\n        $finish;\n    end\nendmodule\n"
    )
    (d / "batch_tb.sv").write_text(tb)
    vvp_file = d / "batch.vvp"
    comp = subprocess.run(
        ["iverilog", "-g2012", "-o", str(vvp_file), *sv_files, "batch_tb.sv"],
        capture_output=True, cwd=d,
    )
    if comp.returncode != 0:
        pytest.fail(
            "Verilog batch failed to compile (a collision or codegen error):\n"
            + comp.stderr.decode(errors="replace")[:3000]
        )
    run = subprocess.run(["vvp", str(vvp_file)], capture_output=True, cwd=d)
    results: dict[str, str] = {}
    for line in run.stdout.decode(errors="replace").splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_IVERILOG, reason="iverilog / vvp not in PATH")
# One xdist worker so the session-scoped iverilog build runs once, not per
# worker.  See CLAUDE.md "Execution tests: batch vs exhaustive".
@pytest.mark.xdist_group("verilog_batch")
@pytest.mark.parametrize("name,variant", _verilog_batch_cases())
def test_verilog_batch_execution(name, variant, verilog_batch_results):
    # Assert -- the single-build sim reported PASS for this case.
    key = f"{name}/{variant}"
    actual = verilog_batch_results.get(key)
    assert actual == "PASS", (
        f"{key}: expected PASS, got {actual!r} "
        f"(missing => absent from the one-shot batch sim's output)"
    )
