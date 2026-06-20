"""Tests for the brute-force CRC detector.

Covers per-mode round-trip (binary + text), multi-packet intersection,
the three match modes (first / all / set), the algorithms glob filter,
and edge cases (width-8 endianness dedup, empty input, too-short packets).
"""

from __future__ import annotations

from typing import Literal

import pytest

from crcglot import (
    ALGORITHMS,
    DetectResult,
    HexFormat,
    TextFormat,
    detect,
    detect_iter,
)
from crcglot._detect import _PRIORITY, _ordered_algorithm_names


# A canonical reveng input: every catalogue entry's ``check`` value is
# the CRC of this exact string.  Sharing it across tests keeps fixtures
# trivial.
CHECK_INPUT_TEXT = "123456789"
CHECK_INPUT_BYTES = b"123456789"


def _binary_packet(
    name: str, endianness: Literal["big", "little"] = "big",
) -> bytes:
    """Build a known-good binary packet for the given algorithm."""
    algo = ALGORITHMS[name]
    # ceil: a sub-byte / non-byte-aligned CRC is right-justified, zero-padded
    # into ceil(width/8) bytes (e.g. CRC-15 -> 2 bytes).
    w = (algo.width + 7) // 8
    return CHECK_INPUT_BYTES + algo.check.to_bytes(w, endianness)


def _text_packet(name: str, sep: str = " ", leader: str = "", upper: bool = False) -> str:
    """Build a canonical text packet for the given algorithm."""
    algo = ALGORITHMS[name]
    hex_chars = (algo.width + 3) // 4  # ceil: CRC-15 -> 4 nibbles ("059e")
    crc_hex = f"{algo.check:0{hex_chars}x}"
    if upper:
        crc_hex = crc_hex.upper()
    return f"{CHECK_INPUT_TEXT}{sep}{leader}{crc_hex}"


class TestBinaryRoundTrip:
    """Every algorithm × every endianness must be identifiable from a
    canonical packet."""

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_big_endian_round_trip(self, name: str) -> None:
        # Arrange
        packet = _binary_packet(name, "big")

        # Act
        result = detect(packet, match="all")

        # Assert
        actual_algos = {m.algorithm for m in result.candidates}
        assert result.matched, f"binary BE detect failed for {name}: result={result}"
        assert name in actual_algos, (
            f"expected {name!r} in candidates, got {actual_algos}"
        )

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_little_endian_round_trip(self, name: str) -> None:
        # Arrange
        algo = ALGORITHMS[name]
        packet = _binary_packet(name, "little")

        # Act
        result = detect(packet, match="all")

        # Assert
        actual = {(m.algorithm, m.endianness) for m in result.candidates}
        # Single-byte CRC fields (width <= 8, incl. the sub-byte 3-7) have
        # BE==LE and dedup to "big"; wider fields must match LE explicitly.
        single_byte = (algo.width + 7) // 8 == 1
        expected_endian = "big" if single_byte else "little"
        assert result.matched, f"binary LE detect failed for {name}: result={result}"
        assert (name, expected_endian) in actual, (
            f"expected ({name!r}, {expected_endian!r}) in {actual}"
        )


class TestSubByteDetect:
    """Detection of sub-byte / non-byte-aligned CRCs: the field occupies
    ceil(width/8) bytes, compared strictly (zero pad bits are evidence,
    not masked away).  Guards the ceil/strict-compare fixes and the
    target_crc byte-reversal that used to overflow on these widths."""

    # crc15-can (15b), crc11-flexray (11b, odd-nibble), crc24-openpgp (24b,
    # byte-aligned-but-new), crc5-usb (5b, single-byte field, reflected).
    SAMPLES = ["crc15-can", "crc11-flexray", "crc24-openpgp", "crc5-usb"]

    # Only non-byte-aligned widths have a pad bit to corrupt (a byte-aligned
    # width like CRC-24 fills its bytes exactly), so this test parametrizes
    # over the sub-byte/non-byte-aligned samples only -- no skips.
    @pytest.mark.parametrize(
        "name", [s for s in SAMPLES if ALGORITHMS[s].width % 8 != 0]
    )
    def test_garbage_pad_bit_is_rejected(self, name: str) -> None:
        # Arrange -- a non-byte-aligned CRC with a high pad bit forced on
        # (a value that doesn't fit the width) must NOT be claimed as a
        # match: strict compare rejects it, masking would falsely accept.
        algo = ALGORITHMS[name]
        w = (algo.width + 7) // 8
        dirty = CHECK_INPUT_BYTES + (algo.check | (1 << algo.width)).to_bytes(w, "big")

        # Act
        result = detect(dirty, algorithms=name)

        # Assert
        assert not result.matched, (
            f"{name}: garbage pad bit must be rejected, got {result.candidates}"
        )

    @pytest.mark.parametrize("name", SAMPLES)
    def test_target_crc_default_endian_does_not_crash(self, name: str) -> None:
        # Arrange -- the default endian="both" byte-reverses the target at
        # the field's byte length; a sub-byte width used to overflow here.
        algo = ALGORITHMS[name]

        # Act -- must not raise, and must identify the algorithm.
        result = detect(CHECK_INPUT_BYTES, target_crc=algo.check, algorithms=name)

        # Assert
        assert result.matched, f"target_crc detect failed for {name}: {result}"
        assert result.algorithm == name, (
            f"expected {name!r}, got {result.algorithm!r}"
        )

    def test_full_catalogue_target_crc_no_overflow(self) -> None:
        # Arrange / Act -- a full-catalogue target_crc scan (default both
        # endian) reaches the sub-byte entries; this used to raise
        # OverflowError and poison every default detect(..., target_crc=...).
        target = ALGORITHMS["crc16-modbus"].check
        result = detect(CHECK_INPUT_BYTES, target_crc=target)

        # Assert
        assert result.matched, "full-catalogue target_crc scan should match crc16-modbus"

    def test_odd_nibble_text_matches_big_only(self) -> None:
        # Arrange -- crc11-flexray is 3 hex nibbles; LE across 1.5 bytes is
        # undefined, so it must match big-endian and never crash on fromhex.
        algo = ALGORITHMS["crc11-flexray"]
        packet = f"{CHECK_INPUT_TEXT} {algo.check:03x}"

        # Act
        result = detect(packet, mode="text", algorithms="crc11-flexray", match="all")

        # Assert
        endians = {(m.algorithm, m.endianness) for m in result.candidates}
        assert ("crc11-flexray", "big") in endians, (
            f"expected big-endian crc11-flexray match, got {endians}"
        )
        assert ("crc11-flexray", "little") not in endians, (
            f"odd-nibble width must not yield a little-endian match, got {endians}"
        )


