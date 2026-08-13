"""Tests for the Lua CRC code generator.

Two layers:

* **Structural** (fast, always run) -- shape checks on the emitted
  source: the ``local M = {} ... return M`` module shape, snake_case
  default function names, the explicit ``[0] =`` table entry, the
  Lua 5.3+ requirement in the header, per-symbol table locals, the
  reflect-finalize branch for ``refout != refin``.

* **Execution-verified** (marked ``slow``, skipped without ``lua``) --
  the ``lua_batch`` single-run tier combines the whole catalogue x
  variant matrix into ONE module via the real combiner and grades every
  case in a single ``lua`` invocation; per-algorithm isolation classes
  are behind ``--exhaustive``.  The driver prints results to stdout
  (plain ``print`` -- no printing-API drama in Lua).
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
    generate_lua,
    generate_lua_from_entry,
)
from crcglot._helpers import crc_function_names


HAS_LUA = shutil.which("lua") is not None


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


class TestGenerateLua:
    """generate_lua returns one ``local M = {} ... return M`` module.

    Lua's module idiom means the file needs no class container or
    header pair: ``dofile("mycrc.lua")`` / ``require`` hands the caller
    the table of functions.
    """

    def test_generates_code(self):
        # Act
        code = generate_lua("crc16-modbus")

        # Assert -- snake_case is the Lua default naming
        assert code is not None, "generator returned code"
        names = crc_function_names(_func_name("crc16-modbus"), "snake")
        assert f"function M.{names['oneshot']}(" in code, "one-shot function name"
        assert "0x4B37" in code, "check value embedded"
        assert f"function M.{names['self_test']}()" in code, "self-test emitted"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_lua("nonexistent") is None, (
            "unknown algorithm should return None"
        )

    def test_module_shape(self):
        # Act
        code = generate_lua("crc32")

        # Assert -- exactly one module table, returned at the end
        assert code is not None, "generator returned code"
        assert code.count("local M = {}") == 1, "one module table"
        assert code.rstrip().endswith("return M"), "module returns M"

    def test_header_states_lua_53_requirement(self):
        # Act
        code = generate_lua("crc32")

        # Assert -- the 5.1/5.2/LuaJIT exclusion is a documented scope
        # decision (53-bit doubles cannot represent a CRC-64), not an
        # accident; the generated file must say so where users read it.
        assert code is not None, "generator returned code"
        assert "Lua 5.3+" in code, "header names the minimum Lua version"

    def test_camel_naming_available(self):
        # Act
        code = generate_lua("crc16-modbus", naming="camel")

        # Assert
        assert code is not None, "generator returned code"
        assert "function M.crc16ModbusUpdate(" in code, "camelCase update name"

    def test_symbol_override(self):
        # Act
        code = generate_lua("crc32", symbol="my_crc32")

        # Assert -- an explicit symbol is emitted verbatim (snake-joined)
        assert code is not None, "generator returned code"
        assert "function M.my_crc32(" in code, "symbol override applied"
        assert "function M.my_crc32_self_test()" in code, (
            "self-test uses the overridden symbol"
        )

    def test_table_emits_per_symbol_zero_indexed_table(self):
        # Act
        code = generate_lua("crc32", variant="table")

        # Assert -- the local is symbol-namespaced (combiner-safe) and the
        # array carries an explicit [0] entry so lookups need no +1.
        assert code is not None, "generator returned code"
        assert "local crcglot_table_crc32 = {" in code, (
            "table variant emits the per-symbol lookup table"
        )
        assert "[0] = 0x" in code, "table is explicitly zero-indexed"

    def test_slice8_not_offered(self):
        # Act + Assert -- same interpreter-overhead rationale as Python.
        with pytest.raises(ValueError, match="not supported by this generator"):
            generate_lua("crc32", variant="slice8")  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    def test_sub_byte_table_request_degrades_to_bitwise(self):
        # Act
        code = generate_lua("crc5-usb", variant="table")

        # Assert
        assert code is not None, "generator returned code"
        assert "crcglot_table_" not in code, (
            "sub-byte table request must degrade to bitwise (no table local)"
        )

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_all_catalogue_entries_compile_shape(self, name):
        # Act
        code = generate_lua(name)

        # Assert -- structural only; execution tests verify behaviour
        assert code is not None, f"generate_lua({name!r}) returned code"
        names = crc_function_names(_func_name(name), "snake")
        assert f"function M.{names['oneshot']}(" in code, (
            f"{name}: one-shot function present"
        )
        assert f"function M.{names['self_test']}()" in code, (
            f"{name}: self-test present"
        )


class TestGenerateLuaFromEntryRefoutBranch:
    """The ``refout != refin`` finalize emits an explicit reflect loop
    over ``width`` bits (Lua has no bit-reverse builtin).  crc12-umts
    covers one direction at execution time via the batch; the synthetic
    entry pins the emitted shape.
    """

    def test_refout_differs_from_refin_emits_reflect_loop(self):
        # Arrange
        algo = AlgorithmInfo(
            width=16, poly=0x1021, init=0x0000,
            refin=False, refout=True, xorout=0x0000,
            check=0x0000, desc="synthetic refout!=refin probe",
            source="custom",
        )

        # Act
        code = generate_lua_from_entry("synthetic_refout", algo)

        # Assert
        assert "reflect output (refout != refin)" in code, (
            "reflection comment present"
        )
        assert "local reflected = 0" in code, "reflect accumulator declared"


_EXIT_CODE_LABEL = {
    0: "(all checks passed)",
    1: "self_test failed (one-shot check value wrong)",
    2: "split-at-4 streamed result wrong",
    3: "empty-chunk-first streamed result wrong",
    4: "empty-chunk-last streamed result wrong",
}


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_LUA, reason="lua interpreter not on PATH")
class TestGeneratedLuaExecutes:
    """Run the generated module through the real ``lua`` interpreter.

    The driver checks four things per case (self-test, split-at-4,
    empty-chunk-first, empty-chunk-last) with distinct exit codes 1..4,
    matching the other targets' exhaustive runners.
    """

    @pytest.mark.parametrize("variant", ["bitwise", "table"])
    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_oneshot_and_streaming(self, name, variant, tmp_path):
        # Arrange
        algo = ALGORITHMS[name]
        expected = hex(algo.check)
        code = generate_lua(name, variant=variant)
        assert code is not None, f"generate_lua({name!r}) returned code"
        names = crc_function_names(_func_name(name), "snake")
        (tmp_path / "mod.lua").write_text(code, encoding="utf-8")
        driver = textwrap.dedent(f"""
            local M = dofile("mod.lua")
            if not M.{names['self_test']}() then os.exit(1) end
            local s = M.{names['init']}()
            s = M.{names['update']}(s, "1234")
            s = M.{names['update']}(s, "56789")
            if M.{names['finalize']}(s) ~= {expected} then os.exit(2) end
            s = M.{names['init']}()
            s = M.{names['update']}(s, "")
            s = M.{names['update']}(s, "123456789")
            if M.{names['finalize']}(s) ~= {expected} then os.exit(3) end
            s = M.{names['init']}()
            s = M.{names['update']}(s, "123456789")
            s = M.{names['update']}(s, "")
            if M.{names['finalize']}(s) ~= {expected} then os.exit(4) end
        """)
        (tmp_path / "driver.lua").write_text(driver, encoding="utf-8")

        # Act
        result = subprocess.run(
            ["lua", "driver.lua"],
            capture_output=True, text=True, timeout=60, cwd=tmp_path,
        )

        # Assert
        label = _EXIT_CODE_LABEL.get(result.returncode, "(interpreter error)")
        assert result.returncode == 0, (
            f"{name} (variant={variant}): lua exited "
            f"{result.returncode} {label}; stderr={result.stderr!r}"
        )


# ─────────────────────────────────────────────────────────────────────
# Batch execution -- whole catalogue x every variant combined into one
# module (via the REAL combiner, so bundling is exercised on every run)
# and graded in ONE lua invocation.  DEFAULT path; the per-algorithm
# class above is kept behind ``exhaustive`` for isolation.  Full
# rationale incl. the mandatory ``xdist_group`` pin is in CLAUDE.md,
# "Execution tests: batch vs exhaustive".
# ─────────────────────────────────────────────────────────────────────

_LuaVariant = Literal["bitwise", "table"]
_LUA_VARIANT_TAG: dict[_LuaVariant, str] = {"bitwise": "b", "table": "t"}


def _lua_batch_cases() -> list[tuple[str, _LuaVariant]]:
    """(name, variant) for every algorithm x supported Lua variant."""
    cases: list[tuple[str, _LuaVariant]] = []
    for name in sorted(ALGORITHMS.keys()):
        variants: list[_LuaVariant] = ["bitwise"]
        if ALGORITHMS[name].width >= 8:
            variants.append("table")
        for v in variants:
            cases.append((name, v))
    return cases


def _lua_batch_driver_case(name: str, variant: _LuaVariant) -> str:
    """One Lua block: self_test + split-streaming + byte-at-a-time,
    printing ``<name>/<variant> PASS|FAIL:<phase>``."""
    sym = f"{_func_name(name)}_{_LUA_VARIANT_TAG[variant]}"
    lit = hex(ALGORITHMS[name].check)
    tag = f"{name}/{variant}"
    return (
        f"do\n"
        f"    if not M.{sym}_self_test() then\n"
        f'        print("{tag} FAIL:oneshot")\n'
        f"    else\n"
        f"        local s = M.{sym}_init()\n"
        f"        local s2 = M.{sym}_init()\n"
        f'        local full = "123456789"\n'
        f"        s = M.{sym}_update(s, full:sub(1, 4))\n"
        f"        s = M.{sym}_update(s, full:sub(5, 9))\n"
        f"        for i = 1, 9 do s2 = M.{sym}_update(s2, full:sub(i, i)) end\n"
        f"        if M.{sym}_finalize(s) ~= {lit} then\n"
        f'            print("{tag} FAIL:streaming")\n'
        f"        elseif M.{sym}_finalize(s2) ~= {lit} then\n"
        f'            print("{tag} FAIL:bytewise")\n'
        f"        else\n"
        f'            print("{tag} PASS")\n'
        f"        end\n"
        f"    end\n"
        f"end"
    )


@pytest.fixture(scope="session")
def lua_batch_results(tmp_path_factory) -> dict[str, str]:
    """Generate every (algorithm, variant) under a unique symbol, merge
    into ONE module with the real combiner, run once, return
    ``{"name/variant": "PASS"|"FAIL:phase"}``."""
    if not HAS_LUA:
        return {}
    cases = _lua_batch_cases()
    outputs, driver = [], []
    for name, variant in cases:
        sym = f"{_func_name(name)}_{_LUA_VARIANT_TAG[variant]}"
        code = generate_lua(name, symbol=sym, variant=variant)
        assert code is not None, f"generate_lua({name!r}) returned None"
        outputs.append(code)
        driver.append(_lua_batch_driver_case(name, variant))
    combined = LANGUAGES["lua"].combiner(outputs, None)
    d = tmp_path_factory.mktemp("lua_batch")
    (d / "mod.lua").write_text(combined, encoding="utf-8")
    (d / "driver.lua").write_text(
        'local M = dofile("mod.lua")\n' + "\n".join(driver) + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["lua", "driver.lua"],
        capture_output=True, text=True, timeout=300, cwd=d,
    )
    if proc.returncode != 0:
        pytest.fail(
            "Lua batch failed to load/run (a collision or codegen error):\n"
            + proc.stderr[:3000]
        )
    results: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_LUA, reason="lua interpreter not on PATH")
# One xdist worker so the session-scoped combined run happens once, not per
# worker.  See CLAUDE.md "Execution tests: batch vs exhaustive".
@pytest.mark.xdist_group("lua_batch")
@pytest.mark.parametrize("name,variant", _lua_batch_cases())
def test_lua_batch_execution(name, variant, lua_batch_results):
    # Assert -- the single-run driver reported PASS for this case.
    key = f"{name}/{variant}"
    actual = lua_batch_results.get(key)
    assert actual == "PASS", (
        f"{key}: expected PASS, got {actual!r} "
        f"(missing => absent from the one-shot batch run's output)"
    )


_ASYM_IDS = ["refin-only", "refout-only-xor"]


@pytest.mark.slow
@pytest.mark.skipif(not HAS_LUA, reason="lua interpreter not on PATH")
@pytest.mark.xdist_group("lua_batch")
class TestAsymmetricCustomExecution:
    """Generated Lua for ``refin != refout`` customs must reproduce the
    value two independent oracles agreed on -- the asymmetry direction and
    the reflect+XOR finalize that no catalogue algorithm reaches."""

    @pytest.mark.parametrize("idx", [0, 1], ids=_ASYM_IDS)
    def test_generated_code_matches_oracle(
        self, idx, asymmetric_oracle_cases, tmp_path
    ):
        # Arrange
        label, algo, oracle = asymmetric_oracle_cases[idx]
        sym = "asym_" + label.replace("-", "_")
        code = generate_lua_from_entry(sym, algo, symbol=sym)
        assert code is not None, f"generate_lua_from_entry({label!r}) returned None"
        (tmp_path / "mod.lua").write_text(code, encoding="utf-8")
        driver = (
            'local M = dofile("mod.lua")\n'
            f"local s = M.{sym}_init()\n"
            f's = M.{sym}_update(s, "123456789")\n'
            f"if M.{sym}_finalize(s) ~= {hex(oracle)} then os.exit(1) end\n"
        )
        (tmp_path / "driver.lua").write_text(driver, encoding="utf-8")

        # Act
        result = subprocess.run(
            ["lua", "driver.lua"],
            capture_output=True, text=True, timeout=60, cwd=tmp_path,
        )

        # Assert
        assert result.returncode == 0, (
            f"{label}: generated Lua disagreed with the two-oracle value "
            f"0x{oracle:X}; stderr={result.stderr!r}"
        )
