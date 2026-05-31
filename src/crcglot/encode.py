"""CRC packet encoder -- the round-trip partner to ``crcglot.detect``.

``encode()`` appends a binary CRC; ``encode_text()`` builds a
``"data <sep> [<leader>]<hex>"`` string; ``encode_match()`` takes the
``DetectMatch`` produced by ``detect()`` and reproduces a packet in the
exact same format -- which is the cleanest round-trip correctness test
the shape inference can have.
"""

from __future__ import annotations

from crcglot.catalogue import ALGORITHMS, AlgorithmInfo, generic_crc
from crcglot.detect import DetectMatch, Endianness, TextFormat


def _lookup(algorithm: str) -> AlgorithmInfo:
    # Trivial wrapper; the helpful error message is the only value-add.
    try:
        return ALGORITHMS[algorithm]
    except KeyError:
        raise ValueError(
            f"unknown algorithm {algorithm!r}; "
            f"use 'crcglot list' or crcglot.ALGORITHMS to browse"
        ) from None


def encode(
    data: bytes | bytearray,
    algorithm: str,
    *,
    endianness: Endianness = "big",
) -> bytes:
    """Build a binary packet by appending the CRC to ``data``.

    The CRC occupies ``width // 8`` bytes (1, 2, 4, or 8) in the
    requested byte order.  Pair with :func:`crcglot.detect.detect` for
    round-trip identification.

    Args:
        data: Payload to checksum.  ``bytes`` and ``bytearray`` are both
            accepted; the result is always ``bytes``.
        algorithm: Catalogue name (e.g. ``"crc32"``); see ``crcglot list``.
        endianness: Byte order of the trailing CRC bytes.  Default ``"big"``.

    Returns:
        ``data + crc_bytes`` -- the original payload followed by the
        CRC encoded per ``endianness``.

    Raises:
        ValueError: ``algorithm`` is not in the catalogue.

    Examples:
        >>> encode(b"123456789", "crc32").hex()
        '313233343536373839cbf43926'
    """
    algo = _lookup(algorithm)
    data_bytes = bytes(data)
    crc = generic_crc(
        data_bytes, algo.width, algo.poly, algo.init,
        algo.refin, algo.refout, algo.xorout,
    )
    w = algo.width // 8
    return data_bytes + crc.to_bytes(w, endianness)


def encode_text(
    data: str,
    algorithm: str,
    *,
    sep: str = " ",
    leader: str = "",
    uppercase: bool = False,
    endianness: Endianness = "big",
    encoding: str = "utf-8",
    fmt: str = "{data}{sep}{leader}{crc}",
) -> str:
    """Build a text packet by appending the CRC as hex digits.

    The default format is ``"<data> <hexcrc>"``; ``fmt`` lets callers
    reorder or wrap the tokens for atypical layouts.

    Args:
        data: The text payload (encoded to bytes via ``encoding`` for
            CRC computation).
        algorithm: Catalogue name (e.g. ``"crc32"``).
        sep: Separator between data and hex; defaults to a single space.
        leader: Hex prefix; typically ``""``, ``"0x"``, or ``"0X"``.
        uppercase: Emit hex digits in upper-case A-F.
        endianness: ``"big"`` gives natural integer reading;
            ``"little"`` dumps the CRC bytes in storage order before
            hex-encoding.  Default ``"big"``.
        encoding: Encoding used to turn ``data`` into bytes for the CRC.
            Default ``"utf-8"``.
        fmt: ``str.format``-style template; the four tokens
            ``{data}``, ``{sep}``, ``{leader}``, ``{crc}`` may be
            reordered or partly omitted.

    Returns:
        The formatted text packet.

    Raises:
        ValueError: ``algorithm`` is not in the catalogue.

    Examples:
        >>> encode_text("123456789", "crc32")
        '123456789 cbf43926'
        >>> encode_text("123456789", "crc32", leader="0X", uppercase=True)
        '123456789 0XCBF43926'
    """
    algo = _lookup(algorithm)
    data_bytes = data.encode(encoding)
    crc = generic_crc(
        data_bytes, algo.width, algo.poly, algo.init,
        algo.refin, algo.refout, algo.xorout,
    )
    hex_chars = algo.width // 4
    if endianness == "big":
        crc_hex = f"{crc:0{hex_chars}x}"
    else:
        w = algo.width // 8
        crc_hex = crc.to_bytes(w, "little").hex()
    if uppercase:
        crc_hex = crc_hex.upper()
    return fmt.format(data=data, sep=sep, leader=leader, crc=crc_hex)


def encode_match(
    data: bytes | bytearray | str,
    match: DetectMatch,
) -> bytes | str:
    """Round-trip pair to :func:`crcglot.detect.detect`: rebuild a
    packet using the shape it identified.

    The ``match`` distinguishes binary from text by its ``padding`` field
    (``None`` -> binary; a :class:`TextFormat` -> text).  The data type
    must match; mismatches are a clear ``TypeError`` rather than a silent
    misinterpretation.

    Args:
        data: Payload (bytes-like for a binary match, ``str`` for text).
        match: A ``DetectMatch`` from ``detect()``.

    Returns:
        A bytes-like or ``str`` packet, matching the original format.

    Raises:
        TypeError: ``data`` does not fit the match's mode.

    Examples:
        >>> from crcglot import detect
        >>> original = "123456789 cbf43926"
        >>> m = detect(original).candidates[0]
        >>> encode_match("123456789", m)
        '123456789 cbf43926'
    """
    if match.padding is None:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError(
                "binary match (padding=None) requires bytes/bytearray data"
            )
        return encode(data, match.algorithm, endianness=match.endianness)
    if not isinstance(data, str):
        raise TypeError(
            "text match (padding=TextFormat) requires str data"
        )
    tf: TextFormat = match.padding
    return encode_text(
        data,
        match.algorithm,
        sep=tf.separator,
        leader=tf.hex_prefix,
        uppercase=tf.uppercase,
        endianness=match.endianness,
    )


def encode_int(
    data: bytes | bytearray | str,
    algorithm: str,
    *,
    encoding: str = "utf-8",
) -> int:
    """Compute the CRC of ``data`` as an integer, without packaging it.

    Convenience for callers who need the raw numeric value -- e.g. to
    write it into a struct field, or compare against a captured CRC.

    Args:
        data: Payload; ``str`` is encoded via ``encoding`` first.
        algorithm: Catalogue name.
        encoding: Used only when ``data`` is ``str``.  Default ``"utf-8"``.

    Returns:
        The CRC value as a non-negative ``int``.

    Raises:
        ValueError: ``algorithm`` is not in the catalogue.

    Examples:
        >>> hex(encode_int(b"123456789", "crc32"))
        '0xcbf43926'
    """
    algo = _lookup(algorithm)
    if isinstance(data, str):
        data = data.encode(encoding)
    return generic_crc(
        bytes(data), algo.width, algo.poly, algo.init,
        algo.refin, algo.refout, algo.xorout,
    )