class TestTextRoundTrip:
    """Canonical text formats and their variants must be identifiable."""

    def test_canonical_form_single_space_no_leader(self) -> None:
        # Arrange
        packet = _text_packet("crc32", sep=" ", leader="", upper=False)

        # Act
        result = detect(packet)

        # Assert
        assert result.matched, f"canonical text detect failed: result={result}"
        actual_padding = result.candidates[0].padding
        expected_padding = TextFormat(separator=" ", prefix="", uppercase=False)
        assert actual_padding == expected_padding, (
            f"expected padding={expected_padding}, got {actual_padding}"
        )

    @pytest.mark.parametrize(
        "sep,leader,upper",
        [
            ("\t", "", False),
            ("  ", "", False),
            (" ", "0x", False),
            (" ", "0X", True),
            ("\t", "0X", True),
        ],
    )
    def test_text_variants(self, sep: str, leader: str, upper: bool) -> None:
        # Arrange
        packet = _text_packet("crc32", sep=sep, leader=leader, upper=upper)

        # Act
        result = detect(packet, match="all")

        # Assert
        actual = result.candidates[0].padding
        expected = TextFormat(separator=sep, prefix=leader, uppercase=upper)
        assert result.matched, f"text variant detect failed: result={result}"
        assert actual == expected, f"padding mismatch: actual={actual} expected={expected}"


class TestHexTextInput:
    """A ``str`` that's a hex-encoded byte packet -- with any common
    formatting (``0x`` prefix, spaces, commas, colons, newlines) -- is
    auto-detected and decoded; the text-mode fallback is preserved when
    the input doesn't look like pure hex."""

    # All cases below are the canonical CRC-32 packet
    # ``b"123456789" + 0xCBF43926.to_bytes(4, "big")`` formatted as hex.
    @pytest.mark.parametrize(
        "raw",
        [
            "313233343536373839cbf43926",                                # no separators
            "31 32 33 34 35 36 37 38 39 cb f4 39 26",                    # spaces (wireshark)
            "0x31 0x32 0x33 0x34 0x35 0x36 0x37 0x38 0x39 0xcb 0xf4 0x39 0x26",  # 0x prefix per byte
            "0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39,0xcb,0xf4,0x39,0x26",  # comma-separated (C array style)
            "31:32:33:34:35:36:37:38:39:CB:F4:39:26",                    # xxd / MAC-address style (colon + upper)
            "31 32 33 34 35\n36 37 38 39\ncb f4 39 26",                  # multi-line
            "  0X313233343536373839CBF43926  ",                          # one big 0X-prefixed token, outer whitespace
        ],
        ids=[
            "no-separator",
            "space-per-byte",
            "0x-per-byte",
            "comma-separated",
            "colon-uppercase",
            "multi-line",
            "single-prefixed-token",
        ],
    )
    def test_hex_text_auto_detect(self, raw: str) -> None:
        # Act
        result = detect(raw)
        # Assert
        assert result.matched, f"hex-text auto-detect failed: {raw!r}"
        assert result.algorithm == "crc32", f"expected crc32, got {result.algorithm!r}"
        # Hex-decoded packets get a HexFormat in padding -- captures the
        # surface formatting so encode_match can round-trip.
        assert isinstance(result.candidates[0].padding, HexFormat), (
            f"hex packet should yield HexFormat padding: {result.candidates[0]}"
        )

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (
                "313233343536373839cbf43926",
                HexFormat(separator="", prefix="", prefix_per_byte=False, uppercase=False),
            ),
            (
                "31 32 33 34 35 36 37 38 39 cb f4 39 26",
                HexFormat(separator=" ", prefix="", prefix_per_byte=False, uppercase=False),
            ),
            (
                "0x31 0x32 0x33 0x34 0x35 0x36 0x37 0x38 0x39 0xcb 0xf4 0x39 0x26",
                HexFormat(separator=" ", prefix="0x", prefix_per_byte=True, uppercase=False),
            ),
            (
                "0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39,0xcb,0xf4,0x39,0x26",
                HexFormat(separator=",", prefix="0x", prefix_per_byte=True, uppercase=False),
            ),
            (
                "31:32:33:34:35:36:37:38:39:CB:F4:39:26",
                HexFormat(separator=":", prefix="", prefix_per_byte=False, uppercase=True),
            ),
            (
                "0X313233343536373839CBF43926",
                HexFormat(separator="", prefix="0X", prefix_per_byte=False, uppercase=True),
            ),
        ],
        ids=[
            "no-separator",
            "space",
            "0x-per-byte-space",
            "0x-per-byte-comma",
            "colon-upper",
            "0X-single-prefix-upper",
        ],
    )
    def test_hex_format_captured_precisely(self, raw: str, expected: HexFormat) -> None:
        # Act
        actual_padding = detect(raw).candidates[0].padding
        # Assert
        assert actual_padding == expected, (
            f"HexFormat mismatch for {raw!r}: actual={actual_padding} expected={expected}"
        )

    def test_text_mode_still_works_when_string_isnt_pure_hex(self) -> None:
        # Arrange -- after stripping the separator, "123456789cbf43926" is
        # 17 chars (odd) so it can't be hex; falls through to text mode.
        # Act
        result = detect("123456789 cbf43926")
        # Assert
        assert result.matched, "canonical text-mode should still resolve"
        assert result.candidates[0].padding is not None, (
            "text-mode hit should preserve padding"
        )

    def test_explicit_hex_mode_rejects_non_hex(self) -> None:
        # Arrange -- ``mode="hex"`` must NOT fall back to text mode
        # when the input doesn't decode as hex bytes.
        # Act
        result = detect("hello world this is not hex", mode="hex")
        # Assert
        assert not result.matched, (
            f"mode='hex' on non-hex input should not match: {result}"
        )

    def test_explicit_hex_mode_on_bytes_raises(self) -> None:
        # Act / Assert -- bytes input + mode="hex" is a caller error; the message
        # echoes the offending type and position.
        with pytest.raises(TypeError, match=r"hex mode requires all str packets; got bytes at index 0"):
            detect(b"abc", mode="hex")

    def test_explicit_hex_mode_iter_on_bytes_raises(self) -> None:
        # Act / Assert -- same for the iter API, with the offending type echoed.
        from crcglot import detect_iter
        with pytest.raises(TypeError, match=r"hex mode requires a str packet; got bytes"):
            list(detect_iter(b"abc", mode="hex"))


