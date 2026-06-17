"""Tests for the Java CRC code generator.

Two layers:

* **Structural** (fast, always run) -- ``TestGenerateJava`` checks the
  shape of ``generate_java(...)`` output: the flat container class,
  ``int``/``long`` state types, per-symbol table names, the signed-int
  arithmetic canaries (``>>>`` and ``& 0xFF``), and the slice8 width guard.

* **Execution-verified** (marked ``slow``, skipped without a JDK) --
  compiles generated Java with ``javac`` and runs it with ``java``.  The
  batch test compiles the whole catalogue x variants in ONE container class
  + ``main()`` and a single ``javac``/``java`` invocation.

Java has no unsigned integer types, so the correctness-critical thing the
execution tier proves is that the ``int``/``long`` + ``& 0xFF`` + ``>>>``
loops reproduce the reveng check values.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Literal

import pytest

from crcglot import (
    ALGORITHMS,
    LANGUAGES,
    AlgorithmInfo,
    generate_java,
    generate_java_from_entry,
)
from crcglot._helpers import crc_function_names

_JAVAC = shutil.which("javac")
_JAVA = shutil.which("java")


def _has_jdk() -> bool:
    """A JDK (javac + java) usable for compile-and-run is on PATH."""
    if _JAVAC is None or _JAVA is None:
        return False
    try:
        return subprocess.run(
            [_JAVAC, "-version"], capture_output=True, timeout=30
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


HAS_JDK = _has_jdk()

_Variant = Literal["bitwise", "table", "slice8"]
_VARIANT_TAG: dict[_Variant, str] = {"bitwise": "b", "table": "t", "slice8": "s8"}


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


def _java_jtype(width: int) -> str:
    return "long" if width == 64 else "int"


def _java_check_lit(name: str) -> str:
    """Check value as a Java literal (``L`` suffix only at width 64)."""
    algo = ALGORITHMS[name]
    return f"0x{algo.check:X}" + ("L" if algo.width == 64 else "")


# ─────────────────────────────────────────────────────────────────────
# Structural tests -- fast, no toolchain needed.
# ─────────────────────────────────────────────────────────────────────


class TestGenerateJava:
    """``generate_java`` emits a flat ``public final class`` container."""

    def test_flat_container_and_methods(self):
        # Act
        code = generate_java("crc16-modbus")
        names = crc_function_names(_func_name("crc16-modbus"), "camel")

        # Assert -- one container, algorithm-named flat static methods.
        assert code is not None, "generator returned code"
        assert "public final class CrcGlot {" in code, "flat container class"
        assert f"public static int {names['init']}()" in code, "init method"
        assert f"public static int {names['oneshot']}(byte[] data)" in code, "one-shot"
        assert f"public static boolean {names['self_test']}()" in code, "self_test"
        assert "0x4B37" in code, "embedded reveng check value"

    def test_width8_uses_int_not_byte(self):
        # Assert -- Java byte is signed; the generator uses int for w<=32.
        code = generate_java("crc8")
        names = crc_function_names(_func_name("crc8"), "camel")
        assert code is not None, "generator returned code"
        assert f"public static int {names['init']}()" in code, "w8 state type is int"
        assert f"byte {names['init']}" not in code, "must not use signed byte state"

    def test_width32_check_literal_has_no_suffix(self):
        # Assert -- unlike C# (u/UL), Java width-32 is a plain int literal.
        code = generate_java("crc32")
        assert code is not None, "generator returned code"
        assert "0xCBF43926" in code, "w32 check literal present as bare int"
        assert "0xCBF43926u" not in code and "0xCBF43926UL" not in code, (
            "no C#-style unsigned suffix"
        )

    def test_width64_uses_long_with_L_suffix(self):
        # Assert
        code = generate_java("crc64-xz", variant="table")
        names = crc_function_names(_func_name("crc64-xz"), "camel")
        assert code is not None, "generator returned code"
        assert f"public static long {names['init']}()" in code, "w64 state is long"
        assert "L," in code, "w64 table entries carry the L suffix"

    def test_table_is_per_symbol_named(self):
        # Assert -- flat container => tables must be per-symbol to coexist.
        code = generate_java("crc32", variant="table")
        assert code is not None, "generator returned code"
        assert "crcglot_table_crc32" in code, "per-symbol table name"
        assert "CRC_TABLE" not in code, "placeholder fully rewritten"

    def test_slice8_per_symbol_and_blocks(self):
        # Assert
        code = generate_java("crc32", variant="slice8")
        assert code is not None, "generator returned code"
        assert "crcglot_slice_crc32" in code, "per-symbol slice table name"
        assert "// T0" in code and "// T7" in code, "8 slice-table blocks"

    def test_signed_int_arithmetic_canaries(self):
        # Assert -- the two ways Java's signed ints bite: logical right
        # shift (>>>) and byte zero-extension (& 0xFF) must both appear, and
        # no arithmetic >> may leak into the CRC loops.
        code = generate_java("crc32", variant="table")
        assert code is not None, "generator returned code"
        assert ">>>" in code, "logical (unsigned) right shift used"
        assert "& 0xFF" in code, "bytes zero-extended"
        assert ">> 8" not in code.replace(">>> 8", ""), (
            "no sign-extending arithmetic shift in the CRC loop"
        )

    def test_slice8_rejected_for_narrow_widths(self):
        # Assert
        for name in ("crc8", "crc16-modbus"):
            with pytest.raises(ValueError, match="variant=.slice8. requires width"):
                generate_java(name, variant="slice8")

    def test_unknown_algorithm(self):
        # Assert
        assert generate_java("nonexistent") is None, "unknown name returns None"

    def test_refout_ne_refin_emits_reflection(self):
        # Arrange -- a synthetic algorithm where refout != refin (no
        # catalogue entry reaches this branch).
        algo = AlgorithmInfo(
            width=16, poly=0x1021, init=0xFFFF,
            refin=False, refout=True, xorout=0x0000,
            check=0, desc="refout != refin synthetic", source="custom",
        )

        # Act
        code = generate_java_from_entry("weird", algo)

        # Assert
        assert "reflect output" in code, "reflection comment present"
        assert "reflected |= ((state >>> k) & 1)" in code, "reflection loop"

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_all_catalogue_algorithms_generate(self, name):
        # Assert -- every catalogue entry produces non-empty Java.
        code = generate_java(name)
        assert code is not None and "public final class" in code, (
            f"{name}: generated a container class"
        )


class TestCombineJava:
    """``combine_java`` merges members into ONE container class."""

    def test_bundle_one_class_all_methods(self):
        # Act
        a = generate_java("crc32")
        b = generate_java("crc16-modbus")
        assert a is not None and b is not None, "both generated"
        bundle = LANGUAGES["java"].combiner([a, b], "Bundle")

        # Assert
        actual_classes = bundle.count("public final class")
        crc32_name = crc_function_names(_func_name("crc32"), "camel")["oneshot"]
        modbus_name = crc_function_names(_func_name("crc16-modbus"), "camel")["oneshot"]
        assert actual_classes == 1, "exactly one container class"
        assert "public final class Bundle {" in bundle, "named from the stem"
        assert (
            f"{crc32_name}(byte[] data)" in bundle
            and f"{modbus_name}(byte[] data)" in bundle
        ), "both algorithms present"
        assert "CrcGlot" not in bundle, "default name fully renamed"

    def test_bundle_preserves_each_provenance_block(self):
        """combine_java keeps every algorithm's header, so each one's
        ``Reproduce with crcglot`` block survives (regression: the file header
        was dropped, leaving Java with no provenance block via the CLI)."""
        # Act
        a = generate_java("crc32")
        b = generate_java("crc16-modbus")
        assert a is not None and b is not None, "both generated"
        bundle = LANGUAGES["java"].combiner([a, b], "Bundle")

        # Assert -- one provenance block per algorithm, each correctly labelled.
        actual_blocks = bundle.count("Reproduce with crcglot:")
        assert actual_blocks == 2, f"expected 2 provenance blocks, got {actual_blocks}"
        assert "algorithm: crc32" in bundle, "crc32 provenance present"
        assert "algorithm: crc16-modbus" in bundle, "crc16-modbus provenance present"


# ─────────────────────────────────────────────────────────────────────
# Execution-verified -- compile with javac, run with java.
# Batch (DEFAULT): the whole catalogue x variants in ONE container + main.
# The full run-model rationale (session fixture builds once, parametrized
# lookup, the mandatory xdist_group pin) lives in CLAUDE.md, section
# "Execution tests: batch vs exhaustive".
# ─────────────────────────────────────────────────────────────────────


def _java_batch_cases() -> list[tuple[str, _Variant]]:
    cases: list[tuple[str, _Variant]] = []
    for name in sorted(ALGORITHMS.keys()):
        variants: list[_Variant] = ["bitwise", "table"]
        if ALGORITHMS[name].width in (32, 64):
            variants.append("slice8")
        for v in variants:
            cases.append((name, v))
    return cases


def _java_batch_member_class(name: str, variant: _Variant) -> str:
    """A package-private ``class C_<sym>`` holding one (algo, variant)'s
    members.  Java caps a method (incl. a static initializer) at 64 KB of
    bytecode, so the whole catalogue's tables can't share one class's
    ``<clinit>`` -- give each case its own small class instead (each compiles
    to its own .class with its own small initializer)."""
    sym = f"{_func_name(name)}_{_VARIANT_TAG[variant]}"
    out = generate_java(name, symbol=sym, variant=variant)
    assert out is not None, f"generate_java({name!r}) returned None"
    inner = out.split("{", 1)[1].rsplit("}", 1)[0].strip("\n")
    return f"final class C_{sym} {{\n{inner}\n}}"


def _java_batch_check_method(idx: int, name: str, variant: _Variant) -> str:
    """A ``static void c<idx>()`` running one case + printing its result.

    Split out of ``main`` so ``main`` (164 calls) stays under the 64 KB
    method limit too."""
    sym = f"{_func_name(name)}_{_VARIANT_TAG[variant]}"
    cls = f"C_{sym}"
    jtype = _java_jtype(ALGORITHMS[name].width)
    lit = _java_check_lit(name)
    tag = f"{name}/{variant}"
    return (
        f"    static void c{idx}() {{\n"
        "        try {\n"
        "            String r;\n"
        f"            if (!{cls}.{sym}_self_test()) r = \"FAIL:oneshot\";\n"
        "            else {\n"
        f"                {jtype} s = {cls}.{sym}_init();\n"
        f"                s = {cls}.{sym}_update(s, new byte[] {{ 0x31,0x32,0x33,0x34 }});\n"
        f"                s = {cls}.{sym}_update(s, new byte[] {{ 0x35,0x36,0x37,0x38,0x39 }});\n"
        f"                r = ({cls}.{sym}_finalize(s) == {lit}) ? \"PASS\" : \"FAIL:streaming\";\n"
        "            }\n"
        f"            System.out.println(\"{tag} \" + r);\n"
        f"        }} catch (Throwable e) {{ System.out.println(\"{tag} FAIL:exception\"); }}\n"
        "    }"
    )


def _insert_java_main(container_src: str, main_body: str) -> str:
    """Splice a ``main`` method in before the container's closing brace."""
    head = container_src.rstrip().rstrip("}").rstrip()
    return (
        head
        + "\n\n    public static void main(String[] args) {\n"
        + main_body
        + "\n    }\n}\n"
    )


