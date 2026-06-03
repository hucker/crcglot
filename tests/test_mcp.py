"""Tests for the FastMCP server in :mod:`crcglot.mcp`.

In-process via ``FastMCP.call_tool`` / ``read_resource`` -- no
subprocess, no stdio loop, mirrors how :mod:`tests.test_cli` exercises
the CLI by calling ``main(argv=...)`` in-process.

Per-tool: one happy path + one error path.  Per-resource: deserialize
and check expected top-level keys.  The MCP layer is transport
adaptation; the underlying CRC engines and generators already have
~2,900 tests of their own, so we don't re-cover that ground here.

Coverage target ≥80% per CLAUDE.md.

See also: ``CLAUDE.md`` "Skipped tests are not 'passed'" -- there
should be zero skips in this file.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys

import pytest

from crcglot import ALGORITHMS, LANGUAGES
from crcglot.mcp.server import build_server


def _run(coro):
    """Run an async function on a fresh event loop.

    Avoids requiring ``pytest-asyncio`` (one fewer dev dep) by giving
    each test its own loop -- FastMCP's tool dispatch is fully async.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


def _call(tool: str, args: dict) -> dict:
    """Dispatch ``tool`` with ``args`` and return the structured payload.

    ``FastMCP.call_tool`` returns ``(content_blocks, structured_dict)``
    -- the dict is what every caller actually wants.
    """
    mcp = build_server()
    _, payload = _run(mcp.call_tool(tool, args))
    return payload


# ---------------------------------------------------------------------------
# crc_list
# ---------------------------------------------------------------------------


class TestCrcList:
    """`crc_list` mirrors ``crcglot list [glob]``."""

    def test_unfiltered_returns_full_catalogue(self):
        # Act
        out = _call("crc_list", {})

        # Assert
        actual_count = out["count"]
        expected_count = len(ALGORITHMS)
        assert actual_count == expected_count, (
            f"expected {expected_count} entries, got {actual_count}"
        )
        names = {a["name"] for a in out["algorithms"]}
        assert "crc32" in names, "crc32 missing from full list"
        assert "crc16-usb" in names, "crc16-usb missing from full list"

    def test_glob_narrows_to_family(self):
        # Act
        out = _call("crc_list", {"glob": "crc16-*"})

        # Assert -- every returned name starts with crc16-.
        prefixes = {a["name"].split("-", 1)[0] for a in out["algorithms"]}
        assert prefixes == {"crc16"}, (
            f"expected only crc16-* names, got prefixes {prefixes}"
        )

    def test_unmatched_glob_returns_empty_not_error(self):
        # Act
        out = _call("crc_list", {"glob": "nope-*"})

        # Assert
        assert out["count"] == 0, f"expected 0 entries, got {out['count']}"
        assert out["algorithms"] == [], "expected empty list"


# ---------------------------------------------------------------------------
# crc_info
# ---------------------------------------------------------------------------


class TestCrcInfo:
    """`crc_info` mirrors ``crcglot info <name>``."""

    def test_known_algorithm_returns_full_dict(self):
        # Act
        out = _call("crc_info", {"name": "crc32"})

        # Assert
        assert out["name"] == "crc32", "name field roundtrip"
        assert out["width"] == 32, "width"
        assert out["poly"] == 0x04C11DB7, "poly decimal"
        assert out["poly_hex"] == "0x04C11DB7", "poly hex form"
        assert out["check"] == 0xCBF43926, "reveng check value"
        assert out["check_hex"] == "0xCBF43926", "check hex form"

    def test_unknown_algorithm_raises(self):
        # Assert
        with pytest.raises(Exception, match="unknown algorithm"):
            _call("crc_info", {"name": "nope-not-a-crc"})


# ---------------------------------------------------------------------------
# crc_detect
# ---------------------------------------------------------------------------