class TestTextOuterWhitespace:
    """Outer whitespace -- leading indentation, trailing newlines, CRLF
    line endings -- must be transparent.  The CRC is over the trimmed
    payload; the internal ``separator`` between data and hex is still
    captured verbatim."""

    @pytest.mark.parametrize(
        "wrapper",
        [
            "  {p}",            # leading spaces
            "\t{p}",            # leading tab
            "{p}\n",            # trailing newline (already worked; regression guard)
            "{p}\r\n",          # CRLF line ending
            "\n\n{p}\n\n",      # both ends, newlines
            "  \t{p}\r\n",      # mixed leading + CRLF trailing
        ],
        ids=[
            "leading-spaces",
            "leading-tab",
            "trailing-newline",
            "trailing-crlf",
            "both-newlines",
            "mixed",
        ],
    )
    def test_outer_whitespace_is_transparent(self, wrapper: str) -> None:
        # Arrange
        canonical = _text_packet("crc32", sep=" ", leader="", upper=False)
        packet = wrapper.format(p=canonical)

        # Act
        result = detect(packet)

        # Assert
        assert result.matched, (
            f"outer whitespace should be ignored: wrapper={wrapper!r} packet={packet!r}"
        )
        assert result.candidates[0].algorithm == "crc32", (
            f"expected crc32, got {result.algorithm}"
        )
        # The internal separator (between data and hex) is still preserved.
        padding = result.candidates[0].padding
        assert isinstance(padding, TextFormat), (
            f"outer-whitespace text-mode hit should yield TextFormat padding, got {padding!r}"
        )
        actual_sep = padding.separator
        assert actual_sep == " ", (
            f"internal separator should still be ' ', got {actual_sep!r}"
        )


