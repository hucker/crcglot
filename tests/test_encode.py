"""Tests for the CRC packet encoder + round-trip suite (encode -> detect).

The round-trip class is the proof that the encoder produces packets the
decoder identifies as the same shape they were built from.
"""

from __future__ import annotations

import pytest

from crcglot import (
    ALGORITHMS,
    HexFormat,
    TextFormat,
    detect,
    encode,
    encode_int,
    encode_match,
    encode_text,
    verify,
)


CHECK_INPUT_TEXT = "123456789"
CHECK_INPUT_BYTES = b"123456789"


class TestEncodeBinary:
    """Basic binary encode behavior."""

    def test_canonical_crc32_big_endian(self) -> None:
        # Act
        packet = encode(CHECK_INPUT_BYTES, "crc32", endianness="big")
        # Assert -- last 4 bytes are the BE encoding of the standard crc32 check value.
        actual = packet[-4:].hex()
        expected = "cbf43926"
        assert actual == expected, f"crc32 BE check mismatch: actual={actual} expected={expected}"
        assert packet[:-4] == CHECK_INPUT_BYTES, "data prefix changed"

    def test_canonical_crc32_little_endian(self) -> None:
        # Act
        packet = encode(CHECK_INPUT_BYTES, "crc32", endianness="little")
        # Assert -- LE byte order of 0xCBF43926.
        actual = packet[-4:].hex()
        expected = "2639f4cb"
        assert actual == expected, f"crc32 LE check mismatch: actual={actual} expected={expected}"

    def test_unknown_algorithm_raises_value_error(self) -> None:
        # Act / Assert
        with pytest.raises(ValueError, match="unknown algorithm"):
            encode(CHECK_INPUT_BYTES, "not-a-real-algorithm")

    def test_accepts_bytearray(self) -> None:
        # Act
        actual = encode(bytearray(CHECK_INPUT_BYTES), "crc32")
        expected = encode(CHECK_INPUT_BYTES, "crc32")
        # Assert
        assert actual == expected, "bytearray vs bytes mismatch"


class TestEncodeText:
    """Text encode + format-string options."""

    def test_default_format_canonical(self) -> None:
        # Act
        actual = encode_text(CHECK_INPUT_TEXT, "crc32")
        expected = "123456789 cbf43926"
        # Assert
        assert actual == expected, f"actual={actual!r} expected={expected!r}"

    def test_separator_and_leader(self) -> None:
        # Act
        actual = encode_text(CHECK_INPUT_TEXT, "crc32", sep="\t", leader="0x")
        expected = "123456789\t0xcbf43926"
        # Assert
        assert actual == expected, f"actual={actual!r} expected={expected!r}"

    def test_uppercase_hex(self) -> None:
        # Act
        actual = encode_text(CHECK_INPUT_TEXT, "crc32", leader="0X", uppercase=True)
        expected = "123456789 0XCBF43926"
        # Assert
        assert actual == expected, f"actual={actual!r} expected={expected!r}"

    def test_little_endian_hex(self) -> None:
        # Act -- LE hex dumps the storage-order byte sequence.
        actual = encode_text(CHECK_INPUT_TEXT, "crc32", endianness="little")
        expected = "123456789 2639f4cb"
        # Assert
        assert actual == expected, f"actual={actual!r} expected={expected!r}"

    def test_custom_fmt_reorders_tokens(self) -> None:
        # Act -- "{crc}{sep}{data}" puts the CRC first.
        actual = encode_text(
            CHECK_INPUT_TEXT, "crc32",
            fmt="{crc}{sep}{data}",
        )
        expected = "cbf43926 123456789"
        # Assert
        assert actual == expected, f"actual={actual!r} expected={expected!r}"


