"""crcglot's CRC engine vs the independent two-oracle goldens, across the catalogue.

``src/crcglot/_vectors.py`` carries the CRC of four inputs for every catalogue
algorithm, computed by two independent engines (anycrc + crccheck) that had to
agree, anchored to reveng's published check (see ``scripts/gen_vectors.py``).
These tests assert crcglot reproduces every value -- one-shot and, for the large
inputs, streamed in chunks.  Pure-stdlib; no oracle needed to run.  A new
catalogue entry without a golden fails ``TestFixtureIntegrity`` -- regenerate
with ``uv run python scripts/gen_vectors.py``.
"""

from __future__ import annotations

import pytest

from crcglot import ALGORITHMS, CrcStream, generic_crc
from crcglot._vectors import VECTORS

# The four fixed inputs (reconstructed here; they are constants).
_INPUTS: dict[str, bytes] = {
    "empty": b"",
    "check": b"123456789",
    "all_bytes": bytes(range(256)),
    "binary_1k": bytes((i * 167 + 13) & 0xFF for i in range(1024)),
}

_CASES = [
    (name, inp, _INPUTS[inp], VECTORS[name][inp])
    for name in sorted(VECTORS) for inp in _INPUTS
]
_LARGE = ("all_bytes", "binary_1k")
_STREAM_CASES = [
    (name, inp, _INPUTS[inp], VECTORS[name][inp])
    for name in sorted(VECTORS) for inp in _LARGE
]

# The same 120-byte message (values 1..120), framed three ways that each probe a
# distinct boundary hazard in the streaming path.  Twelve divisor sizes would
# re-run one fold at clean boundaries twelve times; these three instead hit
# clean boundaries, a short trailing write, and a zero-length write -- the cases
# that actually differ.
_CHUNK_DATA = bytes(range(1, 121))


def _chunk_framings(data: bytes) -> dict[str, list[bytes]]:
    """Three write sequences for ``data``, keyed by the hazard each one probes."""
    return {
        # 8 divides 120: every write lands on a clean boundary.
        "fixed8-divisor": [data[i:i + 8] for i in range(0, len(data), 8)],
        # 7 does not: the last write is 120 % 7 == 1 byte (the tail path).
        "fixed7-remainder": [data[i:i + 7] for i in range(0, len(data), 7)],
        # A zero-length write mid-stream must not disturb the carried state.
        "empty-midstream": [data[:50], b"", data[50:]],
    }


_CHUNK_FRAMINGS = ("fixed8-divisor", "fixed7-remainder", "empty-midstream")
_CHUNK_CASES = [
    (name, framing) for name in sorted(VECTORS) for framing in _CHUNK_FRAMINGS
]


class TestFixtureIntegrity:
    def test_covers_every_catalogue_algorithm(self) -> None:
        # Assert -- a new catalogue entry without a golden fails here.
        actual = set(VECTORS)
        expected = set(ALGORITHMS)
        assert actual == expected, (
            f"_vectors / catalogue drift; missing={expected - actual}, "
            f"extra={actual - expected} -- regenerate with scripts/gen_vectors.py"
        )


class TestEngineMatchesGoldens:
    """One-shot ``generic_crc`` must equal the two-oracle golden, for every
    algorithm x every input."""

    @pytest.mark.parametrize(
        "name,inp,data,expected", _CASES, ids=[f"{c[0]}-{c[1]}" for c in _CASES]
    )
    def test_one_shot(
        self, name: str, inp: str, data: bytes, expected: int,
    ) -> None:
        actual = generic_crc(data, ALGORITHMS[name])
        hexw = (ALGORITHMS[name].width + 3) // 4
        assert actual == expected, (
            f"{name} on {inp!r}: engine=0x{actual:0{hexw}X} != "
            f"golden=0x{expected:0{hexw}X} (anycrc/crccheck)"
        )


class TestStreamingMatchesGoldens:
    """For the large inputs, the streaming engine (CrcStream, fed in chunks)
    must also equal the golden -- the realistic embedded path."""

    @pytest.mark.parametrize(
        "name,inp,data,expected", _STREAM_CASES,
        ids=[f"{c[0]}-{c[1]}" for c in _STREAM_CASES],
    )
    def test_streamed_in_chunks(
        self, name: str, inp: str, data: bytes, expected: int,
    ) -> None:
        stream = CrcStream.from_info(ALGORITHMS[name])
        for i in range(0, len(data), 64):
            stream.update(data[i:i + 64])
        actual = stream.digest()
        hexw = (ALGORITHMS[name].width + 3) // 4
        assert actual == expected, (
            f"{name} streamed on {inp!r}: digest=0x{actual:0{hexw}X} != "
            f"golden=0x{expected:0{hexw}X} (anycrc/crccheck)"
        )


class TestChunkingInvariance:
    """A 120-byte message must stream to the one-shot CRC no matter how the
    caller frames its writes.  Three framings probe the boundary hazards that
    actually differ: clean divisor chunks, a short trailing chunk (remainder),
    and a zero-length write mid-stream.  Pure-stdlib -- the invariant is the
    assertion, so no external oracle is needed."""

    @pytest.mark.parametrize(
        "name,framing", _CHUNK_CASES,
        ids=[f"{c[0]}-{c[1]}" for c in _CHUNK_CASES],
    )
    def test_chunked_equals_one_shot(self, name: str, framing: str) -> None:
        # Arrange -- the one-shot CRC is the reference every framing must match.
        algo = ALGORITHMS[name]
        expected = generic_crc(_CHUNK_DATA, algo)
        chunks = _chunk_framings(_CHUNK_DATA)[framing]
        # Act -- feed the message as this framing's sequence of writes.
        stream = CrcStream.from_info(algo)
        for chunk in chunks:
            stream.update(chunk)
        actual = stream.digest()
        # Assert
        hexw = (algo.width + 3) // 4
        assert actual == expected, (
            f"{name} framed {framing}: digest=0x{actual:0{hexw}X} != "
            f"one-shot=0x{expected:0{hexw}X}"
        )