class TestMultiPacketIntersection:
    """Multi-packet input intersects per-packet candidate sets."""

    def test_three_packets_same_algorithm(self) -> None:
        # Arrange -- three real CRC-32 packets over different data.
        from crcglot import generic_crc
        algo = ALGORITHMS["crc32"]
        datas = [b"123456789", b"hello world", b"the quick brown fox"]
        packets: list[bytes] = []
        for d in datas:
            crc = generic_crc(d, algo)
            packets.append(d + crc.to_bytes(algo.width // 8, "big"))

        # Act
        result = detect(packets, match="all")

        # Assert
        actual_names = {m.algorithm for m in result.candidates}
        assert result.matched, f"multi-packet positive failed: result={result}"
        assert "crc32" in actual_names, f"expected crc32 in {actual_names}"

    def test_two_packets_different_algorithm_no_match(self) -> None:
        # Arrange
        packets = [
            _binary_packet("crc32", "big"),
            _binary_packet("crc16-arc", "big"),
        ]

        # Act
        result = detect(packets, match="all")

        # Assert
        # crc32 only matches packet 0; crc16-arc only matches packet 1.
        # Intersection should be empty (unless some 8-bit happens to fit both,
        # which is highly unlikely on real check values).
        candidates_with_crc32 = {m.algorithm for m in result.candidates}
        assert "crc32" not in candidates_with_crc32, (
            f"crc32 should not match packet 2, got {candidates_with_crc32}"
        )
        assert "crc16-arc" not in candidates_with_crc32, (
            f"crc16-arc should not match packet 1, got {candidates_with_crc32}"
        )


class TestWidth8Dedup:
    """8-bit algorithms must not yield duplicate (algo, 'little') entries
    because BE == LE for a single byte."""

    def test_no_duplicate_endianness_for_width_8(self) -> None:
        # Arrange
        # Pick one well-known 8-bit algorithm; the dedup is structural.
        name = "crc8"
        packet = _binary_packet(name)

        # Act
        result = detect(packet, match="all")

        # Assert
        endianness_for_name = [m.endianness for m in result.candidates if m.algorithm == name]
        assert len(endianness_for_name) == 1, (
            f"width-8 {name} should appear once, got {len(endianness_for_name)}: {endianness_for_name}"
        )
        assert endianness_for_name[0] == "big", (
            f"width-8 dedup should keep 'big', got {endianness_for_name[0]!r}"
        )


class TestEmptyAndShort:
    """Empty / too-short / unparseable input must return matched=False."""

    def test_empty_bytes(self) -> None:
        # Act
        actual = detect(b"", match="all")
        # Assert
        assert not actual.matched, f"empty bytes should not match: {actual}"

    def test_single_byte_too_short_for_width_16(self) -> None:
        # Act -- 1 byte is too short for any algorithm with width > 8.
        # Width-8 may still try (data=b'', crc=b'\x00'); whether it matches is fine.
        actual = detect(b"\x00", match="all")
        # Assert -- no crash; matched may be True or False (depends on CRC catalogue).
        assert isinstance(actual, DetectResult), (
            f"expected DetectResult, got {type(actual).__name__}"
        )

    def test_unparseable_text_no_whitespace(self) -> None:
        # Act -- text without a whitespace+hex tail can't be parsed.
        actual = detect("nothex")
        # Assert
        assert not actual.matched, f"unparseable text should not match: {actual}"

    def test_empty_iterable(self) -> None:
        # Act
        actual = detect([], match="all")
        # Assert
        assert not actual.matched, f"empty iterable should not match: {actual}"


class TestPriorityOrder:
    """The scan must visit crc32 / crc32-jamcrc / crc32-iscsi first."""

    def test_priority_head_in_order(self) -> None:
        # Arrange
        actual_order = _ordered_algorithm_names(None)

        # Assert
        actual_head = tuple(actual_order[:3])
        expected_head = _PRIORITY
        assert actual_head == expected_head, (
            f"priority head wrong: actual={actual_head} expected={expected_head}"
        )

    def test_detect_iter_yields_priority_first(self) -> None:
        # Arrange
        packet = _binary_packet("crc32", "big")
        attempts = []
        # Act -- consume the first 6 yields (3 priority × big/little where applicable).
        it = detect_iter(packet)
        for _ in range(6):
            attempts.append(next(it))

        # Assert
        actual_names_first_three = [a.algorithm for a in attempts[:3]]
        # The first attempt is crc32 big, then crc32 little, then jamcrc big, ...
        # We assert priority NAMES appear before any non-priority name.
        assert all(
            n in _PRIORITY for n in actual_names_first_three
        ), f"first three attempts not all priority: {actual_names_first_three}"


class TestMatchModes:
    """first / all / set must each behave per their contract."""

    def test_first_returns_at_most_one(self) -> None:
        # Arrange
        packet = _binary_packet("crc32", "big")

        # Act
        actual = detect(packet, match="first")

        # Assert
        assert actual.matched, f"first mode missed crc32: {actual}"
        assert len(actual.candidates) == 1, (
            f"first mode returned multiple: {actual.candidates}"
        )
        assert actual.candidates[0].algorithm == "crc32", (
            f"first should hit crc32, got {actual.algorithm}"
        )

    def test_all_returns_at_least_first_match(self) -> None:
        # Arrange
        packet = _binary_packet("crc32", "big")

        # Act
        actual = detect(packet, match="all")

        # Assert
        actual_names = {m.algorithm for m in actual.candidates}
        assert actual.matched, f"all mode missed crc32: {actual}"
        assert "crc32" in actual_names, f"crc32 not in {actual_names}"

    def test_set_succeeds_when_unique(self) -> None:
        # Arrange -- crc32 is the only algorithm with width 32 and these exact
        # parameters that matches; force uniqueness by filtering.
        packet = _binary_packet("crc32", "big")

        # Act
        actual = detect(packet, match="set", algorithms="crc32")

        # Assert
        assert actual.matched, f"set mode missed unique crc32: {actual}"
        assert len(actual.candidates) == 1, (
            f"set mode returned multiple: {actual.candidates}"
        )
        assert actual.candidates[0].algorithm == "crc32"

    def test_set_fails_when_ambiguous_8bit(self) -> None:
        # Arrange -- a single random byte is a valid CRC for many 8-bit algos
        # over an empty data prefix.  ``match="all"`` will return >=2 candidates;
        # ``match="set"`` should refuse.
        # Build a 2-byte packet so several CRC-8 entries try BOTH the empty-prefix
        # case AND the 1-byte-data case.
        packet = b"\x00\x00"

        # Act
        all_result = detect(packet, match="all")
        set_result = detect(packet, match="set")

        # Assert -- if all_result has >=2 distinct algorithms, set must reject.
        unique_algos_all = {m.algorithm for m in all_result.candidates}
        if len(unique_algos_all) >= 2:
            assert not set_result.matched, (
                f"set should refuse ambiguous result; "
                f"all={unique_algos_all} set={set_result}"
            )


class TestAlgorithmsFilter:
    """fnmatch glob filtering narrows the scan and reduces false positives."""

    def test_filter_narrows_to_family(self) -> None:
        # Arrange
        packet = _binary_packet("crc16-arc", "big")

        # Act
        actual = detect(packet, algorithms="crc16-*", match="all")

        # Assert
        actual_names = {m.algorithm for m in actual.candidates}
        assert actual.matched, f"filter missed crc16-arc: {actual}"
        assert all(
            n.startswith("crc16-") for n in actual_names
        ), f"non-crc16 in filtered result: {actual_names}"
        assert "crc16-arc" in actual_names, (
            f"crc16-arc missing from {actual_names}"
        )

    def test_filter_excludes_others(self) -> None:
        # Arrange -- a crc32 packet with the filter set to crc16-*.
        packet = _binary_packet("crc32", "big")

        # Act
        actual = detect(packet, algorithms="crc16-*", match="all")

        # Assert -- crc32 should not be reachable through a crc16-* filter.
        assert "crc32" not in {m.algorithm for m in actual.candidates}, (
            f"crc32 leaked through crc16-* filter: {actual.candidates}"
        )

    def test_unmatched_glob_returns_no_match(self) -> None:
        # Arrange
        packet = _binary_packet("crc32", "big")

        # Act
        actual = detect(packet, algorithms="nope-*", match="all")

        # Assert
        assert not actual.matched, f"unmatched glob should fail: {actual}"


class TestMultiPacketSet:
    """match='set' collapses ambiguity when paired with more packets."""

    def test_set_multi_packet_collapse(self) -> None:
        # Arrange -- a CRC-32 packet built over data X, and another over data Y.
        # crc32 should match both; the intersection collapses incidental hits.
        from crcglot import generic_crc
        algo = ALGORITHMS["crc32"]
        data1, data2 = b"123456789", b"the quick brown fox"
        crc1 = generic_crc(data1, algo)
        crc2 = generic_crc(data2, algo)
        packets = [
            data1 + crc1.to_bytes(4, "big"),
            data2 + crc2.to_bytes(4, "big"),
        ]

        # Act
        actual = detect(packets, match="set")

        # Assert -- two real CRC-32 packets must collapse to a single algorithm.
        assert actual.matched, f"two crc32 packets should agree: {actual}"
        actual_names = {m.algorithm for m in actual.candidates}
        assert actual_names == {"crc32"}, (
            f"expected only crc32, got {actual_names}"
        )


class TestModeAndEncoding:
    """Auto-mode resolution and explicit overrides."""

    def test_auto_mode_bytes_is_binary(self) -> None:
        # Act
        actual = detect(_binary_packet("crc32", "big"))
        # Assert
        assert actual.matched, f"auto-mode bytes should detect: {actual}"
        # Binary mode means padding=None.
        assert actual.candidates[0].padding is None, (
            f"binary mode should have padding=None: {actual.candidates[0]}"
        )

    def test_auto_mode_str_is_text(self) -> None:
        # Act
        actual = detect(_text_packet("crc32"))
        # Assert
        assert actual.matched, f"auto-mode str should detect: {actual}"
        assert actual.candidates[0].padding is not None, (
            f"text mode should have padding set: {actual.candidates[0]}"
        )

    def test_mixed_types_rejected(self) -> None:
        # Act / Assert
        with pytest.raises(TypeError, match="mixed"):
            detect([b"abc", "def"], match="all")


class TestDetectCli:
    """End-to-end CLI smoke tests for ``crcglot detect``."""

    def test_text_inline_round_trip(self) -> None:
        import subprocess
        import sys
        # Act
        proc = subprocess.run(
            [sys.executable, "-m", "crcglot.cli",
             "detect", "--text", "123456789 cbf43926"],
            capture_output=True, text=True,
        )
        # Assert
        assert proc.returncode == 0, f"exit={proc.returncode} stderr={proc.stderr}"
        assert "crc32" in proc.stdout, f"crc32 missing from stdout: {proc.stdout!r}"

    def test_hex_binary_round_trip(self) -> None:
        import subprocess
        import sys
        # Arrange -- canonical crc32 of '123456789' is cbf43926.
        hex_packet = "313233343536373839cbf43926"
        # Act
        proc = subprocess.run(
            [sys.executable, "-m", "crcglot.cli", "detect", "--hex", hex_packet],
            capture_output=True, text=True,
        )
        # Assert
        assert proc.returncode == 0, f"exit={proc.returncode} stderr={proc.stderr}"
        assert "crc32" in proc.stdout

    def test_no_match_exits_one(self) -> None:
        import subprocess
        import sys
        # Act
        proc = subprocess.run(
            [sys.executable, "-m", "crcglot.cli",
             "detect", "--text", "no match here 00000000"],
            capture_output=True, text=True,
        )
        # Assert
        assert proc.returncode == 1, (
            f"expected exit 1 for garbage, got {proc.returncode}: stdout={proc.stdout!r}"
        )


class TestDetectIter:
    """detect_iter is a generator surface for streaming attempts."""

    def test_detect_iter_yields_attempts(self) -> None:
        # Arrange
        packet = _binary_packet("crc32", "big")

        # Act
        attempts = list(detect_iter(packet))

        # Assert -- at least 69*1 attempts (≥1 endianness per algo); at least
        # one Attempt.matched=True (crc32 BE).
        actual_matched = [a for a in attempts if a.matched]
        assert len(attempts) >= len(ALGORITHMS), (
            f"too few attempts: {len(attempts)} < {len(ALGORITHMS)}"
        )
        assert any(a.algorithm == "crc32" and a.endianness == "big" and a.matched
                   for a in actual_matched), (
            f"crc32 BE matched not in attempts: {[(a.algorithm, a.endianness) for a in actual_matched]}"
        )


class TestTargetCrc:
    """``target_crc=<int>`` short-circuits CRC-tail extraction: the
    whole packet is data, and the supplied integer is what
    ``generic_crc(data)`` must match."""

    def test_binary_data_with_target_crc(self) -> None:
        # Arrange -- canonical reveng input, canonical crc32 check value.
        # Act
        result = detect(b"123456789", target_crc=0xCBF43926)
        # Assert
        assert result.matched, "binary data + target_crc=0xCBF43926 should match crc32"
        assert result.algorithm == "crc32", f"expected crc32, got {result.algorithm}"
        assert result.candidates[0].padding is None, (
            "target_crc path returns padding=None (no surface format captured)"
        )
        assert result.candidates[0].endianness == "big", (
            "0xCBF43926 is the natural BE reading of crc32(123456789); "
            "the match should report endianness='big'"
        )

    def test_text_data_with_target_crc(self) -> None:
        # Arrange -- explicit text mode encodes the str via utf-8.
        # Act
        result = detect("123456789", mode="text", target_crc=0xCBF43926)
        # Assert
        assert result.matched
        assert result.algorithm == "crc32"

    def test_hex_data_with_target_crc(self) -> None:
        # Arrange -- the same payload via hex-text input.
        # Act
        result = detect(
            "0x31 0x32 0x33 0x34 0x35 0x36 0x37 0x38 0x39",
            target_crc=0xCBF43926,
        )
        # Assert
        assert result.matched
        assert result.algorithm == "crc32"

    def test_target_crc_too_big_for_8bit_skips_those_algos(self) -> None:
        # Arrange -- 0xCBF43926 doesn't fit in 8 bits; no crc8-* match
        # should appear in the candidate list.
        # Act
        result = detect(b"123456789", target_crc=0xCBF43926, match="all")
        # Assert
        actual_widths = {m.info.width for m in result.candidates}
        assert 8 not in actual_widths, (
            f"width-8 algos should be skipped (target overflows): {actual_widths}"
        )

    def test_target_crc_no_match_returns_empty(self) -> None:
        # Arrange -- a random integer that no catalogue algorithm produces
        # for the given data; the result should just be matched=False.
        # Act
        result = detect(b"123456789", target_crc=0xDEADBEEF, match="all")
        # Assert
        assert not result.matched, (
            f"no algorithm computes 0xDEADBEEF for '123456789'; got {result}"
        )

    def test_target_crc_multi_packet_requires_all_to_agree(self) -> None:
        # Arrange -- two reveng-canonical samples; the second one's crc32
        # is NOT 0xCBF43926.  Multi-packet with a single target_crc means
        # every packet's CRC under the same algorithm must equal it -- and
        # only the first packet does.  Expect matched=False.
        # Act
        result = detect(
            [b"123456789", b"hello world"],
            target_crc=0xCBF43926,
            match="all",
        )
        # Assert
        assert not result.matched, (
            f"second packet's crc32 isn't 0xCBF43926; should not match: {result}"
        )

    def test_target_crc_negative_raises(self) -> None:
        # Act / Assert
        with pytest.raises(ValueError, match="non-negative"):
            detect(b"abc", target_crc=-1)

    def test_target_crc_via_detect_iter(self) -> None:
        # Arrange -- 0xCBF43926 is the BE reading of crc32(123456789).
        attempts = list(detect_iter(b"123456789", target_crc=0xCBF43926))
        # Assert -- crc32 (BE) is in the priority head and matches.
        be_matches = {
            a.algorithm for a in attempts if a.matched and a.endianness == "big"
        }
        assert "crc32" in be_matches, (
            f"crc32 (BE) missing from matched attempts: "
            f"{[(a.algorithm, a.endianness) for a in attempts if a.matched]}"
        )

    def test_target_crc_iter_yields_both_endians(self) -> None:
        # Arrange -- the default endian='both' yields one Attempt per
        # (algorithm, byte_order) pair, so width-32 algos contribute two
        # and the width-1 algorithms contribute one (BE==LE dedup).
        attempts = list(detect_iter(b"123456789", target_crc=0xCBF43926))
        # Assert
        labels = {a.endianness for a in attempts}
        assert labels == {"big", "little"}, (
            f"expected both endians yielded, got {labels}"
        )

    def test_target_crc_iter_skips_width_overflow(self) -> None:
        # Arrange -- 0xCBF43926 overflows 8/16-bit widths; no such Attempts.
        attempts = list(detect_iter(b"123456789", target_crc=0xCBF43926))
        # Assert
        from crcglot import ALGORITHMS
        for a in attempts:
            assert ALGORITHMS[a.algorithm].width >= 32, (
                f"width-{ALGORITHMS[a.algorithm].width} algo {a.algorithm} "
                f"yielded for target_crc that needs >=32 bits"
            )


class TestEndianSelector:
    """The ``endian`` parameter narrows the byte-order scan: ``"both"``
    (default) tries big and little; ``"big"`` or ``"little"`` forces a
    single ordering, halving the scan and ruling out coincidental
    cross-endianness matches.
    """

    def test_endian_big_finds_be_packet(self) -> None:
        # Arrange -- canonical big-endian crc32 packet.
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="big")
        # Act
        result = detect(packet, endian="big")
        # Assert
        assert result.matched, "BE packet under endian='big' should match"
        assert result.algorithm == "crc32", (
            f"expected crc32, got {result.algorithm}"
        )
        assert result.endianness == "big", (
            f"expected endianness='big', got {result.endianness}"
        )

    def test_endian_big_rejects_le_packet(self) -> None:
        # Arrange -- LE packet must NOT match when only big-endian is tried.
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="little")
        # Act
        result = detect(packet, endian="big", match="all")
        # Assert -- no crc32 candidate should appear (its only fit is LE).
        actual_algos = {m.algorithm for m in result.candidates}
        assert "crc32" not in actual_algos, (
            f"endian='big' should not surface LE crc32 packet: {actual_algos}"
        )

    def test_endian_little_finds_le_packet(self) -> None:
        # Arrange
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="little")
        # Act
        result = detect(packet, endian="little")
        # Assert
        assert result.matched, "LE packet under endian='little' should match"
        assert result.algorithm == "crc32"
        assert result.endianness == "little"

    def test_endian_little_rejects_be_packet(self) -> None:
        # Arrange
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="big")
        # Act
        result = detect(packet, endian="little", match="all")
        # Assert
        actual_algos = {m.algorithm for m in result.candidates}
        assert "crc32" not in actual_algos, (
            f"endian='little' should not surface BE crc32 packet: {actual_algos}"
        )

    def test_endian_both_finds_either_packet(self) -> None:
        # Arrange
        from crcglot import encode

        be = encode(b"123456789", "crc32", endianness="big")
        le = encode(b"123456789", "crc32", endianness="little")
        # Act
        r_be = detect(be, endian="both")
        r_le = detect(le, endian="both")
        # Assert
        assert (r_be.algorithm, r_be.endianness) == ("crc32", "big"), (
            f"BE under both: got {(r_be.algorithm, r_be.endianness)}"
        )
        assert (r_le.algorithm, r_le.endianness) == ("crc32", "little"), (
            f"LE under both: got {(r_le.algorithm, r_le.endianness)}"
        )

    def test_endian_default_is_both(self) -> None:
        # Arrange -- omitting endian= must behave exactly like endian="both".
        from crcglot import encode

        le = encode(b"123456789", "crc32", endianness="little")
        # Act
        default = detect(le)
        explicit_both = detect(le, endian="both")
        # Assert
        actual = (default.algorithm, default.endianness)
        expected = (explicit_both.algorithm, explicit_both.endianness)
        assert actual == expected, (
            f"default behavior diverged from endian='both': {actual} vs {expected}"
        )

    def test_endian_text_mode_big(self) -> None:
        # Arrange -- canonical text packet, hex digits in natural BE reading.
        # Act
        result = detect("123456789 cbf43926", endian="big")
        # Assert
        assert result.matched
        assert result.algorithm == "crc32"
        assert result.endianness == "big"

    def test_endian_text_mode_little_rejects_big_hex(self) -> None:
        # Arrange -- "cbf43926" is the BE reading of crc32(123456789).
        # endian='little' interprets the hex bytes as LE, so no match.
        # Act
        result = detect("123456789 cbf43926", endian="little", match="all")
        # Assert
        actual_algos = {m.algorithm for m in result.candidates}
        assert "crc32" not in actual_algos, (
            f"endian='little' should not match BE hex reading: {actual_algos}"
        )

    def test_endian_iter_big_halves_attempt_count(self) -> None:
        # Arrange -- a 4-byte CRC packet so most algorithms contribute 2
        # attempts under both, 1 under big/little.
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="big")
        # Act
        n_both = sum(1 for _ in detect_iter(packet, endian="both"))
        n_big = sum(1 for _ in detect_iter(packet, endian="big"))
        n_little = sum(1 for _ in detect_iter(packet, endian="little"))
        # Assert -- big and little narrowing produce the same (smaller) count;
        # both is strictly larger because width>1 algos contribute 2 attempts.
        assert n_big == n_little, (
            f"big and little should produce equal attempt counts: {n_big}, {n_little}"
        )
        assert n_both > n_big, (
            f"endian='both' should produce more attempts than 'big' "
            f"(both={n_both}, big={n_big})"
        )

    def test_endian_iter_big_only_emits_big_attempts(self) -> None:
        # Arrange
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="big")
        # Act
        attempts = list(detect_iter(packet, endian="big"))
        # Assert
        non_big = [a for a in attempts if a.endianness != "big"]
        assert not non_big, (
            f"endian='big' should only yield big attempts, got: {non_big[:3]}"
        )

    def test_endian_iter_little_only_emits_little_attempts(self) -> None:
        # Arrange -- width-1 algorithms still report whatever ordering was
        # requested (BE==LE byte-wise but the label respects the caller).
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="little")
        # Act
        attempts = list(detect_iter(packet, endian="little"))
        # Assert
        non_little = [a for a in attempts if a.endianness != "little"]
        assert not non_little, (
            f"endian='little' should only yield little attempts, "
            f"got: {non_little[:3]}"
        )

    def test_endian_multi_packet_intersection(self) -> None:
        # Arrange -- three LE crc32 packets must agree under endian='little'.
        from crcglot import encode

        packets = [
            encode(b"123456789", "crc32", endianness="little"),
            encode(b"abc", "crc32", endianness="little"),
            encode(b"hello world", "crc32", endianness="little"),
        ]
        # Act
        result = detect(packets, endian="little", match="all")
        # Assert -- intersection must contain (crc32, little); under
        # endian='little' no (crc32, big) candidate can sneak in.
        actual_pairs = {(m.algorithm, m.endianness) for m in result.candidates}
        assert ("crc32", "little") in actual_pairs, (
            f"multi-packet LE intersect should pin crc32+little: {actual_pairs}"
        )
        assert ("crc32", "big") not in actual_pairs, (
            f"endian='little' must not surface big candidates: {actual_pairs}"
        )

    def test_endian_set_mode_collapses_to_singleton(self) -> None:
        # Arrange -- BE packet; match='set' under endian='big' should
        # collapse to a single algorithm.  endian='both' on the same
        # packet may also be unique, but narrowing further can only help.
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="big")
        # Act
        result = detect(packet, endian="big", match="set")
        # Assert
        assert result.matched, f"endian='big' match='set' should match: {result}"
        assert result.algorithm == "crc32"
        unique_algos = {m.algorithm for m in result.candidates}
        assert len(unique_algos) == 1, (
            f"match='set' must yield a single algorithm: {unique_algos}"
        )

    def test_endian_set_mode_rejects_wrong_endian(self) -> None:
        # Arrange -- LE packet; endian='big' match='set' should fail to
        # match (the only consistent reading is LE, which is excluded).
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="little")
        # Act
        result = detect(packet, endian="big", match="set")
        # Assert
        assert not result.matched, (
            f"LE packet under endian='big' match='set' should not match: {result}"
        )

    def test_endian_hex_text_mode_narrowing(self) -> None:
        # Arrange -- a hex-encoded byte string (auto-decoded by detect)
        # containing data + BE-ordered crc32 trailer.
        from crcglot import encode

        full = encode(b"123456789", "crc32", endianness="big")
        # Format as "0x12 0x34 ..." style
        hex_text = " ".join(f"0x{b:02x}" for b in full)
        # Act
        be_result = detect(hex_text, endian="big")
        le_result = detect(hex_text, endian="little", match="all")
        # Assert -- BE finds it, LE doesn't surface crc32.
        assert be_result.algorithm == "crc32", (
            f"hex-text under endian='big' should match crc32: {be_result}"
        )
        le_algos = {m.algorithm for m in le_result.candidates}
        assert "crc32" not in le_algos, (
            f"hex-text under endian='little' should not match crc32: {le_algos}"
        )

    def test_target_crc_matches_byte_reversed_le_reading(self) -> None:
        # Arrange -- the caller's tool printed the CRC bytes and read
        # them little-endian, yielding 0x2639F4CB (the byte-reversal of
        # crc32's canonical 0xCBF43926).  Default endian='both' should
        # still identify crc32, reported as endianness='little'.
        # Act
        result = detect(b"123456789", target_crc=0x2639F4CB)
        # Assert
        assert (result.algorithm, result.endianness) == ("crc32", "little"), (
            f"expected (crc32, little) for LE reading of crc32(123456789); "
            f"got {(result.algorithm, result.endianness)}"
        )

    def test_target_crc_le_reading_rejected_under_endian_big(self) -> None:
        # Arrange -- endian='big' tests only the natural integer reading.
        # 0x2639F4CB is NOT the BE reading of any catalogue algorithm's
        # CRC of "123456789", so no match.
        # Act
        result = detect(b"123456789", target_crc=0x2639F4CB, endian="big")
        # Assert -- crc32 specifically must be absent.
        assert not result.matched, (
            f"endian='big' should reject the LE reading of crc32: {result}"
        )

    def test_target_crc_le_reading_accepted_under_endian_little(self) -> None:
        # Arrange -- endian='little' tests only the byte-reversed reading.
        # 0x2639F4CB byte-reversed at width 32 is 0xCBF43926 = crc32(123456789).
        # Act
        result = detect(b"123456789", target_crc=0x2639F4CB, endian="little")
        # Assert
        assert (result.algorithm, result.endianness) == ("crc32", "little"), (
            f"endian='little' + LE reading: "
            f"got {(result.algorithm, result.endianness)}"
        )

    def test_target_crc_endian_both_tries_both_readings(self) -> None:
        # Arrange -- under endian='both', match='all', a width-32
        # target gets BOTH readings tried.  For the canonical BE
        # value, BE wins for crc32; for the LE-reversed value, LE wins.
        # Multi-packet sanity: data = b"123456789", a single packet.
        be_target = 0xCBF43926
        le_target = 0x2639F4CB
        # Act
        r_be = detect(b"123456789", target_crc=be_target, match="all")
        r_le = detect(b"123456789", target_crc=le_target, match="all")
        # Assert -- the BE reading match for the BE target must NOT
        # also report as LE (the two integers differ).
        be_pairs = {(m.algorithm, m.endianness) for m in r_be.candidates}
        le_pairs = {(m.algorithm, m.endianness) for m in r_le.candidates}
        assert ("crc32", "big") in be_pairs, (
            f"BE target should yield (crc32, big): {be_pairs}"
        )
        assert ("crc32", "little") not in be_pairs, (
            f"BE target should NOT yield (crc32, little): {be_pairs}"
        )
        assert ("crc32", "little") in le_pairs, (
            f"LE target should yield (crc32, little): {le_pairs}"
        )
        assert ("crc32", "big") not in le_pairs, (
            f"LE target should NOT yield (crc32, big): {le_pairs}"
        )

    def test_endian_narrows_target_crc_be_reading(self) -> None:
        # Arrange -- 0xCBF43926 is the natural BE integer reading of
        # crc32(123456789).  endian='big' and 'both' should match;
        # endian='little' should NOT (the LE byte-reversed-at-width
        # form of 0xCBF43926 is 0x2639F4CB, not the computed CRC).
        # Act
        r_big = detect(b"123456789", target_crc=0xCBF43926, endian="big")
        r_little = detect(b"123456789", target_crc=0xCBF43926, endian="little")
        r_both = detect(b"123456789", target_crc=0xCBF43926, endian="both")
        # Assert
        assert (r_big.algorithm, r_big.endianness) == ("crc32", "big"), (
            f"endian='big' + BE target: {(r_big.algorithm, r_big.endianness)}"
        )
        assert not r_little.matched, (
            f"endian='little' + BE target should not match; got {r_little}"
        )
        assert (r_both.algorithm, r_both.endianness) == ("crc32", "big"), (
            f"endian='both' + BE target: {(r_both.algorithm, r_both.endianness)}"
        )


