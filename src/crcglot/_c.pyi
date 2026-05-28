"""Type stub for the ``crcglot._c`` C extension.

The actual module is compiled from ``_c.c`` at install time.  This
stub lets static type-checkers (mypy, ty, pyright) reason about the
API even when the package is being analyzed without first building
the extension -- crucial when CI runs lint/typecheck before install,
or when a contributor edits Python code without running ``uv sync``
to rebuild the extension.

Keep in sync with the ``PyMethodDef`` table in ``_c.c``.
"""

from collections.abc import Sequence
from typing import Union

_BytesLike = Union[bytes, bytearray, memoryview]


def c_generic_crc(
    data: _BytesLike,
    width: int,
    poly: int,
    init: int,
    refin: bool,
    refout: bool,
    xorout: int,
    /,
) -> int:
    """Compute CRC using Rocksoft/Williams parameterization.

    C-backed equivalent of ``crcglot.generic_crc``.  Auto-selects
    slice-by-8 / table-driven / bit-by-bit by width and caches tables
    per (width, poly, refin).

    Raises:
        ValueError: if ``width`` is not in ``[8, 64]``.
        MemoryError: if a lookup-table allocation fails.
    """
    ...


def c_crc_many(
    buffers: Sequence[_BytesLike],
    width: int,
    poly: int,
    init: int,
    refin: bool,
    refout: bool,
    xorout: int,
    /,
) -> list[int]:
    """CRC of each bytes-like object in ``buffers``, in order.

    Equivalent to ``[c_generic_crc(b, ...) for b in buffers]`` but pays
    the Python->C transition and table fetch once for the whole batch
    -- the win for high-volume small-buffer workloads.

    Raises:
        ValueError: if ``width`` is not in ``[8, 64]``.
        TypeError: if an element isn't bytes-like.
        MemoryError: if a lookup-table allocation fails.
    """
    ...


class CrcStream:
    """Incremental CRC over chunked data (hashlib-style).

    Binds the Rocksoft/Williams parameters once; ``update`` runs the
    tight engine per chunk.  Not thread-safe for concurrent mutation
    of one object.
    """

    def __init__(
        self,
        *,
        width: int,
        poly: int,
        init: int,
        refin: bool = False,
        refout: bool = False,
        xorout: int = 0,
    ) -> None: ...
    def update(self, data: _BytesLike, /) -> None:
        """Feed bytes-like ``data`` into the running CRC state."""
        ...
    def digest(self) -> int:
        """Finalized CRC of everything fed so far (non-destructive)."""
        ...
    def reset(self) -> None:
        """Reset running state to the initial value for reuse."""
        ...
    def copy(self) -> "CrcStream":
        """Independent copy with the same params and current state."""
        ...