class TestEncodeMatch:
    """encode_match consumes a DetectMatch and reproduces the packet."""

    def test_binary_round_trip(self) -> None:
        # Arrange
        original = encode(CHECK_INPUT_BYTES, "crc32-iscsi", endianness="little")
        match = detect(original).candidates[0]
        # Act
        rebuilt = encode_match(CHECK_INPUT_BYTES, match)
        # Assert -- binary match → encode_match returns bytes; isinstance
        # narrows the type for .hex() and for byte-level comparison.
        assert isinstance(rebuilt, bytes), (
            f"binary encode_match should return bytes, got {type(rebuilt).__name__}"
        )
        assert rebuilt == original, (
            f"binary round-trip mismatch: rebuilt={rebuilt.hex()} original={original.hex()}"
        )

    def test_text_round_trip_canonical(self) -> None:
        # Arrange
        original = "123456789 cbf43926"
        match = detect(original).candidates[0]
        # Act
        rebuilt = encode_match(CHECK_INPUT_TEXT, match)
        # Assert
        assert rebuilt == original, f"text round-trip: rebuilt={rebuilt!r} original={original!r}"

    def test_text_round_trip_with_tab_and_0x(self) -> None:
        # Arrange
        original = "123456789\t0xcbf43926"
        match = detect(original).candidates[0]
        # Act
        rebuilt = encode_match(CHECK_INPUT_TEXT, match)
        # Assert
        assert rebuilt == original, f"tab/0x round-trip: rebuilt={rebuilt!r} original={original!r}"

    def test_text_round_trip_uppercase(self) -> None:
        # Arrange
        original = "123456789 0XCBF43926"
        match = detect(original).candidates[0]
        # Act
        rebuilt = encode_match(CHECK_INPUT_TEXT, match)
        # Assert
        assert rebuilt == original, f"uppercase round-trip: rebuilt={rebuilt!r} original={original!r}"

    def test_binary_match_with_str_data_raises(self) -> None:
        # Arrange
        binary_match = detect(encode(CHECK_INPUT_BYTES, "crc32")).candidates[0]
        # Act / Assert
        with pytest.raises(TypeError, match="binary match"):
            encode_match("string data", binary_match)

    def test_text_match_with_bytes_data_raises(self) -> None:
        # Arrange
        text_match = detect("123456789 cbf43926").candidates[0]
        # Act / Assert
        with pytest.raises(TypeError, match="text match"):
            encode_match(b"bytes data", text_match)


class TestRoundTripHexText:
    """Hex-text packets in any supported formatting round-trip
    byte-for-byte through ``detect -> encode_match``."""

    @pytest.mark.parametrize(
        "original",
        [
            "313233343536373839cbf43926",
            "31 32 33 34 35 36 37 38 39 cb f4 39 26",
            "0x31 0x32 0x33 0x34 0x35 0x36 0x37 0x38 0x39 0xcb 0xf4 0x39 0x26",
            "0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39,0xcb,0xf4,0x39,0x26",
            "31:32:33:34:35:36:37:38:39:CB:F4:39:26",
            "0X313233343536373839CBF43926",
            "31\t32\t33\t34\t35\t36\t37\t38\t39\tcb\tf4\t39\t26",
        ],
        ids=[
            "no-separator",
            "space",
            "0x-per-byte-space",
            "0x-per-byte-comma",
            "colon-upper",
            "0X-single-prefix",
            "tab-separated",
        ],
    )
    def test_round_trip_byte_for_byte(self, original: str) -> None:
        # Arrange
        match = detect(original).candidates[0]
        # Act
        rebuilt = encode_match(CHECK_INPUT_BYTES, match)
        # Assert
        assert isinstance(rebuilt, str), (
            f"hex-text encode_match should return str, got {type(rebuilt).__name__}"
        )
        assert rebuilt == original, (
            f"hex-text round-trip mismatch:\n  in:  {original!r}\n  out: {rebuilt!r}"
        )

    def test_hex_match_with_str_data_raises(self) -> None:
        # Arrange -- get a HexFormat-padded match from a known-good
        # hex-encoded packet.
        hex_match = detect("313233343536373839cbf43926").candidates[0]
        assert isinstance(hex_match.padding, HexFormat), "fixture must yield HexFormat"
        # Act / Assert
        with pytest.raises(TypeError, match="hex-text match"):
            encode_match("string instead of bytes", hex_match)


class TestRoundTripBinary:
    """encode every algorithm × every endianness, then detect identifies it."""

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_round_trip_big_endian(self, name: str) -> None:
        # Arrange
        packet = encode(CHECK_INPUT_BYTES, name, endianness="big")
        # Act
        result = detect(packet, match="all")
        # Assert
        actual = {(m.algorithm, m.endianness) for m in result.candidates}
        assert (name, "big") in actual, (
            f"{name} BE not found in detect candidates: {actual}"
        )

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_round_trip_little_endian(self, name: str) -> None:
        # Arrange
        algo = ALGORITHMS[name]
        packet = encode(CHECK_INPUT_BYTES, name, endianness="little")
        # Act
        result = detect(packet, match="all")
        # Assert -- single-byte CRC fields (width <= 8) collapse BE/LE.
        expected_endian = "big" if (algo.width + 7) // 8 == 1 else "little"
        actual = {(m.algorithm, m.endianness) for m in result.candidates}
        assert (name, expected_endian) in actual, (
            f"{name} {expected_endian} not found in detect candidates: {actual}"
        )


class TestRoundTripText:
    """Text-mode round-trip across separator / leader / uppercase combinations."""

    @pytest.mark.parametrize(
        "sep,leader,upper",
        [
            (" ", "", False),
            ("\t", "", False),
            ("  ", "", False),
            (" ", "0x", False),
            (" ", "0X", True),
            ("\t", "0X", True),
        ],
    )
    def test_round_trip_combinations(self, sep: str, leader: str, upper: bool) -> None:
        # Arrange
        packet = encode_text(
            CHECK_INPUT_TEXT, "crc32",
            sep=sep, leader=leader, uppercase=upper,
        )
        # Act
        result = detect(packet)
        # Assert
        assert result.matched, f"text packet not detected: {packet!r}"
        actual_padding = result.candidates[0].padding
        expected_padding = TextFormat(separator=sep, hex_prefix=leader, uppercase=upper)
        assert actual_padding == expected_padding, (
            f"padding mismatch: actual={actual_padding} expected={expected_padding}"
        )


