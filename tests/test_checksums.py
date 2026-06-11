"""Tests for the non-CRC checksum identifier (``crcglot.identify_checksum``).

Identification only -- crcglot does not generate code for these.  Covered here:
the compute functions (against reference vectors), round-trip identification
across input modes, multi-packet corroboration (the confidence story), endian
handling, the metadata registry, and the detect()/reverse() ``checksum_hint``
integration.
"""

import zlib
from typing import Literal

import pytest

from crcglot import (
    CHECKSUMS,
    ChecksumInfo,
    checksum_info,
    detect,
    encode,
    identify_checksum,
    reverse,
)
from crcglot.checksums import (
    _COMPUTE,
    _fletcher16,
    _fletcher32,
    _inet16,
    _lrc8,
    _sum8,
    _xor8,
)


def _frame(name: str, data: bytes, order: Literal["big", "little"] = "big") -> bytes:
    """Build ``data + checksum`` for the named checksum."""
    info = CHECKSUMS[name]
    w = (info.width + 7) // 8
    return data + _COMPUTE[name](data).to_bytes(w, order)


class TestComputeVectors:
    """Compute functions against independent reference values."""

    def test_fletcher16_wikipedia(self):
        # Assert -- Wikipedia's Fletcher-16 vector for "abcde".
        actual = _fletcher16(b"abcde")
        assert actual == 0xC8F0, f"fletcher16('abcde') = {actual:#06x}, want 0xc8f0"

    def test_fletcher32_wikipedia(self):
        # Assert -- Wikipedia's Fletcher-32 vector for "abcde".
        actual = _fletcher32(b"abcde")
        assert actual == 0xF04FC729, (
            f"fletcher32('abcde') = {actual:#010x}, want 0xf04fc729"
        )

    def test_adler32_matches_zlib(self):
        # Assert -- our Adler-32 is zlib's.
        data = b"123456789"
        assert _COMPUTE["adler32"](data) == zlib.adler32(data), "adler32 != zlib"

    def test_inet16_complement_extremes(self):
        # Assert -- one's-complement 16-bit sum at the extremes.
        assert _inet16(b"\x00\x00") == 0xFFFF, "inet16 of zero sum is all-ones"
        assert _inet16(b"\xff\xff") == 0x0000, "inet16 of 0xffff sum is zero"

    def test_8bit_by_hand(self):
        # Assert -- the trivial 8-bit functions on a known payload.
        data = b"\x01\x02\x03"
        assert _sum8(data) == 6, "sum8"
        assert _lrc8(data) == 250, "lrc8 = (-6) & 0xff"
        assert _xor8(data) == 0, "xor8 of 1^2^3"


class TestIdentifyRoundTrip:
    """A frame built from a checksum is identified as that checksum."""

    @pytest.mark.parametrize("name", sorted(CHECKSUMS))
    def test_binary_round_trip(self, name):
        # Arrange / Act
        result = identify_checksum(_frame(name, b"123456789"))
        # Assert
        actual = {c.name for c in result.candidates}
        assert name in actual, f"{name}: expected in candidates, got {actual}"

    def test_text_mode_round_trip(self):
        # Arrange -- "data <sep> hexcrc" with an 8-bit LRC.
        data = b"123456789"
        frame = f"123456789 {_lrc8(data):02x}"
        # Act
        result = identify_checksum(frame, mode="text")
        # Assert
        assert result.name == "lrc8", f"text-mode lrc8 expected, got {result.name}"

    def test_hex_string_mode_round_trip(self):
        # Arrange -- a hex-encoded byte string with a Fletcher-16 trailer.
        data = b"123456789"
        frame_bytes = _frame("fletcher16", data, "big")
        hex_text = " ".join(f"0x{b:02x}" for b in frame_bytes)
        # Act
        result = identify_checksum(hex_text)
        # Assert
        assert "fletcher16" in {c.name for c in result.candidates}, (
            f"fletcher16 expected from hex string, got {result.candidates}"
        )


