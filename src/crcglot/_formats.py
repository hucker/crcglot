"""Named payload "forms": CRC-bearing text wrappers that ``detect`` recognises.

A payload form is a text wrapper around a message and a CRC, described by a
regex that pulls the two apart.  Once a form has stripped a frame down to
``(message, crc)``, the rest is crcglot's ordinary CRC identification -- a form
adds recognition of the *wrapper*, not new CRC logic.

The first form is **crclink** (PyPI), which frames a JSON object with a trailing
CRC-16/XMODEM field::

    {"t":1234,"v":42,"crc":"1352"}

The ``"crc"`` value is the CRC-16/XMODEM of the frame text up to the opening
quote of that trailing ``"crc"`` key (here, of ``{"t":1234,"v":42,``).
``detect`` reports such a frame as ``crc16-xmodem`` with ``form=crclink``.

This mirrors the trailer registry (:mod:`crcglot._trailers`): a frozen
:class:`FormatInfo` record, a :data:`FORMATS` registry, and a
:func:`format_info` lookup.  ``detect`` imports the engine pre-pass
(:func:`_detect_formats`) lazily to avoid an import cycle.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Literal

from crcglot._detect import (
    DetectResult,
    Endianness,
    Packet,
    _attach_padding,
    _detect_with_target_crc,
)


@dataclass(frozen=True)
class FormatInfo:
    """Metadata for one named payload form (a CRC-bearing text wrapper).

    Identification-only: the form describes how to pull a ``(message, crc)``
    pair out of a wrapped frame so the ordinary CRC matcher can name the
    algorithm.  ``pattern`` must expose two named groups -- ``message`` (the
    exact text the CRC covers) and ``crc`` (the raw CRC field) -- and should be
    end-anchored so a ``crc`` token inside the payload is never mistaken for the
    trailing field.

    Attributes:
        name: Machine identifier (e.g. ``"crclink"``).
        label: Human-readable label.
        description: One-line description (the wrapper + a typical user).
        category: The payload category -- ``"json"`` (the CRC lives inside a
            JSON object) or ``"text"`` (a plain text tail).  Lets a UI group
            forms; the algorithm is reported separately.
        pattern: Compiled regex with named groups ``message`` and ``crc``.
        crc_encoding: How the ``crc`` group decodes to an integer -- ``"hex"``
            (bare hex), ``"0xhex"`` (``0x``-prefixed hex), or ``"int"`` (decimal).
        crc_endian: Byte order of the embedded CRC reading; a hex string is read
            big-endian.
        message_encoding: Text encoding used to turn the ``message`` group into
            the bytes the CRC is computed over.
        crc_nibbles: Hex-digit count of the CRC field (e.g. ``4`` for a 16-bit
            CRC); carried for a future round-trip, unused for identification.
        crc_uppercase: Whether the CRC field uses upper-case hex; carried for a
            future round-trip.
    """

    name: str
    label: str
    description: str
    category: Literal["json", "text"]
    pattern: re.Pattern[str]
    crc_encoding: Literal["hex", "0xhex", "int"]
    crc_endian: Endianness
    message_encoding: str = "utf-8"
    crc_nibbles: int | None = None
    crc_uppercase: bool = False


# Registry.  One entry today -- crclink's JSON frame.  ``message`` captures the
# covered prefix (it ends at the opening quote of the trailing ``"crc"`` key,
# including the comma before it); ``crc`` captures the 4-hex CRC.  ``re.DOTALL``
# lets ``message`` span a frame that (unusually) contains newlines; the end
# anchor keeps an inner ``"crc"`` substring from matching as the trailing field.
FORMATS: dict[str, FormatInfo] = {
    "crclink": FormatInfo(
        name="crclink",
        label="crclink JSON frame",
        description=(
            'JSON object with a trailing CRC-16/XMODEM "crc" field '
            "(crclink on PyPI); MCP-friendly message integrity."
        ),
        category="json",
        pattern=re.compile(
            r'^(?P<message>.*?)"crc":"(?P<crc>[0-9a-fA-F]{4})"\}\s*$', re.DOTALL
        ),
        crc_encoding="hex",
        crc_endian="big",
        crc_nibbles=4,
    ),
}


def format_info(name: str) -> FormatInfo:
    """Look up a payload form's metadata by name.

    Args:
        name: A key of :data:`FORMATS` (e.g. ``"crclink"``).

    Returns:
        The :class:`FormatInfo` record.

    Raises:
        KeyError: ``name`` is not a known form.

    Examples:
        >>> from crcglot import format_info
        >>> format_info("crclink").category
        'json'
    """
    return FORMATS[name]


@dataclass(frozen=True)
class FormatMatch:
    """A payload form recognised in a packet -- the ``padding`` of a form match.

    Sits on :attr:`crcglot.DetectMatch.padding` alongside ``TextFormat`` /
    ``HexFormat``, so a caller sees both *which algorithm* and *which wrapper*
    were identified.

    Attributes:
        info: The matching :class:`FormatInfo`.
        crc_text: The raw CRC field as it appeared (e.g. ``"1352"``).
        message: The exact text the CRC covered (e.g. ``'{"t":1234,"v":42,'``).
    """

    info: FormatInfo
    crc_text: str
    message: str


def _decode_crc(crc_text: str, encoding: Literal["hex", "0xhex", "int"]) -> int:
    """Decode a form's captured CRC field to an integer (per ``crc_encoding``)."""
    if encoding == "int":
        return int(crc_text, 10)
    # "hex" and "0xhex" both arrive without the 0x (the regex group excludes it).
    return int(crc_text, 16)