def _compile_and_run_java(tmp_path, class_name: str, source: str):
    """Write ``<class_name>.java``, javac it, run it; return CompletedProcess
    of the ``java`` run (or fail on compile error)."""
    src_path = tmp_path / f"{class_name}.java"
    src_path.write_text(source, encoding="utf-8")
    comp = subprocess.run(
        [str(_JAVAC), "-d", str(tmp_path), str(src_path)],
        capture_output=True, cwd=tmp_path,
    )
    if comp.returncode != 0:
        pytest.fail(
            f"javac failed for {class_name}:\n"
            + comp.stderr.decode(errors="replace")[:3000]
        )
    return subprocess.run(
        [str(_JAVA), "-cp", str(tmp_path), class_name],
        capture_output=True, cwd=tmp_path,
    )


@pytest.fixture(scope="session")
def java_batch_results(tmp_path_factory) -> dict[str, str]:
    """Generate every (algorithm, variant) under a unique symbol into ONE
    container class with a ``main``, compile + run once, return
    ``{"name/variant": result}``."""
    if not HAS_JDK:
        return {}
    cases = _java_batch_cases()
    member_classes = [_java_batch_member_class(n, v) for n, v in cases]
    check_methods = [
        _java_batch_check_method(i, n, v) for i, (n, v) in enumerate(cases)
    ]
    main_calls = "\n".join(f"        c{i}();" for i in range(len(cases)))
    src = (
        "\n\n".join(member_classes)
        + "\n\npublic final class CrcGlotBatch {\n"
        + "\n".join(check_methods)
        + "\n\n    public static void main(String[] args) {\n"
        + main_calls
        + "\n    }\n}\n"
    )
    d = tmp_path_factory.mktemp("java_batch")
    run = _compile_and_run_java(d, "CrcGlotBatch", src)
    results: dict[str, str] = {}
    for line in run.stdout.decode(errors="replace").splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_JDK, reason="JDK (javac/java) not in PATH")
