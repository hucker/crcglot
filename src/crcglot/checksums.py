"""Non-CRC checksum identification -- a "heads up", not a code generator.

When :func:`crcglot.detect` finds no catalogue CRC and
:func:`crcglot.reverse` can't recover a polynomial, the trailing field is
often a *non-CRC* checksum: an 8-bit sum / LRC / XOR, Adler-32, Fletcher, or
the Internet checksum.  :func:`identify_checksum` recognises those and tells
the caller which one fits -- **identification only**.  crcglot deliberately
does not generate code for these (they're trivial one-liners); the value here
is the heads-up that the frame isn't a CRC at all.

Reliability comes from corroboration, not a single packet: an 8-bit checksum
matches a random frame about 1 in 256 of the time, so a hit on one packet is
weak.  :func:`identify_checksum` intersects across every packet you give it and
reports :attr:`ChecksumResult.frames_agreed` -- the more frames that agree, the
more trustworthy the call (N agreeing 8-bit frames ~ 1 in 256**N).

The packet shape mirrors :func:`crcglot.detect`: whole frames with the checksum
as the trailing field, as bytes, a hex string (``"0x12 0x34"``), or a
``"data <sep> hex"`` text line.
"""

from __future__ import annotations

import fnmatch
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Callable

from crcglot.detect import (
    Endianness,
    EndianSelector,
    Packet,
    _byte_reversed,
    _crc_byte_len,
    _crc_nibble_len,
    _endians_for,
    _looks_like_hex,
    _normalize_packets,
    _parse_text,
    _read_hex_crc,
)

# ---------------------------------------------------------------------------
# Compute functions -- one O(n) pass each, no lookup tables
# ---------------------------------------------------------------------------


def _sum8(data: bytes) -> int:
    """8-bit modular sum of the bytes."""
    return sum(data) & 0xFF


def _lrc8(data: bytes) -> int:
    """8-bit two's-complement sum (LRC) -- e.g. Modbus-ASCII frames."""
    return (-sum(data)) & 0xFF


def _sum8_ones(data: bytes) -> int:
    """8-bit one's-complement of the modular sum."""
    return (~sum(data)) & 0xFF


def _xor8(data: bytes) -> int:
    """XOR of all bytes (block check character) -- e.g. NMEA 0183."""
    acc = 0
    for b in data:
        acc ^= b
    return acc


def _sum16(data: bytes) -> int:
    """16-bit modular sum of the bytes."""
    return sum(data) & 0xFFFF


def _inet16(data: bytes) -> int:
    """Internet checksum (RFC 1071): one's-complement 16-bit sum.

    Big-endian 16-bit words, end-around carry folded in, then complemented.
    Used by IP / UDP / TCP / ICMP.
    """
    total = 0
    for i in range(0, len(data) - 1, 2):
        total += (data[i] << 8) | data[i + 1]
    if len(data) & 1:
        total += data[-1] << 8
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def _fletcher16(data: bytes) -> int:
    """Fletcher-16: two running 8-bit sums mod 255, packed ``(sum2 << 8) | sum1``."""
    s1 = s2 = 0
    for b in data:
        s1 = (s1 + b) % 255
        s2 = (s2 + s1) % 255
    return (s2 << 8) | s1


def _fletcher32(data: bytes) -> int:
    """Fletcher-32: two running 16-bit sums mod 65535 over little-endian words.

    A trailing odd byte is treated as a final word with a zero high byte.
    """
    s1 = s2 = 0
    for i in range(0, len(data) - 1, 2):
        word = data[i] | (data[i + 1] << 8)
        s1 = (s1 + word) % 65535
        s2 = (s2 + s1) % 65535
    if len(data) & 1:
        s1 = (s1 + data[-1]) % 65535
        s2 = (s2 + s1) % 65535
    return (s2 << 16) | s1


def _adler32(data: bytes) -> int:
    """Adler-32 (zlib) -- two sums mod 65521; weak on short inputs."""
    return zlib.adler32(data)


# ---------------------------------------------------------------------------
# Typed metadata record + registry + lookup (mirrors VariantInfo / NamingInfo)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChecksumInfo:
    """Metadata for one non-CRC checksum.

    Identification-only: there is no generator callable here (crcglot does not
    emit code for these).  A parallel private map holds the compute function.

    Attributes:
        name: Machine identifier (e.g. ``"lrc8"``, ``"fletcher16"``).
        label: Human-readable label.
        description: One-line description (algorithm + a typical user).
        width: Bit width of the trailing field (8, 16, or 32).
    """

    name: str
    label: str
    description: str
    width: int


