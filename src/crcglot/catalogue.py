"""CRC catalogue + generic compute engine.

64+ named algorithms from the reveng catalogue (Greg Cook,
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
from dataclasses import dataclass


# Optional C accelerator.  Installed by the wheel; absent when crcglot
# is built from sdist on a platform without a C compiler.  ``generic_crc``
# transparently dispatches to it when present -- same result, slice-by-8
# / table-driven speed (~1-2 GB/s).  When absent, the pure-Python loop
# below runs unchanged.
try:
    from crcglot._c import c_generic_crc as _c_generic_crc
except ImportError:
    _c_generic_crc = None  # type: ignore[assignment]  # ty: ignore[invalid-assignment]


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
    if _c_generic_crc is not None:
        return _c_generic_crc(data, width, poly, init, refin, refout, xorout)
    return _generic_crc_python(data, width, poly, init, refin, refout, xorout)


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
    else:
        # Normal algorithm: process MSB-first
        msb_mask = 1 << (width - 1)
        for byte in data:
            crc ^= byte << (width - 8)
            for _ in range(8):
                if crc & msb_mask:
                    crc = (crc << 1) ^ poly
                else:
                    crc <<= 1
            crc &= (1 << width) - 1
    if refout != refin:
        crc = _reflect(crc, width)
    return crc ^ xorout


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
    """

    width: int
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int
    check: int
    desc: str


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
    # ---- CRC-8 (20 algorithms) ----
    "crc8": AlgorithmInfo(
        width=8,
        poly=0x07,
        init=0x00,
        refin=False,
        refout=False,
        xorout=0x00,
        check=0xF4,
        desc="ITU-T I.432.1 (ATM HEC), ISDN",
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
    ),
    # ---- CRC-32 (12 algorithms) ----
    "crc32": AlgorithmInfo(
        width=32,
        poly=0x04C11DB7,
        init=0xFFFFFFFF,
        refin=True,
        refout=True,
        xorout=0xFFFFFFFF,
        check=0xCBF43926,
        desc="ISO 3309, ITU-T V.42, Ethernet, PKZIP, PNG",
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
    ),
}
