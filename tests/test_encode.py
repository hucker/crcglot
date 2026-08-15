"""Tests for the CRC packet encoder + round-trip suite (encode -> detect).

The round-trip class is the proof that the encoder produces packets the
decoder identifies as the same shape they were built from.
"""

from __future__ import annotations

import pytest

from crcglot import (
    compute,
    custom_algorithm,
    ALGORITHMS,
    AlgorithmInfo,
    Crc,
    HexFormat,
    TextFormat,
    detect,
    encode,
    encode_int,
    encode_match,
    encode_text,
    MixedFormatError,
    generic_crc,
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


def _hex_text(data: bytes, fmt: HexFormat) -> str:
    """Render ``data`` as hex text in exactly the surface ``fmt`` describes.

    The inverse of what ``detect`` recovers into a :class:`HexFormat`; used to
    build every point of the format cross-product below by construction, so
    the round-trip is exercised on formats nobody had to think of by hand.
    """
    digits = [f"{b:02X}" if fmt.uppercase else f"{b:02x}" for b in data]
    if fmt.prefix and fmt.prefix_per_byte:
        digits = [fmt.prefix + d for d in digits]
    body = fmt.separator.join(digits)
    if fmt.prefix and not fmt.prefix_per_byte:
        body = fmt.prefix + body
    return body


# The HexFormat surface is a CLOSED product: separator x prefix x
# per-byte x case.  It is small enough to enumerate completely, which is
# what the project's method calls for on a countable axis -- so this is a
# parametrized cross-product, deliberately NOT a property test.  (The
# property tests in test_reverse.py / test_stream.py cover axes that are
# genuinely infinite.)
_SEPARATORS = ("", " ", "  ", "\t", ",", ", ", ":", "\n")
_PREFIXES = ("", "0x", "0X")


def _hex_format_cases() -> list[tuple[HexFormat, str]]:
    """Every meaningful (separator, prefix, per-byte, case) combination.

    ``prefix_per_byte`` is only meaningful when there is a prefix, so the
    no-prefix half of that axis is dropped rather than duplicated.
    """
    cases = []
    for sep in _SEPARATORS:
        for prefix in _PREFIXES:
            per_byte_options = (False,) if not prefix else (False, True)
            for per_byte in per_byte_options:
                for upper in (False, True):
                    fmt = HexFormat(
                        separator=sep, prefix=prefix,
                        prefix_per_byte=per_byte, uppercase=upper,
                    )
                    label = (
                        f"sep={sep!r}-prefix={prefix or 'none'}"
                        f"-{'per-byte' if per_byte else 'single'}"
                        f"-{'upper' if upper else 'lower'}"
                    )
                    cases.append((fmt, label))
    return cases


_HEX_FORMAT_CASES = _hex_format_cases()


class TestRoundTripHexText:
    """Hex-text packets in any supported formatting round-trip
    byte-for-byte through ``detect -> encode_match``.

    Enumerates the whole ``HexFormat`` cross-product rather than a handful
    of hand-picked strings, so a formatting the parser or the printer
    mishandles cannot survive by nobody having written it down.  A
    combination ``detect`` declines to read as hex is a real finding, not a
    skip: the assertion says so.
    """

    @pytest.mark.parametrize(
        "fmt",
        [c[0] for c in _HEX_FORMAT_CASES],
        ids=[c[1] for c in _HEX_FORMAT_CASES],
    )
    def test_round_trip_byte_for_byte(self, fmt: HexFormat) -> None:
        # Arrange -- build the packet in this exact surface format.
        packet = _hex_text(CHECK_INPUT_BYTES + b"\xcb\xf4\x39\x26", fmt)

        # Act
        result = detect(packet)
        assert result.candidates, (
            f"detect found no CRC in a well-formed hex packet: {packet!r}"
        )
        rebuilt = encode_match(CHECK_INPUT_BYTES, result.candidates[0])

        # Assert -- the rebuilt packet reproduces the input surface exactly.
        assert isinstance(rebuilt, str), (
            f"hex-text encode_match should return str, got {type(rebuilt).__name__}"
        )
        assert rebuilt == packet, (
            f"hex-text round-trip mismatch:\n  in:  {packet!r}\n  out: {rebuilt!r}"
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
        expected_padding = TextFormat(separator=sep, prefix=leader, uppercase=upper)
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


class TestCustomAlgorithmInfo:
    """encode / encode_int / verify accept an AlgorithmInfo, not just a name --
    so a custom / recovered polynomial can be checksummed with the same code."""

    @staticmethod
    def _custom() -> AlgorithmInfo:
        # A custom poly NOT in the catalogue (width 16, reflected).
        w, p, i, ri, ro, xo = 16, 0x1009, 0xFFFF, True, True, 0x0000
        check = generic_crc(b"123456789", Crc(w, p, i, ri, ro, xo))
        return AlgorithmInfo(w, p, i, ri, ro, xo, check, "custom", "test")

    def test_info_passthrough_matches_name(self) -> None:
        # A catalogue AlgorithmInfo produces the same packet as its name.
        info = ALGORITHMS["crc32"]
        by_info, by_name = encode(b"hello", info), encode(b"hello", "crc32")
        assert by_info == by_name, "AlgorithmInfo and name must encode identically"

    def test_encode_verify_round_trip_custom(self) -> None:
        info = self._custom()
        packet = encode(b"payload", info)
        result = verify(packet, info)
        assert result.valid is True, "custom AlgorithmInfo must encode->verify clean"
        actual = encode_int(b"payload", info)
        assert actual == result.expected, "encode_int agrees with verify.expected"


class TestCompute:
    """``compute``: the name-string CRC, sharing the CLI/MCP verb."""

    def test_compute_by_name_matches_check(self):
        # Assert -- the canonical check value via the shared verb.
        actual = compute(b"123456789", "crc16-modbus")
        assert actual == 0x4B37, f"compute crc16-modbus check, got {actual:#x}"

    def test_compute_is_encode_int(self):
        # Assert -- one implementation, two names; no behavioral drift.
        data = b"the quick brown fox"
        actual = compute(data, "crc32")
        expected = encode_int(data, "crc32")
        assert actual == expected, "compute must be an alias of encode_int"

    def test_compute_accepts_custom_algorithm(self):
        # Arrange -- a one-call custom spec.
        algo = custom_algorithm(width=8, poly=0x07)
        # Assert -- compute takes the AlgorithmInfo directly.
        actual = compute(b"123456789", algo)
        assert actual == algo.check, (
            f"compute over a custom algorithm: {actual:#x} != {algo.check:#x}"
        )


_MODBUS_PAYLOADS = [
    b"\x01\x03\x00\x00\x00\x01",
    b"\x01\x03\x02\x00\x2a",
    b"\x02\x06\x00\x10\x12\x34",
    b"\x11\x03\x00\x6b\x00\x03",
    b"\x0a\x01\x00\x13\x00\x25",
    b"\x04\x04\x00\x08\x00\x01",
]


def _modbus_frames(trail=b""):
    algo = ALGORITHMS["crc16-modbus"]
    return [
        p + generic_crc(p, algo).to_bytes(2, "little") + trail
        for p in _MODBUS_PAYLOADS
    ]


class TestEncodeMatchRoundTripsATrailingDelimiter:
    """``encode_match`` is the round-trip pair to ``detect``, so a frame that
    detected with a delimiter has to rebuild *with* that delimiter.  Dropping
    it would emit a frame the device would not accept.
    """

    def test_a_binary_frame_rebuilds_with_its_delimiter(self):
        # Arrange
        frames = _modbus_frames(b"\r\n")
        match = detect(frames).candidates[0]

        # Act
        actual = encode_match(_MODBUS_PAYLOADS[0], match)

        # Assert -- byte-identical to the frame that went in.
        assert actual == frames[0], f"{actual!r} != original {frames[0]!r}"

    def test_a_hex_frame_rebuilds_with_its_delimiter(self):
        # Arrange -- the delimiter was inside the hex string on the way in.
        frames = [f.hex() for f in _modbus_frames(b"\r\n")]
        match = detect(frames).candidates[0]

        # Act
        actual = encode_match(_MODBUS_PAYLOADS[0], match)

        # Assert
        assert actual == frames[0], f"{actual!r} != original {frames[0]!r}"

    def test_a_clean_frame_gains_nothing(self):
        # Arrange
        frames = _modbus_frames()
        match = detect(frames).candidates[0]

        # Act / Assert -- unchanged behaviour for a frame with no delimiter.
        actual = encode_match(_MODBUS_PAYLOADS[0], match)
        assert actual == frames[0], f"{actual!r} != original {frames[0]!r}"


class TestEncodeMatchRefusesAMixedRecord:
    """``detect`` reports the first packet's separator when packets disagree,
    and names the disagreement in ``mixed``.  That is fine as a description,
    but rebuilding from it would emit a shape part of the input never had, so
    ``encode_match`` refuses rather than picking for the caller.
    """

    def _mixed_match(self):
        algo = ALGORITHMS["crc16-xmodem"]
        msgs = [b"HELLO", b"WORLD", b"THIRD", b"FOURTH"]
        frames = [
            f"{m.decode()}{' ' if i % 2 else chr(9)}{generic_crc(m, algo):04X}"
            for i, m in enumerate(msgs)
        ]
        return detect(frames).candidates[0]

    def test_rebuilding_from_a_mixed_record_raises(self):
        # Arrange
        match = self._mixed_match()
        assert match.padding.mixed, "arrange failed: this record should be mixed"

        # Act / Assert
        with pytest.raises(MixedFormatError) as exc:
            encode_match("HELLO", match)
        message = str(exc.value)
        assert "separator" in message, f"message must name the field: {message}"

    def test_the_refusal_is_also_a_value_error(self):
        # Assert -- house hierarchy: crcglot base AND the stdlib type.
        with pytest.raises(ValueError):
            encode_match("HELLO", self._mixed_match())

    def test_a_uniform_record_still_rebuilds(self):
        # Arrange -- same frames, one shape.
        algo = ALGORITHMS["crc16-xmodem"]
        msgs = [b"HELLO", b"WORLD", b"THIRD"]
        frames = [f"{m.decode()} {generic_crc(m, algo):04X}" for m in msgs]
        match = detect(frames).candidates[0]

        # Act
        actual = encode_match("HELLO", match)

        # Assert
        assert actual == frames[0], f"{actual!r} != original {frames[0]!r}"
