"""Tests for the per-language naming-convention axis.

Covers the four layers the feature spans: the casing helper
(:func:`crcglot._helpers.crc_function_names`), the metadata axis
(:class:`NamingInfo` / :func:`naming_info` / ``LanguageInfo.naming``), the
validation seam (:func:`naming_convention_for`), and the generators emitting
the cased public function names while keeping internal symbols snake.

Mirrors ``test_comments.py`` in spirit: structural assertions over the public
surface, not execution (the slow tier proves the cased code compiles + runs).
"""

from __future__ import annotations

import pytest

from crcglot import (
    LANGUAGES,
    NAMING_ORDER,
    NamingInfo,
    naming_info,
)
from crcglot._helpers import _func_name, crc_function_names
from crcglot.targets import naming_convention_for

# The idiomatic default and full offered set per language -- the contract a
# UI / CLI reads.  Kept here as the single expectation the tests assert.
_EXPECTED = {
    "c": ("snake", {"snake", "camel", "pascal"}),
    "csharp": ("pascal", {"pascal", "camel"}),
    "go": ("pascal", {"pascal", "camel"}),
    "java": ("camel", {"camel", "pascal"}),
    "python": ("snake", {"snake"}),
    "rust": ("snake", {"snake"}),
    "typescript": ("camel", {"snake", "camel", "pascal"}),
    "verilog": ("snake", {"snake"}),
    "vhdl": ("snake", {"snake"}),
}


def _flat(out: str | tuple[str, str]) -> str:
    """Join C's (header, source) pair so assertions stay language-uniform."""
    return "\n".join(out) if isinstance(out, tuple) else out


# ── the casing helper ────────────────────────────────────────────────────


def test_crc_function_names_snake_is_legacy() -> None:
    """Snake reproduces the historical ``<base>_<role>`` identifiers."""
    # Act
    actual = crc_function_names("crc16_modbus", "snake")

    # Assert
    expected = {
        "oneshot": "crc16_modbus",
        "init": "crc16_modbus_init",
        "update": "crc16_modbus_update",
        "finalize": "crc16_modbus_finalize",
        "self_test": "crc16_modbus_self_test",
    }
    assert actual == expected, "snake join must match the legacy names"


def test_crc_function_names_pascal_and_camel() -> None:
    """Pascal/camel re-case every token; self_test is two suffix tokens."""
    # Act
    pascal = crc_function_names("crc16_modbus", "pascal")
    camel = crc_function_names("crc16_modbus", "camel")

    # Assert
    assert pascal["update"] == "Crc16ModbusUpdate", "pascal joins TitleCase tokens"
    assert pascal["self_test"] == "Crc16ModbusSelfTest", "self_test = self+test tokens"
    assert pascal["oneshot"] == "Crc16Modbus", "oneshot has no role suffix"
    assert camel["update"] == "crc16ModbusUpdate", "camel lowercases the first token"
    assert camel["self_test"] == "crc16ModbusSelfTest", "camel self_test"
    assert camel["oneshot"] == "crc16Modbus", "camel oneshot"


def test_crc_function_names_single_token_oneshot_is_invariant() -> None:
    """A single-token one-shot is the same under snake/camel; pascal capitalizes.

    This is why Java/TypeScript (camel default) have far less test churn than
    Go/C# (pascal) -- single-token bare names like ``crc32`` are unchanged.
    """
    # Act / Assert
    assert crc_function_names("crc32", "snake")["oneshot"] == "crc32", "snake crc32"
    assert crc_function_names("crc32", "camel")["oneshot"] == "crc32", "camel crc32"
    assert crc_function_names("crc32", "pascal")["oneshot"] == "Crc32", "pascal Crc32"


def test_crc_function_names_acronym_digit_edges() -> None:
    """Multi-segment catalogue names tokenize on digits/dashes cleanly."""
    # Act -- crc32-base91-d -> crc32_base91_d -> three tokens.
    actual = crc_function_names(_func_name("crc32-base91-d"), "pascal")["update"]

    # Assert
    expected = "Crc32Base91DUpdate"
    assert actual == expected, "each dash/dot-delimited token is TitleCased"


def test_crc_function_names_override_is_verbatim() -> None:
    """An explicit ``symbol=`` is emitted verbatim, ignoring the convention."""
    # Act -- is_override forces snake-style join and preserves case.
    actual = crc_function_names("myCheck", "pascal", is_override=True)

    # Assert
    assert actual["oneshot"] == "myCheck", "override one-shot is verbatim"
    assert actual["self_test"] == "myCheck_self_test", "override keeps snake suffix"


# ── metadata axis ────────────────────────────────────────────────────────