class TestCrcDetect:
    """`crc_detect` mirrors ``crcglot detect`` plus exposes the full
    Python API (``target_crc``, ``endian``, ``algorithms``, ``match``).
    """

    def test_hex_packet_identifies_crc32(self):
        # Arrange -- canonical crc32 packet for b"123456789".
        # Act
        out = _call(
            "crc_detect",
            {"packet_hex": "31 32 33 34 35 36 37 38 39 cb f4 39 26"},
        )

        # Assert
        assert out["matched"], f"expected match, got {out}"
        assert out["candidates"][0]["algorithm"] == "crc32", (
            f"expected crc32, got {out['candidates'][0]['algorithm']}"
        )

    def test_output_uses_crc_byte_order_not_endianness(self):
        # Arrange / Act
        out = _call(
            "crc_detect",
            {"packet_hex": "31 32 33 34 35 36 37 38 39 cb f4 39 26"},
        )

        # Assert -- LLM-misread guard: the wire format relabel from
        # endianness -> crc_byte_order is the whole point.
        candidate = out["candidates"][0]
        assert "crc_byte_order" in candidate, (
            f"output must use crc_byte_order, got keys: {list(candidate)}"
        )
        assert "endianness" not in candidate, (
            f"output must not use 'endianness' (LLM-misread risk); "
            f"got keys: {list(candidate)}"
        )

    def test_target_crc_decimal_via_int_kwarg(self):
        # Arrange -- pass CRC out of band as decimal.
        # Act
        out = _call(
            "crc_detect",
            {"packet_hex": "313233343536373839", "target_crc": 0xCBF43926},
        )

        # Assert
        assert out["matched"]
        assert out["candidates"][0]["algorithm"] == "crc32"

    def test_target_crc_hex_via_hex_kwarg(self):
        # Arrange -- same value as a hex string ("0x..." form).
        # Act
        out = _call(
            "crc_detect",
            {
                "packet_hex": "313233343536373839",
                "target_crc_hex": "0xCBF43926",
            },
        )

        # Assert
        assert out["matched"]
        assert out["candidates"][0]["algorithm"] == "crc32"

    def test_target_crc_both_forms_rejected(self):
        # Assert
        with pytest.raises(Exception, match="mutually exclusive"):
            _call(
                "crc_detect",
                {
                    "packet_hex": "313233343536373839",
                    "target_crc": 0xCBF43926,
                    "target_crc_hex": "0xCBF43926",
                },
            )

    def test_no_packet_form_supplied_rejected(self):
        # Assert
        with pytest.raises(Exception, match="must supply exactly one"):
            _call("crc_detect", {})

    def test_text_packet_form(self):
        # Arrange -- the same canonical packet as "data sep hex".
        # Act
        out = _call("crc_detect", {"packet_text": "123456789 cbf43926"})

        # Assert
        assert out["matched"]
        assert out["candidates"][0]["algorithm"] == "crc32"

    def test_b64_packet_form(self):
        # Arrange
        raw = b"123456789" + (0xCBF43926).to_bytes(4, "big")
        b64 = base64.b64encode(raw).decode("ascii")
        # Act
        out = _call("crc_detect", {"packet_b64": b64})
        # Assert
        assert out["matched"]
        assert out["candidates"][0]["algorithm"] == "crc32"

    def test_endian_narrowing(self):
        # Arrange -- BE packet; under endian='little' the natural reading
        # is suppressed so crc32 shouldn't surface.
        # Act
        out = _call(
            "crc_detect",
            {
                "packet_hex": "31 32 33 34 35 36 37 38 39 cb f4 39 26",
                "endian": "little",
                "match": "all",
            },
        )
        # Assert
        algos = {c["algorithm"] for c in out["candidates"]}
        assert "crc32" not in algos, (
            f"endian='little' should not match BE crc32 packet: {algos}"
        )


# ---------------------------------------------------------------------------
# crc_encode
# ---------------------------------------------------------------------------


class TestCrcEncode:
    """`crc_encode` mirrors ``crcglot encode`` (and round-trips with
    `crc_detect`).
    """

    def test_text_round_trip(self):
        # Arrange / Act
        enc = _call(
            "crc_encode",
            {"algorithm": "crc32", "data_text": "123456789"},
        )

        # Assert
        actual_text = enc["packet_text"]
        expected_text = "123456789 cbf43926"
        assert actual_text == expected_text, (
            f"text round-trip mismatch: {actual_text!r} vs {expected_text!r}"
        )
        # Pipe the result back through detect -- the round-trip pair invariant.
        det = _call("crc_detect", {"packet_text": actual_text})
        assert det["matched"]
        assert det["candidates"][0]["algorithm"] == "crc32"

    def test_binary_round_trip_via_b64(self):
        # Arrange
        b64_in = base64.b64encode(b"123456789").decode("ascii")
        # Act
        enc = _call(
            "crc_encode",
            {"algorithm": "crc32", "data_b64": b64_in},
        )
        # Assert
        actual_hex = enc["packet_hex"]
        expected_hex = b"123456789".hex() + "cbf43926"
        assert actual_hex == expected_hex, (
            f"binary hex mismatch: {actual_hex} vs {expected_hex}"
        )
        # And the base64 of the packet decodes back to the right bytes.
        round_trip = base64.b64decode(enc["packet_b64"])
        assert round_trip[:9] == b"123456789"
        assert int.from_bytes(round_trip[9:], "big") == 0xCBF43926

    def test_text_uppercase(self):
        # Act
        enc = _call(
            "crc_encode",
            {
                "algorithm": "crc32",
                "data_text": "123456789",
                "uppercase": True,
            },
        )
        # Assert
        assert enc["packet_text"] == "123456789 CBF43926", (
            f"uppercase mismatch: {enc['packet_text']!r}"
        )

    def test_unknown_algorithm_rejected(self):
        with pytest.raises(Exception, match="unknown algorithm"):
            _call(
                "crc_encode",
                {"algorithm": "nope", "data_text": "x"},
            )

    def test_both_data_forms_rejected(self):
        with pytest.raises(Exception, match="exactly one"):
            _call(
                "crc_encode",
                {
                    "algorithm": "crc32",
                    "data_text": "x",
                    "data_b64": base64.b64encode(b"x").decode("ascii"),
                },
            )


