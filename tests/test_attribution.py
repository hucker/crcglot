"""Tests for the acknowledgments / credits surface."""

from __future__ import annotations

import subprocess
import sys
from typing import Mapping

import pytest

from crcglot import ACKNOWLEDGMENTS, ATTRIBUTION


class TestAttributionString:
    """``ATTRIBUTION`` is the human-readable form printed by ``crcglot credits``."""

    def test_non_empty(self) -> None:
        # Assert
        assert ATTRIBUTION, "ATTRIBUTION is empty"

    def test_mentions_required_projects(self) -> None:
        # Arrange
        required = ("reveng", "zlib", "Rocksoft")
        # Act
        missing = [w for w in required if w not in ATTRIBUTION]
        # Assert
        assert missing == [], f"ATTRIBUTION missing required mentions: {missing}"

    def test_mentions_authors(self) -> None:
        # Arrange
        required_authors = ("Greg Cook", "Mark Adler", "Ross N. Williams")
        # Act
        missing = [a for a in required_authors if a not in ATTRIBUTION]
        # Assert
        assert missing == [], f"ATTRIBUTION missing author names: {missing}"


class TestAcknowledgmentsStructured:
    """``ACKNOWLEDGMENTS`` is the structured form for programmatic rendering."""

    def test_is_non_empty_tuple(self) -> None:
        # Assert
        assert isinstance(ACKNOWLEDGMENTS, tuple), (
            f"ACKNOWLEDGMENTS should be tuple, got {type(ACKNOWLEDGMENTS).__name__}"
        )
        assert len(ACKNOWLEDGMENTS) >= 3, (
            f"expected >= 3 entries, got {len(ACKNOWLEDGMENTS)}"
        )

    @pytest.mark.parametrize("entry", ACKNOWLEDGMENTS, ids=lambda e: e["name"])
    def test_entry_has_required_fields(self, entry: Mapping[str, str]) -> None:
        # Arrange
        required_keys = {"name", "author", "url", "role"}
        # Assert
        actual_keys = set(entry.keys())
        assert required_keys <= actual_keys, (
            f"missing keys in {entry}: required {required_keys}, got {actual_keys}"
        )
        for k in required_keys:
            v = entry[k]
            assert isinstance(v, str) and v, (
                f"field {k!r} should be a non-empty string in {entry}, got {v!r}"
            )

    def test_urls_look_like_urls(self) -> None:
        # Act
        for e in ACKNOWLEDGMENTS:
            url = e["url"]
            # Assert -- each must start with http:// or https://.
            assert url.startswith(("http://", "https://")), (
                f"url for {e['name']!r} doesn't look like a URL: {url!r}"
            )

    def test_specific_projects_present(self) -> None:
        # Arrange
        names = {e["name"] for e in ACKNOWLEDGMENTS}
        # Assert
        assert "reveng CRC catalogue" in names, f"missing reveng: {names}"
        assert "zlib" in names, f"missing zlib: {names}"
        assert any("Rocksoft" in n for n in names), f"missing Rocksoft: {names}"


class TestCreditsCli:
    """The ``crcglot credits`` subcommand prints ATTRIBUTION verbatim."""

    def test_credits_exits_zero_and_prints_content(self) -> None:
        # Act
        proc = subprocess.run(
            [sys.executable, "-m", "crcglot.cli", "credits"],
            capture_output=True,
            text=True,
        )
        # Assert
        assert proc.returncode == 0, (
            f"crcglot credits exit={proc.returncode}, stderr={proc.stderr}"
        )
        for required in ("reveng", "zlib", "Rocksoft", "Greg Cook", "Mark Adler"):
            assert required in proc.stdout, (
                f"crcglot credits stdout missing {required!r}: {proc.stdout[:200]}"
            )