class TestDetectWidthFilter:
    """``detect(..., width=N)`` narrows the scan to algorithms of that bit
    width -- a first-class int alternative to an ``algorithms`` glob.
    """

    def test_width_keeps_matching_algorithm(self) -> None:
        # Arrange
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="big")
        # Act
        result = detect(packet, width=32)
        # Assert
        assert result.algorithm == "crc32", (
            f"width=32 should still find crc32, got {result}"
        )

    def test_wrong_width_excludes_the_algorithm(self) -> None:
        # Arrange -- a genuine crc32 frame, but restrict the scan to 16-bit.
        from crcglot import encode

        packet = encode(b"123456789", "crc32", endianness="big")
        # Act
        result = detect(packet, width=16, match="all")
        # Assert -- crc32 is excluded, and any survivor is 16-bit.
        algos = {m.algorithm for m in result.candidates}
        actual_widths = {ALGORITHMS[a].width for a in algos}
        assert "crc32" not in algos, f"width=16 must exclude crc32: {algos}"
        assert actual_widths <= {16}, (
            f"width=16 should only surface 16-bit algorithms: {actual_widths}"
        )

    def test_width_narrows_candidate_names(self) -> None:
        # Act -- the helper that detect/detect_iter share.
        names = _ordered_algorithm_names(None, 16)
        # Assert -- only (and all) 16-bit catalogue entries.
        actual_widths = {ALGORITHMS[n].width for n in names}
        expected_widths = {16}
        assert actual_widths == expected_widths, (
            f"width=16 filter should yield exactly 16-bit algos, got {actual_widths}"
        )


