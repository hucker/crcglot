"""crcglot's engine vs both live oracles, on inputs the goldens never contained.

``test_independent_vectors`` pins the engine to the four *committed* goldens
(pure-stdlib, no oracle at test time); ``test_vectors_provenance`` re-runs the
golden generation itself.  What neither does is grade the engine on fresh data:
every committed value was chosen when ``scripts/gen_vectors.py`` last ran.
This file is the complement -- seeded random inputs, never seen by the golden
file, computed **live** by both oracles (anycrc + crccheck) which must agree
before the engine is graded against them.  It also grades the one parameter
region the catalogue barely reaches: asymmetric reflection (``refin != refout``
in both orders, with and without a final XOR); crc12-umts is the catalogue's
single asymmetric entry and covers only the refin=False/refout=True direction.

The oracles are dev-only dependencies, imported hard (via the generation
script, mirroring ``test_vectors_provenance``): a missing oracle must fail
loudly, never skip into a false green.  Inputs are drawn once at module scope
from a fixed seed, so the suite stays reproducible and the ids carry lengths.
"""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

import pytest

from crcglot import ALGORITHMS, custom_algorithm, generic_crc
from crcglot.catalogue import AlgorithmInfo

_GEN = Path(__file__).resolve().parent.parent / "scripts" / "gen_vectors.py"


def _load_gen_vectors():
    """Import ``scripts/gen_vectors.py`` by path (``scripts/`` is not a package).

    Executing the module body hard-imports anycrc and crccheck; this module
    reuses its oracle mapping (``anycrc.CRC`` construction, ``_crccheck_calc``)
    instead of re-encoding the parameter translation here.
    """
    spec = importlib.util.spec_from_file_location("gen_vectors", _GEN)
    assert spec is not None and spec.loader is not None, "gen_vectors must load"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_GV = _load_gen_vectors()

# Seeded random inputs, drawn once: reproducible run to run, but bytes the
# committed goldens have never contained.  Lengths span empty through a few
# hundred bytes so short-tail and long-run paths both get fresh data.
_RNG = random.Random(0x5EEDC5C)
_RANDOM_INPUTS: list[bytes] = [
    _RNG.randbytes(n) for n in (_RNG.randint(0, 300) for _ in range(4))
]

_CASES = [
    (name, idx) for name in sorted(ALGORITHMS) for idx in range(len(_RANDOM_INPUTS))
]
_CASE_IDS = [
    f"{name}-rnd{idx}-len{len(_RANDOM_INPUTS[idx])}" for name, idx in _CASES
]


def _oracle_value(algo: AlgorithmInfo, data: bytes) -> int:
    """The two-oracle CRC of ``data``: anycrc and crccheck, required to agree."""
    v_anycrc = _GV.anycrc.CRC(
        algo.width, algo.poly, algo.init, algo.refin, algo.refout, algo.xorout
    ).calc(data)
    v_crccheck = _GV._crccheck_calc(algo, data)
    assert v_anycrc == v_crccheck, (
        f"anycrc=0x{v_anycrc:X} != crccheck=0x{v_crccheck:X} on "
        f"{len(data)}-byte input: oracle regression, not a crcglot bug"
    )
    return v_anycrc


class TestCatalogueRandomDifferential:
    """Every catalogue algorithm, on seeded random inputs, must match the value
    both live oracles agree on.  The committed goldens cover four fixed inputs;
    these inputs are fresh, so an engine defect that happens to cancel out on
    the fixed inputs cannot hide here."""

    @pytest.mark.parametrize("name,idx", _CASES, ids=_CASE_IDS)
    def test_engine_matches_live_two_oracle_value(self, name: str, idx: int) -> None:
        # Arrange -- the two oracles must agree before the engine is graded.
        algo = ALGORITHMS[name]
        data = _RANDOM_INPUTS[idx]
        expected = _oracle_value(algo, data)

        # Act
        actual = generic_crc(data, algo)

        # Assert
        hexw = (algo.width + 3) // 4
        assert actual == expected, (
            f"{name} on random input {idx} ({len(data)} bytes): "
            f"engine=0x{actual:0{hexw}X} != two-oracle=0x{expected:0{hexw}X}"
        )


# Asymmetric reflection in both orders, with and without a final XOR, at three
# widths.  The catalogue reaches only refin=False/refout=True with xorout=0
# (crc12-umts); everything else here is unreachable through catalogue-driven
# tests and exists only as custom parameters.
_ASYMMETRIC: dict[str, AlgorithmInfo] = {
    "w8-refin-only": custom_algorithm(width=8, poly=0x07, refin=True, refout=False),
    "w8-refout-only-xor": custom_algorithm(
        width=8, poly=0x9B, init=0xFF, refin=False, refout=True, xorout=0xAA
    ),
    "w16-refin-only": custom_algorithm(
        width=16, poly=0x8005, init=0xFFFF, refin=True, refout=False
    ),
    "w16-refout-only-xor": custom_algorithm(
        width=16, poly=0x1021, refin=False, refout=True, xorout=0xFFFF
    ),
    "w32-refin-only-xor": custom_algorithm(
        width=32, poly=0x04C11DB7, init=0xFFFFFFFF, refin=True, refout=False,
        xorout=0xFFFFFFFF,
    ),
    "w32-refout-only": custom_algorithm(width=32, poly=0x04C11DB7, refin=False, refout=True),
}

# Empty (init/finalize path), the canonical check string, and one seeded
# random input long enough to leave the reflection interacting with real data.
_ASYMMETRIC_INPUTS: list[bytes] = [b"", b"123456789", random.Random(0xA5).randbytes(57)]


class TestAsymmetricReflectionDifferential:
    """Custom ``refin != refout`` parameter sets, both orders, must match the
    value both live oracles agree on.  ``AlgorithmInfo.check`` is computed by
    crcglot's own engine, so it is never the reference here; the oracles are."""

    @pytest.mark.parametrize("label", sorted(_ASYMMETRIC), ids=sorted(_ASYMMETRIC))
    def test_engine_matches_live_two_oracle_value(self, label: str) -> None:
        # Arrange -- the custom entry; graded against oracles, not .check.
        algo = _ASYMMETRIC[label]
        hexw = (algo.width + 3) // 4

        for data in _ASYMMETRIC_INPUTS:
            expected = _oracle_value(algo, data)

            # Act
            actual = generic_crc(data, algo)

            # Assert
            assert actual == expected, (
                f"{label} on {len(data)}-byte input: engine=0x{actual:0{hexw}X} "
                f"!= two-oracle=0x{expected:0{hexw}X}"
            )
