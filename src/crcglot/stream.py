"""Incremental (streaming) CRC over chunked data.

The one-shot :func:`crcglot.generic_crc` needs the whole payload at once.
:class:`CrcStream` is the hashlib-style counterpart: bind the algorithm
once, feed bytes in chunks with :meth:`~CrcStream.update`, and read the
finalized value with :meth:`~CrcStream.digest` (non-destructive).  The
result is byte-identical to ``generic_crc`` and to the generated
``<fname>_init`` / ``_update`` / ``_finalize`` triple -- splitting the
input into chunks never changes the answer.

Backend dispatch mirrors :func:`generic_crc` (the fastest applicable path
wins, chosen once at construction):

  1. A zlib hardware fast-path for IEEE ``crc32`` / ``crc32-jamcrc`` --
     :func:`zlib.crc32` streams natively via ``crc32(chunk, running)``.
  2. The ``crcglot._c`` extension's ``CrcStream`` when built (slice-by-8).
  3. The pure-Python reference state machine otherwise -- always available.

Example:
    >>> from crcglot import crc_stream
    >>> s = crc_stream("crc32")
    >>> s.update(b"1234")
    >>> s.update(b"56789")
    >>> hex(s.digest())
    '0xcbf43926'
"""

from __future__ import annotations

import zlib
from typing import Protocol

from crcglot.catalogue import (
    ALGORITHMS,
    AlgorithmInfo,
    _ZLIB_FAST_PATHS,
    _reflect,
)

# Optional C-extension streaming backend.  Guarded exactly like
# ``catalogue._c_generic_crc`` so lint / typecheck / a source checkout all
# work before the extension is built.
try:
    from crcglot._c import CrcStream as _CCrcStream
except ImportError:  # pragma: no cover - exercised only without the extension
    _CCrcStream = None  # type: ignore[assignment,misc]  # ty: ignore[invalid-assignment]

_BytesLike = bytes | bytearray | memoryview


class _Backend(Protocol):
    """The interface every streaming backend (zlib / C / pure-Python) exposes."""

    def update(self, data: _BytesLike, /) -> None: ...
    def digest(self) -> int: ...
    def reset(self) -> None: ...
    def copy(self) -> _Backend: ...


class _ZlibBackend:
    """crc32 / jamcrc via stdlib :func:`zlib.crc32` (hardware-accelerated).

    ``zlib.crc32`` bakes in IEEE-32's init and final-XOR, so its running
    value is the finished crc32 of the data so far.  The catalogue's
    fast-path callable has the form ``zlib.crc32(data) ^ const``; ``const``
    is recovered once as ``fast_path(b"")`` and re-applied at digest, which
    turns crc32 into jamcrc (``^ 0xFFFFFFFF``) without a second table.
    """

    __slots__ = ("_const", "_state")

    def __init__(self, const: int, state: int = 0) -> None:
        self._const = const
        self._state = state

    def update(self, data: _BytesLike, /) -> None:
        self._state = zlib.crc32(data, self._state)

    def digest(self) -> int:
        return self._state ^ self._const

    def reset(self) -> None:
        self._state = 0

    def copy(self) -> _ZlibBackend:
        return _ZlibBackend(self._const, self._state)


class _PyBackend:
    """Pure-Python reference state machine.

    The streaming decomposition of :func:`crcglot.catalogue._generic_crc_python`:
    the running ``state`` is the bare register (init pre-reflected for
    reflected algorithms), ``update`` folds bytes in, and ``digest`` applies
    the output reflection + xorout to a *copy* so it stays non-destructive.
    """

    __slots__ = (
        "_width", "_poly", "_init", "_refin", "_refout", "_xorout",
        "_ref_poly", "_init_state", "_mask", "_msb", "_shift", "_state",
    )

    def __init__(
        self, width: int, poly: int, init: int,
        refin: bool, refout: bool, xorout: int,
    ) -> None:
        self._width = width
        self._poly = poly
        self._init = init
        self._refin = refin
        self._refout = refout
        self._xorout = xorout
        self._ref_poly = _reflect(poly, width)
        self._init_state = _reflect(init, width) if refin else init
        self._mask = (1 << width) - 1
        self._msb = 1 << (width - 1)
        self._shift = width - 8
        self._state = self._init_state

    def update(self, data: _BytesLike, /) -> None:
        crc = self._state
        if self._refin:
            ref_poly = self._ref_poly
            for byte in data:
                crc ^= byte
                for _ in range(8):
                    crc = (crc >> 1) ^ ref_poly if crc & 1 else crc >> 1
        elif self._width >= 8:
            poly, msb, mask, shift = self._poly, self._msb, self._mask, self._shift
            for byte in data:
                crc ^= byte << shift
                for _ in range(8):
                    crc = (crc << 1) ^ poly if crc & msb else crc << 1
                crc &= mask
        else:
            # Sub-byte non-reflected: bit-by-bit, MSB first (the byte-aligned
            # ``byte << (width - 8)`` fold underflows for width < 8).
            poly, msb, mask = self._poly, self._msb, self._mask
            for byte in data:
                for i in range(7, -1, -1):
                    bit = (byte >> i) & 1
                    if (crc & msb != 0) ^ (bit != 0):
                        crc = ((crc << 1) ^ poly) & mask
                    else:
                        crc = (crc << 1) & mask
        self._state = crc

    def digest(self) -> int:
        crc = self._state
        if self._refout != self._refin:
            crc = _reflect(crc, self._width)
        # Mask to width (matches the C backend and generic_crc) so a dirty
        # xorout can't leak bits above the width.
        return (crc ^ self._xorout) & self._mask

    def reset(self) -> None:
        self._state = self._init_state

    def copy(self) -> _PyBackend:
        clone = _PyBackend(
            self._width, self._poly, self._init,
            self._refin, self._refout, self._xorout,
        )
        clone._state = self._state
        return clone


