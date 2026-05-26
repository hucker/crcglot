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

import shutil
import subprocess
import textwrap

import pytest

from crcglot import (
    CRC_CATALOGUE,
    generate_csharp,
    generate_csharp_from_entry,
)


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


def _cs_state_type(width: int) -> str:
    if width <= 8:
        return "byte"
    if width <= 16:
        return "ushort"
    if width <= 32:
        return "uint"
    return "ulong"


class TestGenerateCSharp:
    """generate_csharp returns a single .cs source string."""

    def test_generates_code(self):
        # Act
        code = generate_csharp("crc16-modbus")

        # Assert
        assert code is not None, "generator returned code"
        assert "using System;" in code, "using directive present"
        assert "public static class Crc16Modbus" in code, "PascalCase class"
        assert "public static ushort crc16_modbus(" in code, (
            "one-shot method present"
        )
        assert "0x4B37" in code, "check value embedded"
        assert "public static bool crc16_modbus_self_test()" in code, (
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
        assert "public static byte crc8(" in code, "CRC-8 uses byte"

    def test_crc32_uses_uint_with_suffix(self):
        # Act
        code = generate_csharp("crc32")

        # Assert
        assert code is not None, "generator returned code"
        assert "public static uint crc32(" in code, "CRC-32 uses uint"
        assert "0xFFFFFFFFu" in code, (
            "width-32 hex literals carry the u suffix"
        )

    def test_crc64_uses_ulong_with_suffix(self):
        # Act
        code = generate_csharp("crc64-xz")

        # Assert
        assert code is not None, "generator returned code"
        assert "public static ulong crc64_xz(" in code, "CRC-64 uses ulong"
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
            "class name derives from overridden symbol"
        )
        assert "public static uint my_crc(" in code, "method name overridden"

    def test_table_emits_table_constant(self):
        # Act
        code = generate_csharp("crc32", table=True)

        # Assert
        assert code is not None, "generator returned code"
        assert "private static readonly uint[] _crcTable" in code, (
            "table-driven variant emits the lookup table"
        )

    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_all_catalogue_entries_compile_shape(self, name):
        # Act
        code = generate_csharp(name)

        # Assert
        assert code is not None, f"generate_csharp({name!r}) returned code"
        fname = _func_name(name)
        cls = _pascal(fname)
        assert f"public static class {cls}" in code, (
            f"{name}: class declaration"
        )
        assert f"public static bool {fname}_self_test()" in code, (
            f"{name}: self_test present"
        )


class TestGenerateCSharpFromEntryRefoutBranch:
    """The ``refout != refin`` finalize reflection branch is only
    reachable via generate_csharp_from_entry because no catalogue entry
    has refout differing from refin.
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
        code = generate_csharp_from_entry("synthetic_refout", entry)

        # Assert
        assert "reflect output (refout != refin)" in code, (
            "reflection comment present"
        )
        assert "ushort reflected = 0;" in code, (
            "reflection variable declared"
        )


@pytest.mark.slow
@pytest.mark.skipif(not HAS_DOTNET_SDK, reason="dotnet SDK not available")
class TestGeneratedCSharpExecutes:
    """Compile + run via ``dotnet run`` on a minimal project that
    wraps the generated class and exits 0 iff ``_self_test()`` returns
    true.  Same shape as the C / Rust execution tests.
    """

    @pytest.mark.parametrize("table", [False, True])
    @pytest.mark.parametrize("name", sorted(CRC_CATALOGUE.keys()))
    def test_self_test_passes(self, name, table, tmp_path):
        # Arrange
        code = generate_csharp(name, table=table)
        assert code is not None, f"generate_csharp({name!r}) returned code"
        fname = _func_name(name)
        cls = _pascal(fname)
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
        runner = textwrap.dedent(f"""
            using System;
            public static class Probe
            {{
                public static int Main(string[] args)
                {{
                    return {cls}.{fname}_self_test() ? 0 : 1;
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
        assert result.returncode == 0, (
            f"{name} (table={table}): dotnet run exited "
            f"{result.returncode}; stderr={result.stderr!r}"
        )
