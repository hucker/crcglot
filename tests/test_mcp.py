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

from crcglot import ALGORITHMS, LANGUAGES, Crc, encode, generic_crc
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


def _lrc_frames_hex() -> list[str]:
    """A few frames carrying an 8-bit LRC trailer, as hex strings."""
    from crcglot._trailers import _lrc8

    msgs = [b"123456789", b"hello world", b"\x01\x02\x03\x04\x05"]
    return [(m + bytes([_lrc8(m)])).hex() for m in msgs]


class TestCrcIdentifyChecksum:
    """``crc_identify_trailer`` (identification only) + the trailer_hint
    fields on crc_detect / crc_reverse."""

    def test_identifies_lrc_with_frames_agreed(self):
        # Act
        out = _call("crc_identify_trailer", {"packets": _lrc_frames_hex()})
        # Assert
        names = {c["trailer"] for c in out["candidates"]}
        assert out["matched"], f"expected a checksum match, got {out}"
        assert "lrc8" in names, f"expected lrc8, got {names}"
        assert out["frames_agreed"] == 3, (
            f"frames_agreed should be 3, got {out['frames_agreed']}"
        )

    def test_crc_detect_surfaces_trailer_hint(self):
        # Act -- a single LRC frame: no CRC matches, hint present.
        out = _call("crc_detect", {"packet_hex": _lrc_frames_hex()[0]})
        # Assert
        assert not out["matched"], "an LRC frame is not a catalogue CRC"
        assert out["trailer_hint"] is not None, "expected a trailer_hint"
        names = {c["trailer"] for c in out["trailer_hint"]["candidates"]}
        assert "lrc8" in names, f"hint should name lrc8, got {names}"

    def test_crc_detect_real_crc_has_no_hint(self):
        # Act / Assert -- a genuine crc32 frame matches, so no hint runs.
        out = _call(
            "crc_detect", {"packet_hex": encode(b"123456789", "crc32").hex()})
        assert out["matched"], "crc32 frame should match"
        assert out["trailer_hint"] is None, "no hint when a CRC matched"


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
# crc_self_test_vectors
# ---------------------------------------------------------------------------