# ---------------------------------------------------------------------------
# crc_compute
# ---------------------------------------------------------------------------


class TestCrcCompute:
    """`crc_compute` mirrors the new ``crcglot compute`` CLI subcommand."""

    def test_canonical_reveng_value(self):
        # Act
        out = _call(
            "crc_compute",
            {"algorithm": "crc32", "data_text": "123456789"},
        )
        # Assert
        assert out["crc"] == 0xCBF43926
        assert out["crc_hex"] == "0xCBF43926"
        assert out["width"] == 32

    def test_b64_input_matches_text(self):
        # Arrange
        b64 = base64.b64encode(b"123456789").decode("ascii")
        # Act
        out = _call(
            "crc_compute",
            {"algorithm": "crc32", "data_b64": b64},
        )
        # Assert
        assert out["crc"] == 0xCBF43926

    def test_unknown_algorithm_rejected(self):
        with pytest.raises(Exception, match="unknown algorithm"):
            _call("crc_compute", {"algorithm": "nope", "data_text": "x"})

    def test_no_data_rejected(self):
        with pytest.raises(Exception, match="exactly one"):
            _call("crc_compute", {"algorithm": "crc32"})


# ---------------------------------------------------------------------------
# crc_generate
# ---------------------------------------------------------------------------


class TestCrcGenerate:
    """`crc_generate` collapses the 8 per-language CLI subcommands into
    one MCP tool with a ``language`` enum.
    """

    @pytest.mark.parametrize(
        "language",
        ["c", "csharp", "go", "python", "rust", "typescript", "verilog", "vhdl"],
    )
    def test_each_language_emits_files(self, language):
        # Act
        out = _call(
            "crc_generate",
            {"language": language, "algorithm": "crc32"},
        )
        # Assert
        assert out["language"] == language
        assert out["files"], f"{language}: expected at least one file"
        for f in out["files"]:
            assert f["content"], f"{language}: file content must be non-empty"
            assert f["extension"].startswith("."), (
                f"{language}: extension should be like '.c', got {f['extension']!r}"
            )

    def test_c_emits_header_plus_source(self):
        # Act -- C is the only target that ships two files (.h + .c).
        out = _call(
            "crc_generate",
            {"language": "c", "algorithm": "crc32"},
        )
        # Assert
        exts = sorted(f["extension"] for f in out["files"])
        assert exts == [".c", ".h"], f"C should emit .h + .c, got {exts}"

    def test_invalid_variant_returns_structured_error(self):
        # Act / Assert -- variant=slice8 is invalid for python.
        with pytest.raises(Exception, match="variant"):
            _call(
                "crc_generate",
                {
                    "language": "python",
                    "algorithm": "crc32",
                    "variant": "slice8",
                },
            )

    def test_slice8_on_width_16_rejected(self):
        # Act / Assert -- variant=slice8 is invalid at width 16.
        with pytest.raises(Exception, match="variant"):
            _call(
                "crc_generate",
                {
                    "language": "c",
                    "algorithm": "crc16-modbus",
                    "variant": "slice8",
                },
            )

    def test_custom_params_path(self):
        # Act -- custom CRC-16 (xmodem-equivalent params).
        out = _call(
            "crc_generate",
            {
                "language": "c",
                "custom_params": {
                    "width": 16, "poly": 0x1021, "init": 0x0000,
                    "refin": False, "refout": False, "xorout": 0x0000,
                    "name": "my_xmodem",
                    "desc": "custom xmodem-equivalent",
                },
                "variant": "table",
            },
        )
        # Assert
        assert out["files"], "expected files from custom_params path"
        # Header content should reference the custom name.
        header_content = next(
            f["content"] for f in out["files"] if f["extension"] == ".h"
        )
        assert "my_xmodem" in header_content, (
            "custom name should appear in generated header"
        )

    def test_both_algorithm_and_custom_params_rejected(self):
        with pytest.raises(Exception, match="exactly one"):
            _call(
                "crc_generate",
                {
                    "language": "c",
                    "algorithm": "crc32",
                    "custom_params": {"width": 32, "poly": 0x04C11DB7},
                },
            )


