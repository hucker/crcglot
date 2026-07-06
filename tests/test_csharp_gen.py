"""Tests for the C# CRC code generator.

Two layers:

* **Structural** (fast, always run) -- shape checks on the emitted
  source: ``using System;`` directive, ``public static class``
  declaration with the PascalCase'd class name, methods using the
  correct unsigned integer type, the ``u`` / ``UL`` literal suffixes
  for width-32 and width-64 algorithms respectively, self-test method,
  and the ``refout != refin`` finalize reflection branch (reachable
  only via ``generate_csharp_from_entry``).

* **Execution-verified** (marked ``slow``, skipped without ``dotnet``)
  -- synthesizes a self-contained ``Program.cs`` plus minimal
  ``.csproj`` and invokes ``dotnet run`` to compile and execute the
  generated code, asserting against the reveng canonical check value.
  Same pattern as ``test_rust_gen.py``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from typing import Literal

import pytest

from crcglot import (
    ALGORITHMS,
    AlgorithmInfo,
    generate_csharp,
    generate_csharp_from_entry,
    generate_files,
)
from crcglot.lang.csharp import _cs_method_names


def _has_dotnet_sdk() -> bool:
    """``dotnet`` runtime can be on PATH without an SDK -- a runtime-only
    install rejects ``dotnet run`` even though ``shutil.which`` finds the
    exe.  Probe for ``--list-sdks`` returning at least one entry.
    """
    if shutil.which("dotnet") is None:
        return False
    try:
        result = subprocess.run(
            ["dotnet", "--list-sdks"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and result.stdout.strip() != ""


HAS_DOTNET_SDK = _has_dotnet_sdk()


def _func_name(algo: str) -> str:
    return algo.replace("-", "_").replace(".", "_")


def _pascal(fname: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in fname.split("_") if p)


_SLICE8_INPUT_LENGTHS = (0, 1, 7, 8, 9, 15, 16, 100)


def _slice8_algos() -> list[str]:
    """Catalogue algorithms eligible for slice-by-8 (width 32 or 64)."""
    return sorted(
        n for n, a in ALGORITHMS.items() if a.width in (32, 64)
    )


def _cs_state_type(width: int) -> str:
    if width <= 8:
        return "byte"
    if width <= 16:
        return "ushort"
    if width <= 32:
        return "uint"
    return "ulong"


def _cs_class_and_methods(code: str) -> tuple[str, list[str]]:
    """Pull the class name and every static-method name from C# source.

    Matches ``public static <type> <method>(`` (the trailing ``(``
    excludes the ``public static class <Name>`` declaration, which has no
    parameter list), so the returned methods are exactly the callable
    members the compiler will check against the class name.
    """
    cls = re.search(r"public static class (\w+)", code)
    methods = re.findall(r"public static \w+ (\w+)\s*\(", code)
    return (cls.group(1) if cls else ""), methods


class TestCSharpMethodNamesNeverEqualClass:
    """No emitted C# method may share its enclosing class name.

    C# rejects a member whose name equals the enclosing type (error
    CS0542), so a static class ``Crc8`` cannot contain a method ``Crc8``.
    The default one-shot name is the bare algorithm stem, which
    PascalCases to exactly the class name -- the collision that shipped
    non-compiling C# for every algorithm while the batch-execution test
    (which renames every symbol) stayed green.  This is a pure string
    check on the generated source: no toolchain, runs in the fast tier,
    and cannot be skipped for a missing ``dotnet``.
    """

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_default_naming_has_no_collision(self, name):
        # Arrange -- the artifact a user gets from ``crcglot csharp <name>``.
        code = generate_csharp(name)
        assert code is not None, f"generate_csharp({name!r}) returned code"

        # Act
        cls, methods = _cs_class_and_methods(code)
        collisions = [m for m in methods if m == cls]

        # Assert
        assert collisions == [], (
            f"{name}: C# method(s) {collisions} equal the class name "
            f"{cls!r} -- CS0542, will not compile"
        )

    @pytest.mark.parametrize(
        "kwargs",
        [{"name": "MyCrc"}, {"symbol": "my_crc"}, {"symbol": "Crc32"}],
        ids=["name=MyCrc", "symbol=my_crc", "symbol=Crc32"],
    )
    def test_overrides_have_no_collision(self, kwargs):
        # Arrange -- the name= / symbol= user paths, through the public
        # generate_files surface the CLI uses.  symbol="Crc32" is the
        # tricky one: a PascalCase symbol whose verbatim one-shot would
        # equal the PascalCase class.
        code = generate_files("csharp", "crc32", **kwargs)[0].content

        # Act
        cls, methods = _cs_class_and_methods(code)
        collisions = [m for m in methods if m == cls]

        # Assert
        assert collisions == [], (
            f"override {kwargs}: C# method(s) {collisions} equal the class "
            f"name {cls!r} -- CS0542, will not compile"
        )


class TestGenerateCSharp:
    """generate_csharp returns a single .cs source string."""

    def test_generates_code(self):
        # Act
        code = generate_csharp("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "using System;" in code, "using directive present"
        assert "public static class Crc16Modbus" in code, "PascalCase class"
        assert "public static ushort Compute(" in code, (
            "role-only one-shot method present (class namespaces it)"
        )
        assert "0x4B37" in code, "check value embedded"
        assert "public static bool SelfTest()" in code, (
            "self-test method present"
        )

    def test_unknown_algorithm(self):
        # Assert
        assert generate_csharp("nonexistent") is None, (
            "unknown algorithm should return None"
        )

    def test_crc8_uses_byte(self):
        # Act
        code = generate_csharp("crc8")

        # Assert
        assert code is not None, "generator returned code"
        assert "public static byte Compute(" in code, "CRC-8 uses byte"

    def test_crc32_uses_uint_with_suffix(self):
        # Act
        code = generate_csharp("crc32")

        # Assert
        assert code is not None, "generator returned code"
        assert "public static uint Compute(" in code, "CRC-32 uses uint"
        assert "0xFFFFFFFFu" in code, (
            "width-32 hex literals carry the u suffix"
        )

    def test_crc64_uses_ulong_with_suffix(self):
        # Act
        code = generate_csharp("crc64-xz")

        # Assert
        assert code is not None, "generator returned code"
        assert "public static ulong Compute(" in code, "CRC-64 uses ulong"
        # CRC-64/XZ has init=0xFFFFFFFFFFFFFFFF and xorout=0xFFFFFFFFFFFFFFFF
        assert "0xFFFFFFFFFFFFFFFFUL" in code, (
            "width-64 hex literals carry the UL suffix"
        )

    def test_symbol_override(self):
        # Act
        code = generate_csharp("crc32", symbol="my_crc")

        # Assert
        assert code is not None, "generator returned code"
        assert "public static class MyCrc" in code, (
            "symbol= names the class (which namespaces the role-only methods)"
        )
        assert "public static uint Compute(" in code, (
            "methods stay role-only; symbol= renames the class, not the methods"
        )

    def test_table_emits_table_constant(self):
        # Act
        code = generate_csharp("crc32", variant='table')

        # Assert
        assert code is not None, "generator returned code"
        assert "private static readonly uint[] _crcTable" in code, (
            "table-driven variant emits the lookup table"
        )

    def test_slice8_emits_eight_tables(self):
        # Act
        code = generate_csharp("crc32", variant='slice8')

        # Assert
        assert code is not None, "generator returned code"
        assert "private static readonly uint[,] _crcSliceTables" in code, (
            "slice-by-8 variant emits the 2D table"
        )
        for i in range(8):
            assert f"// T{i}" in code, f"slice-by-8 missing T{i} comment"

    @pytest.mark.parametrize("algo", ["crc8", "crc16-modbus"])
    def test_slice8_rejects_narrow_widths(self, algo):
        # Act + Assert
        with pytest.raises(ValueError, match="variant=.slice8. requires width"):
            generate_csharp(algo, variant='slice8')

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_all_catalogue_entries_compile_shape(self, name):
        # Act
        code = generate_csharp(name)

        # Assert
        assert code is not None, f"generate_csharp({name!r}) returned code"
        fname = _func_name(name)
        cls = _pascal(fname)
        names = _cs_method_names("pascal")
        assert f"public static class {cls}" in code, (
            f"{name}: class declaration"
        )
        assert f"public static bool {names['self_test']}()" in code, (
            f"{name}: self_test present"
        )


class TestGenerateCSharpFromEntryRefoutBranch:
    """The ``refout != refin`` finalize reflection branch is only
    reachable via generate_csharp_from_entry because no catalogue entry
    has refout differing from refin.
    """

    def test_refout_differs_from_refin_emits_reflection(self):
        # Arrange
        algo = AlgorithmInfo(
            width=16, poly=0x1021, init=0x0000,
            refin=False, refout=True, xorout=0x0000,
            check=0x0000, desc="synthetic refout!=refin probe",
            source="custom",
        )

        # Act
        code = generate_csharp_from_entry("synthetic_refout", algo)

        # Assert
        assert "reflect output (refout != refin)" in code, (
            "reflection comment present"
        )
        assert "ushort reflected = 0;" in code, (
            "reflection variable declared"
        )


_CSHARP_EXIT_CODE_LABEL = {
    0: "(all checks passed)",
    1: "_self_test failed (one-shot check value wrong)",
    2: "split-at-4 streamed result wrong",
    3: "empty-chunk-first streamed result wrong",
    4: "empty-chunk-last streamed result wrong",
}


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_DOTNET_SDK, reason="dotnet SDK not available")
class TestGeneratedCSharpExecutes:
    """Compile + run via ``dotnet run`` on a minimal project that
    runs four checks in one compiled binary:

      1. ``_self_test()`` -- four inputs vs independent references
      2. split-at-4 streaming
      3. empty-chunk-first streaming
      4. empty-chunk-last streaming

    Distinct exit codes 1..4 identify which pattern broke; 0 means
    every pattern matched the catalogue check value.
    """

    @pytest.mark.parametrize("variant", ["bitwise", "table"])
    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_oneshot_and_streaming(self, name, variant, tmp_path):
        # Arrange
        algo = ALGORITHMS[name]
        expected = algo.check
        cstype = _cs_state_type(algo.width)
        suffix = "u" if 16 < algo.width <= 32 else (
            "UL" if algo.width > 32 else ""
        )
        code = generate_csharp(name, variant=variant)
        assert code is not None, f"generate_csharp({name!r}) returned code"
        fname = _func_name(name)
        cls = _pascal(fname)
        names = _cs_method_names("pascal")
        proj = tmp_path / "Probe.csproj"
        proj.write_text(textwrap.dedent("""
            <Project Sdk="Microsoft.NET.Sdk">
              <PropertyGroup>
                <OutputType>Exe</OutputType>
                <TargetFramework>net8.0</TargetFramework>
                <Nullable>disable</Nullable>
                <RootNamespace>Probe</RootNamespace>
            </PropertyGroup>
            </Project>
        """).strip(), encoding="utf-8")
        gen_path = tmp_path / "Gen.cs"
        gen_path.write_text(code, encoding="utf-8")
        ascii_one_to_nine = (
            "new byte[] { 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39 }"
        )
        ascii_one_to_four = "new byte[] { 0x31, 0x32, 0x33, 0x34 }"
        ascii_five_to_nine = "new byte[] { 0x35, 0x36, 0x37, 0x38, 0x39 }"
        ascii_empty = "new byte[] { }"
        runner = textwrap.dedent(f"""
            using System;
            public static class Probe
            {{
                public static int Main(string[] args)
                {{
                    {cstype} expected = {hex(expected)}{suffix};
                    if (!{cls}.{names['self_test']}()) return 1;
                    {cstype} s;
                    // split-at-4
                    s = {cls}.{names['init']}();
                    s = {cls}.{names['update']}(s, {ascii_one_to_four});
                    s = {cls}.{names['update']}(s, {ascii_five_to_nine});
                    if ({cls}.{names['finalize']}(s) != expected) return 2;
                    // empty-chunk-first
                    s = {cls}.{names['init']}();
                    s = {cls}.{names['update']}(s, {ascii_empty});
                    s = {cls}.{names['update']}(s, {ascii_one_to_nine});
                    if ({cls}.{names['finalize']}(s) != expected) return 3;
                    // empty-chunk-last
                    s = {cls}.{names['init']}();
                    s = {cls}.{names['update']}(s, {ascii_one_to_nine});
                    s = {cls}.{names['update']}(s, {ascii_empty});
                    if ({cls}.{names['finalize']}(s) != expected) return 4;
                    return 0;
                }}
            }}
        """).strip()
        (tmp_path / "Program.cs").write_text(runner, encoding="utf-8")

        # Act
        result = subprocess.run(
            ["dotnet", "run", "--project", str(proj), "-c", "Release"],
            capture_output=True, text=True, timeout=120,
            cwd=tmp_path,
        )

        # Assert
        label = _CSHARP_EXIT_CODE_LABEL.get(
            result.returncode, "(compile or runtime error)"
        )
        assert result.returncode == 0, (
            f"{name} (variant={variant}): dotnet run exited "
            f"{result.returncode} {label}; stderr={result.stderr!r}"
        )


@pytest.mark.exhaustive
@pytest.mark.slow
@pytest.mark.skipif(not HAS_DOTNET_SDK, reason="dotnet SDK not available")
class TestGeneratedCSharpSliceBy8Executes:
    """Slice-by-8 equivalence with bit-by-bit in generated C#.

    Generates both forms under disjoint symbol names (so the two
    PascalCase'd class names don't collide), compiles them into one
    .NET project, and asserts byte-equal output across a range of
    input lengths.  Since bit-by-bit is reveng-verified, equivalence
    proves slice-by-8 is correct.

    Limited to CRC-32 / CRC-64 algorithms; slice-by-8 only makes
    sense at those widths (the generator raises ValueError otherwise).
    """

    @pytest.mark.parametrize("name", _slice8_algos())
    def test_slice8_matches_bitbybit(self, name, tmp_path):
        # Arrange -- generate two .cs files with disjoint symbol names
        # so the two PascalCase'd class names don't collide.
        bb_sym = f"{_func_name(name)}_bb"
        s8_sym = f"{_func_name(name)}_s8"
        bb_code = generate_csharp(name, symbol=bb_sym)
        s8_code = generate_csharp(name, variant='slice8', symbol=s8_sym)
        assert bb_code is not None, f"generate_csharp({name!r}) returned None"
        assert s8_code is not None, (
            f"generate_csharp({name!r}, variant='slice8') returned None"
        )
        bb_cls = _pascal(bb_sym)
        s8_cls = _pascal(s8_sym)
        oneshot = _cs_method_names("pascal")["oneshot"]
        cstype = _cs_state_type(ALGORITHMS[name].width)

        proj = tmp_path / "Probe.csproj"
        proj.write_text(textwrap.dedent("""
            <Project Sdk="Microsoft.NET.Sdk">
              <PropertyGroup>
                <OutputType>Exe</OutputType>
                <TargetFramework>net8.0</TargetFramework>
                <Nullable>disable</Nullable>
                <RootNamespace>Probe</RootNamespace>
            </PropertyGroup>
            </Project>
        """).strip(), encoding="utf-8")
        (tmp_path / "Bb.cs").write_text(bb_code, encoding="utf-8")
        (tmp_path / "S8.cs").write_text(s8_code, encoding="utf-8")

        lengths_csv = ", ".join(str(n) for n in _SLICE8_INPUT_LENGTHS)
        runner = textwrap.dedent(f"""
            using System;
            public static class Probe
            {{
                public static int Main(string[] args)
                {{
                    var buf = new byte[256];
                    for (int k = 0; k < 256; k++) buf[k] = (byte)k;
                    int[] lengths = new int[] {{ {lengths_csv} }};
                    for (int li = 0; li < lengths.Length; li++)
                    {{
                        int n = lengths[li];
                        var slice = new byte[n];
                        Array.Copy(buf, slice, n);
                        {cstype} bb = {bb_cls}.{oneshot}(slice);
                        {cstype} s8 = {s8_cls}.{oneshot}(slice);
                        if (bb != s8) return li + 1;
                    }}
                    return 0;
                }}
            }}
        """).strip()
        (tmp_path / "Program.cs").write_text(runner, encoding="utf-8")

        # Act
        result = subprocess.run(
            ["dotnet", "run", "--project", str(proj), "-c", "Release"],
            capture_output=True, text=True, timeout=120,
            cwd=tmp_path,
        )

        # Assert -- exit 0 means slice-by-8 == bit-by-bit at every length;
        # nonzero index identifies which input length disagreed.
        assert result.returncode == 0, (
            f"{name}: dotnet run exited {result.returncode} "
            f"(length index, 0 = ok); stderr={result.stderr!r}"
        )


# ─────────────────────────────────────────────────────────────────────
# Batch execution -- whole catalogue x every variant as one project, built
# + run in ONE dotnet invocation instead of one per case.  DEFAULT path;
# the per-algorithm classes above are kept behind ``exhaustive`` for
# isolation.  (C# classes are already collision-free -- private _crcTable
# per class -- so this needs no Phase-1 change; it's the same harness shape
# for parity.)  Full rationale incl. the mandatory ``xdist_group`` pin is in
# CLAUDE.md, "Execution tests: batch vs exhaustive".
# ─────────────────────────────────────────────────────────────────────

_CsVariant = Literal["bitwise", "table", "slice8"]
_CS_VARIANT_TAG: dict[_CsVariant, str] = {"bitwise": "b", "table": "t", "slice8": "s8"}


def _csharp_batch_cases() -> list[tuple[str, _CsVariant]]:
    """(name, variant) for every algorithm x supported C# variant."""
    cases: list[tuple[str, _CsVariant]] = []
    for name in sorted(ALGORITHMS.keys()):
        variants: list[_CsVariant] = ["bitwise", "table"]
        if ALGORITHMS[name].width in (32, 64):
            variants.append("slice8")
        for v in variants:
            cases.append((name, v))
    return cases


def _cs_check_literal(width: int, check: int) -> str:
    """C# literal for the check value, suffixed to match the state type."""
    suffix = "u" if 16 < width <= 32 else ("UL" if width > 32 else "")
    return f"{hex(check)}{suffix}"


def _csharp_batch_driver_case(name: str, variant: _CsVariant) -> str:
    """One C# block: <Cls>.SelfTest() + split-streaming + byte-at-a-time
    checks, printing ``<name>/<variant> PASS|FAIL:<phase>``.

    Each case is generated under a unique ``symbol=`` so the *class*
    names stay distinct in the one concatenated project; the methods are
    role-only (``SelfTest`` / ``Init`` / ``Update`` / ``Finalize``), so
    the class qualifier is what disambiguates them across algorithms.
    """
    sym = f"{_func_name(name)}_{_CS_VARIANT_TAG[variant]}"
    cls = _pascal(sym)
    n = _cs_method_names("pascal")
    algo = ALGORITHMS[name]
    cstype = _cs_state_type(algo.width)
    lit = _cs_check_literal(algo.width, algo.check)
    tag = f"{name}/{variant}"
    return (
        "            try {\n"
        f"                {cstype} expected = {lit};\n"
        "                string r;\n"
        f"                if (!{cls}.{n['self_test']}()) {{ r = \"FAIL:oneshot\"; }}\n"
        "                else {\n"
        f"                    {cstype} s = {cls}.{n['init']}();\n"
        f"                    {cstype} s2 = {cls}.{n['init']}();\n"
        f"                    s = {cls}.{n['update']}(s, FULL04);\n"
        f"                    s = {cls}.{n['update']}(s, FULL49);\n"
        "                    for (int i = 0; i < FULL.Length; i++) {\n"
        f"                        s2 = {cls}.{n['update']}(s2, new byte[] {{ FULL[i] }});\n"
        "                    }\n"
        f"                    if ({cls}.{n['finalize']}(s) != expected) {{ r = \"FAIL:streaming\"; }}\n"
        f"                    else if ({cls}.{n['finalize']}(s2) != expected) {{ r = \"FAIL:bytewise\"; }}\n"
        "                    else { r = \"PASS\"; }\n"
        "                }\n"
        f"                Console.WriteLine(\"{tag} \" + r);\n"
        f"            }} catch {{ Console.WriteLine(\"{tag} FAIL:exception\"); }}"
    )


@pytest.fixture(scope="session")
def csharp_batch_results(tmp_path_factory) -> dict[str, str]:
    """Generate every (algorithm, variant) under a unique symbol/class into
    one project, build + run once, return ``{"name/variant": result}``."""
    if not HAS_DOTNET_SDK:
        return {}
    cases = _csharp_batch_cases()
    d = tmp_path_factory.mktemp("cs_batch")
    (d / "Probe.csproj").write_text(textwrap.dedent("""
        <Project Sdk="Microsoft.NET.Sdk">
          <PropertyGroup>
            <OutputType>Exe</OutputType>
            <TargetFramework>net8.0</TargetFramework>
            <Nullable>disable</Nullable>
            <RootNamespace>Probe</RootNamespace>
          </PropertyGroup>
        </Project>
    """).strip(), encoding="utf-8")
    driver = []
    for i, (name, variant) in enumerate(cases):
        sym = f"{_func_name(name)}_{_CS_VARIANT_TAG[variant]}"
        code = generate_csharp(name, symbol=sym, variant=variant)
        assert code is not None, f"generate_csharp({name!r}) returned None"
        (d / f"Gen{i}.cs").write_text(code, encoding="utf-8")
        driver.append(_csharp_batch_driver_case(name, variant))
    program = (
        "using System;\npublic static class Probe {\n"
        "    public static void Main() {\n"
        "        byte[] FULL04 = new byte[] { 0x31, 0x32, 0x33, 0x34 };\n"
        "        byte[] FULL49 = new byte[] { 0x35, 0x36, 0x37, 0x38, 0x39 };\n"
        "        byte[] FULL = new byte[] { 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39 };\n"
        + "\n".join(driver)
        + "\n    }\n}\n"
    )
    (d / "Program.cs").write_text(program, encoding="utf-8")
    proc = subprocess.run(
        ["dotnet", "run", "--project", str(d / "Probe.csproj")],
        capture_output=True, text=True, timeout=600, cwd=d,
    )
    if proc.returncode != 0:
        pytest.fail(
            "C# batch failed to build/run (a collision or codegen error):\n"
            + proc.stderr[:3000] + proc.stdout[:1500]
        )
    results: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_DOTNET_SDK, reason="dotnet SDK not available")