# Registry, simplest first (8-bit family, then 16-bit, then 32-bit) -- the same
# slowest/simplest-first convention as VARIANT_ORDER.
CHECKSUMS: dict[str, ChecksumInfo] = {
    "sum8": ChecksumInfo(
        "sum8", "8-bit sum", "Modular sum of the bytes (mod 256).", 8),
    "lrc8": ChecksumInfo(
        "lrc8", "8-bit LRC (two's-complement sum)",
        "Two's-complement of the byte sum; e.g. Modbus-ASCII LRC.", 8),
    "sum8-1c": ChecksumInfo(
        "sum8-1c", "8-bit one's-complement sum",
        "One's-complement of the byte sum.", 8),
    "xor8": ChecksumInfo(
        "xor8", "8-bit XOR (BCC)",
        "XOR of all bytes; e.g. NMEA 0183 block check character.", 8),
    "sum16": ChecksumInfo(
        "sum16", "16-bit sum", "Modular sum of the bytes (mod 65536).", 16),
    "inet16": ChecksumInfo(
        "inet16", "Internet checksum (RFC 1071)",
        "One's-complement 16-bit sum; IP / UDP / TCP / ICMP.", 16),
    "fletcher16": ChecksumInfo(
        "fletcher16", "Fletcher-16",
        "Two running 8-bit sums mod 255; a cheaper-than-CRC alternative.", 16),
    "fletcher32": ChecksumInfo(
        "fletcher32", "Fletcher-32",
        "Two running 16-bit sums mod 65535 over 16-bit words.", 32),
    "adler32": ChecksumInfo(
        "adler32", "Adler-32",
        "zlib's two-sum checksum (mod 65521); used in PNG / zlib / rsync.", 32),
}

_COMPUTE: dict[str, Callable[[bytes], int]] = {
    "sum8": _sum8,
    "lrc8": _lrc8,
    "sum8-1c": _sum8_ones,
    "xor8": _xor8,
    "sum16": _sum16,
    "inet16": _inet16,
    "fletcher16": _fletcher16,
    "fletcher32": _fletcher32,
    "adler32": _adler32,
}


def checksum_info(name: str) -> ChecksumInfo:
    """Look up a checksum's metadata by name.

    Args:
        name: A key of :data:`CHECKSUMS` (e.g. ``"fletcher16"``).

    Returns:
        The :class:`ChecksumInfo` record.

    Raises:
        KeyError: ``name`` is not a known checksum.

    Examples:
        >>> from crcglot import checksum_info
        >>> checksum_info("lrc8").width
        8
    """
    return CHECKSUMS[name]


# ---------------------------------------------------------------------------
# Identification results (mirror DetectMatch / DetectResult)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChecksumMatch:
    """One checksum consistent with every packet.

    Attributes:
        name: Checksum name (a key of :data:`CHECKSUMS`).
        info: The matching :class:`ChecksumInfo`.
        endianness: Byte order of the trailing field that matched.  Always
            ``"big"`` for an 8-bit checksum (byte order is moot).
    """

    name: str
    info: ChecksumInfo
    endianness: Endianness


@dataclass(frozen=True)
class ChecksumResult:
    """Outcome of :func:`identify_checksum`.

    Truthy iff at least one checksum matched across all packets.

    Attributes:
        matched: Whether any checksum survived.
        candidates: The surviving :class:`ChecksumMatch` entries, simplest
            first.
        frames_agreed: How many packets corroborated the match -- the
            confidence signal.  One frame is weak (an 8-bit checksum matches a
            random frame ~1/256); more frames make a hit trustworthy.
    """

    matched: bool
    candidates: tuple[ChecksumMatch, ...] = field(default_factory=tuple)
    frames_agreed: int = 0

    def __bool__(self) -> bool:
        return self.matched

    @property
    def name(self) -> str | None:
        """The first candidate's name, or ``None``."""
        return self.candidates[0].name if self.candidates else None


def _matches_for_bytes(
    pkt: bytes, *, endian: EndianSelector, names: Sequence[str],
) -> set[tuple[str, Endianness]]:
    """``(name, endianness)`` pairs whose checksum fits this binary frame."""
    out: set[tuple[str, Endianness]] = set()
    for name in names:
        info = CHECKSUMS[name]
        w = _crc_byte_len(info.width)
        if len(pkt) <= w:
            continue
        expected = _COMPUTE[name](pkt[:-w])
        field_bytes = pkt[-w:]
        for order in _endians_for(endian, dedup=(w == 1)):
            if int.from_bytes(field_bytes, order) == expected:
                out.add((name, order))
    return out


def _matches_for_text(
    parsed: tuple[bytes, object, int, str], *,
    endian: EndianSelector, names: Sequence[str],
) -> set[tuple[str, Endianness]]:
    """``(name, endianness)`` pairs whose checksum fits a ``data <sep> hex`` frame.

    Only checksums whose width matches the trailing hex field's length are
    eligible (a 4-nibble field can only be a 16-bit checksum, etc.).
    """
    data, _tf, _hex_len, hex_str = parsed
    out: set[tuple[str, Endianness]] = set()
    for name in names:
        info = CHECKSUMS[name]
        if _crc_nibble_len(info.width) != len(hex_str):
            continue
        expected = _COMPUTE[name](data)
        for order in _endians_for(endian, dedup=(_crc_byte_len(info.width) == 1)):
            read = _read_hex_crc(hex_str, order)
            if read is not None and read == expected:
                out.add((name, order))
    return out


