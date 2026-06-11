"""Tests for the non-CRC checksum identifier (``crcglot.identify_trailer``).

Identification only -- crcglot does not generate code for these.  Covered here:
the compute functions (against reference vectors), round-trip identification
across input modes, multi-packet corroboration (the confidence story), endian
handling, the metadata registry, and the detect()/reverse() ``trailer_hint``
integration.
"""

import hashlib
import zlib
from typing import Literal

import pytest

from crcglot import (
    TRAILERS,
    TrailerInfo,
    trailer_info,
    detect,
    encode,
    identify_trailer,
    reverse,
)
from crcglot import _trailers
from crcglot._trailers import (
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
    info = TRAILERS[name]
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

    @pytest.mark.parametrize(
        "name",
        sorted(n for n, i in TRAILERS.items() if i.kind == "checksum"),
    )
    def test_binary_round_trip(self, name):
        # Arrange / Act
        result = identify_trailer(_frame(name, b"123456789"))
        # Assert
        actual = {c.name for c in result.candidates}
        assert name in actual, f"{name}: expected in candidates, got {actual}"

    def test_text_mode_round_trip(self):
        # Arrange -- "data <sep> hexcrc" with an 8-bit LRC.
        data = b"123456789"
        frame = f"123456789 {_lrc8(data):02x}"
        # Act
        result = identify_trailer(frame, mode="text")
        # Assert
        assert result.name == "lrc8", f"text-mode lrc8 expected, got {result.name}"

    def test_hex_string_mode_round_trip(self):
        # Arrange -- a hex-encoded byte string with a Fletcher-16 trailer.
        data = b"123456789"
        frame_bytes = _frame("fletcher16", data, "big")
        hex_text = " ".join(f"0x{b:02x}" for b in frame_bytes)
        # Act
        result = identify_trailer(hex_text)
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
        result = identify_trailer(frames)
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
        result = identify_trailer(frames)
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
        be = identify_trailer(frame, endian="big")
        le = identify_trailer(frame, endian="little")
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
        result = identify_trailer(frame, endian="both")
        # Assert -- exactly one (lrc8, "big") entry, never a "little" duplicate.
        orders = [c.endianness for c in result.candidates if c.name == "lrc8"]
        assert orders == ["big"], f"8-bit should dedup to big only, got {orders}"


class TestFiltersAndEdges:
    """Glob filter, too-short packets, empty input."""

    def test_checksums_glob(self):
        # Act -- restrict to the Fletcher family.
        result = identify_trailer(_frame("fletcher32", b"123456789"),
                                   trailers="fletcher*")
        # Assert -- only fletcher* names can appear.
        names = {c.name for c in result.candidates}
        assert names <= {"fletcher16", "fletcher32"}, (
            f"glob should restrict to fletcher*, got {names}"
        )

    def test_too_short_packet_no_match(self):
        # Assert -- a 1-byte packet can't hold data + any checksum field.
        result = identify_trailer(b"\x00")
        assert not result.matched, "a 1-byte packet has no room for data + field"

    def test_empty_input(self):
        # Assert -- no packets, no match.
        result = identify_trailer([])
        assert not result.matched, "empty input should not match"
        assert result.frames_agreed == 0, "no frames agreed on nothing"


class TestRegistry:
    """TrailerInfo / trailer_info / TRAILERS mirror the catalogue pattern."""

    def test_checksum_info_lookup(self):
        info = trailer_info("lrc8")
        assert isinstance(info, TrailerInfo), "trailer_info returns a TrailerInfo"
        assert info.width == 8, f"lrc8 width should be 8, got {info.width}"

    def test_unknown_raises_keyerror(self):
        with pytest.raises(KeyError):
            trailer_info("not-a-checksum")

    def test_registry_widths_are_byte_aligned(self):
        # Assert -- checksums are 8/16/32 bits; digests are byte-aligned
        # and at least 128 bits.
        cksum_widths = {
            i.width for i in TRAILERS.values() if i.kind == "checksum"
        }
        assert cksum_widths <= {8, 16, 32}, (
            f"unexpected checksum widths: {cksum_widths}"
        )
        digest_widths = {
            i.width for i in TRAILERS.values() if i.kind == "digest"
        }
        bad = {w for w in digest_widths if w % 8 or w < 128}
        assert not bad, f"unexpected digest widths: {bad}"

    def test_registry_kinds_are_closed_set(self):
        # Assert -- every entry is exactly one of the two kinds.
        actual = {i.kind for i in TRAILERS.values()}
        assert actual == {"checksum", "digest"}, f"unexpected kinds: {actual}"


class TestDetectReverseIntegration:
    """The fallback ``trailer_hint`` on detect() / reverse()."""

    def test_detect_non_crc_trailer_yields_hint(self):
        # Arrange -- a frame whose trailer is an 8-bit LRC, not a CRC.
        frame = _frame("lrc8", b"123456789")
        # Act
        result = detect(frame)
        # Assert
        assert not result.matched, "an LRC frame is not a catalogue CRC"
        assert result.trailer_hint is not None, "expected a checksum hint"
        assert result.trailer_hint.name == "lrc8", (
            f"hint should name lrc8, got {result.trailer_hint.name}"
        )

    def test_detect_real_crc_has_no_hint(self):
        # Arrange / Act -- a genuine crc32 frame.
        result = detect(encode(b"123456789", "crc32"))
        # Assert -- a CRC matched, so no fallback hint runs.
        assert result.matched, "crc32 frame should match"
        assert result.trailer_hint is None, "no hint when a CRC matched"

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
        assert result.trailer_hint is not None, "expected a checksum hint"
        assert result.trailer_hint.name == "lrc8", (
            f"hint should name lrc8, got {result.trailer_hint.name}"
        )

    def test_pairs_catch_byte_reversed_multibyte(self):
        # Arrange -- the checksum integer read in the "wrong" (byte-reversed)
        # order, as happens when an LE-stored field is read big-endian.
        from crcglot._trailers import _identify_trailer_pairs
        from crcglot._detect import _byte_reversed

        msgs = [b"123456789", b"hello world", b"abcdefghij"]
        pairs = [(m, _byte_reversed(_fletcher16(m), 16)) for m in msgs]
        # Act
        result = _identify_trailer_pairs(pairs)
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
        assert result.trailer_hint is not None, "expected a checksum hint"
        names = {c.name for c in result.trailer_hint.candidates}
        assert "fletcher16" in names, (
            f"little-endian fletcher16 should be flagged, got {names}"
        )


class TestHexModeOddDigits:
    """identify_trailer's hex mode rejects an odd-length hex byte string."""

    def test_hex_mode_odd_raises(self):
        with pytest.raises(ValueError, match="odd number of hex digits"):
            identify_trailer("abc", mode="hex")

    def test_auto_mode_odd_is_lenient(self):
        # auto: not an error (falls to text); simply no match.
        result = identify_trailer("abc", mode="auto")
        assert not result.matched, "auto must not raise on odd hex"


def _digest_frame(name: str, data: bytes, size: int | None = None) -> bytes:
    """Build ``data + digest`` (optionally leading-truncated to ``size``)."""
    d = _trailers._digest(name, data)
    assert d is not None, f"hashlib refused {name} on this interpreter"
    return data + (d if size is None else d[:size])


class TestDigestRoundTrip:
    """A frame built from a stdlib digest is identified as that digest."""

    _DATA = b"firmware-image-payload-0001"

    @pytest.mark.parametrize(
        "name", sorted(n for n, i in TRAILERS.items() if i.kind == "digest"),
    )
    def test_full_length_binary_round_trip(self, name):
        # Arrange / Act
        result = identify_trailer(_digest_frame(name, self._DATA))
        # Assert
        actual = {c.name for c in result.candidates}
        assert name in actual, f"{name}: expected in candidates, got {actual}"
        match = next(c for c in result.candidates if c.name == name)
        assert match.info.kind == "digest", f"{name} should be kind=digest"
        assert match.truncated_to is None, (
            f"full-length {name} match must not be marked truncated"
        )

    @pytest.mark.parametrize("size", [4, 8])
    def test_truncated_sha256_round_trip(self, size):
        # Arrange -- a frame carrying the leading bytes of sha256(payload).
        frame = _digest_frame("sha256", self._DATA, size)
        # Act
        result = identify_trailer(frame)
        # Assert
        actual = {(c.name, c.truncated_to) for c in result.candidates}
        assert ("sha256", size) in actual, (
            f"sha256 truncated to {size} expected, got {actual}"
        )

    def test_base58check_style_sha256d_4_bytes(self):
        # Arrange -- Bitcoin-style framing: first 4 bytes of sha256(sha256(p)).
        frame = _digest_frame("sha256d", self._DATA, 4)
        # Act
        result = identify_trailer(frame)
        # Assert
        actual = {(c.name, c.truncated_to) for c in result.candidates}
        assert ("sha256d", 4) in actual, (
            f"sha256d[:4] (base58check) expected, got {actual}"
        )

    def test_text_mode_hex_digest_field(self):
        # Arrange -- "data <sep> hexdigest" line (e.g. a manifest row).
        data = b"123456789"
        line = f"123456789 {hashlib.sha256(data).hexdigest()}"
        # Act
        result = identify_trailer(line, mode="text")
        # Assert
        names = {c.name for c in result.candidates}
        assert "sha256" in names, f"hex-ASCII sha256 expected, got {names}"

    def test_digest_glob_filter(self):
        # Act -- restrict candidates to the SHA family.
        frame = _digest_frame("sha256", self._DATA)
        result = identify_trailer(frame, trailers="sha*")
        # Assert
        names = {c.name for c in result.candidates}
        assert "sha256" in names, f"glob sha* should keep sha256, got {names}"
        assert all(n.startswith("sha") for n in names), (
            f"glob sha* leaked non-sha candidates: {names}"
        )

    def test_multi_frame_corroboration(self):
        # Arrange -- three different payloads, all with sha1 trailers.
        msgs = [b"alpha", b"beta-frame", b"gamma-payload-x"]
        frames = [_digest_frame("sha1", m) for m in msgs]
        # Act
        result = identify_trailer(frames)
        # Assert
        assert result.name == "sha1", f"sha1 expected, got {result.name}"
        actual_agreed = result.frames_agreed
        assert actual_agreed == len(msgs), (
            f"frames_agreed should be {len(msgs)}, got {actual_agreed}"
        )

    def test_checksum_and_digest_coexist_in_one_scan(self):
        # Arrange -- one LRC frame: digests must not match it, checksums must.
        data = b"123456789"
        frame = data + bytes([(-sum(data)) & 0xFF])
        # Act
        result = identify_trailer(frame)
        # Assert
        kinds = {c.info.kind for c in result.candidates}
        assert kinds == {"checksum"}, (
            f"an LRC frame must match only checksums, got kinds {kinds}"
        )


class TestMacHeadsUp:
    """A digest-sized delimited field matching nothing gets the MAC note."""

    def test_unmatched_digest_sized_text_field_notes_mac(self):
        # Arrange -- a 32-byte (64-nibble) trailing field that is NOT any
        # unkeyed digest of the data (it's a fixed pattern).
        line = "sensor-frame-001 " + "ab" * 32
        # Act
        result = identify_trailer(line, mode="text")
        # Assert
        assert not result.matched, "a random 32-byte field must not match"
        assert "32-byte" in result.note and "MAC" in result.note, (
            f"expected an observation-first MAC note, got {result.note!r}"
        )

    def test_matched_frame_has_no_note(self):
        # Arrange / Act -- a real sha256 hex field in text mode.
        data = b"123456789"
        line = f"123456789 {hashlib.sha256(data).hexdigest()}"
        result = identify_trailer(line, mode="text")
        # Assert
        assert result.matched, "sha256 line should match"
        assert result.note == "", "a matched result must carry no MAC note"

    def test_small_field_no_note(self):
        # Arrange / Act -- a 2-byte field that matches nothing: too small to
        # be digest-shaped, so no MAC speculation.
        result = identify_trailer("data-without-match zzzz", mode="text")
        # Assert
        assert result.note == "", (
            f"a non-digest-sized miss must not speculate, got {result.note!r}"
        )