# ---------------------------------------------------------------------------
# crc_credits
# ---------------------------------------------------------------------------


class TestCrcCredits:
    def test_returns_attribution_text(self):
        # Act
        out = _call("crc_credits", {})
        # Assert
        assert "reveng" in out["attribution"].lower(), (
            "attribution should mention reveng"
        )
        assert "zlib" in out["attribution"].lower(), (
            "attribution should mention zlib"
        )


# ---------------------------------------------------------------------------
# Cross-algorithm coverage
# ---------------------------------------------------------------------------


# Representative slice of the catalogue covering the full (width,
# reflection, engine-path) matrix.  crcglot's main point is supporting
# every published algorithm; tools that work only for crc32 would
# undersell what the library is for.  Each entry hits one of the
# dimensions exercised in the engine:
#
#   - crc8           width=8, refin=refout=False, smallest path
#   - crc8-maxim     width=8, reflected (Dallas 1-Wire / DS18B20 etc.)
#   - crc8-bacnet    width=8, reflected + xorout=0xFF + non-reveng source
#   - crc16-modbus   width=16, reflected, the Modbus RTU workhorse
#   - crc16-usb      width=16, reflected + xorout=0xFFFF (new in v0.10.0)
#   - crc16-xmodem   width=16, normal (non-reflected) -- different code path
#   - crc32          width=32, IEEE -- delegates to zlib fast-path
#   - crc32-bzip2    width=32, normal -- forces the C engine, NOT zlib
#   - crc32-bacnet   width=32, Koopman poly + xorout=0xFFFFFFFF, non-reveng
#                    source -- covers the BACnet large-frame algorithm
#                    distinct from crc32-mef (same poly, xorout=0)
#   - crc64-xz       width=64, reflected -- largest width, xz file format
_REPRESENTATIVE_ALGOS = [
    "crc8",
    "crc8-maxim",
    "crc8-bacnet",
    "crc16-modbus",
    "crc16-usb",
    "crc16-xmodem",
    "crc32",
    "crc32-bzip2",
    "crc32-bacnet",
    "crc64-xz",
]


