"""CRC catalogue + generic compute engine.

100+ named algorithms from the reveng catalogue (Greg Cook,
https://reveng.sourceforge.io/crc-catalogue/all.htm) plus
generic Rocksoft/Williams CRC computation for any custom
``(width, poly, init, refin, refout, xorout)`` tuple.

The public face is :data:`ALGORITHMS` -- a ``dict[str,
AlgorithmInfo]`` keyed by algorithm name.  Each entry is a frozen
dataclass with the Rocksoft/Williams parameters plus the canonical
reveng ``check`` value (CRC of ``b"123456789"``) and a human-readable
``desc``.  Catalogue entries declare their typed dataclass directly
-- one source of truth, no parallel raw-dict + builder indirection.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence
from dataclasses import dataclass


# Optional C accelerator.  Installed by the wheel; absent when crcglot
# is built from sdist on a platform without a C compiler.  ``generic_crc``
# transparently dispatches to it when present -- same result, slice-by-8
# / table-driven speed (~1-2 GB/s).  When absent, the pure-Python loop
# below runs unchanged.
try:
    from crcglot._c import c_crc_many as _c_crc_many
    from crcglot._c import c_generic_crc as _c_generic_crc
except ImportError:
    _c_generic_crc = None  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
    _c_crc_many = None  # type: ignore[assignment]  # ty: ignore[invalid-assignment]


# zlib hardware fast-paths.  CPython's ``zlib.crc32`` is hardware-
# accelerated on modern CPUs (PCLMULQDQ / VPCLMULQDQ on x86, PMULL /
# crc32 instructions on ARM), reaching tens of GB/s -- ~30x faster
# than our portable software slice-by-8, and on every platform zlib
# supports.  No software CRC engine should try to out-run silicon, so
# ``generic_crc`` delegates to zlib for the algorithms it can compute,
# regardless of whether our own C extension is built.
#
# ``zlib.crc32`` computes IEEE CRC-32 exactly: reflected, poly
# 0x04C11DB7, init 0xFFFFFFFF, with a final XOR of 0xFFFFFFFF baked in.
# That covers two catalogue algorithms cheaply:
#
#   - ``crc32`` (IEEE): zlib.crc32(data) verbatim.
#   - ``crc32-jamcrc``: identical to IEEE-32 but xorout=0.  Since zlib
#     applies xorout=0xFFFFFFFF internally, XOR it back out -- one
#     extra op, still the full hardware path.
#
# The other 0x04C11DB7 variants (bzip2, mpeg-2, cksum) are
# *non-reflected*; mapping them to zlib's reflected core needs a
# per-byte bit-reversal pass that costs as much as just computing the
# CRC, so they go through the C engine instead.  Keyed by the full
# (width, poly, init, refin, refout, xorout) tuple -> transform fn.
_IEEE_CRC32_PARAMS = (32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)
_JAMCRC_PARAMS = (32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0x00000000)

_ZLIB_FAST_PATHS = {
    _IEEE_CRC32_PARAMS: zlib.crc32,
    _JAMCRC_PARAMS: lambda data: zlib.crc32(data) ^ 0xFFFFFFFF,
}


# ---------------------------------------------------------------------------
# Generic CRC engine - Rocksoft/Williams parameterization
# ---------------------------------------------------------------------------


def _reflect(value: int, width: int) -> int:
    """Bit-reverse a value within the given bit width.

    Args:
        value: Integer to reflect.
        width: Number of bits to reverse.

    Returns:
        Bit-reversed value.
    """
    result = 0
    for _ in range(width):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def generic_crc(
    data: bytes | bytearray | memoryview,
    width: int,
    poly: int,
    init: int,
    refin: bool,
    refout: bool,
    xorout: int,
) -> int:
    """Compute CRC using Rocksoft/Williams parameterization.

    Public helper for callers who need a check value for a custom
    polynomial -- e.g. to feed into :class:`AlgorithmInfo` before
    handing it to a ``generator_from_entry`` callable, or to verify a
    one-off CRC in the field without going through the catalogue.

    Dispatch order (fastest applicable path wins):

    1. A zlib hardware fast-path (IEEE crc32, crc32-jamcrc) ->
       :func:`zlib.crc32` (stdlib, hardware-accelerated, tens of GB/s).
       Applies even when our C extension is built -- silicon CRC
       folding beats portable software slice-by-8 ~30x.
    2. Any other algorithm, C extension built -> ``c_generic_crc``
       (slice-by-8 / table-driven, ~1-2 GB/s).
    3. Otherwise -> :func:`_generic_crc_python` (the reference loop).

    All paths produce identical output.  The pure-Python and C engines
    are kept as separately-callable functions so the speedup is
    measurable without uninstalling the extension and so the test suite
    can assert parity directly.

    Performance -- THIS IS A ONE-SHOT.  For a table/slice-by-8 algorithm
    (every byte-aligned width except IEEE crc32 / jamcrc, which ride
    zlib), each call **rebuilds the lookup table from scratch**; there is
    no cache.  Calling this in a loop over many messages of the same
    algorithm therefore rebuilds the table every iteration -- on small
    buffers that is **4-11x slower** than it needs to be (the build, not
    the CRC, dominates), and the cost only grows the more you loop.

    For many CRCs of the same algorithm, build the table **once** and
    reuse it: use :func:`crcglot.crc_stream` / :class:`crcglot.CrcStream`
    (build once, ``update`` per message) or, for a fixed list of buffers,
    ``crcglot._c.c_crc_many``.  Independent streams also run fully in
    parallel across threads.  ``generic_crc`` is the right tool for a
    *single* CRC (or a check value); a hot loop of it is the one
    performance mistake to avoid.

    Args:
        data: Payload bytes.
        width: CRC bit width (8, 16, 32, etc.).
        poly: Generator polynomial in normal (MSB-first) form.
        init: Initial register value.
        refin: True to reflect each input byte.
        refout: True to reflect the final CRC value.
        xorout: XOR applied to the final CRC value.

    Returns:
        Computed CRC value.
    """
    fast_path = _ZLIB_FAST_PATHS.get((width, poly, init, refin, refout, xorout))
    if fast_path is not None:
        return fast_path(data)
    # The C extension's domain is width in [8, 64]; sub-byte CRCs (e.g.
    # crc5-usb, crc7-rohc) fall back to the reference loop, which handles
    # any width.  Bit-identical either way.
    if _c_generic_crc is not None and 8 <= width <= 64:
        return _c_generic_crc(data, width, poly, init, refin, refout, xorout)
    return _generic_crc_python(data, width, poly, init, refin, refout, xorout)


def generic_crc_many(
    buffers: Sequence[bytes | bytearray | memoryview],
    width: int,
    poly: int,
    init: int,
    refin: bool,
    refout: bool,
    xorout: int,
) -> list[int]:
    """CRC of each buffer in ``buffers`` (one algorithm), in order.

    The batch form of :func:`generic_crc`: for a table/slice-by-8 algorithm
    the C extension builds the lookup table **once** for the whole batch
    and pays the Python->C transition once, so this is the right tool for
    "the CRC of many messages with the same algorithm" -- far faster than a
    Python loop of :func:`generic_crc`, which rebuilds the table per call.
    Same dispatch as :func:`generic_crc` (zlib fast-path for crc32 / jamcrc,
    the C extension when built, else the pure-Python loop) and bit-identical
    results.

    Args:
        buffers: The payloads; each is CRC'd independently (not concatenated).
        width: CRC bit width.
        poly: Generator polynomial in normal (MSB-first) form.
        init: Initial register value.
        refin: True to reflect each input byte.
        refout: True to reflect the final CRC value.
        xorout: XOR applied to the final CRC value.

    Returns:
        One CRC value per buffer, in the same order.

    Examples:
        >>> generic_crc_many([b"123456789", b""], 32, 0x04C11DB7,
        ...                   0xFFFFFFFF, True, True, 0xFFFFFFFF)
        [3421780262, 0]
    """
    fast_path = _ZLIB_FAST_PATHS.get((width, poly, init, refin, refout, xorout))
    if fast_path is not None:
        return [fast_path(b) for b in buffers]
    # C extension domain is width in [8, 64]; sub-byte CRCs fall back to the
    # reference loop (bit-identical).  See generic_crc.
    if _c_crc_many is not None and 8 <= width <= 64:
        return _c_crc_many(
            list(buffers), width, poly, init, refin, refout, xorout
        )
    return [
        _generic_crc_python(b, width, poly, init, refin, refout, xorout)
        for b in buffers
    ]


def _generic_crc_python(
    data: bytes | bytearray | memoryview,
    width: int,
    poly: int,
    init: int,
    refin: bool,
    refout: bool,
    xorout: int,
) -> int:
    """Pure-Python Rocksoft/Williams CRC engine.

    The reference implementation -- always callable regardless of
    whether the C extension is installed.  :func:`generic_crc`
    dispatches to the C version when available; this is what it falls
    back to, and what the parity tests / benchmarks compare against
    directly.  Bit-identical to ``crcglot._c.c_generic_crc``.
    """
    crc = init
    if refin:
        # Reflected algorithm: process LSB-first with reflected polynomial.
        # Init must also be reflected to match the reversed register layout.
        ref_poly = _reflect(poly, width)
        crc = _reflect(init, width)
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ ref_poly
                else:
                    crc >>= 1
    elif width >= 8:
        # Normal algorithm: process MSB-first, byte aligned to the register
        # top.  Valid only for width >= 8 -- the shift below underflows
        # otherwise.
        msb_mask = 1 << (width - 1)
        for byte in data:
            crc ^= byte << (width - 8)
            for _ in range(8):
                if crc & msb_mask:
                    crc = (crc << 1) ^ poly
                else:
                    crc <<= 1
            crc &= (1 << width) - 1
    else:
        # Sub-byte non-reflected widths: feed each byte bit-by-bit, MSB
        # first.  The byte-aligned ``byte << (width - 8)`` form above would
        # shift by a negative amount when width < 8.
        mask = (1 << width) - 1
        msb_mask = 1 << (width - 1)
        for byte in data:
            for i in range(7, -1, -1):
                bit = (byte >> i) & 1
                if ((crc & msb_mask) != 0) ^ (bit != 0):
                    crc = ((crc << 1) ^ poly) & mask
                else:
                    crc = (crc << 1) & mask
    if refout != refin:
        crc = _reflect(crc, width)
    # Mask to ``width`` bits so the result stays a CRC value even if a
    # caller passes an ``xorout`` with bits above the width -- matches the
    # C engine's finalize and keeps the two bit-identical for all inputs.
    return (crc ^ xorout) & ((1 << width) - 1)


@dataclass(frozen=True)
class AlgorithmInfo:
    """Typed metadata for one CRC algorithm.

    The algorithm name lives on the :data:`ALGORITHMS` dict key, not on
    the dataclass itself -- one source of truth, no risk of key vs.
    ``info.name`` drift.

    Attributes:
        width: CRC bit width: 8, 16, 32, or 64.
        poly: Generator polynomial in normal (MSB-first) form.
        init: Initial register value.
        refin: True to reflect each input byte.
        refout: True to reflect the final CRC value.
        xorout: XOR applied to the final CRC value.
        check: Canonical reveng check value -- CRC of ``b"123456789"``.
        desc: Human-readable description (may be ``""``).
        source: Provenance of the Rocksoft/Williams parameters --
            ``"reveng"`` for entries derived from the reveng catalogue
            (the majority), a short citation string for others (e.g.
            ``"ietf:draft-lynn-6lo-rfc8163-bis-01"``).  Surfaced in
            ``crcglot info`` / ``crcglot list --json`` / the MCP
            ``crc_info`` tool so consumers can trace any entry back to
            its primary documentation.
    """

    width: int
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int
    check: int
    desc: str
    source: str


# ---------------------------------------------------------------------------
# CRC catalogue - named algorithms from the reveng CRC catalogue
# ---------------------------------------------------------------------------
# Maintained by Greg Cook since 1999.
# Source: https://reveng.sourceforge.io/crc-catalogue/all.htm
# See help/acknowledgments.md (or /credits in-app) for full attribution.
#
# Each entry: width, poly (normal form), init, refin, refout, xorout, check.
# check = CRC of b"123456789" - used as test vectors.
#
ALGORITHMS: dict[str, AlgorithmInfo] = {
    # ---- CRC-3 (2 algorithms) ----
    "crc3-gsm": AlgorithmInfo(
        width=3,
        poly=0x3,
        init=0x0,
        refin=False,
        refout=False,
        xorout=0x7,
        check=0x4,
        desc="GSM cellular (3GPP TS 45.003)",
        source="reveng",
    ),
    "crc3-rohc": AlgorithmInfo(
        width=3,
        poly=0x3,
        init=0x7,
        refin=True,
        refout=True,
        xorout=0x0,
        check=0x6,
        desc="RObust Header Compression (RFC 3095)",
        source="reveng",
    ),
    # ---- CRC-4 (2 algorithms) ----
    "crc4-g-704": AlgorithmInfo(
        width=4,
        poly=0x3,
        init=0x0,
        refin=True,
        refout=True,
        xorout=0x0,
        check=0x7,
        desc="ITU-T G.704 E1/T1 framing",
        source="reveng",
    ),
    "crc4-interlaken": AlgorithmInfo(
        width=4,
        poly=0x3,
        init=0xF,
        refin=False,
        refout=False,
        xorout=0xF,
        check=0xB,
        desc="Interlaken chip-to-chip protocol",
        source="reveng",
    ),
    # ---- CRC-5 (3 algorithms) ----
    "crc5-epc-c1g2": AlgorithmInfo(
        width=5,
        poly=0x09,
        init=0x09,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0x00,
        desc="EPC Class-1 Gen-2 UHF RFID",
        source="reveng",
    ),
    "crc5-g-704": AlgorithmInfo(
        width=5,
        poly=0x15,
        init=0x00,
        refin=True,
        refout=True,
        xorout=0x00,
        check=0x07,
        desc="ITU-T G.704 framing (5-bit)",
        source="reveng",
    ),
    "crc5-usb": AlgorithmInfo(
        width=5,
        poly=0x05,
        init=0x1F,
        refin=True,
        refout=True,
        xorout=0x1F,
        check=0x19,
        desc="USB token/SOF packets",
        source="reveng",
    ),
    # ---- CRC-6 (5 algorithms) ----
    "crc6-cdma2000-a": AlgorithmInfo(
        width=6,
        poly=0x27,
        init=0x3F,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0x0D,
        desc="CDMA2000 control channel A",
        source="reveng",
    ),
    "crc6-cdma2000-b": AlgorithmInfo(
        width=6,
        poly=0x07,
        init=0x3F,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0x3B,
        desc="CDMA2000 control channel B",
        source="reveng",
    ),
    "crc6-darc": AlgorithmInfo(
        width=6,
        poly=0x19,
        init=0x00,
        refin=True,
        refout=True,
        xorout=0x00,
        check=0x26,
        desc="DARC (Data Radio Channel, 6-bit)",
        source="reveng",
    ),
    "crc6-g-704": AlgorithmInfo(
        width=6,
        poly=0x03,
        init=0x00,
        refin=True,
        refout=True,
        xorout=0x00,
        check=0x06,
        desc="ITU-T G.704 framing (6-bit)",
        source="reveng",
    ),
    "crc6-gsm": AlgorithmInfo(
        width=6,
        poly=0x2F,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0x3F,
        check=0x13,
        desc="GSM cellular (6-bit)",
        source="reveng",
    ),
    # ---- CRC-7 (3 algorithms) ----
    "crc7-mmc": AlgorithmInfo(
        width=7,
        poly=0x09,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0x75,
        desc="MMC/SD card commands",
        source="reveng",
    ),
    "crc7-rohc": AlgorithmInfo(
        width=7,
        poly=0x4F,
        init=0x7F,
        refin=True,
        refout=True,
        xorout=0x00,
        check=0x53,
        desc="RObust Header Compression (RFC 3095)",
        source="reveng",
    ),
    "crc7-umts": AlgorithmInfo(
        width=7,
        poly=0x45,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0x61,
        desc="UMTS/WCDMA 3G (7-bit)",
        source="reveng",
    ),
    # ---- CRC-8 (21 algorithms) ----
    "crc8": AlgorithmInfo(
        width=8,
        poly=0x07,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0xF4,
        desc="ITU-T I.432.1 (ATM HEC), ISDN",
        source="reveng",
    ),
    "crc8-autosar": AlgorithmInfo(
        width=8,
        poly=0x2F,
        init=0xFF,
        refin=False,
        refout=False,
        xorout=0xFF,
        check=0xDF,
        desc="AUTOSAR automotive E2E profiles",
        source="reveng",
    ),
    "crc8-bacnet": AlgorithmInfo(
        width=8,
        poly=0x03,
        init=0xFF,
        refin=True,
        refout=True,
        xorout=0xFF,
        check=0x89,
        desc="BACnet MS/TP frame header (X^8 + X + 1; ANSI/ASHRAE 135 Annex G.1)",
        source="ietf:draft-lynn-6lo-rfc8163-bis-01",
    ),
    "crc8-bluetooth": AlgorithmInfo(
        width=8,
        poly=0xA7,
        init=0x00,
        refin=True,
        refout=True,
        xorout=0x00,
        check=0x26,
        desc="Bluetooth HEC (header error check)",
        source="reveng",
    ),
    "crc8-cdma2000": AlgorithmInfo(
        width=8,
        poly=0x9B,
        init=0xFF,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0xDA,
        desc="CDMA2000 mobile telephony",
        source="reveng",
    ),
    "crc8-darc": AlgorithmInfo(
        width=8,
        poly=0x39,
        init=0x00,
        refin=True,
        refout=True,
        xorout=0x00,
        check=0x15,
        desc="DARC (Data Radio Channel)",
        source="reveng",
    ),
    "crc8-dvb-s2": AlgorithmInfo(
        width=8,
        poly=0xD5,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0xBC,
        desc="DVB-S2 satellite TV baseband frames",
        source="reveng",
    ),
    "crc8-gsm-a": AlgorithmInfo(
        width=8,
        poly=0x1D,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0x37,
        desc="GSM/3GPP control channel (type A)",
        source="reveng",
    ),
    "crc8-gsm-b": AlgorithmInfo(
        width=8,
        poly=0x49,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0xFF,
        check=0x94,
        desc="GSM/3GPP control channel (type B)",
        source="reveng",
    ),
    "crc8-hitag": AlgorithmInfo(
        width=8,
        poly=0x1D,
        init=0xFF,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0xB4,
        desc="Philips HITAG RFID transponders",
        source="reveng",
    ),
    "crc8-i-432-1": AlgorithmInfo(
        width=8,
        poly=0x07,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0x55,
        check=0xA1,
        desc="ITU-T I.432.1 ATM HEC (alt init)",
        source="reveng",
    ),
    "crc8-i-code": AlgorithmInfo(
        width=8,
        poly=0x1D,
        init=0xFD,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0x7E,
        desc="Philips ICODE RFID SLI systems",
        source="reveng",
    ),
    "crc8-lte": AlgorithmInfo(
        width=8,
        poly=0x9B,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0xEA,
        desc="3GPP LTE (Long Term Evolution)",
        source="reveng",
    ),
    "crc8-maxim": AlgorithmInfo(
        width=8,
        poly=0x31,
        init=0x00,
        refin=True,
        refout=True,
        xorout=0x00,
        check=0xA1,
        desc="Dallas/Maxim 1-Wire bus (DOW CRC)",
        source="reveng",
    ),
    "crc8-mifare-mad": AlgorithmInfo(
        width=8,
        poly=0x1D,
        init=0xC7,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0x99,
        desc="NXP MIFARE Application Directory",
        source="reveng",
    ),
    "crc8-nrsc-5": AlgorithmInfo(
        width=8,
        poly=0x31,
        init=0xFF,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0xF7,
        desc="NRSC-5 HD Radio digital broadcast",
        source="reveng",
    ),
    "crc8-opensafety": AlgorithmInfo(
        width=8,
        poly=0x2F,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0x3E,
        desc="OpenSAFETY industrial safety protocol",
        source="reveng",
    ),
    "crc8-rohc": AlgorithmInfo(
        width=8,
        poly=0x07,
        init=0xFF,
        refin=True,
        refout=True,
        xorout=0x00,
        check=0xD0,
        desc="ROHC (Robust Header Compression)",
        source="reveng",
    ),
    "crc8-sae-j1850": AlgorithmInfo(
        width=8,
        poly=0x1D,
        init=0xFF,
        refin=False,
        refout=False,
        xorout=0xFF,
        check=0x4B,
        desc="SAE J1850 automotive OBD-II bus",
        source="reveng",
    ),
    "crc8-tech-3250": AlgorithmInfo(
        width=8,
        poly=0x1D,
        init=0xFF,
        refin=True,
        refout=True,
        xorout=0x00,
        check=0x97,
        desc="EBU Tech 3250 (AES3 audio)",
        source="reveng",
    ),
    "crc8-wcdma": AlgorithmInfo(
        width=8,
        poly=0x9B,
        init=0x00,
        refin=True,
        refout=True,
        xorout=0x00,
        check=0x25,
        desc="WCDMA/UMTS 3G mobile embedded",
        source="reveng",
    ),
    # ---- CRC-10 (3 algorithms) ----
    "crc10-atm": AlgorithmInfo(
        width=10,
        poly=0x233,
        init=0x000,
        refin=False,
        refout=False,
        xorout=0x000,
        check=0x199,
        desc="ATM AAL3/4, ITU-T I.610",
        source="reveng",
    ),
    "crc10-cdma2000": AlgorithmInfo(
        width=10,
        poly=0x3D9,
        init=0x3FF,
        refin=False,
        refout=False,
        xorout=0x000,
        check=0x233,
        desc="CDMA2000 forward link",
        source="reveng",
    ),
    "crc10-gsm": AlgorithmInfo(
        width=10,
        poly=0x175,
        init=0x000,
        refin=False,
        refout=False,
        xorout=0x3FF,
        check=0x12A,
        desc="GSM cellular (10-bit)",
        source="reveng",
    ),
    # ---- CRC-11 (2 algorithms) ----
    "crc11-flexray": AlgorithmInfo(
        width=11,
        poly=0x385,
        init=0x01A,
        refin=False,
        refout=False,
        xorout=0x000,
        check=0x5A3,
        desc="FlexRay automotive bus",
        source="reveng",
    ),
    "crc11-umts": AlgorithmInfo(
        width=11,
        poly=0x307,
        init=0x000,
        refin=False,
        refout=False,
        xorout=0x000,
        check=0x061,
        desc="UMTS/WCDMA 3G (11-bit)",
        source="reveng",
    ),
    # ---- CRC-12 (4 algorithms) ----
    "crc12-cdma2000": AlgorithmInfo(
        width=12,
        poly=0xF13,
        init=0xFFF,
        refin=False,
        refout=False,
        xorout=0x000,
        check=0xD4D,
        desc="CDMA2000 forward link (12-bit)",
        source="reveng",
    ),
    "crc12-dect": AlgorithmInfo(
        width=12,
        poly=0x80F,
        init=0x000,
        refin=False,
        refout=False,
        xorout=0x000,
        check=0xF5B,
        desc="DECT cordless telephony (X-CRC-12)",
        source="reveng",
    ),
    "crc12-gsm": AlgorithmInfo(
        width=12,
        poly=0xD31,
        init=0x000,
        refin=False,
        refout=False,
        xorout=0xFFF,
        check=0xB34,
        desc="GSM cellular (12-bit)",
        source="reveng",
    ),
    "crc12-umts": AlgorithmInfo(
        width=12,
        poly=0x80F,
        init=0x000,
        refin=False,
        refout=True,
        xorout=0x000,
        check=0xDAF,
        desc="UMTS/3GPP (refout only)",
        source="reveng",
    ),
    # ---- CRC-13 (1 algorithm) ----
    "crc13-bbc": AlgorithmInfo(
        width=13,
        poly=0x1CF5,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x04FA,
        desc="BBC Radio Data System datacast",
        source="reveng",
    ),
    # ---- CRC-14 (2 algorithms) ----
    "crc14-darc": AlgorithmInfo(
        width=14,
        poly=0x0805,
        init=0x0000,
        refin=True,
        refout=True,
        xorout=0x0000,
        check=0x082D,
        desc="DARC (Data Radio Channel, 14-bit)",
        source="reveng",
    ),
    "crc14-gsm": AlgorithmInfo(
        width=14,
        poly=0x202D,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x3FFF,
        check=0x30AE,
        desc="GSM cellular (14-bit)",
        source="reveng",
    ),
    # ---- CRC-15 (2 algorithms) ----
    "crc15-can": AlgorithmInfo(
        width=15,
        poly=0x4599,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x059E,
        desc="CAN bus frame CRC",
        source="reveng",
    ),
    "crc15-mpt1327": AlgorithmInfo(
        width=15,
        poly=0x6815,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0001,
        check=0x2566,
        desc="MPT-1327 trunked radio",
        source="reveng",
    ),
    # ---- CRC-16 (31 algorithms) ----
    "crc16-arc": AlgorithmInfo(
        width=16,
        poly=0x8005,
        init=0x0000,
        refin=True,
        refout=True,
        xorout=0x0000,
        check=0xBB3D,
        desc="ARC archive, LHA (IBM CRC-16)",
        source="reveng",
    ),
    "crc16-cdma2000": AlgorithmInfo(
        width=16,
        poly=0xC867,
        init=0xFFFF,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x4C06,
        desc="CDMA2000 mobile telephony",
        source="reveng",
    ),
    "crc16-cms": AlgorithmInfo(
        width=16,
        poly=0x8005,
        init=0xFFFF,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0xAEE7,
        desc="CMS (RPM package format)",
        source="reveng",
    ),
    "crc16-dds-110": AlgorithmInfo(
        width=16,
        poly=0x8005,
        init=0x800D,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x9ECF,
        desc="ELV DDS-110 weather station",
        source="reveng",
    ),
    "crc16-dect-r": AlgorithmInfo(
        width=16,
        poly=0x0589,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0001,
        check=0x007E,
        desc="DECT cordless telephony (R-CRC)",
        source="reveng",
    ),
    "crc16-dect-x": AlgorithmInfo(
        width=16,
        poly=0x0589,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x007F,
        desc="DECT cordless telephony (X-CRC)",
        source="reveng",
    ),
    "crc16-dnp": AlgorithmInfo(
        width=16,
        poly=0x3D65,
        init=0x0000,
        refin=True,
        refout=True,
        xorout=0xFFFF,
        check=0xEA82,
        desc="DNP3 (Distributed Network Protocol)",
        source="reveng",
    ),
    "crc16-en-13757": AlgorithmInfo(
        width=16,
        poly=0x3D65,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0xFFFF,
        check=0xC2B7,
        desc="EN 13757 wireless M-Bus metering",
        source="reveng",
    ),
    "crc16-genibus": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0xFFFF,
        refin=False,
        refout=False,
        xorout=0xFFFF,
        check=0xD64E,
        desc="GENIBUS (EPC Gen2 RFID)",
        source="reveng",
    ),
    "crc16-gsm": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0xFFFF,
        check=0xCE3C,
        desc="GSM mobile network control channel",
        source="reveng",
    ),
    "crc16-ibm-3740": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0xFFFF,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x29B1,
        desc="IBM 3740 floppy disk, CCITT-FALSE",
        source="reveng",
    ),
    "crc16-ibm-sdlc": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0xFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFF,
        check=0x906E,
        desc="IBM SDLC, ISO HDLC, X.25 FCS",
        source="reveng",
    ),
    "crc16-iso-iec-14443-3-a": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0xC6C6,
        refin=True,
        refout=True,
        xorout=0x0000,
        check=0xBF05,
        desc="ISO 14443-3 Type A NFC/RFID",
        source="reveng",
    ),
    "crc16-kermit": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0x0000,
        refin=True,
        refout=True,
        xorout=0x0000,
        check=0x2189,
        desc="Kermit file transfer protocol",
        source="reveng",
    ),
    "crc16-lj1200": AlgorithmInfo(
        width=16,
        poly=0x6F63,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0xBDF4,
        desc="LJ1200 telemetry",
        source="reveng",
    ),
    "crc16-m17": AlgorithmInfo(
        width=16,
        poly=0x5935,
        init=0xFFFF,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x772B,
        desc="M17 Project digital voice radio",
        source="reveng",
    ),
    "crc16-maxim": AlgorithmInfo(
        width=16,
        poly=0x8005,
        init=0x0000,
        refin=True,
        refout=True,
        xorout=0xFFFF,
        check=0x44C2,
        desc="Maxim/Dallas 1-Wire 16-bit",
        source="reveng",
    ),
    "crc16-mcrf4xx": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0xFFFF,
        refin=True,
        refout=True,
        xorout=0x0000,
        check=0x6F91,
        desc="Microchip MCRF4xx RFID tags",
        source="reveng",
    ),
    "crc16-modbus": AlgorithmInfo(
        width=16,
        poly=0x8005,
        init=0xFFFF,
        refin=True,
        refout=True,
        xorout=0x0000,
        check=0x4B37,
        desc="Modbus RTU serial protocol",
        source="reveng",
    ),
    "crc16-nrsc-5": AlgorithmInfo(
        width=16,
        poly=0x080B,
        init=0xFFFF,
        refin=True,
        refout=True,
        xorout=0x0000,
        check=0xA066,
        desc="NRSC-5 HD Radio digital broadcast",
        source="reveng",
    ),
    "crc16-opensafety-a": AlgorithmInfo(
        width=16,
        poly=0x5935,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x5D38,
        desc="OpenSAFETY field A",
        source="reveng",
    ),
    "crc16-opensafety-b": AlgorithmInfo(
        width=16,
        poly=0x755B,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x20FE,
        desc="OpenSAFETY field B",
        source="reveng",
    ),
    "crc16-profibus": AlgorithmInfo(
        width=16,
        poly=0x1DCF,
        init=0xFFFF,
        refin=False,
        refout=False,
        xorout=0xFFFF,
        check=0xA819,
        desc="PROFIBUS industrial fieldbus",
        source="reveng",
    ),
    "crc16-riello": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0xB2AA,
        refin=True,
        refout=True,
        xorout=0x0000,
        check=0x63D0,
        desc="Riello UPS dialog protocol",
        source="reveng",
    ),
    "crc16-spi-fujitsu": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0x1D0F,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0xE5CC,
        desc="Fujitsu SPI bus, AUG-CCITT",
        source="reveng",
    ),
    "crc16-t10-dif": AlgorithmInfo(
        width=16,
        poly=0x8BB7,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0xD0DB,
        desc="SCSI T10 Data Integrity Field",
        source="reveng",
    ),
    "crc16-teledisk": AlgorithmInfo(
        width=16,
        poly=0xA097,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x0FB3,
        desc="TeleDisk floppy disk archiver",
        source="reveng",
    ),
    "crc16-tms37157": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0x89EC,
        refin=True,
        refout=True,
        xorout=0x0000,
        check=0x26B1,
        desc="TI TMS37157 RFID transponder",
        source="reveng",
    ),
    "crc16-umts": AlgorithmInfo(
        width=16,
        poly=0x8005,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0xFEE8,
        desc="UMTS/WCDMA 3G (BUYPASS)",
        source="reveng",
    ),
    "crc16-usb": AlgorithmInfo(
        width=16,
        poly=0x8005,
        init=0xFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFF,
        check=0xB4C8,
        desc="USB token / data packet CRC",
        source="reveng",
    ),
    "crc16-xmodem": AlgorithmInfo(
        width=16,
        poly=0x1021,
        init=0x0000,
        refin=False,
        refout=False,
        xorout=0x0000,
        check=0x31C3,
        desc="XMODEM, ZMODEM, ACORN, LTE",
        source="reveng",
    ),
    # ---- CRC-17 (1 algorithm) ----
    "crc17-can-fd": AlgorithmInfo(
        width=17,
        poly=0x1685B,
        init=0x00000,
        refin=False,
        refout=False,
        xorout=0x00000,
        check=0x04F03,
        desc="CAN FD (<=16-byte payload)",
        source="reveng",
    ),
    # ---- CRC-21 (1 algorithm) ----
    "crc21-can-fd": AlgorithmInfo(
        width=21,
        poly=0x102899,
        init=0x000000,
        refin=False,
        refout=False,
        xorout=0x000000,
        check=0x0ED841,
        desc="CAN FD (>16-byte payload)",
        source="reveng",
    ),
    # ---- CRC-24 (8 algorithms) ----
    "crc24-ble": AlgorithmInfo(
        width=24,
        poly=0x00065B,
        init=0x555555,
        refin=True,
        refout=True,
        xorout=0x000000,
        check=0xC25A56,
        desc="Bluetooth Low Energy data CRC",
        source="reveng",
    ),
    "crc24-flexray-a": AlgorithmInfo(
        width=24,
        poly=0x5D6DCB,
        init=0xFEDCBA,
        refin=False,
        refout=False,
        xorout=0x000000,
        check=0x7979BD,
        desc="FlexRay header CRC (preset A)",
        source="reveng",
    ),
    "crc24-flexray-b": AlgorithmInfo(
        width=24,
        poly=0x5D6DCB,
        init=0xABCDEF,
        refin=False,
        refout=False,
        xorout=0x000000,
        check=0x1F23B8,
        desc="FlexRay header CRC (preset B)",
        source="reveng",
    ),
    "crc24-interlaken": AlgorithmInfo(
        width=24,
        poly=0x328B63,
        init=0xFFFFFF,
        refin=False,
        refout=False,
        xorout=0xFFFFFF,
        check=0xB4F3E6,
        desc="Interlaken chip-to-chip (24-bit)",
        source="reveng",
    ),
    "crc24-lte-a": AlgorithmInfo(
        width=24,
        poly=0x864CFB,
        init=0x000000,
        refin=False,
        refout=False,
        xorout=0x000000,
        check=0xCDE703,
        desc="LTE PDCP / transport block A",
        source="reveng",
    ),
    "crc24-lte-b": AlgorithmInfo(
        width=24,
        poly=0x800063,
        init=0x000000,
        refin=False,
        refout=False,
        xorout=0x000000,
        check=0x23EF52,
        desc="LTE transport block B",
        source="reveng",
    ),
    "crc24-openpgp": AlgorithmInfo(
        width=24,
        poly=0x864CFB,
        init=0xB704CE,
        refin=False,
        refout=False,
        xorout=0x000000,
        check=0x21CF02,
        desc="OpenPGP ASCII armor (RFC 4880)",
        source="reveng",
    ),
    "crc24-os-9": AlgorithmInfo(
        width=24,
        poly=0x800063,
        init=0xFFFFFF,
        refin=False,
        refout=False,
        xorout=0xFFFFFF,
        check=0x200FA5,
        desc="OS-9 RTOS module CRC",
        source="reveng",
    ),
    # ---- CRC-30 (1 algorithm) ----
    "crc30-cdma": AlgorithmInfo(
        width=30,
        poly=0x2030B9C7,
        init=0x3FFFFFFF,
        refin=False,
        refout=False,
        xorout=0x3FFFFFFF,
        check=0x04C34ABF,
        desc="CDMA mobile (3GPP2 C.S0024)",
        source="reveng",
    ),
    # ---- CRC-31 (1 algorithm) ----
    "crc31-philips": AlgorithmInfo(
        width=31,
        poly=0x04C11DB7,
        init=0x7FFFFFFF,
        refin=False,
        refout=False,
        xorout=0x7FFFFFFF,
        check=0x0CE9E46C,
        desc="Philips data transmission",
        source="reveng",
    ),
    # ---- CRC-32 (13 algorithms) ----
    "crc32": AlgorithmInfo(
        width=32,
        poly=0x04C11DB7,
        init=0xFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFFFFFF,
        check=0xCBF43926,
        desc="ISO 3309, ITU-T V.42, Ethernet, PKZIP, PNG",
        source="reveng",
    ),
    "crc32-aixm": AlgorithmInfo(
        width=32,
        poly=0x814141AB,
        init=0x00000000,
        refin=False,
        refout=False,
        xorout=0x00000000,
        check=0x3010BF7F,
        desc="AIXM (Aeronautical Information Exchange)",
        source="reveng",
    ),
    "crc32-autosar": AlgorithmInfo(
        width=32,
        poly=0xF4ACFB13,
        init=0xFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFFFFFF,
        check=0x1697D06A,
        desc="AUTOSAR automotive E2E Profile 4",
        source="reveng",
    ),
    "crc32-bacnet": AlgorithmInfo(
        width=32,
        poly=0x741B8CD7,
        init=0xFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFFFFFF,
        check=0x2D3DD0AE,
        desc="BACnet MS/TP large frames, CRC-32K Koopman (ANSI/ASHRAE 135 Annex G.3.2)",
        source="ietf:draft-lynn-6lo-rfc8163-bis-01",
    ),
    "crc32-base91-d": AlgorithmInfo(
        width=32,
        poly=0xA833982B,
        init=0xFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFFFFFF,
        check=0x87315576,
        desc="base91 encoding (CRC-32D)",
        source="reveng",
    ),
    "crc32-bzip2": AlgorithmInfo(
        width=32,
        poly=0x04C11DB7,
        init=0xFFFFFFFF,
        refin=False,
        refout=False,
        xorout=0xFFFFFFFF,
        check=0xFC891918,
        desc="bzip2 file compression, AAL5",
        source="reveng",
    ),
    "crc32-cd-rom-edc": AlgorithmInfo(
        width=32,
        poly=0x8001801B,
        init=0x00000000,
        refin=True,
        refout=True,
        xorout=0x00000000,
        check=0x6EC2EDC4,
        desc="CD-ROM Error Detection Code",
        source="reveng",
    ),
    "crc32-cksum": AlgorithmInfo(
        width=32,
        poly=0x04C11DB7,
        init=0x00000000,
        refin=False,
        refout=False,
        xorout=0xFFFFFFFF,
        check=0x765E7680,
        desc="POSIX cksum command",
        source="reveng",
    ),
    "crc32-iscsi": AlgorithmInfo(
        width=32,
        poly=0x1EDC6F41,
        init=0xFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFFFFFF,
        check=0xE3069283,
        desc="iSCSI, SCTP, Castagnoli (CRC-32C)",
        source="reveng",
    ),
    "crc32-jamcrc": AlgorithmInfo(
        width=32,
        poly=0x04C11DB7,
        init=0xFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0x00000000,
        check=0x340BC6D9,
        desc="Altera Jam STAPL programming language",
        source="reveng",
    ),
    "crc32-mef": AlgorithmInfo(
        width=32,
        poly=0x741B8CD7,
        init=0xFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0x00000000,
        check=0xD2C22F51,
        desc="Metro Ethernet Forum (MEF)",
        source="reveng",
    ),
    "crc32-mpeg-2": AlgorithmInfo(
        width=32,
        poly=0x04C11DB7,
        init=0xFFFFFFFF,
        refin=False,
        refout=False,
        xorout=0x00000000,
        check=0x0376E6E7,
        desc="MPEG-2 transport stream",
        source="reveng",
    ),
    "crc32-xfer": AlgorithmInfo(
        width=32,
        poly=0x000000AF,
        init=0x00000000,
        refin=False,
        refout=False,
        xorout=0x00000000,
        check=0xBD0BE338,
        desc="XFER file transfer protocol",
        source="reveng",
    ),
    # ---- CRC-64 (7 algorithms) ----
    "crc64-ecma-182": AlgorithmInfo(
        width=64,
        poly=0x42F0E1EBA9EA3693,
        init=0x0000000000000000,
        refin=False,
        refout=False,
        xorout=0x0000000000000000,
        check=0x6C40DF5F0B497347,
        desc="ECMA-182 (DLT tape, original)",
        source="reveng",
    ),
    "crc64-go-iso": AlgorithmInfo(
        width=64,
        poly=0x000000000000001B,
        init=0xFFFFFFFFFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFFFFFFFFFFFFFF,
        check=0xB90956C775A41001,
        desc="Go standard library (hash/crc64.ISO)",
        source="reveng",
    ),
    "crc64-ms": AlgorithmInfo(
        width=64,
        poly=0x259C84CBA6426349,
        init=0xFFFFFFFFFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0x0000000000000000,
        check=0x75D4B74F024ECEEA,
        desc="Microsoft (jhash.c)",
        source="reveng",
    ),
    "crc64-nvme": AlgorithmInfo(
        width=64,
        poly=0xAD93D23594C93659,
        init=0xFFFFFFFFFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFFFFFFFFFFFFFF,
        check=0xAE8B14860A799888,
        desc="NVMe storage protocol",
        source="reveng",
    ),
    "crc64-redis": AlgorithmInfo(
        width=64,
        poly=0xAD93D23594C935A9,
        init=0x0000000000000000,
        refin=True,
        refout=True,
        xorout=0x0000000000000000,
        check=0xE9C6D914C4B8D9CA,
        desc="Redis in-memory data store",
        source="reveng",
    ),
    "crc64-we": AlgorithmInfo(
        width=64,
        poly=0x42F0E1EBA9EA3693,
        init=0xFFFFFFFFFFFFFFFF,
        refin=False,
        refout=False,
        xorout=0xFFFFFFFFFFFFFFFF,
        check=0x62EC59E3F1A4F00A,
        desc="Wolfgang Ehrhardt CRC-64",
        source="reveng",
    ),
    "crc64-xz": AlgorithmInfo(
        width=64,
        poly=0x42F0E1EBA9EA3693,
        init=0xFFFFFFFFFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFFFFFFFFFFFFFF,
        check=0x995DC9BBDF1939FA,
        desc="XZ file format (LZMA2 streams)",
        source="reveng",
    ),
}
