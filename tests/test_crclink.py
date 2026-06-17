"""Tests for the crclink JSON-frame verifier (``crcglot._crclink``).

The frame format and the published example come from crclink on PyPI:
``{"t":1234,"v":42,"crc":"1352"}``, where ``"1352"`` is the CRC-16/XMODEM of
the prefix ``{"t":1234,"v":42,`` (the frame up to the opening quote of the
trailing ``"crc"`` key).  crcglot verifies it with its own engine; crclink is
NOT a dependency or an import here.  The oracle is therefore crcglot's own
``generic_crc`` over the prefix -- the same computation the verifier makes,
exercised here from the encode side so a regression in either direction shows.
"""

from __future__ import annotations

import json

import pytest

from crcglot.catalogue import ALGORITHMS, generic_crc
from crcglot._crclink import CrclinkResult, verify_crclink

_XMODEM = ALGORITHMS["crc16-xmodem"]


def _encode(obj: dict) -> str:
    """Build a valid crclink frame for ``obj`` the way crclink does.

    Compact-serialize, then append ``"crc":"<hex>"`` where the hex is
    CRC-16/XMODEM over the frame text up to that key's opening quote.
    """
    body = json.dumps(obj, separators=(",", ":"))
    prefix = body[:-1] + ("," if obj else "")  # drop the closing brace
    crc = generic_crc(prefix.encode(), _XMODEM)
    return f'{prefix}"crc":"{crc:04x}"}}'


class TestPublishedExample:
    """The exact frame from the crclink PyPI page must verify."""

    def test_pypi_example_verifies(self):
        # Act
        result = verify_crclink('{"t":1234,"v":42,"crc":"1352"}')

        # Assert
        assert result.valid, f"published example should verify: {result.reason}"
        assert result.expected == 0x1352, "embedded CRC parsed"
        assert result.actual == 0x1352, "computed CRC matches"
        assert result.coverage == '{"t":1234,"v":42,', "coverage is the prefix"


class TestRoundTrip:
    """Frames built the crclink way verify; any tampering fails."""

    @pytest.mark.parametrize(
        "obj",
        [
            {},
            {"v": 42},
            {"t": 1234, "v": 42},
            {"s": "hello, world", "n": -7, "ok": True},
            {"nested": {"a": [1, 2, 3]}, "x": None},
        ],
        ids=["empty", "one", "two", "mixed", "nested"],
    )
    def test_encoded_frame_verifies(self, obj):
        # Arrange
        frame = _encode(obj)

        # Act
        result = verify_crclink(frame)

        # Assert -- the frame is valid JSON and the CRC matches.
        assert result.valid, f"{frame!r} should verify: {result.reason}"
        actual = json.loads(frame)
        assert actual["crc"] == frame.split('"crc":"')[1][:4], "crc key present"

    def test_tampered_payload_fails_with_mismatch(self):
        # Arrange -- flip a data byte, keep the original CRC.
        frame = _encode({"t": 1234, "v": 42})
        tampered = frame.replace('"v":42', '"v":43')

        # Act
        result = verify_crclink(tampered)

        # Assert
        assert not result.valid, "a tampered payload must not verify"
        assert result.expected != result.actual, "expected != actual on tamper"
        assert "mismatch" in result.reason, f"reason names the mismatch: {result.reason}"

    def test_tampered_crc_fails(self):
        # Arrange -- corrupt one hex digit of the CRC field.
        frame = '{"t":1234,"v":42,"crc":"1352"}'
        tampered = frame.replace('"1352"', '"1353"')

        # Act / Assert
        assert not verify_crclink(tampered).valid, "a wrong CRC must not verify"


class TestRejections:
    """Non-frames are reported, not crashed on."""

    def test_invalid_json_is_reported(self):
        # Act
        result = verify_crclink('{"t":1234,"v":42,"crc":"1352"')  # missing brace

        # Assert
        assert not result.valid, "malformed JSON must not verify"
        assert "JSON" in result.reason, f"reason names the JSON failure: {result.reason}"

    def test_valid_json_without_crc_field_is_reported(self):
        # Act -- valid JSON, but no trailing crc integrity field.
        result = verify_crclink('{"t":1234,"v":42}')

        # Assert
        assert not result.valid, "a frame without a crc field is not crclink"
        assert "crc" in result.reason, f"reason names the missing field: {result.reason}"

    def test_crc_substring_in_payload_is_not_the_integrity_field(self):
        # Arrange -- a payload string literally containing "crc", plus a real
        # trailing crc field.  The anchored match must use the trailing one.
        obj = {"note": 'has "crc" inside', "v": 1}
        frame = _encode(obj)

        # Act
        result = verify_crclink(frame)

        # Assert
        assert result.valid, f"the trailing crc field must win: {result.reason}"


class TestEdgeCases:
    """Whitespace tolerance, bytes input, and truthiness."""

    def test_trailing_whitespace_tolerated(self):
        # Act -- a trailing newline/space after the closing brace.
        result = verify_crclink('{"t":1234,"v":42,"crc":"1352"}\n  ')

        # Assert
        assert result.valid, f"trailing whitespace should be fine: {result.reason}"

    def test_bytes_input_verifies(self):
        # Act
        result = verify_crclink(b'{"t":1234,"v":42,"crc":"1352"}')

        # Assert
        assert result.valid, f"bytes frame should verify: {result.reason}"

    def test_result_is_truthy_on_success_falsy_on_failure(self):
        # Assert -- the dataclass doubles as a boolean.
        assert verify_crclink('{"t":1234,"v":42,"crc":"1352"}'), "truthy on success"
        assert not verify_crclink('{"v":42}'), "falsy on failure"

    def test_result_type(self):
        # Assert
        actual = verify_crclink('{"v":42}')
        assert isinstance(actual, CrclinkResult), "returns a CrclinkResult"