class TestCrossAlgorithmCoverage:
    """The MCP layer is transport adaptation; the underlying engine is
    correct for every catalogue algorithm (verified in test_catalogue
    / test_detect).  These tests prove the transport works for a
    representative sample across width / reflection / engine-path
    dimensions -- not just the crc32 happy path.

    The reveng-canonical check value for ``b"123456789"`` is the
    ground-truth oracle here; every per-tool assertion compares against
    it directly via ``ALGORITHMS[name].check``.
    """

    @pytest.mark.parametrize("name", _REPRESENTATIVE_ALGOS)
    def test_info_reports_catalogue_check(self, name):
        # Act
        out = _call("crc_info", {"name": name})
        # Assert
        algo = ALGORITHMS[name]
        actual = (out["width"], out["check"], out["poly"], out["init"])
        expected = (algo.width, algo.check, algo.poly, algo.init)
        assert actual == expected, (
            f"{name}: crc_info fields drift from catalogue: "
            f"{actual} vs {expected}"
        )

    @pytest.mark.parametrize("name", _REPRESENTATIVE_ALGOS)
    def test_compute_matches_catalogue_check(self, name):
        # Act -- canonical reveng input.
        out = _call(
            "crc_compute",
            {"algorithm": name, "data_text": "123456789"},
        )
        # Assert
        expected = ALGORITHMS[name].check
        assert out["crc"] == expected, (
            f"{name}: crc_compute returned {out['crc']:#x} "
            f"but catalogue says {expected:#x}"
        )

    @pytest.mark.parametrize("name", _REPRESENTATIVE_ALGOS)
    def test_encode_then_detect_round_trips(self, name):
        # Arrange / Act -- encode_text, then detect what we just built.
        enc = _call(
            "crc_encode",
            {"algorithm": name, "data_text": "123456789"},
        )
        det = _call("crc_detect", {"packet_text": enc["packet_text"]})
        # Assert -- the algorithm we encoded should appear among the
        # detect candidates (match='first' default may return another
        # algorithm with the same canonical (width, check) so we widen).
        det_all = _call(
            "crc_detect",
            {"packet_text": enc["packet_text"], "match": "all"},
        )
        actual_algos = {c["algorithm"] for c in det_all["candidates"]}
        assert name in actual_algos, (
            f"{name}: encoded packet should round-trip through detect; "
            f"detect candidates = {actual_algos}"
        )
        # And the first-mode result for the canonical packet should at
        # least match SOMETHING -- guards against silent transport
        # failures even when first-mode happens to pick a different
        # algorithm in the same equivalence class.
        assert det["matched"], (
            f"{name}: detect(match='first') failed on encoded packet"
        )

    @pytest.mark.parametrize(
        "name,expected_width",
        [
            ("crc8", 8),
            ("crc16-modbus", 16),
            ("crc32", 32),
            ("crc64-xz", 64),
        ],
    )
    def test_catalogue_resource_surfaces_all_widths(self, name, expected_width):
        # Act
        data = _read_resource("crcglot://catalogue.json")
        # Assert -- the resource serializes every width band.
        assert name in data["algorithms"], (
            f"catalogue resource missing {name}"
        )
        assert data["algorithms"][name]["width"] == expected_width, (
            f"{name}: resource width mismatch"
        )

    @pytest.mark.parametrize(
        "name,language",
        [
            ("crc8", "c"),
            ("crc16-modbus", "rust"),
            ("crc32-bzip2", "go"),
            ("crc64-xz", "python"),
        ],
    )
    def test_generate_works_for_non_crc32_combinations(self, name, language):
        # Act -- 4 (algorithm, language) cells that aren't the crc32/c
        # happy path: cover the smallest width, a non-IEEE 16-bit, a
        # non-reflected 32-bit, and the largest width in pure Python.
        out = _call(
            "crc_generate",
            {"language": language, "algorithm": name, "variant": "bitwise"},
        )
        # Assert
        assert out["files"], f"{language}/{name}: generator returned no files"
        for f in out["files"]:
            assert f["content"], (
                f"{language}/{name}: empty file content"
            )

    @pytest.mark.parametrize(
        "name,crc_byte_order",
        [
            ("crc8-maxim", "big"),    # 1-byte CRC: BE / LE byte-identical
            ("crc16-usb", "big"),
            ("crc16-usb", "little"),
            ("crc32", "big"),
            ("crc32", "little"),
            ("crc64-xz", "big"),
            ("crc64-xz", "little"),
        ],
    )
    def test_encode_byte_order_round_trips_through_detect(
        self, name, crc_byte_order,
    ):
        # Arrange / Act -- binary mode covers the byte-order question
        # cleanly (text-mode hex obscures it).
        b64_in = base64.b64encode(b"123456789").decode("ascii")
        enc = _call(
            "crc_encode",
            {
                "algorithm": name,
                "data_b64": b64_in,
                "crc_byte_order": crc_byte_order,
            },
        )
        det = _call(
            "crc_detect",
            {"packet_hex": enc["packet_hex"], "match": "all"},
        )
        # Assert -- the algorithm we encoded surfaces; its
        # crc_byte_order matches what we asked for.
        candidates = [
            c for c in det["candidates"]
            if c["algorithm"] == name
            and c["crc_byte_order"] == crc_byte_order
        ]
        # For width-1 algos (crc8-*), the byte ordering is moot and
        # detect collapses to "big" regardless of the input flag.
        if ALGORITHMS[name].width == 8:
            candidates = [
                c for c in det["candidates"]
                if c["algorithm"] == name and c["crc_byte_order"] == "big"
            ]
        assert candidates, (
            f"{name}/{crc_byte_order}: encode-detect round-trip lost the "
            f"algorithm; detect saw {det['candidates']}"
        )


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def _read_resource(uri: str) -> dict:
    mcp = build_server()
    contents = _run(mcp.read_resource(uri))
    # ``read_resource`` returns an iterable of ReadResourceContents; we
    # only ever register one chunk per URI, so first one wins.
    first = next(iter(contents))
    return json.loads(first.content)


