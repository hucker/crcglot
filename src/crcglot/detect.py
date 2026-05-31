"""CRC algorithm detection -- brute-force identification.

Given a packet whose tail is a CRC, scan the 71-entry reveng catalogue x
both byte orders to find which algorithm matches.  Supports binary
packets (``bytes``/``bytearray``) and text packets (``str``) of the form
``"data <whitespace> [0x]hex"``.  Multi-packet input is intersected so
single-packet false positives collapse fast.

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
class DetectMatch:
    """One consistent identification of an algorithm.

    Attributes:
        algorithm: Catalogue name (e.g. ``"crc32"``).
        info: The matching :class:`AlgorithmInfo` (full parameters).
        endianness: Byte order of the trailing CRC in the packet.
        padding: Text-mode formatting (separator + leader + case); ``None``
            for binary packets.
    """

    algorithm: str
    info: AlgorithmInfo
    endianness: Endianness
    padding: TextFormat | None = None


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
    mode: Literal["auto", "binary", "text"] = "auto",
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
        mode: ``"auto"`` picks binary for bytes-like, text for ``str``.
            Override with ``"binary"`` or ``"text"`` to force.
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
    """
    packets = _normalize_packets(packet)
    if not packets:
        return DetectResult(matched=False)
    actual_mode = _resolve_mode(packets, mode)
    names = _ordered_algorithm_names(algorithms)

    if match == "first":
        return _detect_first(packets, actual_mode, names, encoding)
    return _detect_all_or_set(
        packets, actual_mode, names, encoding, strict_set=(match == "set"),
    )


def detect_iter(
    packet: bytes | bytearray | str,
    *,
    mode: Literal["auto", "binary", "text"] = "auto",
    encoding: str = "utf-8",
    algorithms: str | None = None,
) -> Iterator[Attempt]:
    """Stream every ``(algorithm, endianness)`` attempt for one packet.

    Lower-level than :func:`detect`: yields each :class:`Attempt`
    (matched or not) in priority order so the caller can drive a
    progress UI, log every step, or break out early on the first hit.

    Args:
        packet: A single bytes-like or ``str`` packet (no iterables).
        mode: ``"auto"`` picks binary for bytes-like, text for ``str``.
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