class TestMultiFrameCorroboration:
    """Intersection across packets is the trustworthiness signal."""

    _MSGS = [b"123456789", b"hello world", b"\x01\x02\x03\x04", b"abcABCxyz!"]

    def test_shared_checksum_survives_with_frames_agreed(self):
        # Arrange -- four frames that all carry an 8-bit LRC.
        frames = [_frame("lrc8", m) for m in self._MSGS]
        # Act
        result = identify_checksum(frames)
        # Assert
        assert result.name == "lrc8", f"lrc8 expected, got {result.name}"
        assert result.frames_agreed == len(self._MSGS), (
            f"frames_agreed should be {len(self._MSGS)}, got {result.frames_agreed}"
        )

    def test_one_breaking_frame_drops_the_candidate(self):
        # Arrange -- three good LRC frames plus one with a corrupted trailer.
        frames = [_frame("lrc8", m) for m in self._MSGS[:3]]
        bad = bytearray(_frame("lrc8", self._MSGS[3]))
        bad[-1] ^= 0xFF
        frames.append(bytes(bad))
        # Act
        result = identify_checksum(frames)
        # Assert
        assert "lrc8" not in {c.name for c in result.candidates}, (
            "a frame that breaks the pattern must drop the candidate"
        )


class TestEndian:
    """Byte order matters for 16/32-bit checksums; not for 8-bit."""

    def test_fletcher16_little_endian_needs_little(self):
        # Arrange -- a Fletcher-16 frame stored little-endian.
        frame = _frame("fletcher16", b"123456789", "little")
        # Act
        be = identify_checksum(frame, endian="big")
        le = identify_checksum(frame, endian="little")
        # Assert
        assert "fletcher16" not in {c.name for c in be.candidates}, (
            "a little-endian field must not match under endian='big'"
        )
        assert "fletcher16" in {c.name for c in le.candidates}, (
            "endian='little' should match the little-endian field"
        )

    def test_8bit_dedups_to_big(self):
        # Arrange -- an 8-bit checksum has no byte order to vary.
        frame = _frame("lrc8", b"123456789")
        # Act
        result = identify_checksum(frame, endian="both")
        # Assert -- exactly one (lrc8, "big") entry, never a "little" duplicate.
        orders = [c.endianness for c in result.candidates if c.name == "lrc8"]
        assert orders == ["big"], f"8-bit should dedup to big only, got {orders}"


class TestFiltersAndEdges:
    """Glob filter, too-short packets, empty input."""

    def test_checksums_glob(self):
        # Act -- restrict to the Fletcher family.
        result = identify_checksum(_frame("fletcher32", b"123456789"),
                                   checksums="fletcher*")
        # Assert -- only fletcher* names can appear.
        names = {c.name for c in result.candidates}
        assert names <= {"fletcher16", "fletcher32"}, (
            f"glob should restrict to fletcher*, got {names}"
        )

    def test_too_short_packet_no_match(self):
        # Assert -- a 1-byte packet can't hold data + any checksum field.
        result = identify_checksum(b"\x00")
        assert not result.matched, "a 1-byte packet has no room for data + field"

    def test_empty_input(self):
        # Assert -- no packets, no match.
        result = identify_checksum([])
        assert not result.matched, "empty input should not match"
        assert result.frames_agreed == 0, "no frames agreed on nothing"


class TestRegistry:
    """ChecksumInfo / checksum_info / CHECKSUMS mirror the catalogue pattern."""

    def test_checksum_info_lookup(self):
        info = checksum_info("lrc8")
        assert isinstance(info, ChecksumInfo), "checksum_info returns a ChecksumInfo"
        assert info.width == 8, f"lrc8 width should be 8, got {info.width}"

    def test_unknown_raises_keyerror(self):
        with pytest.raises(KeyError):
            checksum_info("not-a-checksum")

    def test_registry_widths_are_byte_aligned(self):
        # Assert -- every checksum is 8, 16, or 32 bits.
        actual = {i.width for i in CHECKSUMS.values()}
        assert actual <= {8, 16, 32}, f"unexpected widths: {actual}"