class TestResources:
    """Each resource serializes to JSON with expected top-level keys."""

    def test_catalogue_resource(self):
        # Act
        data = _read_resource("crcglot://catalogue.json")
        # Assert
        assert "algorithms" in data
        assert data["count"] == len(ALGORITHMS)
        assert "crc32" in data["algorithms"], "crc32 missing from catalogue resource"
        assert data["algorithms"]["crc32"]["poly_hex"] == "0x04C11DB7"

    def test_languages_resource(self):
        # Act
        data = _read_resource("crcglot://languages.json")
        # Assert
        assert "languages" in data
        assert set(data["languages"].keys()) == set(LANGUAGES.keys())
        c_info = data["languages"]["c"]
        assert c_info["extensions"] == [".h", ".c"], "C extensions"
        assert "slice8" in c_info["variants"], "C supports slice8"

    def test_variants_resource_excludes_slice8_appropriately(self):
        # Act
        data = _read_resource("crcglot://variants.json")
        # Assert
        bw = data["variants_by_width"]
        assert set(bw.keys()) == {"8", "16", "32", "64"}, "width keys"
        # python: no slice8 at any width.
        for w in ("8", "16", "32", "64"):
            assert "slice8" not in bw[w]["python"], (
                f"python should not include slice8 at width {w}"
            )
        # c: slice8 at 32/64 only.
        assert "slice8" in bw["32"]["c"]
        assert "slice8" in bw["64"]["c"]
        assert "slice8" not in bw["8"]["c"]
        assert "slice8" not in bw["16"]["c"]
        # vhdl: bitwise only at every width.
        for w in ("8", "16", "32", "64"):
            assert bw[w]["vhdl"] == ["bitwise"], (
                f"vhdl should be bitwise-only at width {w}, got {bw[w]['vhdl']}"
            )


# ---------------------------------------------------------------------------
# Defensive: import-without-mcp-extra
# ---------------------------------------------------------------------------


class TestLazyImport:
    """``import crcglot.mcp`` must succeed even without the ``mcp`` SDK
    installed -- only ``crcglot.mcp.main()`` materializes the SDK
    dependency.  Plain ``import crcglot`` must also succeed.
    """

    def test_crcglot_init_has_no_mcp_import(self):
        # Arrange -- the static invariant: the public top-level package
        # __init__.py source must not import ``mcp`` at top level (any
        # ``import mcp`` line would make ``pip install crcglot`` require
        # the SDK).  The test reads the source directly so a transient
        # ``sys.modules["crcglot.mcp"]`` entry from earlier in this test
        # session doesn't mask the regression.
        import crcglot
        from pathlib import Path

        init_src = Path(crcglot.__file__).read_text(encoding="utf-8")
        for line in init_src.splitlines():
            stripped = line.strip()
            assert not stripped.startswith("import mcp"), (
                f"crcglot/__init__.py must not import mcp: {line!r}"
            )
            assert not stripped.startswith("from mcp"), (
                f"crcglot/__init__.py must not import from mcp: {line!r}"
            )

    def test_crcglot_mcp_init_has_no_eager_server_import(self):
        # Arrange / Assert -- mirrors the above for crcglot/mcp/__init__.py:
        # the subpackage __init__ must lazily import server (which has
        # the actual ``from mcp.server import FastMCP``) so that
        # ``import crcglot.mcp`` itself doesn't hit the SDK.
        from pathlib import Path
        import crcglot.mcp

        init_src = Path(crcglot.mcp.__file__).read_text(encoding="utf-8")
        for line in init_src.splitlines():
            stripped = line.strip()
            if stripped.startswith(("from crcglot.mcp.server", "import crcglot.mcp.server")):
                # The lazy import inside main() lives indented; bare
                # top-level forms shouldn't appear.
                assert "    " in line[: line.find(stripped[0]) + 1] or "\t" in line, (
                    f"crcglot/mcp/__init__.py must lazy-import server: {line!r}"
                )

    def test_import_crcglot_mcp_subpackage_works_without_sdk(self, monkeypatch):
        # Arrange -- nuke any cached crcglot.mcp.server, then poison the
        # mcp SDK in sys.modules so an eager import would crash.
        for name in list(sys.modules):
            if name.startswith("crcglot.mcp"):
                monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setitem(sys.modules, "mcp", None)
        monkeypatch.setitem(sys.modules, "mcp.server", None)
        # Act
        import crcglot.mcp as mcp_pkg
        # Assert
        assert hasattr(mcp_pkg, "main"), "crcglot.mcp must export main"
