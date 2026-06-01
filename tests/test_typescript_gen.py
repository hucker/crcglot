"""Tests for the TypeScript CRC code generator.

Two layers:

* **Structural** (fast, always run) -- ``TestGenerateTypeScript`` and
  ``TestGenerateTypeScript*Variants`` check the shape of
  ``generate_typescript(...)`` output: state-type selection
  (``number`` for w<=32, ``bigint`` for w=64), check-value literal,
  exported function signatures, table / slice-by-8 declarations.

* **Execution-verified** (marked ``slow``, skipped without ``tsx``) --
  generates each algorithm, appends a ``main()`` that calls
  ``<fname>_self_test()`` and throws on failure, then runs the file
  through ``tsx``.  Non-zero exit = self_test returned false =
  generator produced wrong CRC.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from crcglot import (
    ALGORITHMS,
    AlgorithmInfo,
    generate_typescript,
    generate_typescript_from_entry,
)


_TSX_PATH = (
    shutil.which("tsx")
    or shutil.which("tsx.cmd")
    or shutil.which("tsx.CMD")
)
HAS_TSX = _TSX_PATH is not None
# Narrow the type for ty: when HAS_TSX is True (the skipif gate),
# _TSX_PATH is a str.  Reading the module-level value at test time
# through this name lets each test pass it directly to subprocess.run
# without per-call casting.
TSX_PATH: str = _TSX_PATH if _TSX_PATH is not None else ""


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


def _ts_state_type(width: int) -> str:
    """Match the typescript module's state-type selection."""
    return "bigint" if width == 64 else "number"


def _slice8_algos() -> list[str]:
    """Catalogue algorithms eligible for slice-by-8 (width 32 or 64)."""
    return sorted(n for n, a in ALGORITHMS.items() if a.width in (32, 64))


# ─────────────────────────────────────────────────────────────────────
# Structural tests -- fast, no runtime needed.
# ─────────────────────────────────────────────────────────────────────


