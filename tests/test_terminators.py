"""Tests for the frame-terminator registry (``crcglot._terminators``).

A terminator is a delimiter a device appends *after* the CRC field, so it is
not covered by the CRC and moves the field crcglot looks for.  ``detect`` and
``reverse_packets`` consult the registry only after the frames-as-given
reading fails; the tests for that behaviour live with those surfaces
(``test_detect.py`` / ``test_reverse.py``).  This file covers the registry
itself and :func:`common_terminators`, the rule that decides which candidates
the search is even allowed to try.

That rule is where the accuracy budget is spent, so it gets the most attention
here: a candidate must be in the registry *and* end every frame.  The registry
alone would admit plausible bytes that only fit one frame; "ends every frame"
alone would admit any shared byte at all.
"""

from __future__ import annotations

import pytest

from crcglot import TERMINATORS, TerminatorInfo, terminator_info
from crcglot._terminators import MIN_TRAIL_FRAMES, common_terminators
from crcglot.exceptions import CrcglotError, UnknownTerminatorError


class TestRegistry:
    """The registry follows the metadata-record pattern the project uses for
    every other vocabulary (TRAILERS, FORMATS, VARIANTS): a frozen record, a
    dict, and a lookup that raises a crcglot error rather than a bare KeyError.
    """

    @pytest.mark.parametrize("name", sorted(TERMINATORS), ids=sorted(TERMINATORS))
    def test_every_entry_is_well_formed(self, name):
        # Act
        info = terminator_info(name)

        # Assert -- each field carries something a consumer can display.
        assert info.name == name, f"{name}: record name disagrees with its key"
        assert isinstance(info, TerminatorInfo), f"{name}: wrong record type"
        assert info.value, f"{name}: terminator bytes must be non-empty"
        assert isinstance(info.value, bytes), f"{name}: value must be bytes"
        assert info.label, f"{name}: needs a display label"
        assert info.description.endswith("."), f"{name}: description is a sentence"

    def test_len_is_the_byte_count(self):
        # Assert -- callers order candidates shortest-first, so this carries weight.
        actual = len(terminator_info("crlf"))
        assert actual == 2, f"CRLF is two bytes, got {actual}"

    def test_values_are_distinct(self):
        # Assert -- two names for one byte sequence would try the same hypothesis twice.
        values = [t.value for t in TERMINATORS.values()]
        assert len(set(values)) == len(values), f"duplicate terminator bytes: {values}"

    def test_unknown_name_raises_a_crcglot_error_listing_the_vocabulary(self):
        # Act / Assert -- small closed set, so the message lists all of it.
        with pytest.raises(UnknownTerminatorError) as exc:
            terminator_info("crlf-ish")
        message = str(exc.value)
        assert "crlf-ish" in message, f"message must echo the bad value: {message}"
        for name in TERMINATORS:
            assert name in message, f"message must list {name!r}: {message}"

    def test_unknown_name_is_also_a_value_error(self):
        # Assert -- the house hierarchy: crcglot base AND the stdlib type.
        with pytest.raises(ValueError):
            terminator_info("nope")
        with pytest.raises(CrcglotError):
            terminator_info("nope")


class TestCommonTerminators:
    """Which candidates the search may try.

    The intersection of "in the registry" and "ends every frame".  Each half
    refuses something the other would admit, so both are tested from both
    directions.
    """

    def test_a_terminator_on_every_frame_is_offered(self):
        # Act
        actual = [t.name for t in common_terminators([b"abcd\r\n", b"efgh\r\n"])]

        # Assert -- LF and CRLF both end these frames; CR does not.
        assert actual == ["lf", "crlf"], f"expected lf then crlf, got {actual}"

    def test_candidates_come_back_shortest_first(self):
        # Assert -- the caller tries them in order and takes the first that fits,
        # so a one-byte reading must be offered before the two-byte one holding it.
        lengths = [len(t) for t in common_terminators([b"abcd\r\n", b"efgh\r\n"])]
        assert lengths == sorted(lengths), f"not shortest-first: {lengths}"

    def test_a_terminator_on_only_some_frames_is_refused(self):
        # Act -- the second frame ends differently.
        actual = common_terminators([b"abcd\r\n", b"efghij"])

        # Assert
        assert actual == (), f"a non-universal suffix must not be offered: {actual}"

    def test_a_shared_byte_outside_the_registry_is_refused(self):
        # Act -- every frame ends 0x7E, but that is not a registry entry.
        actual = common_terminators([b"abcd\x7e", b"efgh\x7e"])

        # Assert -- "ends every frame" alone is not enough to be tried.
        assert actual == (), f"an unregistered shared suffix must be refused: {actual}"

    def test_frames_that_share_nothing_offer_nothing(self):
        actual = common_terminators([b"abcd", b"efgh"])
        assert actual == (), f"expected no candidates, got {actual}"

    def test_no_frames_offers_nothing(self):
        assert common_terminators([]) == (), "empty input must not crash or guess"

    def test_a_frame_too_short_to_survive_stripping_is_refused(self):
        # Arrange -- stripping CRLF from b"\r\n" leaves nothing to hold a CRC.
        frames = [b"abcd\r\n", b"\r\n"]

        # Act
        actual = common_terminators(frames)

        # Assert
        assert actual == (), f"must not offer a strip that empties a frame: {actual}"

    def test_the_varying_crc_stops_a_strip_reaching_the_message(self):
        """The property that removes the need for a length cap.

        Frames here are ``payload + ETX + varying CRC + CRLF``.  Only the CRLF
        is common, because the CRC differs frame to frame, so no candidate can
        reach back to the ETX that sits inside the CRC's span.
        """
        # Arrange
        frames = [
            b"one\x03\x11\x22\r\n",
            b"two\x03\x33\x44\r\n",
            b"six\x03\x55\x66\r\n",
        ]

        # Act
        actual = [t.value for t in common_terminators(frames)]

        # Assert -- CRLF and its LF suffix only; nothing reaching the ETX.
        assert actual == [b"\n", b"\r\n"], f"strip must stop at the CRC: {actual}"


class TestFrameCountFloor:
    """The floor exists because hypotheses are expensive on narrow CRCs."""

    def test_floor_is_above_the_single_frame_case(self):
        # Assert -- one frame already expects ~0.66 spurious catalogue hits
        # before terminators are considered, so the floor has to exceed it.
        assert MIN_TRAIL_FRAMES >= 3, (
            f"floor of {MIN_TRAIL_FRAMES} frames is too low to pay for the "
            "extra hypotheses a terminator search adds"
        )