# One xdist worker so the session-scoped javac build runs once, not per
# worker.  See CLAUDE.md "Execution tests: batch vs exhaustive".
@pytest.mark.xdist_group("java_batch")
@pytest.mark.parametrize("name,variant", _java_batch_cases())
def test_java_batch_execution(name, variant, java_batch_results):
    # Assert -- the single-build driver reported PASS for this case.
    key = f"{name}/{variant}"
    actual = java_batch_results.get(key)
    assert actual == "PASS", (
        f"{key}: expected PASS, got {actual!r} "
        f"(missing => absent from the one-shot batch run's output)"
    )


_MULTI_ALGOS = ["crc8", "crc16-modbus", "crc32", "crc64-xz"]


@pytest.mark.slow
@pytest.mark.skipif(not HAS_JDK, reason="JDK (javac/java) not in PATH")
@pytest.mark.xdist_group("java_multi")
def test_java_combined_multi_algorithm_compiles_and_runs(tmp_path):
    """The CLI's multi-algorithm bundle (combine_java) compiles as one
    container and every self_test passes -- proves mixed int[]/long[]
    tables coexist in one class."""
    outputs = []
    for name in _MULTI_ALGOS:
        out = generate_java(name)
        assert out is not None, f"generate_java({name!r}) returned None"
        outputs.append(out)
    container = LANGUAGES["java"].combiner(outputs, "Bundle")
    checks = "\n".join(
        f"        if (!{crc_function_names(_func_name(n), 'camel')['self_test']}())"
        f" System.exit({i + 1});"
        for i, n in enumerate(_MULTI_ALGOS)
    )
    src = _insert_java_main(container, checks + "\n        System.exit(0);")
    run = _compile_and_run_java(tmp_path, "Bundle", src)
    assert run.returncode == 0, (
        f"bundled self_test #{run.returncode} "
        f"({_MULTI_ALGOS[run.returncode - 1] if 1 <= run.returncode <= 4 else '?'}) "
        f"failed: {run.stderr.decode(errors='replace')}"
    )


