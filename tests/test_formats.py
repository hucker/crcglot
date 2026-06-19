"""Tests for named payload forms (``crcglot._formats`` via ``detect``).

A payload form is a CRC-bearing text wrapper that ``detect`` recognises by a
regex, strips to ``(message, crc)``, and then identifies with the ordinary
matcher.  The first form is crclink (PyPI): a JSON object with a trailing
CRC-16/XMODEM ``"crc"`` field, e.g. ``{"t":1234,"v":42,"crc":"1352"}``, whose
``"1352"`` is the CRC of the prefix ``{"t":1234,"v":42,``.

crcglot validates these with its own engine; crclink is NOT a dependency or an
import.  The oracle is therefore crcglot's own ``generic_crc`` over the prefix,
built from the encode side so a regression in either direction shows.
"""

from __future__ import annotations

import json

import pytest

from crcglot import FORMATS, FormatMatch, detect, encode_match, format_info
from crcglot.catalogue import ALGORITHMS, generic_crc

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


class TestCrclinkPublishedExample:
    """The exact frame from the crclink PyPI page detects as crc16-xmodem."""

    def test_detects_crc16_xmodem_with_form(self):
        # Act
        result = detect('{"t":1234,"v":42,"crc":"1352"}')

        # Assert -- algorithm and the form/coverage that produced it.
        assert result.matched, "the published crclink frame should be detected"
        m = result.candidates[0]
        assert m.algorithm == "crc16-xmodem", "frame is a CRC-16/XMODEM"
        assert m.form == "json", "the representation is json"
        assert isinstance(m.padding, FormatMatch), "padding is a form match"
        assert m.padding.info.name == "crclink", "the matched form is crclink"
        assert m.padding.info.category == "json", "crclink is a JSON form"
        assert m.padding.crc_text == "1352", "the embedded CRC field is reported"
        assert m.padding.message == '{"t":1234,"v":42,', "coverage is the prefix"
        assert m.endianness == "big", "a hex CRC field is read big-endian"


class TestCrclinkRoundTripFrames:
    """Frames built the crclink way detect as crc16-xmodem form=crclink."""

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
    def test_encoded_frame_detects(self, obj):
        # Arrange
        frame = _encode(obj)

        # Act
        result = detect(frame)

        # Assert
        assert result.matched, f"{frame!r} should detect"
        m = result.candidates[0]
        assert m.algorithm == "crc16-xmodem", f"{frame!r} is CRC-16/XMODEM"
        assert isinstance(m.padding, FormatMatch), f"{frame!r} matched as a form"
        assert m.padding.info.name == "crclink", f"{frame!r} is a crclink form"


class TestCrclinkRejections:
    """Tampered or non-form input does not produce a crclink match."""

    def _is_crclink(self, result) -> bool:
        return bool(result) and any(
            isinstance(c.padding, FormatMatch) and c.padding.info.name == "crclink"
            for c in result.candidates
        )

    def test_tampered_payload_is_not_a_crclink_match(self):
        # Arrange -- flip a data byte, keep the original CRC.
        frame = _encode({"t": 1234, "v": 42})
        tampered = frame.replace('"v":42', '"v":43')

        # Act / Assert -- the form's CRC no longer matches the payload.
        assert not self._is_crclink(detect(tampered)), "tampered payload must not match"

    def test_tampered_crc_digit_fails(self):
        # Arrange
        tampered = '{"t":1234,"v":42,"crc":"1353"}'  # 1352 -> 1353

        # Act / Assert
        assert not self._is_crclink(detect(tampered)), "a wrong CRC must not match"

    def test_valid_json_without_crc_field_is_not_a_form(self):
        # Act -- valid JSON, but no trailing crc integrity field.
        result = detect('{"t":1234,"v":42}')

        # Assert
        assert not self._is_crclink(result), "no crc field is not a crclink frame"

    def test_inner_crc_substring_is_not_the_integrity_field(self):
        # Arrange -- a payload string literally containing "crc", plus a real
        # trailing crc field; the anchored regex must use the trailing one.
        frame = _encode({"note": 'has "crc" inside', "v": 1})

        # Act
        result = detect(frame)

        # Assert
        assert self._is_crclink(result), "the trailing crc field must win"
        pad = result.candidates[0].padding
        assert isinstance(pad, FormatMatch), "matched as a form"
        expected_crc = frame.rsplit('"crc":"', 1)[1][:4]
        assert pad.crc_text == expected_crc, "captures the trailing crc field"


class TestFormRegistry:
    """``FORMATS`` / ``format_info`` mirror the trailer registry shape."""

    def test_crclink_is_registered(self):
        # Assert
        assert "crclink" in FORMATS, "crclink ships in the FORMATS registry"

    def test_format_info_lookup(self):
        # Act
        info = format_info("crclink")

        # Assert
        assert info.name == "crclink", "lookup returns the record"
        assert info.category == "json", "crclink is categorized as json"
        assert info.crc_endian == "big", "the hex CRC field is big-endian"

    def test_unknown_form_raises_keyerror(self):
        # Act / Assert
        with pytest.raises(KeyError):
            format_info("nonesuch")


class TestFormFilter:
    """``detect(..., form=glob)`` narrows which forms are tried."""

    def test_matching_glob_engages_the_form(self):
        # Act
        result = detect('{"t":1234,"v":42,"crc":"1352"}', form="crclink")

        # Assert
        assert result.matched, "an explicit form glob should still match"
        pad = result.candidates[0].padding
        assert isinstance(pad, FormatMatch) and pad.info.name == "crclink", (
            "the crclink form matched under its own glob"
        )

    def test_non_matching_glob_disables_forms(self):
        # Act -- no form name matches the glob, so the frame falls through to
        # text mode (which cannot parse a JSON frame) and finds nothing.
        result = detect('{"t":1234,"v":42,"crc":"1352"}', form="nonesuch")

        # Assert
        assert not result.matched, "a non-matching form glob disables form detection"


class TestFormEncodeDeferred:
    """Round-trip for forms is not supported yet -- it raises, not mis-emits."""

    def test_encode_match_raises_for_a_form(self):
        # Arrange
        m = detect('{"t":1234,"v":42,"crc":"1352"}').candidates[0]
        assert isinstance(m.padding, FormatMatch), "matched as a form"

        # Act / Assert
        with pytest.raises(NotImplementedError, match="form"):
            encode_match(m.padding.message, m)


class TestMcpWireSerialization:
    """The MCP wire layer projects a form match to a JSON-serializable dict."""

    def test_form_match_is_json_serializable(self):
        # Arrange
        from crcglot.mcp._wire import detect_match_to_dict

        m = detect('{"t":1234,"v":42,"crc":"1352"}').candidates[0]

        # Act
        wire = detect_match_to_dict(m)

        # Assert -- the representation is the top-level ``form`` string (json,
        # never "crclink"); ``form_detail`` carries the embedded CRC + covered
        # message; ``padding_kind`` stays as the low-level discriminator.
        assert wire["form"] == "json", "the representation is reported as json"
        assert wire["padding_kind"] == "form", "form matches are tagged 'form'"
        assert wire["form_detail"]["crc"] == "1352", "embedded CRC is surfaced"
        assert "crclink" not in json.dumps(wire), "the form name is not surfaced"
        json.dumps(wire)  # raises if any value isn't JSON-serializable
