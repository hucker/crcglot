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

from crcglot import (
    ALGORITHMS,
    AlgorithmInfo,
    generate_zig,
    generate_zig_from_entry,
)


HAS_ZIG = shutil.which("zig") is not None


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


_SLICE8_INPUT_LENGTHS = (0, 1, 7, 8, 9, 15, 16, 100)


def _slice8_algos() -> list[str]:
    """Catalogue algorithms eligible for slice-by-8 (width 32 or 64)."""
    return sorted(
        n for n, a in ALGORITHMS.items() if a.width in (32, 64)
    )


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

    def test_non_reflected_masks_top_bit_before_shift(self):
        # Arrange / Act - crc8 is non-reflected (refin=false).  Zig
        # rejects ``crc << 1`` when the top bit is set (overflow is
        # illegal behaviour); Zig 0.13 has no ``<<%`` wrapping shift
        # operator either.  Generator's workaround: mask the top bit
        # off *before* shifting in the top-bit-set branch, so the
        # shift result always fits in uW.
        code = generate_zig("crc8")

        # Assert
        assert code is not None, "generator returned code"
        # Top bit of u8 is 0x80; the masked-low-bits constant is 0x7F.
        assert "crc & 0x7F" in code, (
            "non-reflected path masks the top bit off before shifting"
        )
        assert "<<%" not in code, (
            "Zig 0.13 has no <<% wrapping shift operator; generator "
            "must not emit it"
        )

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

        # Assert -- table name is fname-prefixed so multiple generated
        # CRCs can live in the same Zig compilation unit.
        assert code is not None, "generator returned code"
        assert "const crc32_table = [256]u32{" in code, (
            "table-driven variant emits the lookup table"
        )

    def test_slice8_emits_eight_tables(self):
        # Act
        code = generate_zig("crc32", slice8=True)

        # Assert
        assert code is not None, "generator returned code"
        assert "const crc32_sliceTables = [8][256]u32{" in code, (
            "slice-by-8 variant emits the 2D table"
        )
        for i in range(8):
            assert f"// T{i}" in code, f"slice-by-8 missing T{i} comment"

    @pytest.mark.parametrize("algo", ["crc8", "crc16-modbus"])
    def test_slice8_rejects_narrow_widths(self, algo):
        # Act + Assert
        with pytest.raises(ValueError, match="slice8=True requires width"):
            generate_zig(algo, slice8=True)

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
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
        algo = AlgorithmInfo(
            name="synthetic_refout",
            width=16, poly=0x1021, init=0x0000,
            refin=False, refout=True, xorout=0x0000,
            check=0x0000, desc="synthetic refout!=refin probe",
        )

        # Act
        code = generate_zig_from_entry("synthetic_refout", algo)

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
    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_oneshot_and_streaming(self, name, table, tmp_path):
        # Arrange
        algo = ALGORITHMS[name]
        expected = algo.check
        ztype = _zig_state_type(algo.width)
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


@pytest.mark.slow
@pytest.mark.skipif(not HAS_ZIG, reason="zig toolchain not on PATH")
class TestGeneratedZigSliceBy8Executes:
    """Slice-by-8 equivalence with bit-by-bit in generated Zig.

    Strategy mirrors the C / Go versions: generate both forms under
    disjoint symbol names (so the per-fname table constants don't
    collide), append both into one .zig file with a runner that
    asserts byte-equal output across a range of input lengths.
    Since bit-by-bit is reveng-verified, equivalence proves slice-by-8
    is correct.

    Limited to CRC-32 / CRC-64 algorithms; slice-by-8 only makes sense
    at those widths (the generator raises ValueError otherwise).
    """

    @pytest.mark.parametrize("name", _slice8_algos())
    def test_slice8_matches_bitbybit(self, name, tmp_path):
        # Arrange -- two generated Zig files with disjoint symbol names.
        bb_sym = f"{_func_name(name)}_bb"
        s8_sym = f"{_func_name(name)}_s8"
        bb_code = generate_zig(name, symbol=bb_sym)
        s8_code = generate_zig(name, slice8=True, symbol=s8_sym)
        assert bb_code is not None, f"generate_zig({name!r}) returned None"
        assert s8_code is not None, (
            f"generate_zig({name!r}, slice8=True) returned None"
        )
        ztype = _zig_state_type(ALGORITHMS[name].width)
        lengths_csv = ", ".join(str(n) for n in _SLICE8_INPUT_LENGTHS)
        runner = textwrap.dedent(f"""
            const std = @import("std");
            pub fn main() !void {{
                var buf: [256]u8 = undefined;
                {{
                    var k: usize = 0;
                    while (k < 256) : (k += 1) {{ buf[k] = @as(u8, @intCast(k)); }}
                }}
                const lengths = [_]usize{{ {lengths_csv} }};
                for (lengths, 0..) |n, li| {{
                    const slice = buf[0..n];
                    const bb: {ztype} = {bb_sym}(slice);
                    const s8: {ztype} = {s8_sym}(slice);
                    if (bb != s8) {{
                        std.process.exit(@as(u8, @intCast(li + 1)));
                    }}
                }}
            }}
        """)
        src = bb_code + "\n" + s8_code + "\n" + runner
        src_path = tmp_path / "main.zig"
        src_path.write_text(src, encoding="utf-8")

        # Act
        result = subprocess.run(
            ["zig", "run", str(src_path)],
            capture_output=True, text=True, timeout=60,
            cwd=tmp_path,
        )

        # Assert -- exit 0 means slice-by-8 == bit-by-bit at every length;
        # nonzero index identifies which input length disagreed.
        assert result.returncode == 0, (
            f"{name}: zig run exited {result.returncode} "
            f"(length index, 0 = ok); stderr={result.stderr!r}"
        )