def _make_backend(
    width: int, poly: int, init: int,
    refin: bool, refout: bool, xorout: int,
) -> _Backend:
    """Pick the fastest applicable streaming backend for these parameters."""
    params = (width, poly, init, refin, refout, xorout)
    fast_path = _ZLIB_FAST_PATHS.get(params)
    if fast_path is not None:
        return _ZlibBackend(fast_path(b""))
    # The C extension's domain is width in [8, 64]; sub-byte CRCs fall back
    # to the pure-Python backend, which handles any width (bit-identical).
    if _CCrcStream is not None and 8 <= width <= 64:
        return _CCrcStream(
            width=width, poly=poly, init=init,
            refin=refin, refout=refout, xorout=xorout,
        )
    return _PyBackend(width, poly, init, refin, refout, xorout)


class CrcStream:
    """Incremental CRC over chunked data (hashlib-style).

    Binds the Rocksoft/Williams parameters once, then accepts the message in
    any number of :meth:`update` chunks; :meth:`digest` reads the finalized
    value and may be called repeatedly.  The backend (zlib / C extension /
    pure-Python) is chosen at construction -- see the module docstring.

    Prefer :func:`crc_stream` or :meth:`from_name` for catalogue algorithms;
    the keyword constructor is the low-level path for custom CRCs (its
    signature matches the C extension's ``CrcStream``).

    Example:
        >>> s = CrcStream.from_name("crc16-modbus")
        >>> s.update(b"123456789")
        >>> hex(s.digest())
        '0x4b37'
    """

    __slots__ = ("_backend", "_width")

    def __init__(
        self,
        *,
        width: int,
        poly: int,
        init: int,
        refin: bool = False,
        refout: bool = False,
        xorout: int = 0,
    ) -> None:
        """Build a stream from raw Rocksoft/Williams parameters.

        Args:
            width: CRC bit width (8, 16, 32, 64).
            poly: Generator polynomial in normal (MSB-first) form.
            init: Initial register value.
            refin: Reflect each input byte.
            refout: Reflect the final CRC value.
            xorout: XOR applied to the final CRC value.
        """
        self._backend = _make_backend(width, poly, init, refin, refout, xorout)
        self._width = width

    @classmethod
    def from_info(cls, algo: AlgorithmInfo) -> CrcStream:
        """Build a stream from an :class:`~crcglot.AlgorithmInfo`.

        Examples:
            >>> from crcglot import ALGORITHMS, CrcStream
            >>> CrcStream.from_info(ALGORITHMS["crc32"]).update(b"123456789")
        """
        return cls(
            width=algo.width, poly=algo.poly, init=algo.init,
            refin=algo.refin, refout=algo.refout, xorout=algo.xorout,
        )

    @classmethod
    def from_name(cls, name: str) -> CrcStream:
        """Build a stream for a catalogue algorithm by name.

        Args:
            name: Algorithm name from :data:`crcglot.ALGORITHMS`.

        Raises:
            KeyError: Unknown algorithm name.

        Examples:
            >>> CrcStream.from_name("crc32").hexdigest()
            '00000000'
        """
        try:
            algo = ALGORITHMS[name]
        except KeyError:
            raise KeyError(
                f"unknown algorithm {name!r}; see crcglot.ALGORITHMS for the "
                "catalogue"
            ) from None
        return cls.from_info(algo)

    def update(self, data: _BytesLike, /) -> None:
        """Fold ``data`` into the running CRC state.  May be called repeatedly."""
        self._backend.update(data)

    def digest(self) -> int:
        """Return the finalized CRC of everything fed so far (non-destructive)."""
        return self._backend.digest()

    def hexdigest(self) -> str:
        """Return :meth:`digest` as a zero-padded lowercase hex string."""
        return format(self.digest(), f"0{(self._width + 3) // 4}x")

    def reset(self) -> None:
        """Reset the running state to the initial value for reuse."""
        self._backend.reset()

    def copy(self) -> CrcStream:
        """Return an independent copy with the same parameters and state."""
        clone = CrcStream.__new__(CrcStream)
        clone._backend = self._backend.copy()
        clone._width = self._width
        return clone


def crc_stream(name: str) -> CrcStream:
    """Open a streaming CRC for a catalogue algorithm (the common entry point).

    Thin convenience for :meth:`CrcStream.from_name`.

    Args:
        name: Algorithm name from :data:`crcglot.ALGORITHMS`.

    Returns:
        A fresh :class:`CrcStream` bound to ``name``.

    Raises:
        KeyError: Unknown algorithm name.

    Examples:
        >>> from crcglot import crc_stream
        >>> s = crc_stream("crc32")
        >>> for chunk in (b"1234", b"56789"):
        ...     s.update(chunk)
        >>> hex(s.digest())
        '0xcbf43926'
    """
    return CrcStream.from_name(name)
