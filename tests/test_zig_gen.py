"""Tests for the Zig CRC code generator.

Two layers:

* **Structural** (fast, always run) -- shape checks on the emitted
  source: ``pub fn`` exports, correct Zig integer type, ``[]const u8``
  slice parameter on update, wrapping shift operators on the
  non-reflected bit-by-bit path (overflow-safe in Zig's strict
  integer model), self-test, and the ``refout != refin`` finalize
  branch.

* **Execution-verified** (marked ``slow``, skipped without ``zig``)
  -- shells out to ``zig run`` on a synthesized runner that calls
  ``_self_test()`` and exits 0 on success.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap

import pytest

from crcglot import CRC_CATALOGUE, generate_zig, generate_zig_from_entry


HAS_ZIG = shutil.which("zig") is not None


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


def _zig_state_type(width: int) -> str:
    if width <= 8:
        return "u8"
    if width <= 16:
        return "u16"
    if width <= 32:
        return "u32"
    return "u64"


class TestGenerateZig:
    """generate_zig returns a single .zig source string."""

    def test_generates_code(self):
        # Act
        code = generate_zig("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "pub fn crc16_modbus(" in code, "one-shot fn present"
        assert "u16" in code, "correct state type"
        assert "0x4B37" in code, "check value embedded"
        assert "pub fn crc16_modbus_self_test() bool" in code, (
            "self-test present"
        )
        assert "[]const u8" in code, "slice parameter type"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_zig("nonexistent") is None, (
            "unknown algorithm should return None"
        )

    def test_crc8_uses_u8(self):
        # Act
        code = generate_zig("crc8")

        # Assert
        assert code is not None, "generator returned code"
        assert "pub fn crc8_init() u8" in code, "CRC-8 init returns u8"

    def test_crc32_uses_u32(self):
        # Act
        code = generate_zig("crc32")

        # Assert
        assert code is not None, "generator returned code"
        assert "pub fn crc32_init() u32" in code, "CRC-32 init returns u32"

    def test_crc64_uses_u64(self):
        # Act
        code = generate_zig("crc64-xz")

        # Assert
        assert code is not None, "generator returned code"
        assert "pub fn crc64_xz_init() u64" in code, "CRC-64 init returns u64"

    def test_non_reflected_uses_wrapping_shift(self):
        # Arrange / Act - crc8 is non-reflected (refin=false), so the
        # left-shift in the inner loop must use Zig's wrapping operator
        # to avoid overflow panics when the top bit is set.
        code = generate_zig("crc8")

        # Assert
        assert code is not None, "generator returned code"
        assert "<<%" in code, "non-reflected path uses wrapping shift"

    def test_symbol_override(self):
        # Act
        code = generate_zig("crc32", symbol="myCrc32")

        # Assert
        assert code is not None, "generator returned code"
        assert "pub fn myCrc32(" in code, "symbol override applied"
        assert "pub fn myCrc32_self_test() bool" in code, (
            "self-test uses the overridden symbol"
        )

    def test_table_emits_table_constant(self):
        # Act
        code = generate_zig("crc32", table=True)

        # Assert
        assert code is not None, "generator returned code"
        assert "const crc_table = [256]u32{" in code, (
            "table-driven variant emits the lookup table"
        )

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_all_catalogue_entries_compile_shape(self, name):
        # Act
        code = generate_zig(name)

        # Assert
        assert code is not None, f"generate_zig({name!r}) returned code"
        fname = _func_name(name)
        assert f"pub fn {fname}(" in code, f"{name}: one-shot fn present"
        assert f"pub fn {fname}_self_test() bool" in code, (
            f"{name}: self_test present"
        )


class TestGenerateZigFromEntryRefoutBranch:
    """The ``refout != refin`` finalize-reflection branch is only
    reachable via generate_zig_from_entry since no catalogue entry has
    them unequal.
    """

    def test_refout_differs_from_refin_emits_reflection(self):
        # Arrange
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
        code = generate_zig_from_entry("synthetic_refout", entry)

        # Assert
        assert "reflect output (refout != refin)" in code, (
            "reflection comment present"
        )


_ZIG_EXIT_CODE_LABEL = {
    0: "(all checks passed)",
    1: "_self_test failed (one-shot check value wrong)",
    2: "split-at-4 streamed result wrong",
    3: "empty-chunk-first streamed result wrong",
    4: "empty-chunk-last streamed result wrong",
}


@pytest.mark.slow
@pytest.mark.skipif(not HAS_ZIG, reason="zig toolchain not on PATH")
class TestGeneratedZigExecutes:
    """Compile + run via ``zig run`` on a synthesized runner that
    appends a ``pub fn main()`` running four checks in one binary:

      1. ``_self_test()`` -- one-shot vs reveng check value
      2. split-at-4 streaming
      3. empty-chunk-first streaming
      4. empty-chunk-last streaming

    Distinct exit codes 1..4 identify which pattern broke; 0 means
    every pattern matched the catalogue check value.
    """

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_oneshot_and_streaming(self, name, table, tmp_path):
        # Arrange
        entry = CRC_CATALOGUE[name]
        expected = entry["check"]
        ztype = _zig_state_type(entry["width"])
        code = generate_zig(name, table=table)
        assert code is not None, f"generate_zig({name!r}) returned code"
        fname = _func_name(name)
        runner = textwrap.dedent(f"""
            const std = @import("std");
            pub fn main() !void {{
                const expected: {ztype} = {hex(expected)};
                if (!{fname}_self_test()) std.process.exit(1);
                var s: {ztype} = undefined;
                // split-at-4
                s = {fname}_init();
                s = {fname}_update(s, "1234");
                s = {fname}_update(s, "56789");
                if ({fname}_finalize(s) != expected) std.process.exit(2);
                // empty-chunk-first
                s = {fname}_init();
                s = {fname}_update(s, "");
                s = {fname}_update(s, "123456789");
                if ({fname}_finalize(s) != expected) std.process.exit(3);
                // empty-chunk-last
                s = {fname}_init();
                s = {fname}_update(s, "123456789");
                s = {fname}_update(s, "");
                if ({fname}_finalize(s) != expected) std.process.exit(4);
            }}
        """)
        src = code + runner
        src_path = tmp_path / "main.zig"
        src_path.write_text(src, encoding="utf-8")

        # Act
        result = subprocess.run(
            ["zig", "run", str(src_path)],
            capture_output=True, text=True, timeout=60,
            cwd=tmp_path,
        )

        # Assert
        label = _ZIG_EXIT_CODE_LABEL.get(
            result.returncode, "(compile or runtime error)"
        )
        assert result.returncode == 0, (
            f"{name} (table={table}): zig run exited "
            f"{result.returncode} {label}; stderr={result.stderr!r}"
        )