class TestAutoModeFilterIndependence:
    """``mode="auto"`` decides hex-vs-text against the FULL catalogue, so an
    ``algorithms`` / ``width`` filter never flips a hex frame into a text
    reinterpretation (the filter narrows the scan, not the interpretation).
    """

    # As hex bytes this is a crc32 frame; its space-separated TEXT reading
    # ("31 33 38 54 74" + crc "5b") is a valid crc8-dvb-s2 frame.  The two
    # readings disagree, which is exactly what makes the filter coupling
    # observable.
    _DUAL = "31 33 38 54 74 5b"

    def test_unfiltered_reads_as_hex_crc32(self) -> None:
        # Assert -- the shape (hex bytes) wins: crc32, under a HexFormat.
        result = detect(self._DUAL)
        assert result.algorithm == "crc32", f"expected hex crc32, got {result}"
        assert isinstance(result.candidates[0].padding, HexFormat), (
            "auto should have read the input as hex bytes"
        )

    def test_algorithms_filter_does_not_flip_to_text(self) -> None:
        # Filtering to the algo that ONLY the text reading matches must not
        # re-read the hex bytes as text -- pre-fix this returned a spurious
        # crc8-dvb-s2 text match.
        result = detect(self._DUAL, algorithms="crc8-dvb-s2")
        assert not result.matched, (
            f"a filter must not flip the hex frame into a text match; got {result}"
        )

    def test_width_filter_does_not_surface_text_only_match(self) -> None:
        # Same guard via the width axis: crc8-dvb-s2 (8-bit) is a text-only
        # match here, so restricting to width 8 must not surface it.
        result = detect(self._DUAL, width=8, match="all")
        algos = {m.algorithm for m in result.candidates}
        assert "crc8-dvb-s2" not in algos, (
            f"width=8 must not surface the text-only crc8-dvb-s2 match; got {algos}"
        )