def identify_checksum(
    packet: Packet | Iterable[Packet],
    *,
    mode: str = "auto",
    endian: EndianSelector = "both",
    encoding: str = "utf-8",
    checksums: str | None = None,
) -> ChecksumResult:
    """Identify the non-CRC checksum in a packet's trailing field.

    For each candidate checksum, the trailing field of the matching width is
    peeled off and recomputed over the rest; a checksum is kept only if it fits
    **every** packet (the intersection).  Identification only -- no code is
    generated.

    Args:
        packet: One frame (bytes, hex string, or ``"data <sep> hex"`` text) or
            an iterable of them (all the same kind).  The checksum is the
            trailing field.
        mode: ``"auto"`` (default) decodes a hex-looking ``str`` to bytes, else
            parses it as text; ``"hex"`` requires hex; ``"text"`` forces the
            ``"data <sep> hex"`` reading; ``"binary"`` is for bytes input.
        endian: Byte order of the trailing field for 16/32-bit checksums --
            ``"big"``, ``"little"``, or ``"both"`` (default).  Ignored for
            8-bit checksums.
        encoding: Used only in text mode to encode the data portion.
        checksums: Optional ``fnmatch`` glob (e.g. ``"fletcher*"``) to narrow
            the candidates.

    Returns:
        A :class:`ChecksumResult`, truthy on match, with ``frames_agreed`` set
        to the number of packets that corroborated.

    Examples:
        >>> from crcglot import identify_checksum
        >>> data = b"123456789"
        >>> frame = data + bytes([(-sum(data)) & 0xFF])  # 8-bit LRC trailer
        >>> identify_checksum(frame).name
        'lrc8'
    """
    packets = _normalize_packets(packet)
    if not packets:
        return ChecksumResult(matched=False)
    names = [
        n for n in CHECKSUMS
        if checksums is None or fnmatch.fnmatch(n, checksums)
    ]

    survivor_sets: list[set[tuple[str, Endianness]]] = []
    for p in packets:
        if isinstance(p, (bytes, bytearray)):
            survivor_sets.append(
                _matches_for_bytes(bytes(p), endian=endian, names=names))
            continue
        # A str packet: try hex-decoding first (auto/hex), else parse as text.
        decoded = _looks_like_hex(p) if mode in ("auto", "hex") else None
        if decoded is not None:
            survivor_sets.append(
                _matches_for_bytes(decoded[0], endian=endian, names=names))
        elif mode == "hex":
            survivor_sets.append(set())  # required hex, didn't decode
        else:
            parsed = _parse_text(p, encoding)
            survivor_sets.append(
                _matches_for_text(parsed, endian=endian, names=names)
                if parsed is not None else set())

    common = set.intersection(*survivor_sets) if survivor_sets else set()
    orders: tuple[Endianness, ...] = ("big", "little")
    candidates = tuple(
        ChecksumMatch(n, CHECKSUMS[n], e)
        for n in names for e in orders if (n, e) in common
    )
    return ChecksumResult(
        matched=bool(candidates),
        candidates=candidates,
        frames_agreed=len(packets) if candidates else 0,
    )


def _identify_checksum_pairs(
    pairs: Sequence[tuple[bytes, int]],
) -> ChecksumResult:
    """Checksum hint from ``(message, checksum_int)`` pairs (for ``reverse``).

    A checksum fits a pair when its computed value equals the given integer read
    either way: as-is (``"big"``) or byte-reversed over the checksum's width
    (``"little"``).  Trying both orders means a little-endian-stored multi-byte
    checksum is caught even when the caller read the field big-endian (the
    default).  A checksum is kept only if it fits every pair (the intersection).
    8-bit checksums have no byte order, so they only ever report ``"big"``.
    """
    if not pairs:
        return ChecksumResult(matched=False)
    survivor_sets: list[set[tuple[str, Endianness]]] = []
    for msg, value in pairs:
        fits: set[tuple[str, Endianness]] = set()
        for name, info in CHECKSUMS.items():
            expected = _COMPUTE[name](msg)
            if expected == value:
                fits.add((name, "big"))
            elif (info.width > 8 and value < (1 << info.width)
                    and expected == _byte_reversed(value, info.width)):
                fits.add((name, "little"))
        survivor_sets.append(fits)
    common = set.intersection(*survivor_sets)
    candidates = tuple(
        ChecksumMatch(n, CHECKSUMS[n], e)
        for n in CHECKSUMS for e in ("big", "little") if (n, e) in common
    )
    return ChecksumResult(
        matched=bool(candidates),
        candidates=candidates,
        frames_agreed=len(pairs) if candidates else 0,
    )
