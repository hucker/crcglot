"""Tests for the C extension ``crcglot._c``.

The extension is optional -- the pure-Python ``_generic_crc_python``
in ``crcglot.catalogue`` is always available.  All tests skip cleanly
when the extension isn't built (e.g. on a platform without a C
compiler when installing from sdist).

Two layers:

* **Parity** (this file's core): every catalogue algorithm AND every
  hardcoded reveng-canonical vector is computed via both
  ``_generic_crc_python`` (the reference loop) and ``c_generic_crc``
  (the C engine), then asserted equal.  We compare the two engines
  *directly* rather than through the public ``generic_crc``, which
  dispatches to C when the extension is present -- comparing through
  it would silently compare C against itself.  Drift in either
  direction surfaces here.

* **Edge cases**: empty input, single byte, width boundaries (8 / 64),
  invalid width (out of range), and the buffer protocol acceptance
  (bytes / bytearray / memoryview).
"""

from __future__ import annotations

import pytest

from crcglot import ALGORITHMS
from crcglot.catalogue import _generic_crc_python

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
    """The C extension must produce the same value as the pure-Python
    engine for every catalogue algorithm, on the canonical reveng
    check input.

    Compares ``_generic_crc_python`` (the reference loop) directly
    against ``_c.c_generic_crc`` -- NOT via the public ``generic_crc``,
    which dispatches to C when the extension is present and would
    otherwise have us comparing C against itself.  Also asserts both
    against the catalogue's hardcoded ``check`` field, so an off-by-one
    in either implementation surfaces as a real failure.
    """

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_c_matches_python_and_reveng(self, name):
        # Arrange
        algo = ALGORITHMS[name]
        args = (
            _CHECK_INPUT, algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        )

        # Act -- run BOTH engines explicitly.
        py_result = _generic_crc_python(*args)
        c_result = _c.c_generic_crc(*args)

        # Assert -- three-way: C == pure-Python == reveng catalogue.
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
        expected = _generic_crc_python(
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
        expected = _generic_crc_python(b"\x01", *params)
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
        expected = _generic_crc_python(buf, *params)
        # Assert
        assert actual == expected


def _stream_params(algo):
    return {
        "width": algo.width,
        "poly": algo.poly,
        "init": algo.init,
        "refin": algo.refin,
        "refout": algo.refout,
        "xorout": algo.xorout,
    }


class TestCrcStream:
    """The incremental CrcStream object: chunked updates must match the
    one-shot result, digest() is non-destructive, reset()/copy() behave."""

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_chunked_matches_oneshot(self, name):
        # Arrange -- feed "123456789" split at byte 4.
        algo = ALGORITHMS[name]
        expected = algo.check

        # Act -- streamed in two chunks.
        s = _c.CrcStream(**_stream_params(algo))
        s.update(b"1234")
        s.update(b"56789")
        streamed = s.digest()

        # Assert -- matches the catalogue check value.
        assert streamed == expected, (
            f"{name}: streamed {streamed:#x} != check {expected:#x}"
        )

    @pytest.mark.parametrize("name", sorted(ALGORITHMS.keys()))
    def test_splittability_invariant(self, name):
        # Arrange -- the same input split three different ways (incl.
        # empty chunks) must all produce the catalogue check value.
        algo = ALGORITHMS[name]
        expected = algo.check
        splits = [
            [b"123456789"],
            [b"", b"123456789"],
            [b"123456789", b""],
            [b"1", b"2", b"3", b"4", b"5", b"6", b"7", b"8", b"9"],
            [b"12345", b"6789"],
        ]
        # Act + Assert
        for chunks in splits:
            s = _c.CrcStream(**_stream_params(algo))
            for ch in chunks:
                s.update(ch)
            actual = s.digest()
            assert actual == expected, (
                f"{name}: split {[len(c) for c in chunks]} gave "
                f"{actual:#x}, expected {expected:#x}"
            )

    def test_digest_is_non_destructive(self):
        # Arrange
        algo = ALGORITHMS["crc32"]
        s = _c.CrcStream(**_stream_params(algo))
        s.update(b"12345")
        # Act -- digest mid-stream, then keep feeding.
        mid = s.digest()
        mid_again = s.digest()
        s.update(b"6789")
        final = s.digest()
        # Assert -- digest didn't disturb state; final is the full CRC.
        assert mid == mid_again, "digest() mutated state"
        assert final == algo.check, f"final {final:#x} != {algo.check:#x}"
        assert mid != final, "mid-stream digest should differ from final"

    def test_reset_reuses_params(self):
        # Arrange
        algo = ALGORITHMS["crc32"]
        s = _c.CrcStream(**_stream_params(algo))
        s.update(b"garbage")
        # Act
        s.reset()
        s.update(b"123456789")
        # Assert
        assert s.digest() == algo.check, "reset() didn't restore init state"

    def test_copy_branches_state(self):
        # Arrange
        algo = ALGORITHMS["crc32"]
        s = _c.CrcStream(**_stream_params(algo))
        s.update(b"12345")
        # Act -- branch: copy finishes the input, original stays at prefix.
        c = s.copy()
        c.update(b"6789")
        # Assert -- copy reached the full check value; original is still
        # at the 5-byte prefix (a different, non-final value).
        assert c.digest() == algo.check, "copy() didn't carry state forward"
        assert s.digest() != algo.check, "original advanced unexpectedly"

    def test_copy_is_independent(self):
        # Mutating the copy must not affect the original and vice versa.
        # Arrange
        algo = ALGORITHMS["crc32"]
        s = _c.CrcStream(**_stream_params(algo))
        s.update(b"123456789")
        snapshot = s.digest()
        # Act
        c = s.copy()
        c.update(b"more data")
        # Assert -- original unchanged after copy mutated.
        assert s.digest() == snapshot, "original changed when copy updated"

    @pytest.mark.parametrize("name", ["crc64-xz", "crc16-modbus", "crc8"])
    def test_stream_across_widths(self, name):
        # Exercise the slice8 (64), table (16), table (8) engines through
        # the streaming path.
        algo = ALGORITHMS[name]
        s = _c.CrcStream(**_stream_params(algo))
        s.update(b"123456789")
        assert s.digest() == algo.check

    def test_large_chunked_matches_python(self):
        # Arrange -- big buffer fed in awkward chunk sizes; compare to
        # the pure-Python one-shot.
        algo = ALGORITHMS["crc32"]
        buf = bytes(range(256)) * 500  # 125 KiB
        expected = _generic_crc_python(buf, *(
            algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout,
        ))
        # Act -- feed in 7-byte chunks (not a multiple of the 8-byte
        # slice-by-8 stride, so the tail path is exercised repeatedly).
        s = _c.CrcStream(**_stream_params(algo))
        for i in range(0, len(buf), 7):
            s.update(buf[i:i + 7])
        # Assert
        assert s.digest() == expected

    def test_invalid_width_raises(self):
        with pytest.raises(ValueError, match="width must be in"):
            _c.CrcStream(width=4, poly=0x3, init=0x0)

    def test_init_defaults(self):
        # refin/refout/xorout are optional; crc16-xmodem uses all defaults
        # except width/poly/init.
        # Arrange -- crc16-xmodem: refin=F, refout=F, xorout=0.
        algo = ALGORITHMS["crc16-xmodem"]
        # Act -- omit the defaulted kwargs.
        s = _c.CrcStream(width=algo.width, poly=algo.poly, init=algo.init)
        s.update(b"123456789")
        # Assert
        assert s.digest() == algo.check


class TestCrcMany:
    """The batch API: c_crc_many(buffers, ...) == per-element c_generic_crc."""

    _PARAMS = (32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)

    def test_matches_per_element(self):
        # Arrange
        bufs = [b"123456789", b"", b"a", b"hello world", bytes(range(256))]
        # Act
        batch = _c.c_crc_many(bufs, *self._PARAMS)
        per = [_c.c_generic_crc(b, *self._PARAMS) for b in bufs]
        # Assert
        assert batch == per, f"batch {batch} != per-element {per}"

    def test_preserves_order(self):
        # Distinct inputs -> distinct, order-stable outputs.
        bufs = [b"1", b"12", b"123", b"1234"]
        batch = _c.c_crc_many(bufs, *self._PARAMS)
        assert batch == [_c.c_generic_crc(b, *self._PARAMS) for b in bufs]

    def test_empty_list(self):
        assert _c.c_crc_many([], *self._PARAMS) == []

    def test_first_element_is_reveng_check(self):
        # crc32 of "123456789" is the canonical check value.
        batch = _c.c_crc_many([b"123456789"], *self._PARAMS)
        assert batch == [0xCBF43926]

    def test_accepts_tuple_and_mixed_buffer_types(self):
        # Any sequence of any bytes-like works.
        bufs = (b"123456789", bytearray(b"123456789"), memoryview(b"123456789"))
        batch = _c.c_crc_many(bufs, *self._PARAMS)
        assert batch == [0xCBF43926, 0xCBF43926, 0xCBF43926]

    @pytest.mark.parametrize("name", ["crc64-xz", "crc16-modbus", "crc8"])
    def test_batch_across_widths(self, name):
        algo = ALGORITHMS[name]
        tail = (algo.width, algo.poly, algo.init,
                algo.refin, algo.refout, algo.xorout)
        batch = _c.c_crc_many([b"123456789", b"123456789"], *tail)
        assert batch == [algo.check, algo.check]

    def test_non_bytes_element_raises_typeerror(self):
        with pytest.raises(TypeError):
            _c.c_crc_many([123], *self._PARAMS)  # type: ignore[list-item]  # ty: ignore[invalid-argument-type]

    def test_invalid_width_raises(self):
        with pytest.raises(ValueError, match="width must be in"):
            _c.c_crc_many([b""], 4, 0x3, 0x0, False, False, 0x0)


class TestCExtensionTableCache:
    """Exercise the (width, poly, refin) lookup-table cache.

    The parameterized catalogue parity tests run under pytest-xdist
    (split across workers), so no single process necessarily sees more
    than ``CACHE_CAP`` distinct algorithms.  These tests run many
    distinct polynomials in ONE process to hit cache-hit, cache-fill,
    and cache-overflow (build-and-free) paths -- the last is otherwise
    untested.
    """

    def test_all_catalogue_algorithms_one_process(self):
        # Act + Assert -- every catalogue algorithm, in-process, so the
        # cache fills past CACHE_CAP (70 algorithms > 64).  Each must
        # still match the pure-Python engine.
        for name, algo in ALGORITHMS.items():
            args = (
                _CHECK_INPUT, algo.width, algo.poly, algo.init,
                algo.refin, algo.refout, algo.xorout,
            )
            actual = _c.c_generic_crc(*args)
            expected = _generic_crc_python(*args)
            assert actual == expected, (
                f"{name}: C ({actual:#x}) != Python ({expected:#x})"
            )

    def test_repeated_calls_same_algorithm_use_cache(self):
        # Act -- many calls for one algorithm; tables built once, reused.
        # Correctness is the observable; the cache hit is internal.
        params = (32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)
        results = {_c.c_generic_crc(_CHECK_INPUT, *params) for _ in range(100)}
        # Assert -- all identical, and correct.
        assert results == {0xCBF43926}, f"unstable/incorrect: {results}"

    def test_many_distinct_polys_overflow_cache(self):
        # Arrange -- 200 distinct width-32 polynomials, well past
        # CACHE_CAP=64, forcing the build-and-free overflow path.
        # Act + Assert -- each agrees with the Python engine.
        for k in range(200):
            poly = (0x04C11DB7 ^ (k * 0x9E3779B1)) & 0xFFFFFFFF
            poly |= 1  # keep it a plausible (odd) CRC polynomial
            args = (_CHECK_INPUT, 32, poly, 0xFFFFFFFF, True, True, 0xFFFFFFFF)
            actual = _c.c_generic_crc(*args)
            expected = _generic_crc_python(*args)
            assert actual == expected, (
                f"poly={poly:#x}: C ({actual:#x}) != Python ({expected:#x})"
            )
