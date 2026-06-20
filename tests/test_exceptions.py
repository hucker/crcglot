"""The ``CrcglotError`` hierarchy and the catalogue-aware "unknown algorithm" help.

crcglot answers a missed algorithm name with a suggestion, not "go read the whole
list": a bare width (``crc16``) names its variant family, a typo gets a "did you
mean", and the "where to look next" pointer fits the surface (python / cli / mcp).
Every deliberate error also subclasses the conventional stdlib type, so old
``except ValueError`` handlers keep working while ``except CrcglotError`` now
catches the whole family.

The well-known shortlist (``crc16-modbus`` ahead of ``crc16-cdma2000``) is curated
in ``crcglot.catalogue``; ``TestWellKnownIntegrity`` keeps it from drifting away
from the catalogue it indexes.
"""

from __future__ import annotations

import pytest

from crcglot import (
    ALGORITHMS,
    CrcglotError,
    UnknownAlgorithmError,
    compute,
    crc_stream,
    suggest_algorithms,
)
from crcglot.catalogue import _WELL_KNOWN, unknown_algorithm_error


class TestExceptionHierarchy:
    """Every deliberate error is catchable both as ``CrcglotError`` and as the
    stdlib type it has always been, so adopting the base breaks no caller."""

    def test_unknown_algorithm_is_both_crcglot_error_and_value_error(self):
        # Act
        err = unknown_algorithm_error("nope")

        # Assert -- the new base and the historical stdlib type both apply.
        assert isinstance(err, CrcglotError), "is a CrcglotError"
        assert isinstance(err, ValueError), "is still a ValueError (back-compat)"

    def test_subclass_relationships(self):
        # Assert
        assert issubclass(UnknownAlgorithmError, CrcglotError), (
            "UnknownAlgorithmError is a CrcglotError"
        )
        assert issubclass(UnknownAlgorithmError, ValueError), (
            "UnknownAlgorithmError is a ValueError"
        )


class TestSuggestAlgorithms:
    """``suggest_algorithms`` tiers: exact prefix, bare-width family (well-known
    first), then fuzzy typo match; empty when nothing is close."""

    def test_prefix_match_resolves_a_partial_name(self):
        # Act
        actual = suggest_algorithms("crc16-mod")

        # Assert
        assert actual == ["crc16-modbus"], f"prefix names one variant, got {actual}"

    def test_bare_width_lists_the_family_well_known_first(self):
        # Act
        actual = suggest_algorithms("crc16")

        # Assert -- the recognized variant leads, not the alphabetical head.
        assert actual[0] == "crc16-modbus", f"well-known leads, got {actual[0]!r}"
        assert "crc16-xmodem" in actual, "more well-known variants follow"

    def test_separator_forms_match_the_bare_width(self):
        # Assert -- 'crc-16' and 'crc 16' resolve like 'crc16'.
        expected = suggest_algorithms("crc16")
        assert suggest_algorithms("crc-16") == expected, "dash form matches"
        assert suggest_algorithms("crc 16") == expected, "space form matches"

    def test_fuzzy_match_recovers_a_typo(self):
        # Act
        actual = suggest_algorithms("crc16-modbsu")  # transposed 'modbus'

        # Assert -- the intended name is the top suggestion.
        assert actual[0] == "crc16-modbus", f"typo recovers modbus, got {actual}"

    def test_fuzzy_tier_is_capped_tighter_than_the_family_tier(self):
        # Assert -- past the top match, fuzzy results are noise; cap at 3.
        actual = suggest_algorithms("crc16-modbsu")
        assert len(actual) <= 3, f"fuzzy tier capped at 3, got {len(actual)}"

    def test_no_close_match_returns_empty(self):
        # Assert
        actual = suggest_algorithms("zzzz-not-a-crc")
        assert actual == [], f"nothing close yields no suggestions, got {actual}"

    def test_empty_input_returns_empty(self):
        # Assert
        assert suggest_algorithms("") == [], "empty name yields no suggestions"


class TestUnknownAlgorithmMessage:
    """The built message: family framing for a bare width, "did you mean" for a
    typo, and a surface-appropriate pointer to look further."""

    def test_bare_width_names_the_family_with_a_count_and_example(self):
        # Act
        msg = str(unknown_algorithm_error("crc16"))

        # Assert -- explains the ambiguity (why bare crc16 has no default) and
        # leads with a recognized example.
        assert "CRC-16" in msg, f"names the width family, got {msg!r}"
        assert "31 variants" in msg, "states how many there are"
        assert "crc16-modbus" in msg, "shows a well-known example first"

    def test_typo_offers_did_you_mean(self):
        # Act
        msg = str(unknown_algorithm_error("crc16-modbsu"))

        # Assert
        assert "did you mean crc16-modbus" in msg, f"suggests the fix, got {msg!r}"

    @pytest.mark.parametrize(
        "surface,needle",
        [
            ("python", "crcglot.ALGORITHMS"),
            ("cli", "crcglot list"),
            ("mcp", "crc_list"),
        ],
        ids=["python", "cli", "mcp"],
    )
    def test_pointer_is_surface_specific(self, surface: str, needle: str):
        # Act
        msg = str(unknown_algorithm_error("crc16", surface=surface))

        # Assert -- each surface points at its own tool, not another's.
        assert needle in msg, f"{surface} surface points to {needle!r}; got {msg!r}"

    def test_unknown_surface_falls_back_to_python(self):
        # Assert -- a stray surface label does not crash; it uses the default.
        msg = str(unknown_algorithm_error("crc16", surface="bogus"))
        assert "crcglot.ALGORITHMS" in msg, "unknown surface falls back to python"


class TestWiring:
    """The public entry points raise the typed error, and a legacy
    ``except ValueError`` still catches it."""

    def test_compute_raises_unknown_algorithm_error(self):
        # Act / Assert
        with pytest.raises(UnknownAlgorithmError, match="unknown algorithm"):
            compute(b"123456789", "crc16")

    def test_crc_stream_raises_unknown_algorithm_error(self):
        # Act / Assert
        with pytest.raises(UnknownAlgorithmError, match="unknown algorithm"):
            crc_stream("crc16")

    def test_legacy_except_value_error_still_catches(self):
        # Assert -- the back-compat guarantee: old handlers keep working.
        with pytest.raises(ValueError):
            compute(b"123456789", "crc16")


class TestWellKnownIntegrity:
    """The curated shortlist must reference only real catalogue entries."""

    def test_every_well_known_name_exists_in_the_catalogue(self):
        # Assert -- guards against a rename/removal leaving a dangling example.
        missing = [name for name in _WELL_KNOWN if name not in ALGORITHMS]
        assert missing == [], (
            f"_WELL_KNOWN must reference catalogue entries; missing={missing}"
        )
