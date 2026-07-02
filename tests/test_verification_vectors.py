"""Tests for the public self-test-vector accessor (``crcglot.self_test_vectors``).

The four goldens (empty / check / all_bytes / binary_1k) are generated offline
into :mod:`crcglot._vectors` by two agreeing engines and reveng-anchored at
``check``.  These tests assert the public record is a faithful, runnable view of
that generated data: its fields equal the committed goldens, and each field
equals crcglot's own CRC of the matching :data:`SELF_TEST_INPUTS` entry, so the
inputs and expecteds can never silently drift from the engine.
"""

from __future__ import annotations

import pytest

import crcglot
from crcglot import (
    ALGORITHMS,
    SELF_TEST_INPUTS,
    SelfTestVectors,
    UnknownAlgorithmError,
    custom_algorithm,
    self_test_vectors,
)
from crcglot._vectors import VECTORS
from crcglot.catalogue import generic_crc

_ALGO_IDS = sorted(ALGORITHMS)


class TestSelfTestVectorsContent:
    """The record reproduces the committed goldens and is a runnable view."""

    def test_fields_match_committed_goldens(self):
        # Act
        actual = self_test_vectors("crc32")
        # Assert
        expected = VECTORS["crc32"]
        assert actual == SelfTestVectors(**expected), (
            f"crc32 vectors {actual} != committed goldens {expected}"
        )

    @pytest.mark.parametrize("name", _ALGO_IDS)
    def test_every_field_is_the_engine_crc_of_its_input(self, name):
        """Each field == generic_crc(SELF_TEST_INPUTS[field], algo), so the
        exposed inputs and expecteds agree with crcglot's own engine."""
        # Arrange
        algo = ALGORITHMS[name]
        vectors = self_test_vectors(name)
        assert vectors is not None, f"{name}: catalogue algorithm must have goldens"
        # Act / Assert -- one check per input, values compared as actual==expected
        for input_name, data in SELF_TEST_INPUTS.items():
            actual = getattr(vectors, input_name)
            expected = generic_crc(data, algo)
            assert actual == expected, (
                f"{name}.{input_name}: record {actual:#x} != engine {expected:#x}"
            )

    @pytest.mark.parametrize("name", _ALGO_IDS)
    def test_check_field_matches_identity_check(self, name):
        """The exposed ``check`` and AlgorithmInfo.check cannot diverge."""
        # Arrange
        vectors = self_test_vectors(name)
        assert vectors is not None, f"{name}: catalogue algorithm must have goldens"
        # Act
        actual = vectors.check
        # Assert
        expected = ALGORITHMS[name].check
        assert actual == expected, (
            f"{name}: vectors.check {actual:#x} != AlgorithmInfo.check {expected:#x}"
        )


class TestSelfTestVectorsLookup:
    """Accepting a name or an AlgorithmInfo, and the custom / unknown paths."""

    def test_algorithm_info_argument_matches_name_argument(self):
        # Act
        by_info = self_test_vectors(ALGORITHMS["crc8"])
        by_name = self_test_vectors("crc8")
        # Assert
        assert by_info == by_name, (
            f"AlgorithmInfo lookup {by_info} != name lookup {by_name}"
        )

    def test_non_catalogue_custom_returns_none(self):
        """A custom polynomial not in the catalogue has no independent goldens."""
        # Arrange -- poly 0x1337 / init 0xABCD is not a catalogue entry.
        custom = custom_algorithm(
            width=16, poly=0x1337, init=0xABCD, refin=False, refout=False, xorout=0
        )
        # Act
        actual = self_test_vectors(custom)
        # Assert
        assert actual is None, f"custom algorithm should have no goldens, got {actual}"

    def test_custom_matching_catalogue_params_resolves_by_parameters(self):
        """A custom built with a catalogue entry's exact params is that entry."""
        # Arrange -- these are crc16-xmodem's parameters.
        dup = custom_algorithm(width=16, poly=0x1021)
        # Act
        actual = self_test_vectors(dup)
        # Assert
        expected = self_test_vectors("crc16-xmodem")
        assert actual == expected, (
            f"param-equal custom {actual} != crc16-xmodem {expected}"
        )

    def test_unknown_name_raises_unknown_algorithm_error(self):
        # Act / Assert
        with pytest.raises(UnknownAlgorithmError, match="'crc16'") as excinfo:
            self_test_vectors("crc16")
        # Assert -- also a ValueError for back-compat, echoing the bad value.
        assert isinstance(excinfo.value, ValueError), (
            "UnknownAlgorithmError must remain a ValueError"
        )


class TestPublicSurface:
    """The three names are exported and lazily resolvable from ``crcglot``."""

    @pytest.mark.parametrize(
        "name", ["SELF_TEST_INPUTS", "SelfTestVectors", "self_test_vectors"]
    )
    def test_name_is_public(self, name):
        # Assert
        assert name in crcglot.__all__, f"{name} missing from crcglot.__all__"
        assert hasattr(crcglot, name), f"{name} not resolvable on crcglot"

    def test_inputs_have_the_four_expected_keys(self):
        # Act
        actual = set(SELF_TEST_INPUTS)
        # Assert
        expected = {"empty", "check", "all_bytes", "binary_1k"}
        assert actual == expected, f"SELF_TEST_INPUTS keys {actual} != {expected}"
