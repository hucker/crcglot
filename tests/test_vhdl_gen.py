"""Tests for the VHDL CRC code generator.

Two layers:

* **Structural** (fast, always run) -- ``TestGenerateVhdl`` checks the
  shape of ``generate_vhdl(...)`` output: package header, compute
  function declaration, ``_self_test`` boolean function, ``numeric_std``
  usage, embedded check value, and that ``variant='table'`` is rejected
  with ``ValueError`` (VHDL is bit-by-bit only).

* **Execution-verified** (marked ``slow``, skipped without ghdl) --
  shells out to ``ghdl`` to analyze + elaborate + simulate a synthesized
  testbench, asserting against the reveng canonical check value for
  every algorithm in the catalogue.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from crcglot import ALGORITHMS, generate_vhdl


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
    VHDL is a future enhancement; the ``variant`` parameter accepts
    only ``"bitwise"`` and rejects ``"table"`` / ``"slice8"``.
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
        from crcglot.lang.vhdl import _vhdl_lit
        code = generate_vhdl(name)
        assert code is not None, f"generate_vhdl({name!r}) returned code"
        algo = ALGORITHMS[name]
        g = VECTORS[name]
        fn = name.replace("-", "_") + "_self_test"
        body = code[code.index(f"function {fn} return boolean is"):]
        body = body[: body.index("end function;")]

        # Assert
        assert g["empty"] != g["check"], (
            f"{name}: fixture sanity -- empty and check goldens must differ"
        )
        assert _vhdl_lit(g["empty"], algo.width) in body, (
            f"{name}: self_test no longer checks the empty golden"
        )
        assert _vhdl_lit(g["check"], algo.width) in body, (
            f"{name}: self_test no longer checks the check golden"
        )

    def test_unknown_algorithm(self):
        # Assert
        assert generate_vhdl("nonexistent") is None, "unknown algorithm returns None"

    def test_table_variant_rejected(self):
        # Act / Assert -- VHDL ships bitwise only; variant='table' raises
        # ValueError so callers can't silently get bit-by-bit when they
        # asked for a table-driven implementation.
        with pytest.raises(ValueError, match="variant='table' is not supported"):
            generate_vhdl("crc16-modbus", variant="table")  # type: ignore[call-arg]  # ty: ignore[invalid-argument-type]


# ─────────────────────────────────────────────────────────────────────
# Execution-verified tests -- compile and simulate via ghdl.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.exhaustive
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

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
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


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_GHDL, reason="ghdl not in PATH")
class TestGeneratedVhdlStreaming:
    """Verify the VHDL streaming triple satisfies the splittability invariant."""

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_split_streaming_matches_check(self, name, tmp_path):
        # Arrange
        algo = ALGORITHMS[name]
        w = algo.width
        expected = algo.check
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


# ─────────────────────────────────────────────────────────────────────
# Batch execution -- every algorithm's package (VHDL is bitwise-only)
# analyzed + elaborated + simulated together via ONE ghdl testbench
# (one concurrent process per algorithm, each width-typed), instead of a
# 3-stage ghdl run per algorithm.  DEFAULT path; the per-algorithm classes
# above are kept behind ``exhaustive`` for isolation.  Covers both the
# one-shot self_test and the split-streaming invariant.  Full rationale
# incl. the ``xdist_group`` pin is in CLAUDE.md, "Execution tests: batch
# vs exhaustive".
# ─────────────────────────────────────────────────────────────────────


def _vhdl_batch_cases() -> list[tuple[str, str]]:
    """(name, 'bitwise') for every algorithm -- VHDL has one variant."""
    return [(name, "bitwise") for name in sorted(ALGORITHMS.keys())]


def _vhdl_check_lit(width: int, check: int) -> str:
    """Check value as a VHDL std_logic_vector literal matching the package
    convention (hex bit-string for multiple-of-4 widths, else to_unsigned)."""
    if width % 4 == 0:
        return f'x"{check:0{width // 4}X}"'
    return f"std_logic_vector(to_unsigned({check}, {width}))"


def _vhdl_batch_process(name: str, sym: str) -> str:
    """One concurrent process: self_test + split-streaming, writing
    ``<name>/bitwise PASS|FAIL:<phase>`` to OUTPUT."""
    algo = ALGORITHMS[name]
    w = algo.width
    lit = _vhdl_check_lit(w, algo.check)
    tag = f"{name}/bitwise"
    return (
        "    process\n"
        f"        variable s : std_logic_vector({w - 1} downto 0);\n"
        "        variable l : line;\n"
        "    begin\n"
        f"        if not {sym}_self_test then\n"
        f"            write(l, string'(\"{tag} FAIL:oneshot\"));\n"
        "        else\n"
        f"            s := {sym}_init;\n"
        f'            s := {sym}_update(s, x"31323334");\n'
        f'            s := {sym}_update(s, x"3536373839");\n'
        f"            if {sym}_finalize(s) = {lit} then\n"
        f"                write(l, string'(\"{tag} PASS\"));\n"
        "            else\n"
        f"                write(l, string'(\"{tag} FAIL:streaming\"));\n"
        "            end if;\n"
        "        end if;\n"
        "        writeline(output, l);\n"
        "        wait;\n"
        "    end process;"
    )


@pytest.fixture(scope="session")
def vhdl_batch_results(tmp_path_factory) -> dict[str, str]:
    """Analyze every algorithm's package + one multi-process testbench,
    elaborate + run once, return ``{"name/bitwise": result}``."""
    if not HAS_GHDL:
        return {}
    d = tmp_path_factory.mktemp("vhdl_batch")
    vhd_files, uses, procs = [], [], []
    for name, _variant in _vhdl_batch_cases():
        sym = f"{_func_name(name)}_b"
        code = generate_vhdl(name, symbol=sym)
        assert code is not None, f"generate_vhdl({name!r}) returned None"
        (d / f"{sym}.vhd").write_text(code)
        vhd_files.append(f"{sym}.vhd")
        uses.append(f"use work.{sym}_pkg.all;")
        procs.append(_vhdl_batch_process(name, sym))
    tb = (
        "library ieee;\n"
        "use ieee.std_logic_1164.all;\n"
        "use ieee.numeric_std.all;\n"
        "use std.textio.all;\n"
        + "\n".join(uses)
        + "\n\nentity batch_tb is\nend entity;\n\n"
        + "architecture sim of batch_tb is\nbegin\n"
        + "\n".join(procs)
        + "\nend architecture;\n"
    )
    (d / "batch_tb.vhd").write_text(tb)
    analyze = subprocess.run(
        ["ghdl", "-a", "--std=08", *vhd_files, "batch_tb.vhd"],
        capture_output=True, cwd=d,
    )
    if analyze.returncode != 0:
        pytest.fail(
            "VHDL batch failed to analyze (a collision or codegen error):\n"
            + analyze.stderr.decode(errors="replace")[:3000]
        )
    elaborate = subprocess.run(
        ["ghdl", "-e", "--std=08", "batch_tb"], capture_output=True, cwd=d,
    )
    if elaborate.returncode != 0:
        pytest.fail(
            "VHDL batch failed to elaborate:\n"
            + elaborate.stderr.decode(errors="replace")[:3000]
        )
    run = subprocess.run(
        ["ghdl", "-r", "--std=08", "batch_tb"], capture_output=True, cwd=d,
    )
    results: dict[str, str] = {}
    for line in run.stdout.decode(errors="replace").splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_GHDL, reason="ghdl not in PATH")
# One xdist worker so the session-scoped ghdl build runs once, not per
# worker.  See CLAUDE.md "Execution tests: batch vs exhaustive".
@pytest.mark.xdist_group("vhdl_batch")
@pytest.mark.parametrize("name,variant", _vhdl_batch_cases())
def test_vhdl_batch_execution(name, variant, vhdl_batch_results):
    # Assert -- the single-build sim reported PASS for this case.
    key = f"{name}/{variant}"
    actual = vhdl_batch_results.get(key)
    assert actual == "PASS", (
        f"{key}: expected PASS, got {actual!r} "
        f"(missing => absent from the one-shot batch sim's output)"
    )
