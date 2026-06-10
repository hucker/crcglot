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
