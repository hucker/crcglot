"""Named frame terminators: delimiter bytes that follow a CRC on the wire.

A terminator is what a device appends *after* the CRC field, most often
because the transport is line-oriented: the CRLF a serial protocol ends
each line with, the NUL that closes a C-string frame.  It is not covered
by the CRC.

That last point is the whole distinction.  A delimiter sitting *inside*
the CRC span (ETX in the common ``STX payload ETX BCC`` framing) needs no
special handling at all, because crcglot treats everything before the CRC
field as message and makes no assumption about its content.  Only bytes
*after* the CRC are a problem, because they move the field crcglot is
looking for.

``detect`` and ``reverse_packets`` consult this registry only after the
frames-as-given reading fails, and only for sequences that end *every*
frame (:func:`common_terminators`).  Trying the frames unmodified first is
not merely an optimisation: that attempt is the "the delimiter is inside
the CRC span" hypothesis, and it has to win when it holds.

One consequence of that precedence is worth knowing.  A *single* trailing
``NUL`` after a **reflected** CRC is absorbed by the as-given reading and so
is never reported as a terminator: appending a reflected CRC's low byte to
the message makes the new CRC equal its high byte, so ``payload | crc_lo
crc_hi | 00`` is genuinely also ``payload crc_lo | crc_hi 00``, consistently
across every frame.  Both readings are correct; the search reports the one
that needs no bytes set aside.  Longer terminators (CRLF and the rest) carry
no such identity and are reported normally.

Keeping the vocabulary here rather than inline in the search means a
consumer (a serial terminal, a UI) reads the same list crcglot searches
instead of keeping its own copy, and adding a terminator is data rather
than a code change.

This mirrors the trailer registry (:mod:`crcglot._trailers`) and the
payload-form registry (:mod:`crcglot._formats`): a frozen
:class:`TerminatorInfo` record, a :data:`TERMINATORS` registry, and a
:func:`terminator_info` lookup.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from crcglot.exceptions import UnknownTerminatorError


@dataclass(frozen=True)
class TerminatorInfo:
    """Metadata for one named frame terminator.

    Attributes:
        name: Machine identifier (e.g. ``"crlf"``).
        label: Human-readable label.
        description: One-line description naming a protocol that uses it.
        value: The literal bytes, as they appear after the CRC field.
    """

    name: str
    label: str
    description: str
    value: bytes

    def __len__(self) -> int:
        """Byte length, so callers can order candidates shortest-first."""
        return len(self.value)


# Registry.  Deliberately short: every entry is a hypothesis the search may
# try, so an implausible one costs accuracy on narrow CRCs for nothing.  The
# guard against that cost is :func:`common_terminators`, which admits only
# sequences ending every frame, so an entry that never occurs is never tried.
TERMINATORS: dict[str, TerminatorInfo] = {
    "lf": TerminatorInfo(
        name="lf",
        label="LF",
        description="Line feed (0x0A); Unix-style line-oriented serial protocols.",
        value=b"\n",
    ),
    "cr": TerminatorInfo(
        name="cr",
        label="CR",
        description="Carriage return (0x0D); older line-oriented serial protocols.",
        value=b"\r",
    ),
    "crlf": TerminatorInfo(
        name="crlf",
        label="CRLF",
        description=(
            "Carriage return + line feed (0x0D 0x0A); Modbus ASCII and "
            "NMEA 0183 both close a frame this way."
        ),
        value=b"\r\n",
    ),
    "nul": TerminatorInfo(
        name="nul",
        label="NUL",
        description="Null byte (0x00); C-string style framing.",
        value=b"\x00",
    ),
    "etx": TerminatorInfo(
        name="etx",
        label="ETX",
        description=(
            "End of text (0x03), for ASCII framings that place the delimiter "
            "after the check field rather than inside it."
        ),
        value=b"\x03",
    ),
    "eot": TerminatorInfo(
        name="eot",
        label="EOT",
        description="End of transmission (0x04); closes a framed ASCII exchange.",
        value=b"\x04",
    ),
}


def terminator_info(name: str) -> TerminatorInfo:
    """Look up a frame terminator's metadata by name.

    Args:
        name: A key of :data:`TERMINATORS` (e.g. ``"crlf"``).

    Returns:
        The :class:`TerminatorInfo` record.

    Raises:
        UnknownTerminatorError: ``name`` is not a known terminator.  The
            message lists the full vocabulary, which is small and closed.

    Examples:
        >>> from crcglot import terminator_info
        >>> terminator_info("crlf").value
        b'\\r\\n'
    """
    try:
        return TERMINATORS[name]
    except KeyError:
        known = ", ".join(sorted(TERMINATORS))
        raise UnknownTerminatorError(
            f"unknown frame terminator {name!r}; known terminators: {known}"
        ) from None


# A stripped frame still has to hold a CRC field and at least one message
# byte, so anything shorter than this cannot yield a reading and is not worth
# offering to the search.
_MIN_REMAINDER = 2

# Below this many frames the terminator search is not payable.  Every candidate
# is another hypothesis, and the catalogue's narrow entries make hypotheses
# expensive: a 3-bit CRC matches random data 1 time in 8, so the expected count
# of spurious hits across the catalogue is already ~0.66 at one frame before any
# terminator is considered, and ~4 with them.  Three frames brings it to ~0.03.
MIN_TRAIL_FRAMES = 3


def common_terminators(frames: Sequence[bytes]) -> tuple[TerminatorInfo, ...]:
    """Registry terminators that end *every* frame, shortest first.

    The intersection of two independent constraints, which is what keeps the
    false-positive cost near zero.  The registry rules out implausible bytes;
    "ends every frame" rules out plausible bytes that only happen to fit one
    frame.  Neither alone does both jobs.

    It also needs no length cap.  Because the CRC field differs from frame to
    frame, a byte sequence shared by every frame cannot reach back past the
    CRC into the message: the varying CRC is its own barrier.

    Args:
        frames: The frames, as raw bytes.

    Returns:
        Matching records ordered shortest first, so a one-byte reading is
        offered before the two-byte one that contains it.  Empty when the
        frames share no known terminator.

    Examples:
        >>> from crcglot._terminators import common_terminators
        >>> [t.name for t in common_terminators([b"ab\\r\\n", b"cd\\r\\n"])]
        ['lf', 'crlf']
        >>> common_terminators([b"ab\\r\\n", b"cdef"])
        ()
    """
    if not frames:
        return ()
    found = [
        t
        for t in TERMINATORS.values()
        if all(
            f.endswith(t.value) and len(f) - len(t.value) >= _MIN_REMAINDER
            for f in frames
        )
    ]
    return tuple(sorted(found, key=len))