# One xdist worker so the session-scoped dotnet build runs once, not per
# worker.  See CLAUDE.md "Execution tests: batch vs exhaustive".
@pytest.mark.xdist_group("csharp_batch")
@pytest.mark.parametrize("name,variant", _csharp_batch_cases())
def test_csharp_batch_execution(name, variant, csharp_batch_results):
    # Assert -- the single-build driver reported PASS for this case.
    key = f"{name}/{variant}"
    actual = csharp_batch_results.get(key)
    assert actual == "PASS", (
        f"{key}: expected PASS, got {actual!r} "
        f"(missing => absent from the one-shot batch run's output)"
    )


# ─────────────────────────────────────────────────────────────────────
# Asymmetric custom execution -- see the matching section in test_c_gen.py:
# refin != refout in the direction crc12-umts does not cover, plus the
# reflect+XOR finalize, compiled and graded against two-oracle values.
# ─────────────────────────────────────────────────────────────────────

_ASYM_IDS = ["refin-only", "refout-only-xor"]


@pytest.fixture(scope="session")
def csharp_asymmetric_results(asymmetric_oracle_cases, tmp_path_factory) -> dict[str, str]:
    """Build both refin != refout customs plus an oracle-literal driver as
    one dotnet project, run once, return ``{label: "PASS"|"FAIL"}``."""
    if not HAS_DOTNET_SDK:
        return {}
    d = tmp_path_factory.mktemp("cs_asym")
    (d / "ProbeAsym.csproj").write_text(textwrap.dedent("""
        <Project Sdk="Microsoft.NET.Sdk">
          <PropertyGroup>
            <OutputType>Exe</OutputType>
            <TargetFramework>net8.0</TargetFramework>
            <Nullable>disable</Nullable>
            <RootNamespace>ProbeAsym</RootNamespace>
          </PropertyGroup>
        </Project>
    """).strip(), encoding="utf-8")
    n = _cs_method_names("pascal")
    driver = []
    for i, (label, algo, oracle) in enumerate(asymmetric_oracle_cases):
        sym = "asym_" + label.replace("-", "_")
        code = generate_csharp_from_entry(sym, algo, symbol=sym)
        assert code is not None, f"generate_csharp_from_entry({label!r}) returned None"
        (d / f"Gen{i}.cs").write_text(code, encoding="utf-8")
        cls = _pascal(sym)
        cstype = _cs_state_type(algo.width)
        lit = _cs_check_literal(algo.width, oracle)
        driver.append(
            "            try {\n"
            f"                {cstype} s = {cls}.{n['init']}();\n"
            f"                s = {cls}.{n['update']}(s, FULL);\n"
            f"                Console.WriteLine(\"{label} \" + "
            f"(({cls}.{n['finalize']}(s) == {lit}) ? \"PASS\" : \"FAIL\"));\n"
            f"            }} catch {{ Console.WriteLine(\"{label} FAIL:exception\"); }}"
        )
    program = (
        "using System;\npublic static class ProbeAsym {\n"
        "    public static void Main() {\n"
        "        byte[] FULL = new byte[] { 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39 };\n"
        + "\n".join(driver)
        + "\n    }\n}\n"
    )
    (d / "Program.cs").write_text(program, encoding="utf-8")
    proc = subprocess.run(
        ["dotnet", "run", "--project", str(d / "ProbeAsym.csproj")],
        capture_output=True, text=True, timeout=600, cwd=d,
    )
    if proc.returncode != 0:
        pytest.fail(
            "asymmetric custom C# failed to build/run:\n"
            + proc.stderr[:3000] + proc.stdout[:1500]
        )
    results: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        key, _, res = line.strip().rpartition(" ")
        if key:
            results[key] = res
    return results


@pytest.mark.slow
@pytest.mark.skipif(not HAS_DOTNET_SDK, reason="dotnet SDK not in PATH")
@pytest.mark.xdist_group("csharp_batch")
class TestAsymmetricCustomExecution:
    """Compiled C# for ``refin != refout`` customs must reproduce the value
    two independent oracles agreed on -- the asymmetry direction and the
    reflect+XOR finalize that no catalogue algorithm reaches."""

    @pytest.mark.parametrize("idx", [0, 1], ids=_ASYM_IDS)
    def test_generated_code_matches_oracle(
        self, idx, asymmetric_oracle_cases, csharp_asymmetric_results
    ):
        # Assert -- the single-build driver reported PASS for this custom.
        label, _algo, oracle = asymmetric_oracle_cases[idx]
        actual = csharp_asymmetric_results.get(label)
        assert actual == "PASS", (
            f"{label}: compiled C# disagreed with the two-oracle value "
            f"0x{oracle:X} (got {actual!r}; missing => absent from driver output)"
        )