class TestDetectReverseIntegration:
    """The fallback ``checksum_hint`` on detect() / reverse()."""

    def test_detect_non_crc_trailer_yields_hint(self):
        # Arrange -- a frame whose trailer is an 8-bit LRC, not a CRC.
        frame = _frame("lrc8", b"123456789")
        # Act
        result = detect(frame)
        # Assert
        assert not result.matched, "an LRC frame is not a catalogue CRC"
        assert result.checksum_hint is not None, "expected a checksum hint"
        assert result.checksum_hint.name == "lrc8", (
            f"hint should name lrc8, got {result.checksum_hint.name}"
        )

    def test_detect_real_crc_has_no_hint(self):
        # Arrange / Act -- a genuine crc32 frame.
        result = detect(encode(b"123456789", "crc32"))
        # Assert -- a CRC matched, so no fallback hint runs.
        assert result.matched, "crc32 frame should match"
        assert result.checksum_hint is None, "no hint when a CRC matched"

    def test_reverse_lrc_pairs_yields_hint(self):
        # Arrange -- (message, lrc) pairs, no recoverable CRC.
        msgs = [b"123456789", b"hello world", b"\x01\x02\x03\x04\x05"]
        pairs = [(m, _lrc8(m)) for m in msgs]
        # Act
        result = reverse(pairs)
        # Assert
        assert result.status in ("none", "underdetermined"), (
            f"LRC pairs are not a CRC; got status {result.status}"
        )
        assert result.checksum_hint is not None, "expected a checksum hint"
        assert result.checksum_hint.name == "lrc8", (
            f"hint should name lrc8, got {result.checksum_hint.name}"
        )

    def test_pairs_catch_byte_reversed_multibyte(self):
        # Arrange -- the checksum integer read in the "wrong" (byte-reversed)
        # order, as happens when an LE-stored field is read big-endian.
        from crcglot.checksums import _identify_checksum_pairs
        from crcglot.detect import _byte_reversed

        msgs = [b"123456789", b"hello world", b"abcdefghij"]
        pairs = [(m, _byte_reversed(_fletcher16(m), 16)) for m in msgs]
        # Act
        result = _identify_checksum_pairs(pairs)
        # Assert -- caught, and labelled little (compute matched the reversal).
        actual = {(c.name, c.endianness) for c in result.candidates}
        assert ("fletcher16", "little") in actual, (
            f"byte-reversed fletcher16 should be caught as little, got {actual}"
        )

    def test_reverse_packets_little_endian_multibyte_hint(self):
        # Arrange -- Fletcher-16 stored little-endian, read with the DEFAULT
        # big-endian field order: the hint must still flag it.
        from crcglot import reverse_packets

        msgs = [b"123456789", b"hello world", b"abcdefghij", b"0123456789xy"]
        frames = [m + _fletcher16(m).to_bytes(2, "little") for m in msgs]
        # Act -- binary frames; crc_byte_order defaults to "big".
        result = reverse_packets(frames, crc_bytes=2)
        # Assert
        assert not result, f"Fletcher frames are not a CRC; got {result.status}"
        assert result.checksum_hint is not None, "expected a checksum hint"
        names = {c.name for c in result.checksum_hint.candidates}
        assert "fletcher16" in names, (
            f"little-endian fletcher16 should be flagged, got {names}"
        )


class TestHexModeOddDigits:
    """identify_checksum's hex mode rejects an odd-length hex byte string."""

    def test_hex_mode_odd_raises(self):
        with pytest.raises(ValueError, match="odd number of hex digits"):
            identify_checksum("abc", mode="hex")

    def test_auto_mode_odd_is_lenient(self):
        # auto: not an error (falls to text); simply no match.
        result = identify_checksum("abc", mode="auto")
        assert not result.matched, "auto must not raise on odd hex"
