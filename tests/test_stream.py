"""Tests for the streaming runtime engine (:mod:`crcglot.stream`).

The contract is that :class:`crcglot.CrcStream` is byte-identical to the
one-shot :func:`crcglot.generic_crc` and to the generated
``init/update/finalize`` triple, no matter how the input is chunked -- and
that this holds across all three backends (zlib fast-path, C extension,
pure-Python).  Mirrors ``tests/test_python_gen.py``'s
``TestGeneratedPythonStreaming`` splittability invariant for the library API.
"""

from __future__ import annotations

import pytest

from crcglot import ALGORITHMS, Crc, CrcStream, crc_stream, generic_crc
from crcglot.stream import _CCrcStream, _PyBackend, _ZlibBackend

# The reveng check string and the four chunkings that must all agree: whole,
# split mid-message, empty-first, empty-last.  Any split must be transparent.
_DATA = b"123456789"
_CHUNKINGS = (
    [_DATA],
    [b"1234", b"56789"],
    [b"", _DATA],
    [_DATA, b""],
)

_NAMES = sorted(ALGORITHMS)


def _digest(stream: CrcStream, chunks: list[bytes]) -> int:
    """Feed ``chunks`` into ``stream`` and return the finalized digest."""
    for chunk in chunks:
        stream.update(chunk)
    return stream.digest()


# ── splittability via the public API (real backend dispatch) ──────────────


@pytest.mark.parametrize("name", _NAMES)
def test_stream_matches_check_for_every_chunking(name: str) -> None:
    """Every catalogue algorithm streams to its reveng check value, any split.

    Through ``crc_stream`` -- i.e. the real backend dispatch (zlib for
    crc32/jamcrc, the C extension when built, else pure-Python).
    """
    # Arrange
    expected = ALGORITHMS[name].check

    # Act / Assert
    for chunks in _CHUNKINGS:
        actual = _digest(crc_stream(name), chunks)
        assert actual == expected, (
            f"{name}: {chunks!r} gave {actual:#x}, expected {expected:#x}"
        )


@pytest.mark.parametrize("name", _NAMES)
def test_stream_matches_generic_crc(name: str) -> None:
    """Streaming agrees with the one-shot ``generic_crc`` for every algorithm."""
    # Arrange
    a = ALGORITHMS[name]
    expected = generic_crc(_DATA, a)

    # Act
    actual = _digest(crc_stream(name), [b"123", b"456", b"789"])

    # Assert
    assert actual == expected, f"{name}: stream {actual:#x} != generic_crc {expected:#x}"


# ── pure-Python backend forced across the whole catalogue ─────────────────


