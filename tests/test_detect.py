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
from crcglot.detect import _PRIORITY, _ordered_algorithm_names


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
    w = algo.width // 8
    return CHECK_INPUT_BYTES + algo.check.to_bytes(w, endianness)


def _text_packet(name: str, sep: str = " ", leader: str = "", upper: bool = False) -> str:
    """Build a canonical text packet for the given algorithm."""
    algo = ALGORITHMS[name]
    hex_chars = algo.width // 4
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
        # Width-8 algorithms have BE==LE; everyone else must match LE explicitly.
        expected_endian = "big" if algo.width == 8 else "little"
        assert result.matched, f"binary LE detect failed for {name}: result={result}"
        assert (name, expected_endian) in actual, (
            f"expected ({name!r}, {expected_endian!r}) in {actual}"
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
        expected_padding = TextFormat(separator=" ", hex_prefix="", uppercase=False)
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
        expected = TextFormat(separator=sep, hex_prefix=leader, uppercase=upper)
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
                HexFormat(byte_separator="", prefix="", prefix_per_byte=False, uppercase=False),
            ),
            (
                "31 32 33 34 35 36 37 38 39 cb f4 39 26",
                HexFormat(byte_separator=" ", prefix="", prefix_per_byte=False, uppercase=False),
            ),
            (
                "0x31 0x32 0x33 0x34 0x35 0x36 0x37 0x38 0x39 0xcb 0xf4 0x39 0x26",
                HexFormat(byte_separator=" ", prefix="0x", prefix_per_byte=True, uppercase=False),
            ),
            (
                "0x31,0x32,0x33,0x34,0x35,0x36,0x37,0x38,0x39,0xcb,0xf4,0x39,0x26",
                HexFormat(byte_separator=",", prefix="0x", prefix_per_byte=True, uppercase=False),
            ),
            (
                "31:32:33:34:35:36:37:38:39:CB:F4:39:26",
                HexFormat(byte_separator=":", prefix="", prefix_per_byte=False, uppercase=True),
            ),
            (
                "0X313233343536373839CBF43926",
                HexFormat(byte_separator="", prefix="0X", prefix_per_byte=False, uppercase=True),
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
        # Act / Assert -- bytes input + mode="hex" is a caller error.
        with pytest.raises(TypeError, match="hex mode requires all str"):
            detect(b"abc", mode="hex")

    def test_explicit_hex_mode_iter_on_bytes_raises(self) -> None:
        # Act / Assert -- same for the iter API.
        from crcglot import detect_iter
        with pytest.raises(TypeError, match="hex mode requires str packet"):
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
            crc = generic_crc(
                d, algo.width, algo.poly, algo.init, algo.refin, algo.refout, algo.xorout,
            )
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
        crc1 = generic_crc(
            data1, algo.width, algo.poly, algo.init, algo.refin, algo.refout, algo.xorout,
        )
        crc2 = generic_crc(
            data2, algo.width, algo.poly, algo.init, algo.refin, algo.refout, algo.xorout,
        )
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
