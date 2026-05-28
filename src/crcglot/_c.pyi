"""Type stub for the ``crcglot._c`` C extension.

The actual module is compiled from ``_c.c`` at install time.  This
stub lets static type-checkers (mypy, ty, pyright) reason about the
API even when the package is being analyzed without first building
the extension -- crucial when CI runs lint/typecheck before install,
or when a contributor edits Python code without running ``uv sync``
to rebuild the extension.

Keep in sync with the ``PyMethodDef`` table in ``_c.c``.
"""

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

    C-backed equivalent of ``crcglot.generic_crc``.  Same algorithm,
    same parameter conventions, ~50-200x faster on short buffers.

    Raises:
        ValueError: if ``width`` is not in ``[8, 64]``.
    """
    ...