@pytest.fixture
def force_pure_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``_make_backend`` fall through to ``_PyBackend`` for every algorithm.

    The C extension (when built) and the zlib fast-path would otherwise win, so
    the reference state machine would never be exercised through the public API.
    """
    monkeypatch.setattr("crcglot.stream._CCrcStream", None)
    monkeypatch.setattr("crcglot.stream._ZLIB_FAST_PATHS", {})


@pytest.mark.parametrize("name", _NAMES)
def test_pure_python_backend_matches_check(name: str, force_pure_python: None) -> None:
    """The pure-Python fallback reproduces every check value, any split."""
    # Arrange
    expected = ALGORITHMS[name].check

    # Act / Assert -- forced fallback means crc32/jamcrc run pure-Python too.
    stream = crc_stream(name)
    assert isinstance(stream._backend, _PyBackend), f"{name}: expected pure-Python"
    for chunks in _CHUNKINGS:
        actual = _digest(crc_stream(name), chunks)
        assert actual == expected, f"{name}: {chunks!r} gave {actual:#x}"


# ── backend selection ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["crc32", "crc32-jamcrc"])
def test_fast_path_uses_zlib_backend(name: str) -> None:
    """crc32 / jamcrc select the stdlib zlib backend and still match."""
    # Act
    stream = crc_stream(name)

    # Assert
    assert isinstance(stream._backend, _ZlibBackend), f"{name}: expected zlib backend"
    stream.update(_DATA)
    assert stream.digest() == ALGORITHMS[name].check, f"{name}: zlib digest mismatch"


@pytest.mark.skipif(_CCrcStream is None, reason="C extension not built")
@pytest.mark.parametrize("name", ["crc16-modbus", "crc8-bluetooth", "crc64-xz"])
def test_c_extension_backend_matches(name: str) -> None:
    """When built, non-fast-path algorithms run on the C extension backend."""
    # Act
    stream = crc_stream(name)

    # Assert
    assert isinstance(stream._backend, _CCrcStream), f"{name}: expected C backend"
    stream.update(_DATA)
    assert stream.digest() == ALGORITHMS[name].check, f"{name}: C digest mismatch"


# ── hashlib-style semantics ───────────────────────────────────────────────


def test_digest_is_non_destructive() -> None:
    """``digest`` may be called repeatedly without consuming state."""
    # Arrange
    s = crc_stream("crc32")
    s.update(_DATA)

    # Act / Assert
    first, second = s.digest(), s.digest()
    assert first == second == ALGORITHMS["crc32"].check, "digest must be repeatable"
    # ...and update can continue after a digest.
    s.update(b"more")
    assert s.digest() != first, "update after digest must extend the message"


def test_reset_reuses_the_stream() -> None:
    """``reset`` returns to the initial state, matching a fresh stream."""
    # Arrange
    s = crc_stream("crc16-modbus")
    s.update(b"garbage")

    # Act
    s.reset()
    s.update(_DATA)

    # Assert
    assert s.digest() == ALGORITHMS["crc16-modbus"].check, "reset must clear state"


def test_copy_is_independent() -> None:
    """``copy`` branches the state; mutating the copy never touches the original."""
    # Arrange -- both fed the same prefix.
    a = crc_stream("crc8")
    a.update(b"12345")
    b = a.copy()

    # Act -- feed both the same suffix, then diverge the copy.
    a.update(b"6789")
    b.update(b"6789")
    converged = a.digest() == b.digest() == ALGORITHMS["crc8"].check
    b.update(b"divergence")

    # Assert
    assert converged, "copy must share state at the branch point"
    assert a.digest() != b.digest(), "copy must be independent after divergence"


def test_hexdigest_is_zero_padded_to_width() -> None:
    """``hexdigest`` pads to the algorithm's nibble width."""
    # Act / Assert -- crc32 is 8 nibbles, crc8 is 2.
    s32 = crc_stream("crc32")
    s32.update(_DATA)
    assert s32.hexdigest() == "cbf43926", f"crc32 hexdigest: {s32.hexdigest()}"

    s8 = crc_stream("crc8")
    s8.update(_DATA)
    actual_len = len(s8.hexdigest())
    assert actual_len == 2, f"crc8 hexdigest should be 2 nibbles, got {actual_len}"


# ── construction surfaces & errors ────────────────────────────────────────


def test_from_info_and_raw_constructor_agree() -> None:
    """``from_info`` and the raw keyword constructor match ``from_name``."""
    # Arrange
    algo = ALGORITHMS["crc16-modbus"]

    # Act
    by_info = _digest(CrcStream.from_info(algo), [_DATA])
    by_raw = _digest(
        CrcStream(
            width=algo.width, poly=algo.poly, init=algo.init,
            refin=algo.refin, refout=algo.refout, xorout=algo.xorout,
        ),
        [_DATA],
    )

    # Assert
    assert by_info == by_raw == algo.check, "all construction paths must agree"


def test_unknown_algorithm_raises() -> None:
    """An unknown catalogue name is a clear ``KeyError``."""
    # Act / Assert
    with pytest.raises(KeyError, match="unknown algorithm"):
        crc_stream("definitely-not-a-crc")


def test_dirty_xorout_is_masked_to_width() -> None:
    """A dirty ``xorout`` (bits above the width) is masked at digest, on the
    public stream and the pure-Python backend, matching ``generic_crc``.

    Guards the finalize-masking fix: without it the pure-Python backend
    would leak the high xorout bits while the C/zlib backends would not.
    """
    # Arrange -- crc16-modbus params with an xorout bit above width 16.
    w, poly, init, refin, refout, xorout = 16, 0x8005, 0xFFFF, True, True, 1 << 20
    expected = generic_crc(_DATA, Crc(w, poly, init, refin, refout, xorout))
    assert expected < (1 << w), "reference result must be within the width"

    # Act / Assert -- public stream (whatever backend is selected here).
    s = CrcStream(
        width=w, poly=poly, init=init, refin=refin, refout=refout, xorout=xorout
    )
    s.update(_DATA)
    assert s.digest() == expected, "public stream must match generic_crc"
    assert s.digest() < (1 << w), "digest must stay within the width"

    # Act / Assert -- the pure-Python backend explicitly (the one that was lax).
    b = _PyBackend(w, poly, init, refin, refout, xorout)
    b.update(_DATA)
    assert b.digest() == expected, "pure-Python backend must mask to width too"
