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
from typing import Literal

import pytest

from crcglot import (
    ALGORITHMS,
    AlgorithmInfo,
    generate_typescript,
    generate_typescript_from_entry,
)
from crcglot._helpers import crc_function_names


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
        # Arrange -- default naming is camelCase for TypeScript.
        names = crc_function_names(_func_name("crc16-modbus"), "camel")

        # Act
        code = generate_typescript("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert f"export function {names['oneshot']}" in code, "exported function"
        assert ": number" in code, "number state type for width 16"
        assert "0x4B37" in code, "check value embedded"
        assert f"{names['self_test']}(): boolean" in code, "self_test signature"

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
    """``generate_typescript(..., variant='table')`` emits a per-symbol
    ``crcglot_table_<symbol>`` so multiple modules coexist in one file."""

    def test_table_declaration(self):
        # Act
        code = generate_typescript("crc32", variant='table')

        # Assert
        assert code is not None, "generator returned code"
        assert "const crcglot_table_crc32: number[] = [" in code, (
            "per-symbol table declaration with number type"
        )
        assert "for (const byte of data)" in code, "iteration over Uint8Array"

    def test_table_bigint_for_width_64(self):
        # Act
        code = generate_typescript("crc64-xz", variant='table')

        # Assert
        assert code is not None, "generator returned code"
        assert "const crcglot_table_crc64_xz: bigint[] = [" in code, (
            "per-symbol table declaration with bigint type for CRC-64"
        )


class TestGenerateTypeScriptSlice8Variant:
    """``generate_typescript(..., variant='slice8')`` emits slice tables."""

    def test_slice8_declaration_width32(self):
        # Act
        code = generate_typescript("crc32", variant='slice8')

        # Assert
        assert code is not None, "generator returned code"
        assert "const crcglot_slice_crc32: number[][] = [" in code, (
            "per-symbol 2D slice table declaration"
        )

    def test_slice8_declaration_width64(self):
        # Act
        code = generate_typescript("crc64-xz", variant='slice8')

        # Assert
        assert code is not None, "generator returned code"
        assert "const crcglot_slice_crc64_xz: bigint[][] = [" in code, (
            "per-symbol 2D bigint slice table for CRC-64"
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
            source="custom",
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


def _build_runner(self_test: str, ts_source: str) -> str:
    """Append a top-level self-test invocation that throws on failure.

    ``self_test`` is the fully-resolved (camelCase, for default naming)
    self-test identifier emitted by the generator.
    """
    return (
        ts_source
        + "\n\n"
        + f"if (!{self_test}()) {{\n"
        + f'    throw new Error("{self_test}() returned false");\n'
        + "}\n"
    )


@pytest.mark.exhaustive
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
        names = crc_function_names(fname, "camel")

        runner = _build_runner(names["self_test"], code)
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
        names = crc_function_names(fname, "camel")

        runner = _build_runner(names["self_test"], code)
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


@pytest.mark.exhaustive
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


def _ts_check_literal(algo: AlgorithmInfo) -> str:
    """The reveng check value as a TS literal matching the state type.

    Width-64 modules thread state as ``bigint`` (``0x..n`` literals);
    everything else is a plain ``number``.
    """
    return f"0x{algo.check:X}n" if algo.width == 64 else f"0x{algo.check:X}"


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_TSX, reason="tsx not in PATH")
class TestGeneratedTypeScriptStreaming:
    """The TS streaming triple (init / update / finalize) must satisfy the
    splittability invariant: feeding ``123456789`` across arbitrary chunk
    boundaries yields the reveng check value, just like the one-shot.

    The execute tests above only drive the one-shot self_test (a single
    update), so chunk-boundary state bugs would slip past them -- this
    closes that gap to parity with C / Rust / Go / C# / VHDL.
    """

    @pytest.mark.parametrize("variant", ["bitwise", "table"])
    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_split_streaming_matches_check(self, name, variant, tmp_path):
        # Arrange
        algo = ALGORITHMS[name]
        code = generate_typescript(name, variant=variant)
        assert code is not None, (
            f"generate_typescript({name!r}, variant={variant!r}) returned None"
        )
        fname = _func_name(name)
        names = crc_function_names(fname, "camel")
        init, update, finalize = (
            names["init"], names["update"], names["finalize"]
        )
        lit = _ts_check_literal(algo)
        full = "0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39"

        # Three feed patterns: split mid-stream, empty chunk first,
        # empty chunk last -- each must finalize to the check value.
        runner = (
            code
            + "\n\n"
            + "function _stream_test(): boolean {\n"
            + f"    let s1 = {init}();\n"
            + f"    s1 = {update}(s1, new Uint8Array([0x31,0x32,0x33,0x34]));\n"
            + f"    s1 = {update}(s1, new Uint8Array([0x35,0x36,0x37,0x38,0x39]));\n"
            + f"    if ({finalize}(s1) !== {lit}) return false;\n"
            + f"    let s2 = {init}();\n"
            + f"    s2 = {update}(s2, new Uint8Array([]));\n"
            + f"    s2 = {update}(s2, new Uint8Array([{full}]));\n"
            + f"    if ({finalize}(s2) !== {lit}) return false;\n"
            + f"    let s3 = {init}();\n"
            + f"    s3 = {update}(s3, new Uint8Array([{full}]));\n"
            + f"    s3 = {update}(s3, new Uint8Array([]));\n"
            + f"    if ({finalize}(s3) !== {lit}) return false;\n"
            + "    return true;\n"
            + "}\n"
            + "if (!_stream_test()) {\n"
            + f'    throw new Error("{fname} split-streaming mismatch");\n'
            + "}\n"
        )
        src = tmp_path / f"{fname}_stream.ts"
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
            f"{name} (variant={variant}): streaming tsx exited "
            f"{result.returncode}: {result.stderr.decode(errors='replace')}"
        )