class TestEncodeCli:
    """End-to-end CLI smoke tests for ``crcglot encode``."""

    def test_text_encode_default(self) -> None:
        import subprocess
        import sys
        # Act
        proc = subprocess.run(
            [sys.executable, "-m", "crcglot.cli", "encode", "crc32", "123456789"],
            capture_output=True, text=True,
        )
        # Assert
        assert proc.returncode == 0, f"exit={proc.returncode} stderr={proc.stderr}"
        actual = proc.stdout.strip()
        expected = "123456789 cbf43926"
        assert actual == expected, f"actual={actual!r} expected={expected!r}"

    def test_encode_pipe_detect(self) -> None:
        import subprocess
        import sys
        # Arrange -- encode crc32, pipe stdout into detect --text -.
        enc = subprocess.run(
            [sys.executable, "-m", "crcglot.cli", "encode", "crc32", "123456789"],
            capture_output=True, text=True,
        )
        # Act
        det = subprocess.run(
            [sys.executable, "-m", "crcglot.cli", "detect", "--text", "-"],
            input=enc.stdout, capture_output=True, text=True,
        )
        # Assert
        assert det.returncode == 0, f"detect exit={det.returncode} stderr={det.stderr}"
        assert "crc32" in det.stdout, f"crc32 missing from detect stdout: {det.stdout!r}"


class TestEncodeInt:
    """encode_int returns just the CRC value for the canonical check input."""

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_matches_catalogue_check_value(self, name: str) -> None:
        # Arrange
        algo = ALGORITHMS[name]
        # Act
        actual = encode_int(CHECK_INPUT_BYTES, name)
        # Assert
        expected = algo.check
        assert actual == expected, (
            f"{name}: encode_int({CHECK_INPUT_BYTES!r}) = 0x{actual:X}, "
            f"expected 0x{expected:X}"
        )

    def test_accepts_str_with_encoding(self) -> None:
        # Act
        actual = encode_int(CHECK_INPUT_TEXT, "crc32")
        expected = ALGORITHMS["crc32"].check
        # Assert
        assert actual == expected, (
            f"str input mismatch: actual=0x{actual:X} expected=0x{expected:X}"
        )


class TestVerify:
    """`verify` is the inverse of `encode`: it checks a frame's trailing CRC
    against a known algorithm, for binary and text frames alike."""

    def test_binary_round_trip_valid(self) -> None:
        # Arrange -- a correctly-encoded binary frame.
        packet = encode(CHECK_INPUT_BYTES, "crc32")
        # Act
        result = verify(packet, "crc32")
        # Assert
        assert result.valid is True, "encode -> verify must round-trip valid"
        assert bool(result) is True, "VerifyResult.__bool__ tracks .valid"
        actual, expected = result.actual, result.expected
        assert actual == expected, f"actual 0x{actual:X} != expected 0x{expected:X}"

    def test_binary_tampered_invalid_with_mismatch(self) -> None:
        packet = encode(CHECK_INPUT_BYTES, "crc32")
        bad = packet[:-1] + bytes([packet[-1] ^ 1])  # flip one CRC bit
        result = verify(bad, "crc32")
        assert result.valid is False, "a tampered CRC must fail"
        assert result.expected != result.actual, "the mismatch must be visible"

    def test_little_endian_field(self) -> None:
        packet = encode(b"hello world", "crc16-modbus", endianness="little")
        result = verify(packet, "crc16-modbus", endianness="little")
        assert result.valid is True, "little-endian field round-trips"

    def test_text_frame_valid(self) -> None:
        # Act -- a 'data <sep> hexcrc' line, the way encode_text writes it.
        frame = encode_text(CHECK_INPUT_TEXT, "crc32")
        result = verify(frame, "crc32")
        # Assert
        assert result.valid is True, f"text frame {frame!r} should verify"

    def test_text_frame_invalid(self) -> None:
        result = verify("123456789 deadbeef", "crc32")
        assert result.valid is False, "wrong text CRC must fail"
        assert result.expected == ALGORITHMS["crc32"].check, "expected = true CRC"

    def test_too_short_binary_rejected(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            verify(b"\x01", "crc32")  # 1 byte < 4-byte crc32 field

    def test_non_text_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a text frame"):
            verify("no hex CRC at the end!", "crc32")

    def test_unknown_algorithm_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown algorithm"):
            verify(b"\x00\x00\x00\x00", "definitely-not-real")
