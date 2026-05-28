"""Tests for the C extension ``crcglot._c``.

The extension is optional -- the pure-Python ``generic_crc`` in
``crcglot.catalogue`` is always available.  All tests skip cleanly
when the extension isn't built (e.g. on a platform without a C
compiler when installing from sdist).

Two layers:

* **Parity** (this file's core): every catalogue algorithm AND every
  hardcoded reveng-canonical vector is computed via both the Python
  ``generic_crc`` and the C ``c_generic_crc``, then asserted equal.
  Drift in either direction surfaces here.

* **Edge cases**: empty input, single byte, width boundaries (8 / 64),
  invalid width (out of range), and the buffer protocol acceptance
  (bytes / bytearray / memoryview).
"""

from __future__ import annotations

import pytest

from crcglot import ALGORITHMS, generic_crc

try:
    from crcglot import _c
    HAS_C_EXTENSION = True
except ImportError:
    _c = None  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
    HAS_C_EXTENSION = False


pytestmark = pytest.mark.skipif(
    not HAS_C_EXTENSION,
    reason="crcglot._c not built; install via `uv sync` or pip with a C compiler",
)


_CHECK_INPUT = b"123456789"


# ─────────────────────────────────────────────────────────────────────
# Parity: every catalogue algorithm via both engines
# ─────────────────────────────────────────────────────────────────────


class TestCExtensionParityWithPython:
    """The C extension must produce the same value as ``generic_crc``
    for every algorithm in the catalogue, on the canonical reveng
    check input.  Asserts on the catalogue's ``check`` field too --
    so an off-by-one in the C engine surfaces both as a Python/C
    disagreement AND as a reveng disagreement.
    """

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_c_matches_python_and_reveng(self, name):
        # Arrange
        algo = ALGORITHMS[name]

        # Act
        py_result = generic_crc(
            _CHECK_INPUT, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        c_result = _c.c_generic_crc(
            _CHECK_INPUT, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )

        # Assert -- three-way: C == Python == reveng catalogue.
        assert c_result == py_result, (
            f"{name}: C ({c_result:#x}) != Python ({py_result:#x})"
        )
        assert c_result == algo.check, (
            f"{name}: C ({c_result:#x}) != reveng check ({algo.check:#x})"
        )


# ─────────────────────────────────────────────────────────────────────
# Buffer-protocol input acceptance
# ─────────────────────────────────────────────────────────────────────


class TestCExtensionInputTypes:
    """``c_generic_crc`` accepts any bytes-like input via the buffer
    protocol -- bytes, bytearray, memoryview, array.array.  Sanity-check
    each on the canonical ``crc32`` parameters."""

    _PARAMS = (32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)
    _EXPECTED = 0xCBF43926

    def test_bytes(self):
        actual = _c.c_generic_crc(_CHECK_INPUT, *self._PARAMS)
        assert actual == self._EXPECTED

    def test_bytearray(self):
        actual = _c.c_generic_crc(bytearray(_CHECK_INPUT), *self._PARAMS)
        assert actual == self._EXPECTED

    def test_memoryview(self):
        mv = memoryview(_CHECK_INPUT)
        actual = _c.c_generic_crc(mv, *self._PARAMS)
        assert actual == self._EXPECTED

    def test_memoryview_over_bytearray(self):
        # Mutable buffer -- proves we don't accidentally require
        # PyBUF_READONLY on the buffer protocol acquire.
        ba = bytearray(_CHECK_INPUT)
        actual = _c.c_generic_crc(memoryview(ba), *self._PARAMS)
        assert actual == self._EXPECTED


# ─────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────


class TestCExtensionEdgeCases:
    """Boundary and error cases that the engine should handle gracefully."""

    def test_empty_input_returns_finalized_init(self):
        # Arrange -- crc32: init 0xFFFFFFFF; refout flips; xorout flips again.
        # Empty input means the loop doesn't execute; result is just
        # the init pushed through finalize.
        # Act
        actual = _c.c_generic_crc(
            b"", 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF,
        )
        # Assert -- matches the Python engine on the same empty input.
        expected = generic_crc(
            b"", 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF,
        )
        assert actual == expected

    def test_single_byte(self):
        # Single-byte input shouldn't hit any off-by-one in the
        # buffer-protocol len handling.
        # Arrange
        params = (32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)
        # Act
        actual = _c.c_generic_crc(b"\x01", *params)
        expected = generic_crc(b"\x01", *params)
        # Assert
        assert actual == expected

    def test_width_8_minimum(self):
        # Arrange
        algo = ALGORITHMS["crc8"]
        # Act
        actual = _c.c_generic_crc(
            _CHECK_INPUT, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        # Assert
        assert actual == algo.check

    def test_width_64_maximum(self):
        # Arrange -- crc64-xz is reflected and uses all 64 bits.
        algo = ALGORITHMS["crc64-xz"]
        # Act
        actual = _c.c_generic_crc(
            _CHECK_INPUT, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )
        # Assert
        assert actual == algo.check

    def test_width_out_of_range_too_low(self):
        # Assert -- C should reject width < 8 with ValueError.
        with pytest.raises(ValueError, match="width must be in"):
            _c.c_generic_crc(
                b"", 4, 0x3, 0x0, False, False, 0x0,
            )

    def test_width_out_of_range_too_high(self):
        # Assert -- C should reject width > 64 with ValueError.
        with pytest.raises(ValueError, match="width must be in"):
            _c.c_generic_crc(
                b"", 65, 0x3, 0x0, False, False, 0x0,
            )

    def test_large_buffer_releases_gil(self):
        # Arrange -- buffer above the 64 KiB GIL-release threshold.
        # Doesn't directly test GIL release (would need a second
        # thread), but proves the larger path doesn't crash or
        # produce a wrong value.
        buf = bytes(range(256)) * 256  # 64 KiB exactly
        # Act
        params = (32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)
        actual = _c.c_generic_crc(buf, *params)
        expected = generic_crc(buf, *params)
        # Assert
        assert actual == expected