class TestGenerateTypeScript:
    """``generate_typescript`` returns a single ``.ts`` source string."""

    def test_generates_code(self):
        # Act
        code = generate_typescript("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "export function crc16_modbus" in code, "exported function"
        assert ": number" in code, "number state type for width 16"
        assert "0x4B37" in code, "check value embedded"
        assert "crc16_modbus_self_test(): boolean" in code, "self_test signature"

    def test_unknown_algorithm(self):
        # Assert
        assert generate_typescript("nonexistent") is None, (
            "unknown algorithm returns None"
        )

    def test_crc32_uses_number(self):
        # Act
        code = generate_typescript("crc32")

        # Assert
        assert code is not None, "generator returned code"
        assert ": number" in code, "CRC-32 fits in number"
        assert "bigint" not in code, "CRC-32 must not use bigint"

    def test_crc64_uses_bigint(self):
        # Act
        code = generate_typescript("crc64-xz")

        # Assert
        assert code is not None, "generator returned code"
        assert ": bigint" in code, "CRC-64 uses bigint"
        assert "0xC96C5795D7870F42n" in code, (
            "bigint literal with n-suffix embedded"
        )

    def test_symbol_override(self):
        # Act
        code = generate_typescript("crc32", symbol="my_crc")

        # Assert
        assert code is not None, "generator returned code"
        assert "export function my_crc(" in code, "symbol override one-shot"
        assert "export function my_crc_init(" in code, "symbol override init"
        assert "export function my_crc_self_test(" in code, (
            "symbol override self_test"
        )


class TestGenerateTypeScriptTableVariant:
    """``generate_typescript(..., variant='table')`` emits a CRC_TABLE."""

    def test_table_declaration(self):
        # Act
        code = generate_typescript("crc32", variant='table')

        # Assert
        assert code is not None, "generator returned code"
        assert "const CRC_TABLE: number[] = [" in code, (
            "table declaration with number type"
        )
        assert "for (const byte of data)" in code, "iteration over Uint8Array"

    def test_table_bigint_for_width_64(self):
        # Act
        code = generate_typescript("crc64-xz", variant='table')

        # Assert
        assert code is not None, "generator returned code"
        assert "const CRC_TABLE: bigint[] = [" in code, (
            "table declaration with bigint type for CRC-64"
        )


class TestGenerateTypeScriptSlice8Variant:
    """``generate_typescript(..., variant='slice8')`` emits slice tables."""

    def test_slice8_declaration_width32(self):
        # Act
        code = generate_typescript("crc32", variant='slice8')

        # Assert
        assert code is not None, "generator returned code"
        assert "const CRC_SLICE_TABLES: number[][] = [" in code, (
            "2D slice table declaration"
        )

    def test_slice8_declaration_width64(self):
        # Act
        code = generate_typescript("crc64-xz", variant='slice8')

        # Assert
        assert code is not None, "generator returned code"
        assert "const CRC_SLICE_TABLES: bigint[][] = [" in code, (
            "2D bigint slice table for CRC-64"
        )

    @pytest.mark.parametrize("name", ["crc8", "crc16-modbus"])
    def test_slice8_rejects_narrow_widths(self, name):
        # Assert -- slice8 only makes sense at 32 / 64
        with pytest.raises(ValueError, match="variant=.slice8. requires width"):
            generate_typescript(name, variant='slice8')


class TestGenerateTypeScriptFromEntry:
    """The from_entry path covers refout != refin and custom polys."""

    def test_refout_ne_refin_emits_reflection(self):
        # Arrange -- a synthetic algorithm where refout != refin.
        algo = AlgorithmInfo(
            width=16, poly=0x1021, init=0xFFFF,
            refin=False, refout=True, xorout=0x0000,
            check=0,  # value doesn't matter for structural test
            desc="refout != refin synthetic",
        )

        # Act
        code = generate_typescript_from_entry("weird", algo)

        # Assert
        assert "reflected" in code, (
            "finalize emits the bit-reflection branch when refout != refin"
        )


# ─────────────────────────────────────────────────────────────────────
# Execution-verified tests -- compile/run via tsx (skipped if absent).
# ─────────────────────────────────────────────────────────────────────


def _build_runner(fname: str, ts_source: str) -> str:
    """Append a top-level self-test invocation that throws on failure."""
    return (
        ts_source
        + "\n\n"
        + f"if (!{fname}_self_test()) {{\n"
        + f'    throw new Error("{fname}_self_test() returned false");\n'
        + "}\n"
    )


@pytest.mark.slow
@pytest.mark.skipif(not HAS_TSX, reason="tsx not in PATH")
class TestGeneratedTypeScriptExecutes:
    """Generate each algorithm, append a self-test invocation, run via tsx.

    Throws on self_test == false; ``tsx`` exits non-zero on uncaught
    throw.  Same semantics as the other languages' execution tier.
    """

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_check_value_matches_reveng(self, name, tmp_path):
        # Arrange
        code = generate_typescript(name)
        assert code is not None, f"generate_typescript({name!r}) returned None"
        fname = _func_name(name)

        runner = _build_runner(fname, code)
        src = tmp_path / f"{fname}.ts"
        src.write_text(runner)

        # Act
        result = subprocess.run(
            [TSX_PATH, str(src)],
            capture_output=True,
            cwd=tmp_path,
            shell=False,
        )

        # Assert
        assert result.returncode == 0, (
            f"{name}: tsx exited {result.returncode}: "
            f"{result.stderr.decode(errors='replace')}"
        )

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_table_check_value_matches_reveng(self, name, tmp_path):
        # Arrange
        code = generate_typescript(name, variant='table')
        assert code is not None, (
            f"generate_typescript({name!r}, variant='table') returned None"
        )
        fname = _func_name(name)

        runner = _build_runner(fname, code)
        src = tmp_path / f"{fname}.ts"
        src.write_text(runner)

        # Act
        result = subprocess.run(
            [TSX_PATH, str(src)],
            capture_output=True,
            cwd=tmp_path,
            shell=False,
        )

        # Assert
        assert result.returncode == 0, (
            f"{name} (table): tsx exited {result.returncode}: "
            f"{result.stderr.decode(errors='replace')}"
        )


@pytest.mark.slow
@pytest.mark.skipif(not HAS_TSX, reason="tsx not in PATH")
class TestGeneratedTypeScriptSliceBy8Executes:
    """Slice-by-8 equivalence with bit-by-bit in generated TypeScript.

    Limited to CRC-32 and CRC-64 algorithms; slice-by-8 only makes
    sense at those widths.  Generate both forms under disjoint symbol
    names, compile into the same runner, assert byte-equal output
    across a range of input lengths (mirrors the C / Rust strategy).
    """

    @pytest.mark.parametrize("name", _slice8_algos())
    def test_slice8_matches_bitbybit(self, name, tmp_path):
        # Arrange
        bb_sym = f"{_func_name(name)}_bb"
        s8_sym = f"{_func_name(name)}_s8"
        bb_code = generate_typescript(name, symbol=bb_sym)
        s8_code = generate_typescript(name, variant='slice8', symbol=s8_sym)
        assert bb_code is not None and s8_code is not None, (
            f"{name}: both forms generated"
        )

        # Assemble one runner file: concat both modules then a comparison.
        widths = ALGORITHMS[name].width
        compare_op = "===" if widths != 64 else "==="  # both work for bigint
        lengths = [0, 1, 7, 8, 9, 15, 16, 100]
        body = bb_code + "\n\n" + s8_code + "\n\n"
        for length in lengths:
            body += (
                f"{{\n"
                f"    const buf = new Uint8Array({length});\n"
                f"    for (let i = 0; i < {length}; i++) buf[i] = (i * 31 + 7) & 0xFF;\n"
                f"    const bb = {bb_sym}(buf);\n"
                f"    const s8 = {s8_sym}(buf);\n"
                f"    if (bb {compare_op} s8) {{}} else {{\n"
                f'        throw new Error(`{name} length={length} '
                f'mismatch: bb=${{bb.toString(16)}} s8=${{s8.toString(16)}}`);\n'
                f"    }}\n"
                f"}}\n"
            )
        src = tmp_path / f"{_func_name(name)}_slice8.ts"
        src.write_text(body)

        # Act
        result = subprocess.run(
            [TSX_PATH, str(src)],
            capture_output=True,
            cwd=tmp_path,
            shell=False,
        )

        # Assert
        assert result.returncode == 0, (
            f"{name}: tsx exited {result.returncode}: "
            f"{result.stderr.decode(errors='replace')}"
        )