class TestHexModeOddDigits:
    """Explicit ``mode='hex'`` rejects a malformed (odd-length) hex byte
    string; ``mode='auto'`` stays lenient and reads it as text."""

    def test_hex_mode_odd_raises(self):
        # "abc" is three hex digits -- half a byte short.
        with pytest.raises(ValueError, match="odd number of hex digits"):
            detect("abc", mode="hex")

    def test_hex_mode_odd_with_separators_raises(self):
        # Separators/prefixes don't count: "0x12 0x3" -> "123" -> odd.
        with pytest.raises(ValueError, match="odd number of hex digits"):
            detect("0x12 0x3", mode="hex")

    def test_hex_mode_even_does_not_raise(self):
        # Even-length valid hex decodes fine (match or not, but no error).
        result = detect("1234", mode="hex")
        assert isinstance(result.matched, bool), "even hex must not raise"

    def test_auto_mode_odd_hex_is_lenient(self):
        # auto: an odd hex-ish string is legitimately text, not an error.
        result = detect("abc", mode="auto")
        assert not result.matched, "auto treats odd 'abc' as text -> no match"


class TestDetectMatchForm:
    """``DetectMatch.form`` reports the input representation uniformly:
    binary / hex / text / json (a named form's category).  It derives from
    ``padding`` and never names the form protocol (e.g. crclink)."""

    @pytest.mark.parametrize(
        "packet, expected",
        [
            (CHECK_INPUT_BYTES + (0xCBF43926).to_bytes(4, "big"), "binary"),
            ("313233343536373839cbf43926", "hex"),
            ("123456789 cbf43926", "text"),
            ('{"t":1234,"v":42,"crc":"1352"}', "json"),
        ],
        ids=["binary", "hex", "text", "json"],
    )
    def test_form_reports_representation(self, packet, expected):
        # Act
        result = detect(packet)

        # Assert
        assert result.matched, f"{expected}: packet should detect"
        actual = result.candidates[0].form
        assert actual == expected, f"form {actual!r} != expected {expected!r}"
