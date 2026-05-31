"""CRC algorithm detection -- brute-force identification.

Given a packet whose tail is a CRC, scan the 69-entry reveng catalogue x
both byte orders to find which algorithm matches.  Supports binary
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


@dataclass(frozen=True)
class TextFormat:
    """The text-packet shape captured from the trailing whitespace + hex.

    Attributes:
        separator: The literal whitespace between data and hex.
        hex_prefix: ``""``, ``"0x"``, or ``"0X"``.
        uppercase: ``True`` when the hex digits use upper-case A-F.
    """

    separator: str
    hex_prefix: str
    uppercase: bool = False


@dataclass(frozen=True)
class HexFormat:
    """The hex-text-packet shape captured when ``detect`` auto-decoded a
    ``str`` of hex bytes (``"31 32"``, ``"0x12 0x34"``, ``"AB:CD:EF"``,
    etc.) into raw bytes.  Lets ``encode_match`` rebuild the same
    surface formatting.

    Attributes:
        byte_separator: The literal characters between hex byte pairs
            -- ``""`` (none), ``" "``, ``","``, ``":"``, ``"\\n"``, or
            any short run.
        prefix: ``""``, ``"0x"``, or ``"0X"`` -- which ``0x``-style
            prefix the producer used (if any).
        prefix_per_byte: ``True`` when the prefix appears before *each*
            byte (``"0x12 0x34"``); ``False`` when the prefix appears
            once at the start of the whole string (``"0X1234"``).
        uppercase: ``True`` if hex digits use upper-case A-F.
    """

    byte_separator: str
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


def _ordered_algorithm_names(algorithms_filter: str | None) -> list[str]:
    """Build the scan order: priority head, then the rest of the catalogue.

    Args:
        algorithms_filter: Optional ``fnmatch`` glob (e.g. ``"crc16-*"``).
            ``None`` means no filtering.

    Returns:
        Algorithm names in scan order, after applying the filter.
    """
    rest = [n for n in ALGORITHMS if n not in _PRIORITY]
    ordered = [*_PRIORITY, *rest]
    if algorithms_filter is not None:
        ordered = [n for n in ordered if fnmatch.fnmatch(n, algorithms_filter)]
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
        >>> b, fmt.byte_separator, fmt.prefix, fmt.prefix_per_byte
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
    byte_separator = sep_match.group(0) if sep_match else ""

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
        byte_separator=byte_separator,
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
        TextFormat(separator=sep, hex_prefix=leader, uppercase=uppercase),
        len(hex_str),
        hex_str,
    )


def _check_binary(packet: bytes, algo: AlgorithmInfo, w: int, endian: Endianness) -> bool:
    """Recompute the CRC of ``packet[:-w]`` and compare to ``packet[-w:]``.

    Args:
        packet: The full packet bytes.
        algo: Algorithm to apply.
        w: CRC width in bytes (``algo.width // 8``).
        endian: How to interpret the trailing CRC bytes.

    Returns:
        ``True`` iff the trailing bytes equal the computed CRC.
    """
    parsed = int.from_bytes(packet[-w:], endian)
    computed = generic_crc(
        packet[:-w], algo.width, algo.poly, algo.init, algo.refin, algo.refout, algo.xorout,
    )
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
        parsed_int = int.from_bytes(bytes.fromhex(hex_str), "little")
    computed = generic_crc(
        data, algo.width, algo.poly, algo.init, algo.refin, algo.refout, algo.xorout,
    )
    return parsed_int == computed


def _matches_for_binary_packet(
    pb: bytes, names: list[str],
) -> set[tuple[str, Endianness]]:
    """All ``(name, endian)`` pairs that fit one binary packet.

    Width-8 algorithms are tried in ``"big"`` only -- BE and LE encode
    identically for a single byte.

    Args:
        pb: One binary packet.
        names: Scan order (already filtered and priority-ordered).

    Returns:
        The set of matching ``(algorithm_name, endianness)`` pairs.
    """
    matches: set[tuple[str, Endianness]] = set()
    for name in names:
        algo = ALGORITHMS[name]
        w = algo.width // 8
        if len(pb) <= w:
            continue
        endians: tuple[Endianness, ...] = ("big",) if w == 1 else ("big", "little")
        for endian in endians:
            if _check_binary(pb, algo, w, endian):
                matches.add((name, endian))
    return matches


def _matches_for_text_packet(
    parsed: _ParsedText, names: list[str],
) -> set[tuple[str, Endianness]]:
    """All ``(name, endian)`` pairs that fit one text packet.

    Width inference filters early: an algorithm can only match if its
    width matches the hex string length (``hex_len == width // 4``), so
    most of the catalogue is rejected without calling ``generic_crc``.

    Args:
        parsed: Output of :func:`_parse_text`.
        names: Scan order.

    Returns:
        The set of matching ``(algorithm_name, endianness)`` pairs.
    """
    matches: set[tuple[str, Endianness]] = set()
    _data, _tf, hex_len, _hex_str = parsed
    for name in names:
        algo = ALGORITHMS[name]
        if algo.width // 4 != hex_len:
            continue
        endians: tuple[Endianness, ...] = ("big",) if hex_len <= 2 else ("big", "little")
        for endian in endians:
            if _check_text(parsed, algo, endian):
                matches.add((name, endian))
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
    match: Literal["first", "all", "set"] = "first",
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
        match: Selection strategy.  ``"first"`` (default) stops at the
            first hit in priority order; ``"all"`` returns every
            consistent candidate; ``"set"`` succeeds only if exactly one
            algorithm survives across all packets.

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
    """
    packets = _normalize_packets(packet)
    if not packets:
        return DetectResult(matched=False)
    names = _ordered_algorithm_names(algorithms)

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
            decoded_bytes_explicit, "binary", names, encoding, match,
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
                decoded_bytes, "binary", names, encoding, match,
            )
            if hex_result.matched:
                return _attach_padding(hex_result, hex_format)
        # Fall through to text mode for the original str packets.

    actual_mode = _resolve_mode(packets, mode)
    return _run_detect(packets, actual_mode, names, encoding, match)


def _run_detect(
    packets: list[Packet],
    mode: Literal["binary", "text"],
    names: list[str],
    encoding: str,
    match: Literal["first", "all", "set"],
) -> DetectResult:
    """Dispatch to the right match-mode helper after mode + packets are settled."""
    if match == "first":
        return _detect_first(packets, mode, names, encoding)
    return _detect_all_or_set(
        packets, mode, names, encoding, strict_set=(match == "set"),
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


def detect_iter(
    packet: bytes | bytearray | str,
    *,
    mode: Literal["auto", "binary", "text", "hex"] = "auto",
    encoding: str = "utf-8",
    algorithms: str | None = None,
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

    Yields:
        :class:`Attempt` per ``(algorithm, endianness)`` tried, in
        priority + catalogue order.  For text mode the generator
        terminates immediately if the packet doesn't parse.

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
    names = _ordered_algorithm_names(algorithms)

    if actual_mode == "binary":
        if not isinstance(packet, (bytes, bytearray)):
            raise TypeError("binary mode requires bytes/bytearray packet")
        pb = bytes(packet)
        for name in names:
            algo = ALGORITHMS[name]
            w = algo.width // 8
            if len(pb) <= w:
                continue
            endians: tuple[Endianness, ...] = ("big",) if w == 1 else ("big", "little")
            for endian in endians:
                yield Attempt(name, endian, _check_binary(pb, algo, w, endian))
    else:
        if not isinstance(packet, str):
            raise TypeError("text mode requires str packet")
        parsed = _parse_text(packet, encoding)
        if parsed is None:
            return
        _data, _tf, hex_len, _hex_str = parsed
        for name in names:
            algo = ALGORITHMS[name]
            if algo.width // 4 != hex_len:
                continue
            endians = ("big",) if hex_len <= 2 else ("big", "little")
            for endian in endians:
                yield Attempt(name, endian, _check_text(parsed, algo, endian))


# ---------------------------------------------------------------------------
# Eager match-mode dispatchers
# ---------------------------------------------------------------------------


def _detect_first(
    packets: list[Packet],
    mode: Literal["binary", "text"],
    names: list[str],
    encoding: str,
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

    Returns:
        A :class:`DetectResult` with at most one candidate.
    """
    if mode == "binary":
        # By construction (mode resolution upstream), every entry is bytes-like.
        bin_packets = cast(list[bytes | bytearray], packets)
        packets_b = [bytes(p) for p in bin_packets]
        for name in names:
            algo = ALGORITHMS[name]
            w = algo.width // 8
            if any(len(p) <= w for p in packets_b):
                continue
            endians: tuple[Endianness, ...] = ("big",) if w == 1 else ("big", "little")
            for endian in endians:
                if all(_check_binary(p, algo, w, endian) for p in packets_b):
                    return DetectResult(
                        matched=True,
                        candidates=(DetectMatch(name, algo, endian, None),),
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
        if algo.width // 4 != hex_len:
            continue
        endians = ("big",) if hex_len <= 2 else ("big", "little")
        for endian in endians:
            if all(_check_text(pp, algo, endian) for pp in parsed_packets):
                return DetectResult(
                    matched=True,
                    candidates=(DetectMatch(name, algo, endian, text_format),),
                )
    return DetectResult(matched=False)


def _detect_all_or_set(
    packets: list[Packet],
    mode: Literal["binary", "text"],
    names: list[str],
    encoding: str,
    *,
    strict_set: bool,
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

    Returns:
        A :class:`DetectResult` with 0..N candidates in scan order.
    """
    text_format: TextFormat | None = None
    per_packet: list[set[tuple[str, Endianness]]] = []

    if mode == "binary":
        bin_packets = cast(list[bytes | bytearray], packets)
        for p in bin_packets:
            per_packet.append(_matches_for_binary_packet(bytes(p), names))
    else:
        text_packets = cast(list[str], packets)
        for p in text_packets:
            parsed = _parse_text(p, encoding)
            if parsed is None:
                return DetectResult(matched=False)
            if text_format is None:
                text_format = parsed[1]
            per_packet.append(_matches_for_text_packet(parsed, names))

    intersection = per_packet[0].copy()
    for m in per_packet[1:]:
        intersection &= m
    if not intersection:
        return DetectResult(matched=False)

    # Order by priority + catalogue scan.
    ordered_pairs: list[tuple[str, Endianness]] = []
    endians: tuple[Endianness, ...] = ("big", "little")
    for name in names:
        for endian in endians:
            if (name, endian) in intersection:
                ordered_pairs.append((name, endian))

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
