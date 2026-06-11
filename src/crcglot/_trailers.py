"""Non-CRC trailer identification -- a "heads up", not a code generator.

When :func:`crcglot.detect` finds no catalogue CRC and
:func:`crcglot.reverse` can't recover a polynomial, the trailing field is
often not a CRC at all: a simple checksum (8-bit sum / LRC / XOR, Adler-32,
Fletcher, the Internet checksum) or a **cryptographic digest** (MD5, the
SHA families, BLAKE2 -- full or truncated).  :func:`identify_trailer`
recognises both kinds and tells the caller which one fits --
**identification only**.  crcglot deliberately does not generate code for
these (checksums are trivial one-liners; digests live in every language's
standard library).

The point of the search is information, not action: the answer goes to a
human -- or to an LLM driving this as a tool -- who must decide what to do
next with an unfamiliar packet.  "The trailing field is an Adler-32" or
"found a 32-byte field that matches no unkeyed digest, so likely a MAC"
redirects the whole investigation in one step: it ends the CRC parameter
hunt, names the likely protocol family, and says whether verification is
even possible without a key.

Reliability comes from corroboration, not a single packet: an 8-bit checksum
matches a random frame about 1 in 256 of the time, so a hit on one packet is
weak.  :func:`identify_trailer` intersects across every packet you give it and
reports :attr:`TrailerResult.frames_agreed` -- the more frames that agree, the
more trustworthy the call (N agreeing 8-bit frames ~ 1 in 256**N).  Digest
matches are individually strong (16+ matching bytes), but corroboration still
guards against a frame that happens to embed its own hash for other reasons.

Keyed constructions (HMAC, CMAC, SipHash) are **undetectable by design** --
without the key, a MAC trailer is indistinguishable from random bytes.  When a
delimited trailing field is digest-sized but matches no unkeyed digest,
:attr:`TrailerResult.note` says so, which is itself useful: it tells you to
stop burning time on CRC space because the answer is cryptographic.

The packet shape mirrors :func:`crcglot.detect`: whole frames with the trailer
as the trailing field, as bytes, a hex string (``"0x12 0x34"``), or a
``"data <sep> hex"`` text line.
"""

from __future__ import annotations

import fnmatch
import hashlib
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Callable

from crcglot._detect import (
    Endianness,
    EndianSelector,
    Packet,
    _byte_reversed,
    _crc_byte_len,
    _crc_nibble_len,
    _endians_for,
    _is_odd_hex,
    _looks_like_hex,
    _normalize_packets,
    _parse_text,
    _read_hex_crc,
)

# ---------------------------------------------------------------------------
# Checksum compute functions -- one O(n) pass each, no lookup tables
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
# Digest compute functions -- stdlib hashlib only
# ---------------------------------------------------------------------------

# Registry name -> hashlib constructor name.  Everything here is guaranteed
# in CPython 3.11+ (SHA-3 and BLAKE2 landed in 3.6).
_HASHLIB_NAMES: dict[str, str] = {
    "md5": "md5",
    "sha1": "sha1",
    "sha224": "sha224",
    "sha256": "sha256",
    "sha384": "sha384",
    "sha512": "sha512",
    "sha3-224": "sha3_224",
    "sha3-256": "sha3_256",
    "sha3-384": "sha3_384",
    "sha3-512": "sha3_512",
    "blake2s": "blake2s",
    "blake2b": "blake2b",
}


def _digest(name: str, data: bytes) -> bytes | None:
    """Digest of ``data`` for a registry digest name, or ``None`` if blocked.

    ``None`` covers FIPS-restricted builds where e.g. MD5 construction raises;
    the candidate is silently skipped rather than failing identification.
    """
    if name == "sha256d":
        first = _digest("sha256", data)
        return None if first is None else _digest("sha256", first)
    try:
        return hashlib.new(_HASHLIB_NAMES[name], data).digest()
    except ValueError:  # pragma: no cover - FIPS-mode interpreter
        return None


# Truncated-digest tail sizes worth testing, besides the full digest: 4- and
# 8-byte leading truncations are the common framing choices (base58check is
# sha256d[:4]).  Anything shorter would false-positive too readily.
_TRUNCATION_SIZES = (4, 8)

# Delimited trailing-field byte sizes that look digest-shaped; used for the
# "matched nothing but smells cryptographic" note.
_DIGEST_FIELD_SIZES = frozenset({16, 20, 28, 32, 48, 64})

