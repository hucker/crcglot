"""External-authority verification of the CRC engine.

The existing ``test_catalogue.py`` checks that every catalogue entry's
declared ``check`` field is reproduced by the engine on
``b"123456789"`` -- one input per algorithm, hardcoded values lifted
from the reveng (and other) primary sources.  That's necessary but not
sufficient: it catches "engine produces a different value at the
canonical input" but doesn't catch "engine has a bug that only fires
for certain shapes of input we never test".

This file widens the coverage by cross-checking the engine against
**independent external authorities** over **multiple inputs per
algorithm**.  The two strongest authorities we have:

1. ``zlib.crc32`` -- a hardware-accelerated implementation of IEEE
   CRC-32 (= our ``crc32`` algorithm) that ships with CPython.  We
   already use it as the runtime fast-path; here we use it as an
   *oracle*: for every input, the engine's output MUST equal
   ``zlib.crc32(data)``.  Any disagreement is an engine bug at
   ``(width=32, poly=0x04C11DB7, refin=True, refout=True,
   xorout=0xFFFFFFFF)``.

2. Hardcoded vectors for the new ``crc32-bacnet`` and ``crc8-bacnet``
   entries, derived by porting the BACnet MS/TP reference C
   implementation (``bacnet-stack/src/bacnet/datalink/crc.c``, which
   the file itself states is "copied directly from the BACnet
   standard") and running it over a fixed input set.  This is the only
   independent verification we have for the two BACnet entries; their
   primary source (ANSI/ASHRAE 135 Annex G.1 / G.3.2) is paywalled.

The reveng ``check`` cross-check at ``b"123456789"`` is also
parametrised here over *every* catalogue entry -- a redundant but
useful belt-and-braces against silent regression in
``test_catalogue.py``.

A regression here means the engine produces a value that disagrees
with an external authority.  That's the strongest "is the math right"
signal we can write without paying for ASHRAE or licencing AUTOSAR.
"""

from __future__ import annotations

import zlib

import pytest

from crcglot import ALGORITHMS, generic_crc


# ---------------------------------------------------------------------------
# Authority 1: zlib.crc32 as an oracle for the IEEE crc32 algorithm
# ---------------------------------------------------------------------------


# A varied input set -- empty, single bytes, pattern inputs, and
# pseudo-random bytes seeded for determinism.  The seed is fixed so the
# test is reproducible; if we ever need to expand coverage, just bump
# the upper range without changing the seed.
def _build_input_set() -> list[tuple[str, bytes]]:
    """Return a list of (label, data) pairs covering common shapes.

    Labels are stable so pytest's parametrize IDs are readable in
    failure output.
    """
    rng = bytes(((i * 73 + 11) & 0xFF) for i in range(2048))
    return [
        ("empty", b""),
        ("one_zero", b"\x00"),
        ("one_ff", b"\xFF"),
        ("123456789", b"123456789"),
        ("ascii_short", b"The quick brown fox"),
        ("zeros_8", b"\x00" * 8),
        ("zeros_64", b"\x00" * 64),
        ("ones_8", b"\xFF" * 8),
        ("ones_64", b"\xFF" * 64),
        ("seq_256", bytes(range(256))),
        ("deadbeef_4", bytes.fromhex("DEADBEEF")),
        ("nullbytes_1k", b"\x00" * 1024),
        ("random_pattern_2k", rng),
    ]


_ZLIB_INPUTS = _build_input_set()