# ─────────────────────────────────────────────────────────────────────
# Batch execution -- the whole catalogue (every algorithm x every
# supported variant) compiled and run in ONE tsx invocation, instead of
# one process per case.  This is the DEFAULT execution-verification path;
# the per-algorithm classes above are kept behind the ``exhaustive``
# marker for single-algorithm isolation.  The single combined module is
# also the coexistence proof: it only runs because the generated tables
# are per-symbol (``crcglot_table_<sym>``) and so don't collide.
#
# The full run-model rationale -- session fixture builds once, parametrized
# lookup keeps per-algorithm nodes, ``exhaustive`` opt-in, and why the
# ``xdist_group`` pin below is mandatory (a session fixture otherwise
# rebuilds once PER xdist worker) -- lives in CLAUDE.md, section
# "Execution tests: batch vs exhaustive".  Read it before touching this.
# ─────────────────────────────────────────────────────────────────────

_Variant = Literal["bitwise", "table", "slice8"]
_VARIANT_TAG: dict[_Variant, str] = {"bitwise": "b", "table": "t", "slice8": "s8"}


def _ts_batch_cases() -> list[tuple[str, _Variant]]:
    """(name, variant) for every algorithm x supported TS variant."""
    cases: list[tuple[str, _Variant]] = []
    for name in sorted(ALGORITHMS.keys()):
        width = ALGORITHMS[name].width
        variants: list[_Variant] = ["bitwise", "table"]
        if width in (32, 64):
            variants.append("slice8")
        for v in variants:
            cases.append((name, v))
    return cases


def _ts_batch_driver_case(name: str, variant: _Variant) -> str:
    """One JS block: run <sym>_self_test() + a split-streaming check and
    print ``<name>/<variant> PASS|FAIL:<phase>`` (never throws out)."""
    sym = f"{_func_name(name)}_{_VARIANT_TAG[variant]}"
    lit = _ts_check_literal(ALGORITHMS[name])
    tag = f"{name}/{variant}"
    return (
        "{\n"
        "  try {\n"
        "    let r: string;\n"
        f"    if (!{sym}_self_test()) {{ r = 'FAIL:oneshot'; }}\n"
        "    else {\n"
        "      const full = new Uint8Array([0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39]);\n"
        f"      let s = {sym}_init();\n"
        f"      s = {sym}_update(s, full.slice(0, 4));\n"
        f"      s = {sym}_update(s, full.slice(4));\n"
        f"      r = ({sym}_finalize(s) === {lit}) ? 'PASS' : 'FAIL:streaming';\n"
        "    }\n"
        f"    console.log('{tag} ' + r);\n"
        f"  }} catch (e) {{ console.log('{tag} FAIL:exception'); }}\n"
        "}"
    )


@pytest.fixture(scope="session")
def ts_batch_results(tmp_path_factory) -> dict[str, str]:
    """Generate every (algorithm, variant) under a unique symbol into one
    module, run it once via tsx, and return ``{"name/variant": result}``.

    Session-scoped so the single build/run is shared across all the
    parametrized lookups (one tsx invocation per xdist worker).
    """
    if not HAS_TSX:
        return {}
    cases = _ts_batch_cases()
    bodies: list[str] = []
    for name, variant in cases:
        code = generate_typescript(
            name, symbol=f"{_func_name(name)}_{_VARIANT_TAG[variant]}",
            variant=variant,
        )
        assert code is not None, f"generate_typescript({name!r}) returned None"
        bodies.append(code)
    driver = [_ts_batch_driver_case(name, variant) for name, variant in cases]
    src = "\n\n".join(bodies) + "\n\n" + "\n".join(driver) + "\n"

    d = tmp_path_factory.mktemp("ts_batch")
    entry = d / "batch.ts"
    entry.write_text(src)
    result = subprocess.run(
        [TSX_PATH, str(entry)],
        capture_output=True, cwd=d, shell=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "TS batch failed to compile/run (a collision or codegen error):\n"
            + result.stderr.decode(errors="replace")[:3000]
        )
    results: dict[str, str] = {}
    for line in result.stdout.decode(errors="replace").splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_TSX, reason="tsx not in PATH")
# Pin every case to one xdist worker so the session-scoped batch build runs
# ONCE, not once per worker.  See CLAUDE.md "Execution tests: batch vs
# exhaustive".  Removing this silently re-spends most of the speedup.
@pytest.mark.xdist_group("ts_batch")
@pytest.mark.parametrize("name,variant", _ts_batch_cases())
def test_ts_batch_execution(name, variant, ts_batch_results):
    # Assert -- the single-build driver reported PASS for this case.
    key = f"{name}/{variant}"
    actual = ts_batch_results.get(key)
    assert actual == "PASS", (
        f"{key}: expected PASS, got {actual!r} "
        f"(missing => absent from the one-shot batch run's output)"
    )
