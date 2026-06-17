"""Recognise and verify crclink JSON frames without depending on crclink.

crclink (PyPI) frames a JSON object with a trailing CRC-16/XMODEM integrity
field::

    {"t":1234,"v":42,"crc":"1352"}

The ``"crc"`` value is the 4-digit-hex CRC-16/XMODEM of the frame text up to
(and excluding) the opening quote of that trailing ``"crc"`` key -- here, of
``{"t":1234,"v":42,``.  Both endpoints compute the CRC over that same coverage.

This module lets crcglot validate such a frame with its own CRC engine, so a
consumer can check crclink traffic without taking on crclink as a dependency.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from crcglot.catalogue import ALGORITHMS, generic_crc

# The trailing integrity field ``"crc":"<4 hex>"}`` at the very end of the
# frame (optional trailing whitespace).  Anchored to the end so a ``"crc"``
# string *inside* the payload can never be mistaken for the integrity field.
_CRC_TAIL = re.compile(r'"crc":"([0-9a-fA-F]{4})"\}\s*$')

#: crclink fixes the algorithm at CRC-16/XMODEM; resolve it once.
_CRC16_XMODEM = ALGORITHMS["crc16-xmodem"]


@dataclass(frozen=True)
class CrclinkResult:
    """Outcome of :func:`verify_crclink`.

    ``valid`` is the bottom line: the frame is well-formed JSON *and* its
    trailing CRC-16/XMODEM matches the covered prefix.  ``expected`` is the CRC
    carried in the frame; ``actual`` is the one crcglot computed over
    ``coverage`` (the exact prefix the CRC is taken over).  ``reason`` explains
    a ``False`` result; it is empty on success.
    """

    valid: bool
    expected: int | None = None
    actual: int | None = None
    coverage: str | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        """A result is truthy iff the frame verified."""
        return self.valid


def verify_crclink(frame: str | bytes, *, encoding: str = "utf-8") -> CrclinkResult:
    """Verify a crclink JSON frame against its trailing CRC-16/XMODEM.

    A crclink frame is a JSON object whose final key is ``"crc"``, a 4-digit
    hex CRC-16/XMODEM taken over the frame text up to the opening quote of that
    key (see the module docstring).  This checks all three conditions in turn:
    the frame parses as JSON, it carries the trailing ``"crc"`` field, and the
    embedded CRC matches the one computed over the covered prefix.

    Args:
        frame: The frame, as ``str`` or already-encoded ``bytes``.
        encoding: Text encoding used to take the CRC over the prefix (the CRC
            is defined over bytes).  crclink frames are ASCII/UTF-8.

    Returns:
        A :class:`CrclinkResult`.  ``valid`` is ``True`` only when the frame is
        valid JSON and the embedded CRC matches; the result is also truthy in
        that case, so ``if verify_crclink(frame): ...`` works.

    Examples:
        >>> verify_crclink('{"t":1234,"v":42,"crc":"1352"}').valid
        True
        >>> verify_crclink('{"t":1234,"v":43,"crc":"1352"}').valid
        False
        >>> verify_crclink('{"t":1234,"v":42,"crc":"1352"}').coverage
        '{"t":1234,"v":42,'
    """
    text = frame.decode(encoding) if isinstance(frame, (bytes, bytearray)) else frame

    # 1) The whole frame -- crc field included -- must be valid JSON.
    try:
        json.loads(text)
    except (ValueError, TypeError) as exc:
        return CrclinkResult(valid=False, reason=f"not valid JSON: {exc}")

    # 2) It must end with the trailing ``"crc":"<hex>"}`` integrity field.
    match = _CRC_TAIL.search(text)
    if match is None:
        return CrclinkResult(valid=False, reason='no trailing "crc" field')

    # 3) The embedded CRC must match the one over the covered prefix (the frame
    #    text up to the opening quote of the trailing "crc" key).
    coverage = text[: match.start()]
    expected = int(match.group(1), 16)
    actual = generic_crc(coverage.encode(encoding), _CRC16_XMODEM)
    if actual != expected:
        return CrclinkResult(
            valid=False, expected=expected, actual=actual, coverage=coverage,
            reason=(
                f"CRC mismatch: frame says 0x{expected:04X}, "
                f"computed 0x{actual:04X}"
            ),
        )
    return CrclinkResult(
        valid=True, expected=expected, actual=actual, coverage=coverage,
    )