_SLICE8_LENGTHS = (0, 1, 7, 8, 9, 15, 16, 100)
_SLICE8_ALGOS = sorted(n for n, a in ALGORITHMS.items() if a.width in (32, 64))


@pytest.mark.slow
@pytest.mark.skipif(not HAS_JDK, reason="JDK (javac/java) not in PATH")
@pytest.mark.xdist_group("java_multi")
@pytest.mark.parametrize("name", _SLICE8_ALGOS)
def test_java_slice8_matches_bitbybit(name, tmp_path):
    """slice-by-8 must agree with bit-by-bit at every input length (the
    bit-by-bit form is reveng-verified, so equivalence proves slice8)."""
    bb = generate_java(name, symbol=f"{_func_name(name)}_bb", variant="bitwise")
    s8 = generate_java(name, symbol=f"{_func_name(name)}_s8", variant="slice8")
    assert bb is not None and s8 is not None, "both forms generated"
    container = LANGUAGES["java"].combiner([bb, s8], "Equiv")
    bbf, s8f = f"{_func_name(name)}_bb", f"{_func_name(name)}_s8"
    lengths = ", ".join(str(n) for n in _SLICE8_LENGTHS)
    main = (
        f"        int[] lengths = {{ {lengths} }};\n"
        "        for (int li = 0; li < lengths.length; li++) {\n"
        "            byte[] buf = new byte[lengths[li]];\n"
        "            for (int i = 0; i < buf.length; i++) buf[i] = (byte)((i * 31 + 7) & 0xFF);\n"
        f"            if ({bbf}(buf) != {s8f}(buf)) System.exit(li + 1);\n"
        "        }\n"
        "        System.exit(0);"
    )
    src = _insert_java_main(container, main)
    run = _compile_and_run_java(tmp_path, "Equiv", src)
    idx = run.returncode
    assert idx == 0, (
        f"{name}: slice8 != bit-by-bit at length "
        f"{_SLICE8_LENGTHS[idx - 1] if 1 <= idx <= len(_SLICE8_LENGTHS) else '?'}: "
        f"{run.stderr.decode(errors='replace')}"
    )
