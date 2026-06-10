"""CRC algorithm detection -- brute-force identification.

Given a packet whose tail is a CRC, scan the catalogue x both byte
orders to find which algorithm matches.  Supports binary
packets (``bytes``/``bytearray``) and text packets (``str``).  A ``str``
that's a hex-encoded byte string in any common formatting (``"12 34"``,
``"0x12,0x34"``, ``xxd``-style ``"AB:CD:EF"``, etc.) is decoded
transparently into bytes; anything else is parsed as text in the
``"data <whitespace> [0x]hex"`` shape.  Multi-packet input is
intersected so single-packet false positives collapse fast.

Three match modes select what gets returned:

* ``"first"`` (default) -- early-stop at the first ``(algorithm,
  endianness)`` that survives all packets, in priority order (``crc32`` /
  ``crc32-jamcrc`` / ``crc32-iscsi`` first, then catalogue).  Fastest.
* ``"all"`` -- exhaustive forensic view; every consistent candidate.
* ``"set"`` -- strict singleton; success only if exactly one algorithm
  survives across all packets.

A lower-level ``detect_iter`` yields each ``Attempt`` as it's tried so
callers can stream a progress UI or stop early.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Iterator, Literal, cast

from crcglot.catalogue import ALGORITHMS, AlgorithmInfo, generic_crc


# A "packet" is either a bytes-like value (binary mode) or a string
# (text mode).  Used throughout the internal helpers so we don't pass
# bare ``object`` around.
Packet = bytes | bytearray | str


# Priority head -- crc32 / crc32-jamcrc both delegate to zlib hardware
# CRC at runtime (PCLMULQDQ/PMULL), and crc32-iscsi (Castagnoli) is the
# other ubiquitous one.  Most "did this packet match anything?" queries
# land on one of these and exit in microseconds.
_PRIORITY: tuple[str, ...] = ("crc32", "crc32-jamcrc", "crc32-iscsi")


# Text-mode parser: capture data, the whitespace separator, an optional
# 0x/0X prefix, and the hex digits.  DOTALL so multi-line data works.
_TEXT_RE = re.compile(
    r"^(.*?)(\s+)(0[xX])?([0-9a-fA-F]+)\s*$",
    re.DOTALL,
)


# Hex-text input cleaning: any number of ``0x``/``0X`` prefixes plus
# common separator characters (whitespace, commas, colons) get stripped
# before the all-hex / even-length check.  Colons are included because
# ``xxd`` output, MAC addresses, and similar dumps use them as
# byte separators.
_HEX_CLEAN = re.compile(r"0[xX]|[\s,:]+")


Endianness = Literal["big", "little"]
EndianSelector = Literal["big", "little", "both"]


def _endians_for(
    selector: EndianSelector, dedup: bool,
) -> tuple[Endianness, ...]:
    """Resolve the ``endian`` selector to a tuple of byte orderings.

    Args:
        selector: ``"big"``, ``"little"``, or ``"both"``.
        dedup: ``True`` when big and little are byte-identical for the
            current algorithm (single-byte CRC).  In that case
            ``"both"`` collapses to ``("big",)`` to avoid duplicate hits.

    Returns:
        The byte orderings to iterate.
    """
    if selector == "both":
        return ("big",) if dedup else ("big", "little")
    return (selector,)


def _crc_byte_len(width: int) -> int:
    """Bytes needed to hold a ``width``-bit CRC field, rounding up.

    The CRC occupies the low ``width`` bits, right-justified and zero-padded
    into ``ceil(width / 8)`` bytes -- so a 15-bit CRC takes 2 bytes, not the
    1 that floor division (``width // 8``) would give.  Floor division here
    silently truncated sub-byte CRCs (and overflowed ``_byte_reversed``).
    """
    return (width + 7) // 8


def _crc_nibble_len(width: int) -> int:
    """Hex nibbles needed to hold a ``width``-bit CRC field, rounding up.

    A 15-bit CRC needs ``ceil(15 / 4) = 4`` nibbles (``"059e"``); floor
    division (``width // 4``) would demand 3 and reject the real hex.
    """
    return (width + 3) // 4


def _byte_reversed(value: int, width_bits: int) -> int:
    """Byte-reverse ``value`` over the CRC field's byte length.

    The CRC integer has a canonical big-endian byte form (most-significant
    byte first); a caller whose tool printed the bytes and read them
    little-endian gets the byte-reversed integer.  This lets the
    ``target_crc`` path compare against both readings.

    Args:
        value: The CRC integer.
        width_bits: Algorithm width in bits.  The field spans
            ``ceil(width / 8)`` bytes, so a sub-byte / non-byte-aligned
            width (e.g. 15) reverses over its padded byte length rather
            than overflowing on ``floor`` division.

    Returns:
        ``value`` with its bytes reversed.  For a single-byte field
        (width <= 8) returns ``value`` unchanged (endianness-invariant).
    """
    n_bytes = _crc_byte_len(width_bits)
    return int.from_bytes(value.to_bytes(n_bytes, "big"), "little")


@dataclass(frozen=True)
class TextFormat:
    """The text-packet shape captured from the trailing whitespace + hex.

    Attributes:
        separator: The literal whitespace between data and hex.
        prefix: ``""``, ``"0x"``, or ``"0X"``.
        uppercase: ``True`` when the hex digits use upper-case A-F.
    """

    separator: str
    prefix: str
    uppercase: bool = False


@dataclass(frozen=True)
class HexFormat:
    """The hex-text-packet shape captured when ``detect`` auto-decoded a
    ``str`` of hex bytes (``"31 32"``, ``"0x12 0x34"``, ``"AB:CD:EF"``,
    etc.) into raw bytes.  Lets ``encode_match`` rebuild the same
    surface formatting.

    Attributes:
        separator: The literal characters between hex byte pairs
            -- ``""`` (none), ``" "``, ``","``, ``":"``, ``"\\n"``, or
            any short run.
        prefix: ``""``, ``"0x"``, or ``"0X"`` -- which ``0x``-style
            prefix the producer used (if any).
        prefix_per_byte: ``True`` when the prefix appears before *each*
            byte (``"0x12 0x34"``); ``False`` when the prefix appears
            once at the start of the whole string (``"0X1234"``).
        uppercase: ``True`` if hex digits use upper-case A-F.
    """

    separator: str
    prefix: str
    prefix_per_byte: bool
    uppercase: bool = False


@dataclass(frozen=True)
class DetectMatch:
    """One consistent identification of an algorithm.

    Attributes:
        algorithm: Catalogue name (e.g. ``"crc32"``).
        info: The matching :class:`AlgorithmInfo` (full parameters).
        endianness: Byte order of the trailing CRC in the packet.
        padding: The surface formatting that wrapped the bytes:
            ``None`` for a plain binary packet,
            :class:`TextFormat` for a ``"data <sep> hex"`` text packet,
            :class:`HexFormat` for a hex-encoded byte string
            (``"0x12 0x34"`` and friends).  ``encode_match`` dispatches
            on this type to round-trip the exact same shape.
    """

    algorithm: str
    info: AlgorithmInfo
    endianness: Endianness
    padding: TextFormat | HexFormat | None = None


@dataclass(frozen=True)
class Attempt:
    """One ``(algorithm, endianness)`` scan step yielded by ``detect_iter``.

    Attributes:
        algorithm: Catalogue name being tried.
        endianness: Byte order being tried for this attempt.
        matched: Whether the trailing bytes matched the computed CRC.
    """

    algorithm: str
    endianness: Endianness
    matched: bool


@dataclass(frozen=True)
class DetectResult:
    """The eager result of :func:`detect`.

    Truthy iff at least one candidate survived.  ``.algorithm`` /
    ``.endianness`` expose the first candidate's fields for the common
    single-match case; iterate ``.candidates`` for the full set.

    Attributes:
        matched: ``True`` if at least one candidate matched.
        candidates: All surviving :class:`DetectMatch` entries, in scan
            order (priority head first, then catalogue).
    """

    matched: bool
    candidates: tuple[DetectMatch, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return self.matched

    @property
    def algorithm(self) -> str | None:
        return self.candidates[0].algorithm if self.candidates else None

    @property
    def endianness(self) -> Endianness | None:
        return self.candidates[0].endianness if self.candidates else None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _ordered_algorithm_names(
    algorithms_filter: str | None, width: int | None = None,
) -> list[str]:
    """Build the scan order: priority head, then the rest of the catalogue.

    Args:
        algorithms_filter: Optional ``fnmatch`` glob (e.g. ``"crc16-*"``).
            ``None`` means no filtering.
        width: Optional CRC bit width; keep only algorithms of that width.

    Returns:
        Algorithm names in scan order, after applying the filter(s).
    """
    rest = [n for n in ALGORITHMS if n not in _PRIORITY]
    ordered = [*_PRIORITY, *rest]
    if algorithms_filter is not None:
        ordered = [n for n in ordered if fnmatch.fnmatch(n, algorithms_filter)]
    if width is not None:
        ordered = [n for n in ordered if ALGORITHMS[n].width == width]
    return ordered


def _looks_like_hex(text: str) -> tuple[bytes, HexFormat] | None:
    """Decode a hex-encoded byte string and capture its surface format.

    Accepts any common formatting: ``0x``/``0X`` prefixes (one global
    or one per byte), spaces, tabs, newlines, commas, and colons as
    separators.  After stripping those the remainder must be all hex
    digits and even length; otherwise the input isn't unambiguously
    hex-bytes and we return ``None`` so the caller can fall back to
    plain text mode.

    Args:
        text: A single packet (already trimmed of outer whitespace).

    Returns:
        ``(bytes, HexFormat)`` on a successful decode (the format
        captures what ``encode_match`` needs to reproduce the same
        surface), or ``None`` when the input doesn't look like
        hex-encoded bytes.

    Examples:
        >>> b, fmt = _looks_like_hex("0x12 0x34")
        >>> b, fmt.separator, fmt.prefix, fmt.prefix_per_byte
        (b'\\x124', ' ', '0x', True)
        >>> _looks_like_hex("hello") is None
        True
    """
    # ----- Prefix detection -----
    n_lower = text.count("0x")
    n_upper = text.count("0X")
    if n_upper and not n_lower:
        prefix = "0X"
        n_prefix = n_upper
    elif n_lower and not n_upper:
        prefix = "0x"
        n_prefix = n_lower
    elif n_upper and n_lower:
        # Mixed case: pick the more common form; count both as prefix
        # hits so per-byte detection still works for mixed input.
        prefix = "0X" if n_upper >= n_lower else "0x"
        n_prefix = n_upper + n_lower
    else:
        prefix = ""
        n_prefix = 0

    # Strip prefixes first so the separator search sees only the bytes.
    text_no_prefix = re.sub(r"0[xX]", "", text)

    # First run of separator characters (whitespace, comma, colon)
    # between hex pairs -- captured verbatim so round-trip preserves
    # tabs vs spaces, ", " vs "," etc.
    sep_match = re.search(r"[\s,:]+", text_no_prefix)
    separator = sep_match.group(0) if sep_match else ""

    # Final hex string for the validity check.
    cleaned = re.sub(r"[\s,:]+", "", text_no_prefix)
    if not cleaned or len(cleaned) % 2 != 0:
        return None
    if not all(c in "0123456789abcdefABCDEF" for c in cleaned):
        return None

    # Per-byte vs single-leading prefix.  ``>=`` because the mixed-case
    # branch above can over-count, and the worst that happens is we
    # treat a 1-prefix-for-1-byte input as per-byte (correct round-trip
    # either way for a 1-byte packet).
    n_bytes = len(cleaned) // 2
    prefix_per_byte = bool(prefix) and n_prefix >= n_bytes

    # Case: digits themselves AND/OR a ``0X`` prefix.
    uppercase = any(c.isupper() for c in cleaned) or prefix == "0X"

    return bytes.fromhex(cleaned), HexFormat(
        separator=separator,
        prefix=prefix,
        prefix_per_byte=prefix_per_byte,
        uppercase=uppercase,
    )


def _normalize_packets(packet: Packet | Iterable[Packet]) -> list[Packet]:
    """Coerce input to a homogeneous packet list.

    Args:
        packet: A single bytes-like / ``str`` packet, or an iterable of
            them.  All items in an iterable must be the same kind.

    Returns:
        A list of packets ready for mode resolution.  Empty input gives
        ``[]``.

    Raises:
        TypeError: ``packet`` is neither a packet nor an iterable, or
            an iterable mixes bytes-like and ``str`` entries.
    """
    if isinstance(packet, (bytes, bytearray, str)):
        return [packet]
    try:
        items_raw = list(packet)
    except TypeError as e:
        raise TypeError(
            "packet must be bytes/bytearray/str or an iterable of them"
        ) from e
    out: list[Packet] = []
    has_binary = False
    has_text = False
    for it in items_raw:
        if isinstance(it, (bytes, bytearray)):
            has_binary = True
            out.append(it)
        elif isinstance(it, str):
            has_text = True
            out.append(it)
        else:
            raise TypeError(
                f"packets must be bytes-like or str (got {type(it).__name__})"
            )
    if has_binary and has_text:
        raise TypeError("packets must all be bytes-like or all str (got mixed)")
    return out


def _resolve_mode(
    packets: list[Packet],
    mode: Literal["auto", "binary", "text"],
) -> Literal["binary", "text"]:
    """Pick the concrete mode, defaulting from the first packet's type.

    Args:
        packets: Normalized packets (non-empty).
        mode: ``"auto"``, ``"binary"``, or ``"text"``.

    Returns:
        ``"binary"`` or ``"text"``.
    """
    if mode == "auto":
        return "binary" if isinstance(packets[0], (bytes, bytearray)) else "text"
    return mode


# Pre-parsed text packet: (data_bytes, format, hex_len, hex_str).
_ParsedText = tuple[bytes, TextFormat, int, str]


def _parse_text(text: str, encoding: str) -> _ParsedText | None:
    """Pull the trailing whitespace + hex out of a text packet.

    Hex-digit case is inferred from the digits themselves *or* a ``0X``
    leader -- either signals upper-case for round-trip purposes.

    Outer whitespace -- leading indentation, trailing newlines, and the
    CRLF / LF line endings you get from copy-paste or ``stdin`` -- is
    stripped before the regex runs so callers don't have to pre-clean
    their packets.  Whitespace **between** the data and the hex (the
    ``separator``) is preserved in the returned :class:`TextFormat` so
    ``encode_match`` can reproduce the same shape.

    Args:
        text: One text packet.
        encoding: How to bytes-encode the data portion for CRC compute.

    Returns:
        ``(data_bytes, TextFormat, hex_len, hex_str)`` on success, or
        ``None`` when the regex didn't match.
    """
    # Strip outer whitespace before matching: leading indentation would
    # otherwise be captured into ``data_str`` and change the computed
    # CRC; trailing whitespace is mostly absorbed by ``\s*$`` in the
    # regex but stripping is consistent and cheap.
    text = text.strip()
    m = _TEXT_RE.match(text)
    if m is None:
        return None
    data_str, sep, leader, hex_str = m.group(1), m.group(2), m.group(3) or "", m.group(4)
    uppercase = any(c.isupper() for c in hex_str) or leader == "0X"
    return (
        data_str.encode(encoding),
        TextFormat(separator=sep, prefix=leader, uppercase=uppercase),
        len(hex_str),
        hex_str,
    )


def _read_hex_crc(hex_str: str, endian: Endianness) -> int | None:
    """Read a trailing hex CRC field as an integer per byte order.

    Shared by :func:`crcglot.verify` and :func:`crcglot.reverse_packets` so a
    text frame ("data <sep> hexcrc") splits the same way :func:`detect` reads
    it.  Returns ``None`` when a little-endian reading is asked of an odd-nibble
    field (a sub-byte width like 11 -> 3 nibbles has no whole bytes to reverse).
    """
    if endian == "big":
        return int(hex_str, 16)
    if len(hex_str) % 2 == 1:
        return None
    return int.from_bytes(bytes.fromhex(hex_str), "little")


def _check_binary(packet: bytes, algo: AlgorithmInfo, w: int, endian: Endianness) -> bool:
    """Recompute the CRC of ``packet[:-w]`` and compare to ``packet[-w:]``.

    Args:
        packet: The full packet bytes.
        algo: Algorithm to apply.
        w: CRC width in bytes, ``ceil(algo.width / 8)`` (a sub-byte /
            non-byte-aligned CRC occupies its zero-padded byte length).
        endian: How to interpret the trailing CRC bytes.

    Returns:
        ``True`` iff the trailing bytes equal the computed CRC.
    """
    parsed = int.from_bytes(packet[-w:], endian)
    computed = generic_crc(packet[:-w], algo)
    return parsed == computed


def _check_text(parsed: _ParsedText, algo: AlgorithmInfo, endian: Endianness) -> bool:
    """Compare the parsed hex against the CRC under one ``(algo, endian)``.

    Args:
        parsed: Output of :func:`_parse_text`.
        algo: Algorithm to apply.
        endian: ``"big"`` reads the hex as an integer; ``"little"`` reads
            the hex as LE byte order.

    Returns:
        ``True`` iff the hex value matches the computed CRC.
    """
    data, _tf, _hex_len, hex_str = parsed
    if endian == "big":
        parsed_int = int(hex_str, 16)
    else:
        # Little-endian needs a whole number of bytes; an odd-nibble hex
        # field (a sub-byte / non-byte-aligned width like 11 -> 3 nibbles)
        # has no byte order to reverse, so it can't match an LE reading.
        if len(hex_str) % 2 == 1:
            return False
        parsed_int = int.from_bytes(bytes.fromhex(hex_str), "little")
    computed = generic_crc(data, algo)
    return parsed_int == computed


def _matches_for_binary_packet(
    pb: bytes, names: list[str], endian: EndianSelector = "both",
) -> set[tuple[str, Endianness]]:
    """All ``(name, endian)`` pairs that fit one binary packet.

    Width-8 algorithms are tried in ``"big"`` only -- BE and LE encode
    identically for a single byte.

    Args:
        pb: One binary packet.
        names: Scan order (already filtered and priority-ordered).
        endian: ``"both"`` (default) tries big and little; ``"big"`` or
            ``"little"`` forces a single ordering.

    Returns:
        The set of matching ``(algorithm_name, endianness)`` pairs.
    """
    matches: set[tuple[str, Endianness]] = set()
    for name in names:
        algo = ALGORITHMS[name]
        w = _crc_byte_len(algo.width)
        if len(pb) <= w:
            continue
        for byte_order in _endians_for(endian, dedup=(w == 1)):
            if _check_binary(pb, algo, w, byte_order):
                matches.add((name, byte_order))
    return matches


def _matches_for_text_packet(
    parsed: _ParsedText, names: list[str], endian: EndianSelector = "both",
) -> set[tuple[str, Endianness]]:
    """All ``(name, endian)`` pairs that fit one text packet.

    Width inference filters early: an algorithm can only match if its
    field length matches the hex string (``hex_len == ceil(width / 4)``),
    so most of the catalogue is rejected without calling ``generic_crc``.

    Args:
        parsed: Output of :func:`_parse_text`.
        names: Scan order.
        endian: ``"both"`` (default) tries big and little hex
            interpretations; ``"big"`` or ``"little"`` forces a single
            ordering.

    Returns:
        The set of matching ``(algorithm_name, endianness)`` pairs.
    """
    matches: set[tuple[str, Endianness]] = set()
    _data, _tf, hex_len, _hex_str = parsed
    for name in names:
        algo = ALGORITHMS[name]
        if _crc_nibble_len(algo.width) != hex_len:
            continue
        for byte_order in _endians_for(
            endian, dedup=(hex_len <= 2 or hex_len % 2 == 1)
        ):
            if _check_text(parsed, algo, byte_order):
                matches.add((name, byte_order))
    return matches


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect(
    packet: Packet | Iterable[Packet],
    *,
    mode: Literal["auto", "binary", "text", "hex"] = "auto",
    encoding: str = "utf-8",
    algorithms: str | None = None,
    width: int | None = None,
    match: Literal["first", "all", "set"] = "first",
    target_crc: int | None = None,
    endian: EndianSelector = "both",
) -> DetectResult:
    """Identify which catalogue CRC produced the trailing bytes of a packet.

    Multi-packet input is intersected, which collapses single-packet
    false positives quickly (two agreeing packets typically pin the
    algorithm down).

    Args:
        packet: A single bytes-like / ``str`` packet, or an iterable of
            them (all of the same type).
        mode: ``"auto"`` (default) picks binary for bytes-like; for
            ``str`` it first checks whether the input is a hex-encoded
            byte string in any common formatting (``"12 34"``,
            ``"0x12,0x34"``, ``xxd``-style ``"AB:CD:EF"``, etc.) and
            decodes if so; otherwise falls through to text mode
            (``"data <sep> hex"``).  Three explicit overrides:
            ``"binary"`` forces bytes-like interpretation; ``"text"``
            skips the hex-as-bytes step entirely and parses as a
            ``"data <sep> hex"`` packet; ``"hex"`` requires the input
            be hex-encoded bytes (no text-mode fallback) -- useful when
            the caller knows the wire format and wants a "no match"
            rather than a chance text-mode reinterpretation.
        encoding: Used only in text mode to encode the data portion
            before computing the CRC.  Default ``"utf-8"``.
        algorithms: Optional ``fnmatch`` glob (e.g. ``"crc16-*"``) to
            narrow the scan.  Same convention as ``crcglot list <glob>``.
        width: Optional CRC bit width (e.g. ``16``) to narrow the scan to
            algorithms of that width -- a first-class alternative to a
            ``"crc16-*"``-style ``algorithms`` glob.
        match: Selection strategy.  ``"first"`` (default) stops at the
            first hit in priority order; ``"all"`` returns every
            consistent candidate; ``"set"`` succeeds only if exactly one
            algorithm survives across all packets.
        target_crc: When supplied, treat ``packet`` as **data only** --
            no CRC is extracted from the tail.  The integer is the
            externally-known CRC value to match against.  The integer is
            tried as **two readings**: as-is (big-endian) and byte-
            reversed at the algorithm's width (little-endian) -- the
            caller's tooling might have printed the CRC bytes in either
            wire order.  For each candidate algorithm, ``generic_crc(
            data)`` is compared to both readings; whichever matches
            wins, and the corresponding endianness label is recorded on
            the :class:`DetectMatch`.  The ``endian`` parameter narrows
            this to a single reading when known.  Algorithms whose
            width can't hold the relevant reading are skipped.  Useful
            when the CRC arrives out-of-band (separate header field,
            separate file, user-typed expected value, etc.) and you
            don't want ``detect`` to slice the last N bytes as the CRC.
            Multi-packet input applies the same ``target_crc`` to every
            packet -- all packets' computed CRCs must agree under the
            candidate algorithm and reading.  ``padding`` is ``None``.
        endian: Which byte ordering(s) of the trailing CRC to try.
            ``"both"`` (default) tries big and little -- handles the
            common "I don't know the wire format" case.  ``"big"`` or
            ``"little"`` forces a single ordering, which halves the scan
            and rules out the false positives that show up when a
            byte-reversed CRC happens to coincide with some other
            algorithm's natural reading.  Useful when the wire format is
            known.  Also narrows the ``target_crc`` path: ``"big"``
            tests only the natural integer reading; ``"little"`` tests
            only the byte-reversed-at-width form.

    Returns:
        A :class:`DetectResult` truthy on match.  ``.candidates`` lists
        every surviving ``(algorithm, endianness)``, ordered priority
        head first.

    Raises:
        TypeError: ``packet`` mixes bytes-like and ``str`` entries, or
            isn't a valid packet / iterable.

    Examples:
        >>> result = detect(b"123456789\\xcb\\xf4\\x39\\x26")
        >>> result.algorithm, result.endianness
        ('crc32', 'big')
        >>> result = detect("123456789 cbf43926")
        >>> bool(result), result.candidates[0].padding.separator
        (True, ' ')
        >>> # Hex-encoded packet: '0x' prefix and spaces both tolerated.
        >>> bool(detect("0x31 0x32 0x33 0x34 0x35 0x36 0x37 0x38 0x39 0xcb 0xf4 0x39 0x26"))
        True
        >>> # CRC provided externally; data passed alone.
        >>> result = detect(b"123456789", target_crc=0xCBF43926)
        >>> result.algorithm
        'crc32'
    """
    packets = _normalize_packets(packet)
    if not packets:
        return DetectResult(matched=False)
    names = _ordered_algorithm_names(algorithms, width)

    # target_crc short-circuit: skip CRC-tail extraction entirely;
    # treat the whole packet as data and compare ``generic_crc(data)``
    # to the caller-supplied integer for every candidate algorithm.
    if target_crc is not None:
        return _detect_with_target_crc(
            packets, mode, encoding, names, match, target_crc, endian,
        )

    # Hex pre-step.  Two modes route through here:
    #
    # - ``"hex"`` (explicit):  *require* every str packet to decode as
    #   hex bytes.  Mixed bytes/str is a caller error; an undecodable
    #   str returns no-match instead of falling through to text.
    # - ``"auto"`` (default):  *try* hex decoding for str packets and
    #   use the result only if every packet decodes AND the binary scan
    #   finds a CRC.  Otherwise fall back to the original str packets
    #   and run text-mode parsing.  This handles the degenerate case
    #   where the input is ASCII text that happens to be valid hex --
    #   binary scan finds nothing, text-mode gets its shot.
    str_count = sum(1 for p in packets if isinstance(p, str))
    if mode == "hex":
        if str_count != len(packets):
            raise TypeError("hex mode requires all str packets")
        parsed = [_looks_like_hex(p) for p in packets if isinstance(p, str)]
        if any(p is None for p in parsed):
            return DetectResult(matched=False)
        decoded_bytes_explicit: list[Packet] = [
            p[0] for p in parsed if p is not None
        ]
        hex_format = next((p[1] for p in parsed if p is not None), None)
        result = _run_detect(
            decoded_bytes_explicit, "binary", names, encoding, match, endian,
        )
        return _attach_padding(result, hex_format)
    if mode == "auto" and str_count == len(packets) and str_count > 0:
        parsed = [_looks_like_hex(p) for p in packets if isinstance(p, str)]
        if all(p is not None for p in parsed):
            # Widen ``list[bytes]`` to ``list[Packet]`` for invariant-list typing.
            decoded_bytes: list[Packet] = [
                p[0] for p in parsed if p is not None
            ]
            hex_format = next((p[1] for p in parsed if p is not None), None)
            hex_result = _run_detect(
                decoded_bytes, "binary", names, encoding, match, endian,
            )
            if hex_result.matched:
                return _attach_padding(hex_result, hex_format)
        # Fall through to text mode for the original str packets.

    actual_mode = _resolve_mode(packets, mode)
    return _run_detect(packets, actual_mode, names, encoding, match, endian)


def _run_detect(
    packets: list[Packet],
    mode: Literal["binary", "text"],
    names: list[str],
    encoding: str,
    match: Literal["first", "all", "set"],
    endian: EndianSelector = "both",
) -> DetectResult:
    """Dispatch to the right match-mode helper after mode + packets are settled."""
    if match == "first":
        return _detect_first(packets, mode, names, encoding, endian)
    return _detect_all_or_set(
        packets, mode, names, encoding,
        strict_set=(match == "set"),
        endian=endian,
    )


def _attach_padding(
    result: DetectResult,
    padding: TextFormat | HexFormat | None,
) -> DetectResult:
    """Rebuild candidates with the given ``padding`` value.

    Used to attach a :class:`HexFormat` to results from the hex-text
    pre-decode path -- ``_run_detect`` runs as binary mode (padding
    None), then this helper stamps the captured surface format onto
    each surviving candidate so ``encode_match`` can round-trip.

    No-op when the result didn't match or when ``padding`` is ``None``.
    """
    if not result.matched or padding is None:
        return result
    rebuilt = tuple(
        DetectMatch(
            algorithm=m.algorithm,
            info=m.info,
            endianness=m.endianness,
            padding=padding,
        )
        for m in result.candidates
    )
    return DetectResult(matched=True, candidates=rebuilt)


def _packet_to_data_bytes(
    packet: Packet,
    mode: Literal["auto", "binary", "text", "hex"],
    encoding: str,
) -> bytes | None:
    """Resolve a single packet to its raw data bytes for the
    ``target_crc`` path.

    No CRC extraction happens -- the whole packet is the data.  ``str``
    inputs follow the same hex-vs-text heuristics as the rest of
    ``detect``: ``"hex"`` requires hex; ``"text"`` encodes via
    ``encoding``; ``"auto"`` tries hex first and falls back to
    ``encoding``.

    Returns ``None`` only for the ``mode="hex"`` case when the str
    doesn't decode -- the caller treats that as "no match for this
    packet."  Other modes always succeed (text encoding can't fail
    structurally; binary just unwraps the bytes).
    """
    if isinstance(packet, (bytes, bytearray)):
        return bytes(packet)
    # str
    if mode == "hex":
        parsed = _looks_like_hex(packet)
        return parsed[0] if parsed is not None else None
    if mode == "auto":
        parsed = _looks_like_hex(packet)
        if parsed is not None:
            return parsed[0]
    # text (explicit, or auto-without-hex-shape)
    return packet.encode(encoding)


def _detect_with_target_crc(
    packets: list[Packet],
    mode: Literal["auto", "binary", "text", "hex"],
    encoding: str,
    names: list[str],
    match: Literal["first", "all", "set"],
    target_crc: int,
    endian: EndianSelector,
) -> DetectResult:
    """Implement the ``target_crc`` short-circuit path.

    For each algorithm in scan order:

    * Skip if the algorithm's width can't hold either endian's
      interpretation of ``target_crc``.
    * Try the caller's integer as both byte orderings of the CRC -- the
      raw integer is the big-endian reading; the byte-reversed-at-width
      integer is the little-endian reading.  Whichever interpretation
      every packet's computed CRC agrees on wins; the corresponding
      endianness is recorded on the :class:`DetectMatch`.
    * The ``endian`` selector narrows which interpretations to try
      (``"big"`` only the natural reading; ``"little"`` only the
      byte-reversed one; ``"both"`` tries both, big-first).
    """
    if target_crc < 0:
        raise ValueError(f"target_crc must be non-negative (got {target_crc})")
    # Resolve every packet up-front so we don't recompute in the inner loop.
    data_packets: list[bytes] = []
    for p in packets:
        data = _packet_to_data_bytes(p, mode, encoding)
        if data is None:
            # mode="hex" on a str that's not hex -> no possible match.
            return DetectResult(matched=False)
        data_packets.append(data)

    candidates: list[DetectMatch] = []
    for name in names:
        algo = ALGORITHMS[name]
        w_bits = algo.width
        # The caller's integer is the CRC bytes under some byte order;
        # if it doesn't fit in the width, no byte-reversal of it can.
        if target_crc >= (1 << w_bits):
            continue
        # Build the (target_int, endianness_label) candidates.
        # Width 1 byte dedups (BE == LE byte-wise).
        targets: list[tuple[int, Endianness]] = []
        for byte_order in _endians_for(endian, dedup=(_crc_byte_len(w_bits) == 1)):
            tgt = (
                target_crc if byte_order == "big"
                else _byte_reversed(target_crc, w_bits)
            )
            targets.append((tgt, byte_order))

        for tgt, byte_order in targets:
            all_match = True
            for data in data_packets:
                computed = generic_crc(data, algo)
                if computed != tgt:
                    all_match = False
                    break
            if all_match:
                candidates.append(
                    DetectMatch(
                        algorithm=name,
                        info=algo,
                        endianness=byte_order,
                        padding=None,
                    )
                )
                if match == "first":
                    return DetectResult(matched=True, candidates=tuple(candidates))

    if match == "set":
        unique_algos = {c.algorithm for c in candidates}
        if len(unique_algos) != 1:
            return DetectResult(matched=False)
    return DetectResult(matched=bool(candidates), candidates=tuple(candidates))


def detect_iter(
    packet: bytes | bytearray | str,
    *,
    mode: Literal["auto", "binary", "text", "hex"] = "auto",
    encoding: str = "utf-8",
    algorithms: str | None = None,
    width: int | None = None,
    target_crc: int | None = None,
    endian: EndianSelector = "both",
) -> Iterator[Attempt]:
    """Stream every ``(algorithm, endianness)`` attempt for one packet.

    Lower-level than :func:`detect`: yields each :class:`Attempt`
    (matched or not) in priority order so the caller can drive a
    progress UI, log every step, or break out early on the first hit.

    Args:
        packet: A single bytes-like or ``str`` packet (no iterables).
        mode: ``"auto"`` (default) picks binary for bytes-like; for
            ``str`` it transparently decodes hex-encoded byte strings
            (``"12 34"``, ``"0x12:0x34"``, etc.) and otherwise parses as
            text.  Override with ``"binary"`` or ``"text"`` to force,
            or ``"hex"`` to require hex-decoding (yields nothing if the
            str doesn't parse as hex).
        encoding: Used in text mode to encode the data portion.
        algorithms: Optional ``fnmatch`` glob to narrow the scan.
        width: Optional CRC bit width to narrow the scan to that width.
        target_crc: When supplied, skip CRC-tail extraction and stream
            one ``Attempt`` per ``(algorithm, endian)`` pair, comparing
            ``generic_crc(data)`` to ``target_crc`` (big-endian reading)
            and to its byte-reversed-at-width form (little-endian
            reading).  ``endian`` narrows which readings are yielded.
            See :func:`detect` for the full semantics.
        endian: ``"both"`` (default) yields one ``Attempt`` per
            ``(algorithm, byte_order)`` -- two per algorithm.  ``"big"``
            or ``"little"`` narrows to a single ordering, one
            ``Attempt`` per algorithm.  No effect on the ``target_crc``
            path (which is always big by convention).

    Yields:
        :class:`Attempt` per ``(algorithm, endianness)`` tried, in
        priority + catalogue order.  For text mode the generator
        terminates immediately if the packet doesn't parse.  In the
        ``target_crc`` path, endianness is always ``"big"`` (no byte
        parsing happened).

    Raises:
        TypeError: ``packet`` is not bytes-like or ``str``.

    Examples:
        >>> hit = next(a for a in detect_iter("123456789 cbf43926")
        ...            if a.matched)
        >>> hit.algorithm, hit.endianness
        ('crc32', 'big')
    """
    if not isinstance(packet, (bytes, bytearray, str)):
        raise TypeError("detect_iter takes a single packet (bytes-like or str)")
    names = _ordered_algorithm_names(algorithms, width)

    # target_crc short-circuit: stream one Attempt per (algo, endian)
    # pair, comparing ``generic_crc(data)`` to ``target_crc`` (BE
    # reading) and to its byte-reversed-at-width form (LE reading).
    # No CRC-tail extraction, no text-mode parse.
    if target_crc is not None:
        if target_crc < 0:
            raise ValueError(
                f"target_crc must be non-negative (got {target_crc})"
            )
        data = _packet_to_data_bytes(packet, mode, encoding)
        if data is None:
            return  # mode="hex" on non-hex str -> nothing to try
        for name in names:
            algo = ALGORITHMS[name]
            if target_crc >= (1 << algo.width):
                continue
            computed = generic_crc(data, algo)
            for byte_order in _endians_for(endian, dedup=(_crc_byte_len(algo.width) == 1)):
                tgt = (
                    target_crc if byte_order == "big"
                    else _byte_reversed(target_crc, algo.width)
                )
                yield Attempt(name, byte_order, computed == tgt)
        return

    # Hex pre-step.  ``mode="hex"`` *requires* the str to be hex-
    # encoded (returns immediately with no attempts otherwise, and
    # raises on a bytes-like packet).  ``mode="auto"`` *tries* hex
    # decoding for str input and uses it only if successful, falling
    # through to text mode on no-decode.
    if isinstance(packet, str) and mode in ("auto", "hex"):
        parsed = _looks_like_hex(packet)
        if parsed is not None:
            # The decoded bytes; the HexFormat is discarded here because
            # detect_iter yields ``Attempt`` values (no padding slot).
            # Callers that need the HexFormat use ``detect()`` instead.
            packet = parsed[0]
            mode = "binary"
        elif mode == "hex":
            return  # explicit hex but the str isn't hex -> no attempts
        # auto + str-not-hex: fall through to text mode (the regex
        # either matches the "data <sep> hex" shape or it doesn't).
    elif mode == "hex":
        # bytes-like packet + mode="hex" is a caller error.
        raise TypeError("hex mode requires str packet")
    # ``mode`` is now ``Literal["auto", "binary", "text"]`` here (the
    # "hex" cases above either turned mode into "binary" or returned/
    # raised), so ty narrows it correctly without an explicit cast.
    actual_mode = _resolve_mode([packet], mode)

    if actual_mode == "binary":
        if not isinstance(packet, (bytes, bytearray)):
            raise TypeError("binary mode requires bytes/bytearray packet")
        pb = bytes(packet)
        for name in names:
            algo = ALGORITHMS[name]
            w = _crc_byte_len(algo.width)
            if len(pb) <= w:
                continue
            for byte_order in _endians_for(endian, dedup=(w == 1)):
                yield Attempt(name, byte_order, _check_binary(pb, algo, w, byte_order))
    else:
        if not isinstance(packet, str):
            raise TypeError("text mode requires str packet")
        parsed = _parse_text(packet, encoding)
        if parsed is None:
            return
        _data, _tf, hex_len, _hex_str = parsed
        for name in names:
            algo = ALGORITHMS[name]
            if _crc_nibble_len(algo.width) != hex_len:
                continue
            for byte_order in _endians_for(
            endian, dedup=(hex_len <= 2 or hex_len % 2 == 1)
        ):
                yield Attempt(name, byte_order, _check_text(parsed, algo, byte_order))


# ---------------------------------------------------------------------------
# Eager match-mode dispatchers
# ---------------------------------------------------------------------------


def _detect_first(
    packets: list[Packet],
    mode: Literal["binary", "text"],
    names: list[str],
    encoding: str,
    endian: EndianSelector = "both",
) -> DetectResult:
    """Implement ``match="first"``: return the first cross-packet hit.

    Iterates ``(name, endian)`` in priority order and returns immediately
    when one survives across every packet.  Microsecond exit on the
    common ``crc32`` case.

    Args:
        packets: Normalized packets, all of the resolved mode.
        mode: Pre-resolved ``"binary"`` or ``"text"``.
        names: Scan order.
        encoding: For text-mode data encoding.
        endian: ``"both"`` (default), ``"big"``, or ``"little"``.

    Returns:
        A :class:`DetectResult` with at most one candidate.
    """
    if mode == "binary":
        # By construction (mode resolution upstream), every entry is bytes-like.
        bin_packets = cast(list[bytes | bytearray], packets)
        packets_b = [bytes(p) for p in bin_packets]
        for name in names:
            algo = ALGORITHMS[name]
            w = _crc_byte_len(algo.width)
            if any(len(p) <= w for p in packets_b):
                continue
            for byte_order in _endians_for(endian, dedup=(w == 1)):
                if all(_check_binary(p, algo, w, byte_order) for p in packets_b):
                    return DetectResult(
                        matched=True,
                        candidates=(DetectMatch(name, algo, byte_order, None),),
                    )
        return DetectResult(matched=False)

    # text mode
    parsed_packets: list[_ParsedText] = []
    for p in packets:
        if not isinstance(p, str):
            raise TypeError("text mode requires str packets")
        pp = _parse_text(p, encoding)
        if pp is None:
            return DetectResult(matched=False)
        parsed_packets.append(pp)
    # All packets must agree on hex length for any algo to possibly match all.
    hex_lens = {pp[2] for pp in parsed_packets}
    if len(hex_lens) > 1:
        return DetectResult(matched=False)
    hex_len = next(iter(hex_lens))
    text_format = parsed_packets[0][1]
    for name in names:
        algo = ALGORITHMS[name]
        if _crc_nibble_len(algo.width) != hex_len:
            continue
        for byte_order in _endians_for(
            endian, dedup=(hex_len <= 2 or hex_len % 2 == 1)
        ):
            if all(_check_text(pp, algo, byte_order) for pp in parsed_packets):
                return DetectResult(
                    matched=True,
                    candidates=(DetectMatch(name, algo, byte_order, text_format),),
                )
    return DetectResult(matched=False)


def _detect_all_or_set(
    packets: list[Packet],
    mode: Literal["binary", "text"],
    names: list[str],
    encoding: str,
    *,
    strict_set: bool,
    endian: EndianSelector = "both",
) -> DetectResult:
    """Implement ``match="all"`` (and ``"set"`` with ``strict_set=True``).

    Builds a per-packet set of candidate ``(name, endian)`` pairs,
    intersects across packets, and -- in strict mode -- requires the
    result to collapse to a single algorithm name.

    Args:
        packets: Normalized packets, all of the resolved mode.
        mode: Pre-resolved ``"binary"`` or ``"text"``.
        names: Scan order.
        encoding: For text-mode data encoding.
        strict_set: When ``True``, ambiguous results (>= 2 distinct
            algorithms surviving) flip ``matched=False``.
        endian: ``"both"`` (default), ``"big"``, or ``"little"``.

    Returns:
        A :class:`DetectResult` with 0..N candidates in scan order.
    """
    text_format: TextFormat | None = None
    per_packet: list[set[tuple[str, Endianness]]] = []

    if mode == "binary":
        bin_packets = cast(list[bytes | bytearray], packets)
        for p in bin_packets:
            per_packet.append(
                _matches_for_binary_packet(bytes(p), names, endian)
            )
    else:
        text_packets = cast(list[str], packets)
        for p in text_packets:
            parsed = _parse_text(p, encoding)
            if parsed is None:
                return DetectResult(matched=False)
            if text_format is None:
                text_format = parsed[1]
            per_packet.append(
                _matches_for_text_packet(parsed, names, endian)
            )

    intersection = per_packet[0].copy()
    for m in per_packet[1:]:
        intersection &= m
    if not intersection:
        return DetectResult(matched=False)

    # Order by priority + catalogue scan.  Full pair list (big, little)
    # used here even when ``endian`` is narrowed -- the intersection
    # already filtered out any pairs the per-packet scan didn't admit.
    ordered_pairs: list[tuple[str, Endianness]] = []
    for name in names:
        for byte_order in ("big", "little"):
            if (name, byte_order) in intersection:
                ordered_pairs.append((name, byte_order))

    if strict_set:
        unique_algos = {n for n, _e in ordered_pairs}
        if len(unique_algos) != 1:
            return DetectResult(matched=False)

    candidates = tuple(
        DetectMatch(
            algorithm=n,
            info=ALGORITHMS[n],
            endianness=e,
            padding=text_format,
        )
        for n, e in ordered_pairs
    )
    return DetectResult(matched=True, candidates=candidates)