class TestCrcSelfTestVectors:
    """`crc_self_test_vectors` mirrors ``crcglot vectors <name>``."""

    def test_returns_the_four_runnable_vectors(self):
        # Act
        out = _call("crc_self_test_vectors", {"algorithm": "crc32"})

        # Assert
        assert out["algorithm"] == "crc32", "algorithm echoed"
        by_input = {v["input"]: v for v in out["vectors"]}
        actual_inputs = set(by_input)
        expected_inputs = {"empty", "check", "all_bytes", "binary_1k"}
        assert actual_inputs == expected_inputs, (
            f"the four fixed inputs, got {actual_inputs}"
        )
        assert by_input["check"]["expected_hex"] == "0xCBF43926", "check golden"
        assert by_input["check"]["input_hex"] == b"123456789".hex(), (
            "each vector carries its runnable input bytes"
        )

    def test_unknown_algorithm_raises(self):
        # Assert
        with pytest.raises(Exception, match="unknown algorithm"):
            _call("crc_self_test_vectors", {"algorithm": "nope-not-a-crc"})


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

    def test_width_filter_narrows_the_scan(self):
        # Arrange -- a crc32 packet; width=16 must exclude the 32-bit match.
        pkt = {"packet_hex": "31 32 33 34 35 36 37 38 39 cb f4 39 26"}
        # Act
        at_32 = _call("crc_detect", {**pkt, "width": 32})
        at_16 = _call("crc_detect", {**pkt, "width": 16, "match": "all"})
        # Assert
        assert at_32["candidates"][0]["algorithm"] == "crc32", (
            f"width=32 should still find crc32, got {at_32}"
        )
        algos_16 = {c["algorithm"] for c in at_16["candidates"]}
        assert "crc32" not in algos_16, (
            f"width=16 must exclude the 32-bit crc32, got {algos_16}"
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
# crc_compute_many
# ---------------------------------------------------------------------------


class TestCrcComputeMany:
    """`crc_compute_many` batches many messages through one call."""

    def test_batch_matches_crc_compute_per_item(self):
        # Arrange
        msgs = ["123456789", "", "hello", "world"]
        # Act -- batch in one call...
        batch = _call(
            "crc_compute_many",
            {"algorithm": "crc16-modbus", "data_texts": msgs},
        )
        # ...vs one crc_compute per message.
        singles = [
            _call("crc_compute", {"algorithm": "crc16-modbus", "data_text": m})["crc"]
            for m in msgs
        ]
        # Assert -- same values, same order, count reported.
        assert batch["count"] == len(msgs), "count reflects the batch size"
        assert [r["crc"] for r in batch["results"]] == singles, "batch == per-item"
        assert batch["results"][0]["crc"] == 0x4B37, "crc16-modbus of '123456789'"

    def test_b64_batch(self):
        # Arrange -- binary payloads as base64.
        b64s = [base64.b64encode(b).decode("ascii") for b in (b"123456789", b"\x00\xff")]
        # Act
        out = _call(
            "crc_compute_many",
            {"algorithm": "crc32", "data_b64s": b64s},
        )
        # Assert
        assert out["results"][0]["crc"] == 0xCBF43926, "first is crc32 check value"
        assert out["count"] == 2, "two results"

    def test_empty_batch(self):
        # Act
        out = _call("crc_compute_many", {"algorithm": "crc32", "data_texts": []})
        # Assert
        assert out["count"] == 0 and out["results"] == [], "empty batch -> no results"

    def test_unknown_algorithm_rejected(self):
        with pytest.raises(Exception, match="unknown algorithm"):
            _call("crc_compute_many", {"algorithm": "nope", "data_texts": ["x"]})

    def test_both_inputs_rejected(self):
        with pytest.raises(Exception, match="exactly one"):
            _call(
                "crc_compute_many",
                {"algorithm": "crc32", "data_texts": ["a"], "data_b64s": ["YQ=="]},
            )


# ---------------------------------------------------------------------------
# crc_reverse
# ---------------------------------------------------------------------------


class TestCrcReverse:
    """`crc_reverse` recovers an UNKNOWN / custom CRC's parameters from whole
    packets (CRC at the tail) -- the recovery counterpart to crc_detect, and
    taking the same packet input shape."""

    @staticmethod
    def _packets(width, poly, init, refin, refout, xorout, *,
                 crc_bytes=2, order="big", b64=False):
        """Build frames = message + CRC, as hex (or base64) strings."""
        import random
        rng = random.Random(1)
        out = []
        for length in [8] * 12 + [9, 11, 13, 17]:
            m = bytes(rng.randrange(256) for _ in range(length))
            c = generic_crc(m, Crc(width, poly, init, refin, refout, xorout))
            frame = m + c.to_bytes(crc_bytes, order)
            out.append(base64.b64encode(frame).decode() if b64 else frame.hex())
        return out

    def test_recovers_custom_crc(self):
        # Arrange -- a custom poly NOT in the catalogue; CRC at the tail.
        pkts = self._packets(16, 0x1009, 0x1234, False, False, 0x5678)
        # Act -- crc_bytes omitted: the tool auto-detects the field size.
        out = _call("crc_reverse", {"packets": pkts})
        # Assert -- recovered; the polynomial is exact; the true (init, xorout)
        # is in the returned class.
        assert out["status"] in ("unique", "equivalent"), f"status {out['status']}"
        assert out["candidates"], "no candidates returned"
        assert out["candidates"][0]["poly"] == 0x1009, "polynomial not recovered"
        pairs = {(c["init"], c["xorout"]) for c in out["candidates"]}
        assert (0x1234, 0x5678) in pairs, f"true (init, xorout) missing from {pairs}"

    def test_autodetects_field_size(self):
        # No crc_bytes -> the chosen split is reported in the note.
        pkts = self._packets(16, 0x1009, 0x1234, False, False, 0x5678)
        out = _call("crc_reverse", {"packets": pkts})
        assert "2 byte" in out["note"], f"field size not reported: {out['note']!r}"

    def test_equivalent_class_returned_complete(self):
        # poly 0x8005's generator carries (x+1) -> 2 identical (init, xorout) sets.
        pkts = self._packets(16, 0x8005, 0x1234, False, False, 0x5678)
        out = _call("crc_reverse", {"packets": pkts})
        assert out["status"] == "equivalent", f"status {out['status']}"
        assert out["ambiguity_bits"] == 1, f"ambiguity_bits {out['ambiguity_bits']}"
        assert len(out["candidates"]) == 2, f"{len(out['candidates'])} candidates"

    def test_catalogue_passthrough(self):
        # A known algorithm -> the catalogue tier names it.
        a = ALGORITHMS["crc16-modbus"]
        pkts = self._packets(a.width, a.poly, a.init, a.refin, a.refout, a.xorout)
        out = _call("crc_reverse", {"packets": pkts})
        assert out["status"] == "catalogue", f"status {out['status']}"
        assert out["catalogue_name"] == "crc16-modbus", out["catalogue_name"]

    def test_little_endian_field(self):
        pkts = self._packets(16, 0x1009, 0x1234, False, False, 0x5678, order="little")
        out = _call("crc_reverse", {"packets": pkts, "crc_byte_order": "little"})
        assert out["candidates"][0]["poly"] == 0x1009, "poly via little-endian field"

    def test_base64_packets_and_fixed_dials(self):
        pkts = self._packets(16, 0x1009, 0x1234, False, False, 0x5678, b64=True)
        out = _call("crc_reverse", {
            "packets": pkts, "packet_format": "base64", "width": 16, "crc_bytes": 2})
        actual_poly, actual_width = out["candidates"][0]["poly"], out["candidates"][0]["width"]
        assert actual_poly == 0x1009, "poly via base64 packets + fixed dials"
        assert actual_width == 16, "width honoured"

    def test_text_frames(self):
        # 'data <sep> hexcrc' lines -- the trailing hex CRC is peeled like detect.
        import random
        rng = random.Random(2)
        frames = []
        for i in range(16):
            data = f"f{i:02d}-" + "".join(
                chr(97 + rng.randrange(26)) for _ in range(rng.randrange(4, 12)))
            c = generic_crc(data.encode(), Crc(16, 0x1009, 0xFFFF, True, True, 0))
            frames.append(f"{data} {c:04x}")
        out = _call("crc_reverse", {"packets": frames, "packet_format": "text"})
        assert out["candidates"], "no candidates from text frames"
        assert out["candidates"][0]["poly"] == 0x1009, "poly recovered from text"

    def test_empty_packets_rejected(self):
        with pytest.raises(Exception, match="non-empty"):
            _call("crc_reverse", {"packets": []})

    def test_bad_packet_rejected(self):
        with pytest.raises(Exception, match=r"packets\[0\]"):
            _call("crc_reverse", {"packets": ["not-hex-zz"]})


# ---------------------------------------------------------------------------
# crc_verify
# ---------------------------------------------------------------------------


class TestCrcVerify:
    """`crc_verify` checks a frame's trailing CRC against a KNOWN algorithm --
    the inverse of crc_encode, taking the same packet shape as crc_detect."""

    def test_valid_packet(self):
        # Arrange -- a correctly-CRC'd frame.
        good = encode(b"123456789", "crc32")
        # Act
        out = _call("crc_verify", {"algorithm": "crc32", "packet_hex": good.hex()})
        # Assert
        assert out["valid"] is True, "a correctly-CRC'd frame must validate"
        assert out["expected"] == out["actual"], "expected == actual when valid"

    def test_invalid_packet_shows_mismatch(self):
        good = encode(b"123456789", "crc32")
        bad = good[:-1] + bytes([good[-1] ^ 1])  # flip one CRC bit
        out = _call("crc_verify", {"algorithm": "crc32", "packet_hex": bad.hex()})
        assert out["valid"] is False, "a tampered CRC must fail"
        assert out["expected"] != out["actual"], "the mismatch must be surfaced"
        assert out["actual"] == (out["expected"] ^ 1), "off by exactly the flipped bit"

    def test_base64_and_little_endian_field(self):
        good = encode(b"hello world", "crc16-modbus", endianness="little")
        out = _call("crc_verify", {
            "algorithm": "crc16-modbus",
            "packet_b64": base64.b64encode(good).decode(),
            "crc_byte_order": "little"})
        assert out["valid"] is True, "little-endian CRC field must round-trip"

    def test_text_frame(self):
        # A 'data <sep> hexcrc' line verifies against the named algorithm.
        out = _call("crc_verify", {
            "algorithm": "crc32", "packet_text": "123456789 cbf43926"})
        assert out["valid"] is True, "text frame should verify"

    def test_unknown_algorithm_rejected(self):
        with pytest.raises(Exception, match="unknown algorithm"):
            _call("crc_verify", {"algorithm": "nope", "packet_hex": "0000"})

    def test_no_packet_form_rejected(self):
        with pytest.raises(Exception, match="exactly one"):
            _call("crc_verify", {"algorithm": "crc32"})


# ---------------------------------------------------------------------------
# custom_params -- compute / verify / encode a CUSTOM (non-catalogue) CRC
# ---------------------------------------------------------------------------


class TestCustomParams:
    """compute / compute_many / encode / verify accept a custom Rocksoft tuple
    (the same shape crc_generate takes), so the output of crc_reverse can be
    *used*, not just turned into code -- the recover -> use loop."""

    # A custom poly NOT in the catalogue.
    CP = {"width": 16, "poly": 0x1009, "init": 0xFFFF,
          "refin": True, "refout": True, "xorout": 0}

    def _truth(self, data: bytes) -> int:
        return generic_crc(data, Crc(16, 0x1009, 0xFFFF, True, True, 0))

    def test_compute_matches_engine(self):
        # Act
        out = _call("crc_compute", {"custom_params": self.CP, "data_text": "hello"})
        # Assert
        actual, expected = out["crc"], self._truth(b"hello")
        assert actual == expected, f"custom compute 0x{actual:X} != 0x{expected:X}"

    def test_compute_many_matches_engine(self):
        out = _call("crc_compute_many",
                    {"custom_params": self.CP, "data_texts": ["a", "bb", "ccc"]})
        actual = [r["crc"] for r in out["results"]]
        expected = [self._truth(b"a"), self._truth(b"bb"), self._truth(b"ccc")]
        assert actual == expected, f"custom batch {actual} != {expected}"
        assert out["algorithm"] == "custom", "label for an unnamed custom CRC"

    def test_encode_then_verify_round_trip(self):
        # Build a packet with the custom CRC, then verify it with the same tuple.
        enc = _call("crc_encode", {"custom_params": self.CP, "data_text": "frame"})
        # encode_text default packet is "data hexcrc"; verify the text frame.
        ver = _call("crc_verify",
                    {"custom_params": self.CP, "packet_text": enc["packet_text"]})
        assert ver["valid"] is True, "custom encode -> custom verify must round-trip"

    def test_reverse_then_verify_loop(self):
        # The headline workflow: recover a custom CRC, then validate a NEW frame
        # against the recovered parameters -- all via MCP.
        import random
        rng = random.Random(5)
        msgs = [bytes(rng.randrange(256) for _ in range(n))
                for n in ([8] * 12 + [9, 11, 13, 17])]
        packets = [(m + generic_crc(m, Crc(16, 0x1009, 0xFFFF, True, True, 0))
                    .to_bytes(2, "big")).hex() for m in msgs]
        rev = _call("crc_reverse", {"packets": packets, "std_algo_only": False})
        model = rev["candidates"][0]
        cp = {k: model[k] for k in
              ("width", "poly", "init", "refin", "refout", "xorout")}
        # A fresh frame the recovery never saw, CRC'd with the true params.
        fresh = b"a brand new frame"
        good = (fresh + generic_crc(fresh, Crc(16, 0x1009, 0xFFFF, True, True, 0))
                .to_bytes(2, "big"))
        ver = _call("crc_verify", {"custom_params": cp, "packet_hex": good.hex()})
        assert ver["valid"] is True, "recovered params must validate an unseen frame"

    def test_requires_exactly_one_source(self):
        with pytest.raises(Exception, match="exactly one of algorithm"):
            _call("crc_compute",
                  {"algorithm": "crc32", "custom_params": self.CP, "data_text": "x"})

    def test_custom_params_needs_width_and_poly(self):
        with pytest.raises(Exception, match="width.*poly|requires"):
            _call("crc_compute", {"custom_params": {"width": 16}, "data_text": "x"})

    def test_hex_string_numerics_accepted(self):
        """An LLM quoting poly / init in hex from a datasheet ("0x1009")
        is accepted, not just bare ints -- bare int() used to reject it."""
        # Arrange -- the same CRC as self.CP but every numeric field as hex.
        hex_cp = {"width": 16, "poly": "0x1009", "init": "0xFFFF",
                  "refin": True, "refout": True, "xorout": "0x0"}

        # Act
        out = _call("crc_compute", {"custom_params": hex_cp, "data_text": "hello"})

        # Assert
        actual, expected = out["crc"], self._truth(b"hello")
        assert actual == expected, (
            f"hex-string custom params gave 0x{actual:X}, expected 0x{expected:X}"
        )

    def test_missing_width_gives_clean_error(self):
        # Regression: a missing width used to surface as the engine's opaque
        # "negative shift count"; it must now name the missing field.
        with pytest.raises(Exception, match=r"missing required field.*width"):
            _call("crc_compute",
                  {"custom_params": {"poly": 0x1009}, "data_text": "x"})

    def test_unparseable_numeric_gives_clean_error(self):
        with pytest.raises(Exception, match="integer or hex string"):
            _call("crc_compute",
                  {"custom_params": {"width": 16, "poly": "nope"}, "data_text": "x"})

    @pytest.mark.parametrize("bad_width", [0, -4, 65, 200])
    def test_out_of_range_width_rejected(self, bad_width):
        # Regression: width=200 used to SUCCEED and emit a nonsense source file
        # (no upper-bound check); the engine guard now rejects it cleanly.
        with pytest.raises(Exception, match="width must be in"):
            _call("crc_generate",
                  {"language": "c",
                   "custom_params": {"width": bad_width, "poly": 0x1009}})


# ---------------------------------------------------------------------------
# crc_generate
# ---------------------------------------------------------------------------


class TestCrcGenerate:
    """`crc_generate` collapses the 8 per-language CLI subcommands into
    one MCP tool with a ``language`` enum.
    """

    @pytest.mark.parametrize(
        "language",
        ["c", "csharp", "go", "java", "python", "rust", "typescript", "verilog", "vhdl"],
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

    def test_default_variant_is_fastest(self):
        # No variant -> "auto" -> fastest the target+width supports.
        rust32 = _call("crc_generate", {"language": "rust", "algorithm": "crc32"})
        assert rust32["variant"] == "slice8", "rust crc32 default should be slice-by-8"
        c8 = _call("crc_generate", {"language": "c", "algorithm": "crc8"})
        assert c8["variant"] == "table", "c crc8 (width 8) default should be table"
        # A mixed-width bundle takes the fastest valid for ALL members: slice8 is
        # invalid for crc8, so the bundle settles on table.
        bundle = _call("crc_generate", {"language": "c", "algorithm": ["crc32", "crc8"]})
        assert bundle["variant"] == "table", "bundle default = fastest common variant"

    def test_explicit_bitwise_still_smallest(self):
        out = _call("crc_generate",
                    {"language": "rust", "algorithm": "crc32", "variant": "bitwise"})
        assert out["variant"] == "bitwise", "explicit bitwise must be honoured"
        assert "slice" not in out["files"][0]["content"].lower(), "no slice table"

    def test_provenance_always_present(self):
        # Act -- provenance is unconditional (no flag).
        out = _call("crc_generate", {"language": "c", "algorithm": "crc16-xmodem"})

        # Assert -- comment block in the header + linkable const record in C.
        content = out["files"][0]["content"]
        assert "Reproduce with crcglot:" in content, (
            "header should carry the reproduce-with comment block"
        )
        assert "crcglot_provenance_t crc16_xmodem_provenance" in content, (
            "C output should carry the linkable const provenance record"
        )

    def test_response_carries_advisories(self):
        # crc32 on a compiled target -> a stdlib-crc32 info advisory (dict-shaped
        # for the JSON wire, mirroring LanguageInfo.advisories_for).
        out = _call("crc_generate", {"language": "rust", "algorithm": "crc32"})
        kinds = [(a["kind"], a["severity"]) for a in out["advisories"]]
        assert ("stdlib-crc32", "info") in kinds, f"missing stdlib advisory: {kinds}"
        # non-crc32 -> none; python -> the use-the-package warning.
        none = _call("crc_generate", {"language": "c", "algorithm": "crc16-modbus"})
        assert none["advisories"] == [], "no advisory for crc16"
        py = _call("crc_generate", {"language": "python", "algorithm": "crc8"})
        assert py["advisories"][0]["kind"] == "python-runtime", py["advisories"]

    def test_output_handling_steering(self):
        # The result must carry a "use the whole file" note (proximate steering,
        # in context when the model decides how to present the output)...
        out = _call("crc_generate", {"language": "rust", "algorithm": "crc32"})
        note = out.get("note", "")
        assert "COMPLETE" in note and "truncate" in note, (
            f"result must steer against abridging the file, got note={note!r}"
        )
        # ...and the tool description (the canonical steer) must say the same.
        tools = _run(build_server().list_tools())
        desc = next(t.description for t in tools if t.name == "crc_generate")
        assert "OUTPUT HANDLING" in desc and "Never truncate" in desc, (
            "crc_generate description should carry the OUTPUT HANDLING directive"
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

    def test_multiple_algorithms_as_list_bundle(self):
        # Act -- a list of names bundles into one C .h/.c pair.
        out = _call(
            "crc_generate",
            {"language": "c", "algorithm": ["crc32", "crc16-modbus", "crc8"]},
        )

        # Assert -- still two files; the header carries every guard and the
        # returned 'algorithms' lists all three.
        assert out["algorithms"] == ["crc32", "crc16-modbus", "crc8"], "lists bundle"
        header = next(f["content"] for f in out["files"] if f["extension"] == ".h")
        source = next(f["content"] for f in out["files"] if f["extension"] == ".c")
        for guard in ("CRC32_H", "CRC16_MODBUS_H", "CRC8_H"):
            assert f"#ifndef {guard}" in header, f"{guard} present in bundle header"
        actual_includes = source.count('#include "')
        assert actual_includes == 1, "combined .c has exactly one quoted include"

    def test_multiple_algorithms_as_space_string(self):
        # Act -- a space-separated string is split into the same bundle.
        out = _call(
            "crc_generate",
            {"language": "rust", "algorithm": "crc32 crc16-modbus"},
        )

        # Assert
        actual = out["algorithms"]
        expected = ["crc32", "crc16-modbus"]
        assert actual == expected, "space-separated string splits to names"
        body = out["files"][0]["content"]
        assert "fn crc32(" in body and "fn crc16_modbus(" in body, "both functions"

    def test_single_string_still_one_algorithm(self):
        # Act -- backward compatibility: a lone name is a single algorithm.
        out = _call(
            "crc_generate",
            {"language": "python", "algorithm": "crc32"},
        )

        # Assert
        assert out["algorithms"] == ["crc32"], "single name -> single algorithm"

    def test_symbol_with_multiple_rejected(self):
        with pytest.raises(Exception, match="symbol"):
            _call(
                "crc_generate",
                {
                    "language": "c",
                    "algorithm": ["crc32", "crc16-modbus"],
                    "symbol": "foo",
                },
            )

    def test_unknown_name_in_bundle_rejected(self):
        with pytest.raises(Exception, match="unknown algorithm"):
            _call(
                "crc_generate",
                {"language": "c", "algorithm": ["crc32", "bogus"]},
            )

    def test_variant_invalid_for_one_bundle_member_rejected(self):
        # Act / Assert -- slice8 is valid for crc32 (w32) but not crc8 (w8),
        # so the mixed-width bundle is rejected naming the offender.
        with pytest.raises(Exception, match="crc8"):
            _call(
                "crc_generate",
                {
                    "language": "c",
                    "algorithm": ["crc32", "crc8"],
                    "variant": "slice8",
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


class TestToolAnnotations:
    """Every tool is a pure, read-only, offline computation, so each must
    advertise read-only / idempotent annotations -- that's what lets an MCP
    client auto-approve the calls instead of prompting per invocation."""

    def test_all_tools_are_annotated_read_only(self):
        # Act
        mcp = build_server()
        tools = _run(mcp.list_tools())

        # Assert -- the whole surface, so a new tool can't slip through.
        assert len(tools) == 12, f"expected 12 tools, got {len(tools)}"
        for t in tools:
            a = t.annotations
            assert a is not None, f"{t.name}: missing annotations"
            assert a.readOnlyHint is True, f"{t.name}: not readOnlyHint"
            assert a.idempotentHint is True, f"{t.name}: not idempotentHint"
            assert a.destructiveHint is False, f"{t.name}: destructiveHint set"
            assert a.openWorldHint is False, f"{t.name}: openWorldHint set"


class TestSteering:
    """The server steers algorithm SELECTION: ambient guidance in the
    instructions (match an existing CRC, else default crc32) plus a
    user-invokable design-a-crc prompt for the open-ended 'I need a CRC' ask."""

    def test_instructions_carry_choose_vs_match_guidance(self):
        # Act
        instr = build_server().instructions or ""
        # Assert -- the two facts a model won't reliably know on its own.
        assert "MATCH" in instr and "CHOOSE" in instr, "match/choose fork missing"
        assert "crc32" in instr, "crc32 default not stated"

    def test_design_a_crc_prompt_registered(self):
        prompts = _run(build_server().list_prompts())
        names = {p.name for p in prompts}
        assert "design-a-crc" in names, f"design-a-crc prompt missing from {names}"

    def test_design_a_crc_renders_with_use_case(self):
        # Act -- render the prompt with a greenfield use case.
        got = _run(build_server().get_prompt(
            "design-a-crc", {"use_case": "a new serial link between two MCUs"}))
        msg = got.messages[0]
        text = getattr(msg.content, "text", str(msg.content))
        # Assert -- it walks the fork and interpolates the use case.
        assert "MATCH vs CHOOSE" in text, "prompt doesn't walk the decision"
        assert "crc_detect" in text and "crc_reverse" in text, "match path missing"
        assert "serial link" in text, "use_case not interpolated"

    def test_design_a_crc_walks_implementation_choice(self):
        # Act -- render the prompt (use case is optional for this step).
        got = _run(build_server().get_prompt("design-a-crc", {}))
        text = getattr(got.messages[0].content, "text", str(got.messages[0].content))
        # Assert -- the bitwise / table / external throughput ladder is walked,
        # sized to payload x frequency, with the external crc32 rung named.
        assert "CHOOSE THE IMPLEMENTATION" in text, "implementation step missing"
        assert "variant='bitwise'" in text, "bitwise rung not steered"
        assert "payload x frequency" in text, "size/frequency heuristic missing"
        assert "crc32" in text and "stdlib" in text, "external hardware path missing"
        # The per-variant fact is sourced from VariantInfo, not restated here.
        from crcglot import variant_info
        assert variant_info("bitwise").description.rstrip(".") in text, (
            "bitwise blurb should come from VariantInfo, keeping one source of truth"
        )

    def test_generate_crc_code_prompt_registered(self):
        prompts = _run(build_server().list_prompts())
        names = {p.name for p in prompts}
        assert "generate-crc-code" in names, (
            f"generate-crc-code prompt missing from {names}"
        )

    def test_generate_crc_code_walks_conditional_picker(self):
        # Act -- render with an algorithm so the interpolation path is covered.
        got = _run(build_server().get_prompt(
            "generate-crc-code", {"algorithm": "crc16-modbus"}))
        text = getattr(got.messages[0].content, "text", str(got.messages[0].content))

        # Assert -- the ordered language/naming/style picker + the generate call.
        for marker in ("LANGUAGE", "NAMING", "COMMENT STYLE", "crc_generate"):
            assert marker in text, f"picker step {marker!r} missing from prompt"
        assert "crc16-modbus" in text, "algorithm not interpolated"
        # Data-driven gating: every language appears, and the single-option
        # axes are marked "(only)" so the model knows not to ask.
        from crcglot import LANGUAGES
        for code in LANGUAGES:
            assert code in text, f"language {code!r} missing from picker map"
        assert "(only)" in text, (
            "single-option axes (e.g. python naming, verilog style) should be "
            "marked so the model skips asking"
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

    def test_languages_resource_exposes_comment_styles(self):
        """The languages resource carries each language's valid comment styles.

        Each is a ``{name, label, description}`` record -- enough for a UI to
        build a dropdown without hardcoding the matrix.
        """
        # Act
        data = _read_resource("crcglot://languages.json")
        langs = data["languages"]

        def names(lang):
            return [s["name"] for s in langs[lang]["comment_styles"]]

        # Assert -- doxygen is offered for C/Java but not Go/Python.
        assert "doxygen" in names("c"), "C should offer doxygen"
        assert "doxygen" in names("java"), "Java should offer doxygen"
        assert "doxygen" not in names("go"), "Go must not offer doxygen"
        assert names("python") == ["plain", "google", "numpy", "rest"], (
            f"python styles: {names('python')}"
        )
        # Each record carries human-readable label + description for the UI.
        google = next(s for s in langs["python"]["comment_styles"] if s["name"] == "google")
        assert google["label"] == "Google", f"google label: {google}"
        assert google["description"], "google must carry a description"

    def test_languages_resource_exposes_naming(self):
        """The languages resource carries each language's naming conventions.

        Mirrors ``comment_styles``: ``{name, label, description}`` records plus
        a ``default_naming`` so a UI can preselect the idiomatic convention.
        """
        # Act
        data = _read_resource("crcglot://languages.json")
        langs = data["languages"]

        def names(lang):
            return [n["name"] for n in langs[lang]["naming"]]

        # Assert -- per-language offered sets + idiomatic defaults.
        assert names("go") == ["camel", "pascal"], f"go naming: {names('go')}"
        assert names("python") == ["snake"], f"python naming: {names('python')}"
        assert langs["go"]["default_naming"] == "pascal", "go defaults to pascal"
        assert langs["java"]["default_naming"] == "camel", "java defaults to camel"
        # Records carry label + description for the UI dropdown.
        pascal = next(n for n in langs["go"]["naming"] if n["name"] == "pascal")
        assert pascal["label"] == "PascalCase", f"pascal label: {pascal}"
        assert pascal["description"], "pascal must carry a description"

    def test_generate_honors_naming(self):
        """crc_generate re-cases function names and rejects an unsupported pair."""
        # Act -- Go default is pascal; camel is offered; snake is not.
        default = _call("crc_generate", {"language": "go", "algorithm": "crc16-modbus"})
        camel = _call(
            "crc_generate",
            {"language": "go", "algorithm": "crc16-modbus", "naming": "camel"},
        )

        # Assert
        assert default["naming"] == "pascal", "go default naming is pascal"
        assert "func Crc16ModbusUpdate(" in default["files"][0]["content"], "pascal funcs"
        assert "func crc16ModbusUpdate(" in camel["files"][0]["content"], "camel funcs"
        with pytest.raises(Exception, match="not valid for language"):
            _call(
                "crc_generate",
                {"language": "rust", "algorithm": "crc32", "naming": "pascal"},
            )

    def test_comment_style_enum_matches_registry(self):
        """The MCP param enum must stay in sync with the style registry.

        ``COMMENT_STYLE_ENUM`` is a typing ``Literal`` (can't be derived at
        runtime), so guard it against drift instead.
        """
        # Arrange
        from typing import get_args

        from crcglot.comments import COMMENT_STYLES
        from crcglot.mcp.server import COMMENT_STYLE_ENUM

        # Act / Assert
        actual = set(get_args(COMMENT_STYLE_ENUM))
        expected = set(COMMENT_STYLES)
        assert actual == expected, (
            f"COMMENT_STYLE_ENUM {actual} drifted from registry {expected}"
        )

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