class TestEngineAgainstZlibCrc32:
    """``zlib.crc32`` is the canonical CPython hardware-accelerated
    implementation of IEEE CRC-32 -- bit-identical to our ``crc32``
    entry's parameters.  Use it as an independent oracle.

    A failure here means our engine has a bug at the IEEE-32 parameter
    set on some specific input shape -- which would also slip past the
    single-input reveng ``check`` test.
    """

    @pytest.mark.parametrize("label,data", _ZLIB_INPUTS, ids=lambda v: v if isinstance(v, str) else "")
    def test_crc32_matches_zlib(self, label: str, data: bytes):
        # Arrange
        algo = ALGORITHMS["crc32"]
        # Act
        actual = generic_crc(
            data, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        expected = zlib.crc32(data) & 0xFFFFFFFF
        # Assert
        assert actual == expected, (
            f"crc32 disagrees with zlib.crc32 on {label!r} "
            f"({len(data)} bytes): "
            f"engine=0x{actual:08X}, zlib=0x{expected:08X}"
        )

    @pytest.mark.parametrize("label,data", _ZLIB_INPUTS, ids=lambda v: v if isinstance(v, str) else "")
    def test_crc32_jamcrc_matches_zlib_xor(self, label: str, data: bytes):
        # Arrange -- JAMCRC is IEEE-32 with xorout=0 instead of 0xFFFFFFFF,
        # i.e. zlib.crc32(data) XOR 0xFFFFFFFF.
        algo = ALGORITHMS["crc32-jamcrc"]
        # Act
        actual = generic_crc(
            data, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        expected = (zlib.crc32(data) ^ 0xFFFFFFFF) & 0xFFFFFFFF
        # Assert
        assert actual == expected, (
            f"crc32-jamcrc disagrees with (zlib.crc32(data) ^ 0xFFFFFFFF) "
            f"on {label!r}: engine=0x{actual:08X}, expected=0x{expected:08X}"
        )

    def test_engine_handles_million_bytes(self):
        # Arrange -- a longer input than any of the parametrised cases,
        # to catch any buffer / chunking bug in the slice-by-8 path.
        data = bytes((i * 31 + 17) & 0xFF for i in range(1_000_000))
        algo = ALGORITHMS["crc32"]
        # Act
        actual = generic_crc(
            data, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        expected = zlib.crc32(data) & 0xFFFFFFFF
        # Assert
        assert actual == expected, (
            f"crc32 disagrees with zlib.crc32 on 1MB pseudo-random input: "
            f"engine=0x{actual:08X}, zlib=0x{expected:08X}"
        )


# ---------------------------------------------------------------------------
# Authority 2: reveng catalogue ``check`` field at b"123456789"
# ---------------------------------------------------------------------------


class TestEveryAlgorithmReproducesCatalogueCheck:
    """For every algorithm in the catalogue, the engine's output on
    ``b"123456789"`` must equal the ``check`` field declared in
    ``catalogue.py`` -- which was lifted from reveng (or, for the two
    BACnet entries, from the IETF draft inlining ANSI/ASHRAE 135).

    This is the single-input cross-check.  It already runs implicitly
    through ``test_catalogue.py::TestCustomCrcChainAgainstRevengTruth``
    for the seven algorithms in ``_REVENG_CHECK_VALUES``, but we run
    it here parametrised over *every catalogue entry* so the entire
    catalogue is held to the same standard.
    """

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_check_matches_catalogue(self, name: str):
        # Arrange
        algo = ALGORITHMS[name]
        # Act
        actual = generic_crc(
            b"123456789", algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        # Assert
        assert actual == algo.check, (
            f"{name}: engine output on b'123456789' disagrees with "
            f"catalogue.check: engine=0x{actual:X}, "
            f"catalogue=0x{algo.check:X}"
        )


# ---------------------------------------------------------------------------
# Authority 3: BACnet MS/TP reference implementation, ported
# ---------------------------------------------------------------------------


def _bacnet_calc_crc8_header(data: bytes) -> int:
    """Port of CRC_Calc_Header from bacnet-stack/src/bacnet/datalink/crc.c.

    The source file's comment states: "This function is copied directly
    from the BACnet standard."  We mirror the byte-step exactly here so
    we have an independent (non-engine) reference for the crc8-bacnet
    check.

    Per the BACnet MS/TP spec Annex G.1: 8-bit CRC over the header
    octets, poly X^8+X^7+1, init 0xFF; final result is complemented
    (xorout 0xFF).  Implemented in the spec as bit-by-bit reflected
    processing.
    """
    crc_value = 0xFF
    for octet in data:
        crc = (crc_value ^ octet) & 0xFF
        for _ in range(8):
            if crc & 0x01:
                crc = (crc >> 1) ^ 0xC0
            else:
                crc >>= 1
            crc &= 0xFF
        crc_value = crc
    return (~crc_value) & 0xFF


def _bacnet_calc_crc32k_data(data: bytes) -> int:
    """Port of CRC_Calc_Data32 from bacnet-stack (the CRC-32K large-frame
    routine), mirroring the IETF draft-lynn-6lo-rfc8163-bis-01 Appendix C
    ``calc_crc32K`` pseudocode.

    Per ANSI/ASHRAE 135 Annex G.3.2 (quoted in the IETF draft): CRC-32K
    Koopman polynomial (reflected form 0xEB31D82E in the per-bit step),
    init 0xFFFFFFFF, reflected processing, final ones-complement
    (xorout 0xFFFFFFFF).
    """
    crc = 0xFFFFFFFF
    for octet in data:
        b = octet
        for _ in range(8):
            data_bit = (b & 0x01) ^ (crc & 0x01)
            crc >>= 1
            if data_bit != 0:
                crc ^= 0xEB31D82E
            b >>= 1
    return (~crc) & 0xFFFFFFFF


_BACNET_INPUTS = [
    ("empty", b""),
    ("123456789", b"123456789"),
    ("single_55", b"\x55"),
    ("single_aa", b"\xAA"),
    ("short_frame", bytes.fromhex("550101020304")),  # plausible MS/TP header octets
    ("zeros_8", b"\x00" * 8),
    ("ones_8", b"\xFF" * 8),
    ("seq_16", bytes(range(16))),
]


class TestCrc8BacnetAgainstReferenceImpl:
    """Cross-check ``crc8-bacnet`` against a Python port of the
    bacnet-stack reference C function ``CRC_Calc_Header``, which the
    upstream file declares as "copied directly from the BACnet
    standard".  Independent of our generic_crc engine; bug in either
    surfaces a disagreement.
    """

    @pytest.mark.parametrize("label,data", _BACNET_INPUTS, ids=lambda v: v if isinstance(v, str) else "")
    def test_engine_matches_reference(self, label: str, data: bytes):
        # Arrange
        algo = ALGORITHMS["crc8-bacnet"]
        # Act
        actual = generic_crc(
            data, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        expected = _bacnet_calc_crc8_header(data)
        # Assert
        assert actual == expected, (
            f"crc8-bacnet disagrees with bacnet-stack reference on "
            f"{label!r}: engine=0x{actual:02X}, reference=0x{expected:02X}"
        )


class TestCrc32BacnetAgainstReferenceImpl:
    """Cross-check ``crc32-bacnet`` against the BACnet MS/TP CRC-32K
    Koopman algorithm as reproduced in IETF draft-lynn-6lo-rfc8163-bis-01
    Appendix C (which inlines ANSI/ASHRAE 135 Annex G.3.2).
    """

    @pytest.mark.parametrize("label,data", _BACNET_INPUTS, ids=lambda v: v if isinstance(v, str) else "")
    def test_engine_matches_reference(self, label: str, data: bytes):
        # Arrange
        algo = ALGORITHMS["crc32-bacnet"]
        # Act
        actual = generic_crc(
            data, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        expected = _bacnet_calc_crc32k_data(data)
        # Assert
        assert actual == expected, (
            f"crc32-bacnet disagrees with IETF draft reference on "
            f"{label!r}: engine=0x{actual:08X}, "
            f"reference=0x{expected:08X}"
        )


# ---------------------------------------------------------------------------
# Authority 4: published multi-input test vectors from primary specs
# ---------------------------------------------------------------------------
#
# Every block below is (data, expected_crc) tuples lifted from a
# primary-source publication.  Each block names its source URL or
# document reference.  Any disagreement between the engine and a vector
# in these blocks is a moment to triangulate -- either our engine has a
# bug we missed, or the source published an incorrect vector (it has
# happened; the Modbus spec famously contradicts itself).  Don't
# silently "fix" by deleting; investigate and document.


# Source: RFC 7143 Appendix A.4 "CRC Examples" + RFC 3720 Appendix B.4.
# https://www.rfc-editor.org/rfc/rfc7143  https://www.rfc-editor.org/rfc/rfc3720
# Note: RFCs print the CRC as 4 on-wire bytes LSB-first; the integer
# below is those bytes interpreted little-endian, matching crcglot.
_VECTORS_CRC32_ISCSI_RFC7143 = [
    (b"\x00" * 32,                          0x8A9136AA),
    (b"\xff" * 32,                          0x62A8AB43),
    (bytes(range(0x00, 0x20)),              0x46DD794E),
    (bytes(range(0x1F, -1, -1)),            0x113FDB5C),
    (bytes.fromhex(
        "01c00000" "00000000" "00000000" "00000000"
        "14000000" "00000400" "00000014" "00000018"
        "28000000" "00000000" "02000000" "00000000"
    ),                                      0xD9963A56),
]

# Source: AUTOSAR_SWS_CRCLibrary R22-11, Tables 7.2 / 7.4 / 7.6 / 7.8 /
# 7.10 / 7.12 / 7.14.  Spec is paywalled; vectors mirrored verbatim at
# https://github.com/richhaar/autosar-crc/blob/main/src/main.test.js
# (test names explicitly cite "AUTOSAR table").
_AUTOSAR_INPUTS_RAW_HEX = [
    "00000000", "F20183", "0FAA0055", "00FF5511",
    "332255AABBCCDDEEFF", "926B55", "FFFFFFFF",
]
_AUTOSAR_INPUTS = [bytes.fromhex(h) for h in _AUTOSAR_INPUTS_RAW_HEX]

_VECTORS_CRC8_SAE_J1850_AUTOSAR = list(zip(
    _AUTOSAR_INPUTS,
    [0x59, 0x37, 0x79, 0xB8, 0xCB, 0x8C, 0x74],
))
_VECTORS_CRC8_AUTOSAR = list(zip(
    _AUTOSAR_INPUTS,
    [0x12, 0xC2, 0xC6, 0x77, 0x11, 0x33, 0x6C],
))
_VECTORS_CRC16_IBM_3740_AUTOSAR = list(zip(
    _AUTOSAR_INPUTS,
    [0x84C0, 0xD374, 0x2023, 0xB8F9, 0xF53F, 0x0745, 0x1D0F],
))
_VECTORS_CRC16_ARC_AUTOSAR = list(zip(
    _AUTOSAR_INPUTS,
    [0x0000, 0xC2E1, 0x0BE3, 0x6CCF, 0xAE98, 0xE24E, 0x9401],
))
_VECTORS_CRC32_AUTOSAR = list(zip(
    _AUTOSAR_INPUTS,
    [0x2144DF1C, 0x24AB9D77, 0xB6C9B287, 0x32A06212,
     0xB0AE863D, 0x9CDEA29B, 0xFFFFFFFF],
))
_VECTORS_CRC32_AUTOSAR_P4 = list(zip(
    _AUTOSAR_INPUTS,
    [0x6FB32240, 0x4F721A25, 0x20662DF8, 0x9BD7996E,
     0xA65A343D, 0xEE688A78, 0xFFFFFFFF],
))
_VECTORS_CRC64_XZ_AUTOSAR = list(zip(
    _AUTOSAR_INPUTS,
    [0xF4A586351E1B9F4B, 0x319C27668164F1C6, 0x54C5D0F7667C1575,
     0xA63822BE7E0704E6, 0x701ECEB219A8E5D5, 0x5FAA96A9B59F3E4E,
     0xFFFFFFFF00000000],
))


# Lookup: name -> (vectors, source_label)
_PUBLISHED_VECTOR_SUITES = {
    "crc32-iscsi": (_VECTORS_CRC32_ISCSI_RFC7143, "RFC 7143 / RFC 3720"),
    "crc8-sae-j1850": (_VECTORS_CRC8_SAE_J1850_AUTOSAR, "AUTOSAR SWS_CRCLibrary Table 7.2"),
    "crc8-autosar": (_VECTORS_CRC8_AUTOSAR, "AUTOSAR SWS_CRCLibrary Table 7.4"),
    "crc16-ibm-3740": (_VECTORS_CRC16_IBM_3740_AUTOSAR, "AUTOSAR SWS_CRCLibrary Table 7.6"),
    "crc16-arc": (_VECTORS_CRC16_ARC_AUTOSAR, "AUTOSAR SWS_CRCLibrary Table 7.8"),
    "crc32": (_VECTORS_CRC32_AUTOSAR, "AUTOSAR SWS_CRCLibrary Table 7.10"),
    "crc32-autosar": (_VECTORS_CRC32_AUTOSAR_P4, "AUTOSAR SWS_CRCLibrary Table 7.12"),
    "crc64-xz": (_VECTORS_CRC64_XZ_AUTOSAR, "AUTOSAR SWS_CRCLibrary Table 7.14"),
}


def _published_vector_cases():
    """Flatten the suites into (algorithm, source_label, hex_input, data, expected) tuples for pytest IDs."""
    out = []
    for name, (vectors, label) in _PUBLISHED_VECTOR_SUITES.items():
        for data, expected in vectors:
            short = data.hex() if len(data) <= 16 else f"{data.hex()[:14]}...{len(data)}B"
            out.append((name, label, short, data, expected))
    return out


_PUBLISHED_CASES = _published_vector_cases()


class TestEngineAgainstPublishedVectors:
    """Parametrised cross-check against vectors that real specifications
    publish.  Where these disagree with the engine, the failure is the
    interesting result -- either the engine has a bug, the catalogue
    parameters are subtly wrong (the bug that surfaced for crc8-bacnet
    earlier), or the published vector itself is wrong (the Modbus spec
    contradicts itself; CRC vector sites occasionally publish errata).
    """

    @pytest.mark.parametrize(
        "name,source,hex_input,data,expected",
        _PUBLISHED_CASES,
        ids=[f"{c[0]}-{c[2]}" for c in _PUBLISHED_CASES],
    )
    def test_published_vector(
        self,
        name: str,
        source: str,
        hex_input: str,
        data: bytes,
        expected: int,
    ):
        # Arrange
        algo = ALGORITHMS[name]
        # Act
        actual = generic_crc(
            data, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        # Assert
        hex_w = (algo.width + 3) // 4
        assert actual == expected, (
            f"{name}: engine disagrees with {source} on input {hex_input}: "
            f"engine=0x{actual:0{hex_w}X}, published=0x{expected:0{hex_w}X}.  "
            f"Triangulate before silently fixing -- the published vector "
            f"may itself be wrong, or our catalogue params may be subtly off."
        )