def test_naming_order() -> None:
    """The canonical scan order is snake, camel, pascal."""
    # Assert
    assert NAMING_ORDER == ("snake", "camel", "pascal"), "canonical naming order"


@pytest.mark.parametrize("name", NAMING_ORDER)
def test_naming_info_records(name: str) -> None:
    """Each convention has a ``NamingInfo`` with a label and description."""
    # Act
    info = naming_info(name)

    # Assert
    assert isinstance(info, NamingInfo), f"{name}: a NamingInfo record"
    assert info.name == name, f"{name}: name round-trips"
    assert info.label and info.description, f"{name}: label/description populated"


def test_naming_info_unknown_raises() -> None:
    """An unknown convention is a KeyError (mirrors ``variant_info``)."""
    # Act / Assert
    with pytest.raises(KeyError):
        naming_info("kebab")


@pytest.mark.parametrize("code", sorted(LANGUAGES))
def test_language_default_and_offered_set(code: str) -> None:
    """Each language declares the expected idiomatic default + offered set."""
    # Arrange
    expected_default, expected_set = _EXPECTED[code]
    info = LANGUAGES[code]

    # Act / Assert
    assert info.default_naming == expected_default, f"{code}: idiomatic default"
    assert set(info.naming) == expected_set, f"{code}: offered convention set"
    assert info.default_naming in info.naming, f"{code}: default is offered"


@pytest.mark.parametrize("code", sorted(LANGUAGES))
def test_naming_infos_ordered(code: str) -> None:
    """``.naming_infos`` returns the offered conventions in NAMING_ORDER."""
    # Act
    actual = tuple(n.name for n in LANGUAGES[code].naming_infos)

    # Assert
    expected = tuple(n for n in NAMING_ORDER if n in LANGUAGES[code].naming)
    assert actual == expected, f"{code}: naming_infos ordered by NAMING_ORDER"


# ── validation seam ──────────────────────────────────────────────────────


def test_naming_convention_for_accepts_offered() -> None:
    """A convention the language offers passes through unchanged."""
    # Act / Assert
    assert naming_convention_for("go", "pascal") == "pascal", "go offers pascal"


def test_naming_convention_for_rejects_unsupported() -> None:
    """A real convention a language does not offer is rejected helpfully."""
    # Act / Assert -- Rust is snake-only.
    with pytest.raises(ValueError, match="not valid for language 'rust'"):
        naming_convention_for("rust", "pascal")


def test_naming_convention_for_rejects_unknown() -> None:
    """An entirely unknown convention name is rejected."""
    # Act / Assert
    with pytest.raises(ValueError, match="unknown naming convention"):
        naming_convention_for("c", "kebab")


# ── generator integration ────────────────────────────────────────────────


@pytest.mark.parametrize("code", sorted(LANGUAGES))
def test_default_generation_uses_idiomatic_name(code: str) -> None:
    """With no ``naming=``, the update function uses the language default."""
    # Act
    src = _flat(LANGUAGES[code].generator("crc16-modbus"))
    expected = crc_function_names(
        _func_name("crc16-modbus"), LANGUAGES[code].default_naming
    )["update"]

    # Assert
    assert expected in src, f"{code}: default emits {expected!r}"


@pytest.mark.parametrize("code", sorted(LANGUAGES))
def test_every_offered_convention_emits_its_casing(code: str) -> None:
    """Each offered convention emits its correctly-cased update name."""
    # Act / Assert
    for conv in LANGUAGES[code].naming:
        src = _flat(LANGUAGES[code].generator("crc16-modbus", naming=conv))
        expected = crc_function_names(_func_name("crc16-modbus"), conv)["update"]
        assert expected in src, f"{code}/{conv}: emits {expected!r}"


def test_generator_rejects_unsupported_naming() -> None:
    """Asking a language for a convention it lacks raises before emitting."""
    # Act / Assert -- the load-bearing example from the plan.
    with pytest.raises(ValueError, match="not valid for language 'rust'"):
        LANGUAGES["rust"].generator("crc32", naming="pascal")


def test_internal_symbols_stay_snake_under_pascal() -> None:
    """Table symbols / guards keep the snake ``base`` even when funcs are Pascal."""
    # Act -- C offers pascal; the guard and table must NOT re-case.
    header, source = LANGUAGES["c"].generator("crc16-modbus", variant="table", naming="pascal")

    # Assert
    assert "Crc16ModbusUpdate(" in header, "functions are PascalCase"
    assert "CRC16_MODBUS_H" in header, "header guard stays SCREAMING_SNAKE on base"
    assert "crcglot_table_crc16_modbus" in source, "table symbol stays snake on base"
