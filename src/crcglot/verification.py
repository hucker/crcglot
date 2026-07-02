"""Public, typed access to a catalogue algorithm's self-test vectors.

The embedded ``self_test`` in generated code drives four fixed inputs and checks
the CRC of each against an expected value.  Those expected values are computed
offline by two independent engines (anycrc + crccheck) that had to agree, with
the ``check`` input anchored to reveng, and stored in the generated
:mod:`crcglot._vectors`.  Only ``check`` was reachable from the public surface
(on :class:`~crcglot.AlgorithmInfo`); the other three were not.

This module exposes all four as first-class data: :data:`SELF_TEST_INPUTS` (the
inputs) and :func:`self_test_vectors` (the expected CRC of each, as a typed
:class:`SelfTestVectors` record).  The record is a runnable view: for a
catalogue algorithm, ``crc(SELF_TEST_INPUTS[name]) == getattr(vectors, name)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from crcglot.catalogue import ALGORITHMS, AlgorithmInfo, unknown_algorithm_error
from crcglot._vectors import goldens_for

#: The four fixed inputs the embedded self-test drives, by name.  The single
#: public definition of what the goldens are computed over; ``empty`` exercises
#: init and finalize with no data, ``check`` is the reveng canonical string,
#: ``all_bytes`` feeds every byte value 0..255, and ``binary_1k`` is a 1 KiB
#: pseudo-random pattern for length / carry coverage.
SELF_TEST_INPUTS: dict[str, bytes] = {
    "empty": b"",
    "check": b"123456789",
    "all_bytes": bytes(range(256)),
    "binary_1k": bytes((i * 167 + 13) & 0xFF for i in range(1024)),
}


@dataclass(frozen=True)
class SelfTestVectors:
    """The four self-test goldens for one algorithm: the CRC of each fixed input.

    Each field is the expected CRC of the same-named :data:`SELF_TEST_INPUTS`
    entry, so the record is directly runnable against an implementation:
    ``crc(SELF_TEST_INPUTS["all_bytes"]) == vectors.all_bytes``.  The ``check``
    field equals :attr:`~crcglot.AlgorithmInfo.check` for the same algorithm.

    Attributes:
        empty: CRC of ``b""`` (init and finalize, no data folded).
        check: CRC of ``b"123456789"`` (the reveng canonical check value).
        all_bytes: CRC of ``bytes(range(256))`` (every byte value).
        binary_1k: CRC of a 1 KiB pseudo-random pattern.
    """

    empty: int
    check: int
    all_bytes: int
    binary_1k: int


def self_test_vectors(algorithm: str | AlgorithmInfo) -> SelfTestVectors | None:
    """The self-test goldens for a catalogue algorithm, or ``None`` if custom.

    Resolves by the algorithm's Rocksoft/Williams parameters (so a renamed
    catalogue entry still resolves), returning the independently-generated
    expected CRCs for the four :data:`SELF_TEST_INPUTS`.  A custom polynomial
    that is not in the catalogue has no independently-generated goldens, so this
    returns ``None`` there.

    Args:
        algorithm: A catalogue name (e.g. ``"crc32"``) or an
            :class:`~crcglot.AlgorithmInfo`.

    Returns:
        A :class:`SelfTestVectors`, or ``None`` for a custom / non-catalogue
        algorithm.

    Raises:
        UnknownAlgorithmError: ``algorithm`` is a name not in the catalogue
            (also a ``ValueError``; the message echoes the value and suggests a
            close match).

    Examples:
        >>> from crcglot import self_test_vectors
        >>> v = self_test_vectors("crc32")
        >>> hex(v.check)
        '0xcbf43926'
        >>> self_test_vectors("crc16-modbus").all_bytes
        56940
    """
    if isinstance(algorithm, str):
        algo = ALGORITHMS.get(algorithm)
        if algo is None:
            raise unknown_algorithm_error(algorithm)
    else:
        algo = algorithm
    goldens = goldens_for(algo)
    if goldens is None:
        return None
    return SelfTestVectors(
        empty=goldens["empty"],
        check=goldens["check"],
        all_bytes=goldens["all_bytes"],
        binary_1k=goldens["binary_1k"],
    )