def _detect_formats(
    packets: list[Packet],
    names: list[str],
    encoding: str,
    match: Literal["first", "all", "set"],
    forms_filter: str | None,
) -> DetectResult | None:
    """Try each payload form against a single text packet (the ``detect`` pre-pass).

    For each form whose name matches ``forms_filter`` (an ``fnmatch`` glob, or
    every form when ``None``), apply its regex; on a hit, decode the CRC and
    hand ``(message_bytes, crc)`` to the existing target-CRC matcher
    (:func:`crcglot._detect._detect_with_target_crc`), then stamp a
    :class:`FormatMatch` onto each surviving candidate.

    v1 is single-packet: a form's CRC covers one frame, and frames carry
    different CRCs, so a multi-packet intersection would need per-packet target
    values (future work).

    Args:
        packets: The normalized packets; only a single ``str`` packet engages a
            form.
        names: The (already filtered) algorithm scan order.
        encoding: Forwarded to the matcher (unused once the message is bytes).
        match: ``"first"`` / ``"all"`` / ``"set"`` selection mode.
        forms_filter: ``fnmatch`` glob over form names, or ``None`` for all.

    Returns:
        A matched :class:`DetectResult` with ``FormatMatch`` padding, or ``None``
        when no form's regex fired (so ``detect`` falls through to its normal
        pipeline) -- including when more than one packet was supplied.
    """
    if len(packets) != 1:
        return None
    packet = packets[0]
    if not isinstance(packet, str):
        return None
    for fmt in FORMATS.values():
        if forms_filter is not None and not fnmatch.fnmatch(fmt.name, forms_filter):
            continue
        m = fmt.pattern.match(packet)
        if m is None:
            continue
        crc_text = m.group("crc")
        message = m.group("message")
        crc_value = _decode_crc(crc_text, fmt.crc_encoding)
        message_bytes = message.encode(fmt.message_encoding)
        result = _detect_with_target_crc(
            [message_bytes],
            "binary",
            encoding,
            names,
            match,
            crc_value,
            fmt.crc_endian,
        )
        if not result.matched:
            continue
        return _attach_padding(
            result, FormatMatch(info=fmt, crc_text=crc_text, message=message)
        )
    return None