def _mac_note(sizes: set[int]) -> str:
    """Observation-first heads-up for an unmatched digest-sized field."""
    found = (
        f"a {sizes.pop()}-byte trailing field" if len(sizes) == 1
        else "a digest-sized trailing field"
    )
    return (
        f"found {found} matching no unkeyed digest; could be a MAC "
        "(HMAC / CMAC -- keyed, unverifiable without the key) or an "
        "uncommon / truncated hash"
    )


# ---------------------------------------------------------------------------
# Typed metadata record + registry + lookup (mirrors VariantInfo / NamingInfo)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrailerInfo:
    """Metadata for one non-CRC trailer algorithm.

    Identification-only: there is no generator callable here (crcglot does not
    emit code for these).  Parallel private maps hold the compute functions.

    Attributes:
        name: Machine identifier (e.g. ``"lrc8"``, ``"sha256"``).
        label: Human-readable label.
        description: One-line description (algorithm + a typical user).
        width: Bit width of the (full) trailing field.
        kind: ``"checksum"`` (arithmetic, weak, tiny) or ``"digest"``
            (cryptographic hash, byte-string trailer).
    """

    name: str
    label: str
    description: str
    width: int
    kind: str


# Registry, simplest first: the checksum family (8-bit, then 16, then 32) in
# the same slowest/simplest-first convention as VARIANT_ORDER, then digests
# by width.
TRAILERS: dict[str, TrailerInfo] = {
    "sum8": TrailerInfo(
        "sum8", "8-bit sum", "Modular sum of the bytes (mod 256).", 8,
        "checksum"),
    "lrc8": TrailerInfo(
        "lrc8", "8-bit LRC (two's-complement sum)",
        "Two's-complement of the byte sum; e.g. Modbus-ASCII LRC.", 8,
        "checksum"),
    "sum8-1c": TrailerInfo(
        "sum8-1c", "8-bit one's-complement sum",
        "One's-complement of the byte sum.", 8, "checksum"),
    "xor8": TrailerInfo(
        "xor8", "8-bit XOR (BCC)",
        "XOR of all bytes; e.g. NMEA 0183 block check character.", 8,
        "checksum"),
    "sum16": TrailerInfo(
        "sum16", "16-bit sum", "Modular sum of the bytes (mod 65536).", 16,
        "checksum"),
    "inet16": TrailerInfo(
        "inet16", "Internet checksum (RFC 1071)",
        "One's-complement 16-bit sum; IP / UDP / TCP / ICMP.", 16,
        "checksum"),
    "fletcher16": TrailerInfo(
        "fletcher16", "Fletcher-16",
        "Two running 8-bit sums mod 255; a cheaper-than-CRC alternative.", 16,
        "checksum"),
    "fletcher32": TrailerInfo(
        "fletcher32", "Fletcher-32",
        "Two running 16-bit sums mod 65535 over 16-bit words.", 32,
        "checksum"),
    "adler32": TrailerInfo(
        "adler32", "Adler-32",
        "zlib's two-sum checksum (mod 65521); used in PNG / zlib / rsync.", 32,
        "checksum"),
    "md5": TrailerInfo(
        "md5", "MD5",
        "128-bit digest; legacy file-transfer manifests and firmware images.",
        128, "digest"),
    "sha1": TrailerInfo(
        "sha1", "SHA-1",
        "160-bit digest; git packfiles end in one, legacy signing.", 160,
        "digest"),
    "sha224": TrailerInfo(
        "sha224", "SHA-224", "SHA-2 family, 224-bit digest.", 224, "digest"),
    "sha256": TrailerInfo(
        "sha256", "SHA-256",
        "SHA-2 family, 256-bit digest; firmware / OTA image trailers and "
        "manifests -- the modern default.", 256, "digest"),
    "sha384": TrailerInfo(
        "sha384", "SHA-384", "SHA-2 family, 384-bit digest.", 384, "digest"),
    "sha512": TrailerInfo(
        "sha512", "SHA-512", "SHA-2 family, 512-bit digest.", 512, "digest"),
    "sha3-224": TrailerInfo(
        "sha3-224", "SHA3-224", "SHA-3 (Keccak), 224-bit digest.", 224,
        "digest"),
    "sha3-256": TrailerInfo(
        "sha3-256", "SHA3-256", "SHA-3 (Keccak), 256-bit digest.", 256,
        "digest"),
    "sha3-384": TrailerInfo(
        "sha3-384", "SHA3-384", "SHA-3 (Keccak), 384-bit digest.", 384,
        "digest"),
    "sha3-512": TrailerInfo(
        "sha3-512", "SHA3-512", "SHA-3 (Keccak), 512-bit digest.", 512,
        "digest"),
    "blake2s": TrailerInfo(
        "blake2s", "BLAKE2s",
        "BLAKE2s, 256-bit digest (unkeyed); b2sum-era tooling on 32-bit "
        "targets.", 256, "digest"),
    "blake2b": TrailerInfo(
        "blake2b", "BLAKE2b",
        "BLAKE2b, 512-bit digest (unkeyed); b2sum and modern archive "
        "tooling.", 512, "digest"),
    "sha256d": TrailerInfo(
        "sha256d", "double SHA-256",
        "sha256(sha256(data)); Bitcoin-style framing -- base58check uses the "
        "first 4 bytes.", 256, "digest"),
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

_CHECKSUM_NAMES = tuple(n for n, i in TRAILERS.items() if i.kind == "checksum")
_DIGEST_NAMES = tuple(n for n, i in TRAILERS.items() if i.kind == "digest")


def trailer_info(name: str) -> TrailerInfo:
    """Look up a trailer algorithm's metadata by name.

    Args:
        name: A key of :data:`TRAILERS` (e.g. ``"fletcher16"``, ``"sha256"``).

    Returns:
        The :class:`TrailerInfo` record.

    Raises:
        KeyError: ``name`` is not a known trailer algorithm.

    Examples:
        >>> from crcglot import trailer_info
        >>> trailer_info("lrc8").width
        8
        >>> trailer_info("sha256").kind
        'digest'
    """
    return TRAILERS[name]


# ---------------------------------------------------------------------------
# Identification results (mirror DetectMatch / DetectResult)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrailerMatch:
    """One trailer algorithm consistent with every packet.

    Attributes:
        name: Trailer name (a key of :data:`TRAILERS`).
        info: The matching :class:`TrailerInfo`.
        endianness: Byte order of the trailing field that matched.  Always
            ``"big"`` for 8-bit checksums and for digests (a digest is a byte
            string, not an integer -- byte order is moot).
        truncated_to: For a digest matched by its leading bytes only, the
            trailer's byte length (e.g. ``4`` for base58check's
            ``sha256d[:4]``); ``None`` for a full-length match and for
            checksums.
    """

    name: str
    info: TrailerInfo
    endianness: Endianness
    truncated_to: int | None = None


@dataclass(frozen=True)
class TrailerResult:
    """Outcome of :func:`identify_trailer`.

    Truthy iff at least one trailer algorithm matched across all packets.

    Attributes:
        matched: Whether any algorithm survived.
        candidates: The surviving :class:`TrailerMatch` entries, simplest
            first (checksums before digests).
        frames_agreed: How many packets corroborated the match -- the
            confidence signal.  One frame is weak for an 8-bit checksum
            (random match ~1/256); more frames make a hit trustworthy.
        note: A heads-up when nothing matched but a delimited trailing field
            is digest-sized -- likely a MAC (keyed, unverifiable) or an
            uncommon hash.  Empty when there is nothing useful to add.
    """

    matched: bool
    candidates: tuple[TrailerMatch, ...] = field(default_factory=tuple)
    frames_agreed: int = 0
    note: str = ""

    def __bool__(self) -> bool:
        return self.matched

    @property
    def name(self) -> str | None:
        """The first candidate's name, or ``None``."""
        return self.candidates[0].name if self.candidates else None


# A survivor is (name, endianness, truncated_to); checksums always carry
# truncated_to=None.
_Survivor = tuple[str, Endianness, "int | None"]


def _checksum_matches_for_bytes(
    pkt: bytes, *, endian: EndianSelector, names: Sequence[str],
) -> set[_Survivor]:
    """Checksum survivors for one binary frame."""
    out: set[_Survivor] = set()
    for name in names:
        info = TRAILERS[name]
        w = _crc_byte_len(info.width)
        if len(pkt) <= w:
            continue
        expected = _COMPUTE[name](pkt[:-w])
        field_bytes = pkt[-w:]
        for order in _endians_for(endian, dedup=(w == 1)):
            if int.from_bytes(field_bytes, order) == expected:
                out.add((name, order, None))
    return out


def _digest_matches_for_bytes(
    pkt: bytes, *, names: Sequence[str],
) -> set[_Survivor]:
    """Digest survivors for one binary frame (full or leading-truncated)."""
    out: set[_Survivor] = set()
    for name in names:
        full = TRAILERS[name].width // 8
        for size in (full, *_TRUNCATION_SIZES):
            if size > full or len(pkt) <= size:
                continue
            d = _digest(name, pkt[:-size])
            if d is not None and pkt[-size:] == d[:size]:
                out.add((name, "big", None if size == full else size))
    return out


def _checksum_matches_for_text(
    parsed: tuple[bytes, object, int, str], *,
    endian: EndianSelector, names: Sequence[str],
) -> set[_Survivor]:
    """Checksum survivors for a ``data <sep> hex`` frame.

    Only checksums whose width matches the trailing hex field's length are
    eligible (a 4-nibble field can only be a 16-bit checksum, etc.).
    """
    data, _tf, _hex_len, hex_str = parsed
    out: set[_Survivor] = set()
    for name in names:
        info = TRAILERS[name]
        if _crc_nibble_len(info.width) != len(hex_str):
            continue
        expected = _COMPUTE[name](data)
        for order in _endians_for(endian, dedup=(_crc_byte_len(info.width) == 1)):
            read = _read_hex_crc(hex_str, order)
            if read is not None and read == expected:
                out.add((name, order, None))
    return out


def _digest_matches_for_text(
    parsed: tuple[bytes, object, int, str], *, names: Sequence[str],
) -> set[_Survivor]:
    """Digest survivors for a ``data <sep> hex`` frame.

    The hex field is compared as raw bytes (a digest has no byte order); the
    field's length selects full-digest vs truncated candidates.
    """
    data, _tf, _hex_len, hex_str = parsed
    if len(hex_str) % 2:
        return set()
    try:
        field_bytes = bytes.fromhex(hex_str)
    except ValueError:
        return set()
    size = len(field_bytes)
    out: set[_Survivor] = set()
    for name in names:
        full = TRAILERS[name].width // 8
        if size != full and size not in _TRUNCATION_SIZES:
            continue
        if size > full:
            continue
        d = _digest(name, data)
        if d is not None and field_bytes == d[:size]:
            out.add((name, "big", None if size == full else size))
    return out


def identify_trailer(
    packet: Packet | Iterable[Packet],
    *,
    mode: str = "auto",
    endian: EndianSelector = "both",
    encoding: str = "utf-8",
    trailers: str | None = None,
) -> TrailerResult:
    """Identify the non-CRC trailing field in a packet: checksum or digest.

    For each candidate algorithm, the trailing field of the matching size is
    peeled off and recomputed over the rest; an algorithm is kept only if it
    fits **every** packet (the intersection).  Digests are tried at full
    length and at the common 4- / 8-byte leading truncations.  Identification
    only -- no code is generated; the result exists to inform the caller's
    (human's or LLM's) next move with an unfamiliar packet.

    Keyed constructions (HMAC, CMAC) are undetectable without the key; when a
    delimited trailing field is digest-sized but nothing matches, the result's
    ``note`` says so.

    Args:
        packet: One frame (bytes, hex string, or ``"data <sep> hex"`` text) or
            an iterable of them (all the same kind).  The trailer is the
            trailing field.
        mode: ``"auto"`` (default) decodes a hex-looking ``str`` to bytes, else
            parses it as text; ``"hex"`` requires hex; ``"text"`` forces the
            ``"data <sep> hex"`` reading; ``"binary"`` is for bytes input.
        endian: Byte order of the trailing field for 16/32-bit checksums --
            ``"big"``, ``"little"``, or ``"both"`` (default).  Ignored for
            8-bit checksums and digests.
        encoding: Used only in text mode to encode the data portion.
        trailers: Optional ``fnmatch`` glob (e.g. ``"fletcher*"``, ``"sha*"``)
            to narrow the candidates.

    Returns:
        A :class:`TrailerResult`, truthy on match, with ``frames_agreed`` set
        to the number of packets that corroborated.

    Examples:
        >>> from crcglot import identify_trailer
        >>> data = b"123456789"
        >>> frame = data + bytes([(-sum(data)) & 0xFF])  # 8-bit LRC trailer
        >>> identify_trailer(frame).name
        'lrc8'
        >>> import hashlib
        >>> frame = data + hashlib.sha256(data).digest()
        >>> identify_trailer(frame).name
        'sha256'
    """
    packets = _normalize_packets(packet)
    if not packets:
        return TrailerResult(matched=False)
    cksum_names = [
        n for n in _CHECKSUM_NAMES
        if trailers is None or fnmatch.fnmatch(n, trailers)
    ]
    digest_names = [
        n for n in _DIGEST_NAMES
        if trailers is None or fnmatch.fnmatch(n, trailers)
    ]

    survivor_sets: list[set[_Survivor]] = []
    digest_sized_fields: list[int] = []
    for p in packets:
        if isinstance(p, (bytes, bytearray)):
            raw = bytes(p)
            survivor_sets.append(
                _checksum_matches_for_bytes(
                    raw, endian=endian, names=cksum_names)
                | _digest_matches_for_bytes(raw, names=digest_names))
            continue
        # A str packet: try hex-decoding first (auto/hex), else parse as text.
        decoded = _looks_like_hex(p) if mode in ("auto", "hex") else None
        if decoded is not None:
            survivor_sets.append(
                _checksum_matches_for_bytes(
                    decoded[0], endian=endian, names=cksum_names)
                | _digest_matches_for_bytes(decoded[0], names=digest_names))
        elif mode == "hex":
            if _is_odd_hex(p):
                raise ValueError(
                    f"hex mode: odd number of hex digits in {p!r} -- a hex byte "
                    "string needs an even count")
            survivor_sets.append(set())  # required hex but didn't decode
        else:
            parsed = _parse_text(p, encoding)
            if parsed is None:
                survivor_sets.append(set())
                continue
            if len(parsed[3]) // 2 in _DIGEST_FIELD_SIZES:
                digest_sized_fields.append(len(parsed[3]) // 2)
            survivor_sets.append(
                _checksum_matches_for_text(
                    parsed, endian=endian, names=cksum_names)
                | _digest_matches_for_text(parsed, names=digest_names))

    common = set.intersection(*survivor_sets) if survivor_sets else set()
    orders: tuple[Endianness, ...] = ("big", "little")
    names_in_order = (*cksum_names, *digest_names)
    candidates = tuple(
        TrailerMatch(n, TRAILERS[n], e, t)
        for n in names_in_order
        for e in orders
        for t in (None, *_TRUNCATION_SIZES)
        if (n, e, t) in common
    )
    note = ""
    if not candidates and packets and len(digest_sized_fields) == len(packets):
        note = _mac_note(set(digest_sized_fields))
    return TrailerResult(
        matched=bool(candidates),
        candidates=candidates,
        frames_agreed=len(packets) if candidates else 0,
        note=note,
    )


def _identify_trailer_pairs(
    pairs: Sequence[tuple[bytes, int]],
) -> TrailerResult:
    """Trailer hint from ``(message, value_int)`` pairs (for ``reverse``).

    Checksums only: ``reverse`` peels CRC-sized (<= 8 byte) trailing fields
    into integers, which a 16+ byte digest never fits.  A checksum fits a pair
    when its computed value equals the given integer read either way: as-is
    (``"big"``) or byte-reversed over the checksum's width (``"little"``).
    Trying both orders means a little-endian-stored multi-byte checksum is
    caught even when the caller read the field big-endian (the default).  A
    checksum is kept only if it fits every pair (the intersection).  8-bit
    checksums have no byte order, so they only ever report ``"big"``.
    """
    if not pairs:
        return TrailerResult(matched=False)
    survivor_sets: list[set[_Survivor]] = []
    for msg, value in pairs:
        fits: set[_Survivor] = set()
        for name in _CHECKSUM_NAMES:
            info = TRAILERS[name]
            expected = _COMPUTE[name](msg)
            if expected == value:
                fits.add((name, "big", None))
            elif (info.width > 8 and value < (1 << info.width)
                    and expected == _byte_reversed(value, info.width)):
                fits.add((name, "little", None))
        survivor_sets.append(fits)
    common = set.intersection(*survivor_sets)
    candidates = tuple(
        TrailerMatch(n, TRAILERS[n], e)
        for n in _CHECKSUM_NAMES for e in ("big", "little")
        if (n, e, None) in common
    )
    return TrailerResult(
        matched=bool(candidates),
        candidates=candidates,
        frames_agreed=len(pairs) if candidates else 0,
    )
