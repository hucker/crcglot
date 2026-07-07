"""The verb manifest (:data:`crcglot.VERBS`): completeness, derivation, lookup.

The manifest exists so frontends (crcglot's own MCP server, external tools
like termapy) render typed tools from one source instead of hand-rolling
parameter metadata.  These tests hold the registry to the same contract as
the other ``*Info`` registries (see ``test_catalogue.py::TestVariantInfo``):
every record complete, choices derived from the backing registries rather
than restated, the lookup helpful on a miss, and the whole structure
JSON-serializable, because external consumers take it as plain data.  The
MCP side of the contract (live tool schemas match the manifest) lives in
``test_mcp.py::TestVerbManifestDrift``.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from crcglot import (
    LANGUAGES,
    NAMING_ORDER,
    VARIANT_ORDER,
    VERBS,
    CrcglotError,
    UnknownVerbError,
    naming_info,
    variant_info,
    verb_info,
)
from crcglot.comments import COMMENT_STYLES, style_info

# The capability-matrix order (docs/api.md) the manifest must present.
_EXPECTED_ORDER = [
    "list",
    "info",
    "vectors",
    "detect",
    "identify_trailer",
    "reverse",
    "verify",
    "compute",
    "compute_many",
    "encode",
    "generate",
    "credits",
]

_VERB_IDS = sorted(VERBS)


class TestVerbManifest:
    """Every verb record is complete: no empty prose, no dangling references.

    A frontend renders tools directly from these fields, so an empty help
    string or an exclusive group naming an undeclared parameter surfaces as
    a broken tool in someone else's UI.
    """

    def test_canonical_order_matches_capability_matrix(self):
        # Assert -- insertion order is the presentation order consumers get.
        actual = list(VERBS)
        expected = _EXPECTED_ORDER
        assert actual == expected, (
            f"VERBS order {actual} != capability-matrix order {expected}"
        )

    def test_every_verbs_key_matches_its_record_name(self):
        for key, spec in VERBS.items():
            assert key == spec.name, f"VERBS[{key!r}] carries name {spec.name!r}"

    @pytest.mark.parametrize("verb", _VERB_IDS)
    def test_verb_has_nonempty_summary_and_description(self, verb):
        spec = VERBS[verb]
        assert spec.summary.strip(), f"{verb}: empty summary"
        assert spec.description.strip(), f"{verb}: empty description"

    @pytest.mark.parametrize("verb", _VERB_IDS)
    def test_every_param_has_nonempty_help(self, verb):
        for p in VERBS[verb].params:
            assert p.help.strip(), f"{verb}.{p.name}: empty help"

    @pytest.mark.parametrize("verb", _VERB_IDS)
    def test_every_choice_has_nonempty_description(self, verb):
        for p in VERBS[verb].params:
            for c in p.choices:
                assert c.description.strip(), (
                    f"{verb}.{p.name} choice {c.name!r}: empty description"
                )

    @pytest.mark.parametrize("verb", _VERB_IDS)
    def test_required_params_carry_no_default(self, verb):
        # A required parameter with a default is a contradiction a schema
        # renderer would have to guess its way around.
        for p in VERBS[verb].params:
            if p.required:
                assert p.default is None, (
                    f"{verb}.{p.name}: required but default={p.default!r}"
                )

    @pytest.mark.parametrize("verb", _VERB_IDS)
    def test_exclusive_groups_reference_declared_params(self, verb):
        spec = VERBS[verb]
        declared = {p.name for p in spec.params}
        for group in spec.mutually_exclusive:
            missing = set(group.params) - declared
            assert not missing, (
                f"{verb}: exclusive group {group.params} references "
                f"undeclared params {sorted(missing)}"
            )

    @pytest.mark.parametrize("verb", _VERB_IDS)
    def test_every_result_field_described(self, verb):
        spec = VERBS[verb]
        assert spec.result_fields, f"{verb}: no result fields documented"
        for f in spec.result_fields:
            assert f.description.strip(), (
                f"{verb} result field {f.name!r}: empty description"
            )

    def test_surface_mapping_complete_and_unique(self):
        # Assert -- every verb names its MCP tool, and no two share one.
        tools = [spec.mcp_tool for spec in VERBS.values()]
        assert all(t.startswith("crc_") for t in tools), (
            f"every mcp_tool must be crc_-prefixed; got {tools}"
        )
        assert len(set(tools)) == len(tools), f"duplicate mcp_tool in {tools}"

    @pytest.mark.parametrize("verb", _VERB_IDS)
    def test_param_types_use_the_closed_vocabulary(self, verb):
        allowed = {
            "string", "integer", "boolean", "object",
            "array[string]", "string | array[string]",
        }
        for p in VERBS[verb].params:
            assert p.type in allowed, (
                f"{verb}.{p.name}: type {p.type!r} outside the vocabulary "
                f"{sorted(allowed)}"
            )


class TestChoicesDerivation:
    """Registry-backed choices are derived, not restated.

    ``language`` / ``variant`` / ``naming`` / ``comment_style`` choices must
    track the registries that own those vocabularies, so a new language or
    style reaches the manifest with no edit here.
    """

    @staticmethod
    def _param(verb, name):
        return next(p for p in VERBS[verb].params if p.name == name)

    def test_language_choices_match_languages_registry(self):
        actual = [(c.name, c.description) for c in self._param("generate", "language").choices]
        expected = [(code, info.display_name) for code, info in LANGUAGES.items()]
        assert actual == expected, "language choices must mirror LANGUAGES"

    def test_variant_choices_are_auto_plus_variant_order(self):
        choices = self._param("generate", "variant").choices
        actual = [c.name for c in choices]
        expected = ["auto", *VARIANT_ORDER]
        assert actual == expected, f"variant choices {actual} != {expected}"
        for c in choices[1:]:
            assert c.description == variant_info(c.name).description, (
                f"variant {c.name!r} description must come from variant_info"
            )

    def test_naming_choices_match_naming_order(self):
        actual = [(c.name, c.description) for c in self._param("generate", "naming").choices]
        expected = [(n, naming_info(n).description) for n in NAMING_ORDER]
        assert actual == expected, "naming choices must mirror NAMING_ORDER"

    def test_comment_style_choices_match_comment_styles_registry(self):
        actual = [(c.name, c.description) for c in self._param("generate", "comment_style").choices]
        expected = [(s, style_info(s).description) for s in COMMENT_STYLES]
        assert actual == expected, "comment_style choices must mirror COMMENT_STYLES"

    def test_reverse_byte_order_keeps_the_endian_trio(self):
        """The reverse tool accepts 'both' for its CRC field byte order while
        encode/verify accept only big/little; the manifest must model that
        surface difference, not normalize it away."""
        actual_reverse = [c.name for c in self._param("reverse", "crc_byte_order").choices]
        actual_verify = [c.name for c in self._param("verify", "crc_byte_order").choices]
        assert actual_reverse == ["big", "little", "both"], (
            f"reverse.crc_byte_order choices: {actual_reverse}"
        )
        assert actual_verify == ["big", "little"], (
            f"verify.crc_byte_order choices: {actual_verify}"
        )


class TestVerbInfoLookup:
    """``verb_info`` follows the house error rules: echo, suggest, list the set."""

    def test_verb_info_returns_the_record(self):
        actual = verb_info("detect")
        expected = VERBS["detect"]
        assert actual is expected, "verb_info must return the registry record"

    def test_unknown_verb_lists_the_full_vocabulary(self):
        with pytest.raises(UnknownVerbError) as exc:
            verb_info("frobnicate")
        message = str(exc.value)
        for verb in VERBS:
            assert verb in message, f"error message must list {verb!r}: {message}"

    def test_unknown_verb_suggests_close_match(self):
        with pytest.raises(UnknownVerbError, match="did you mean 'detect'"):
            verb_info("detct")

    def test_unknown_verb_error_is_crcglot_and_value_error(self):
        with pytest.raises(UnknownVerbError) as exc:
            verb_info("nope")
        assert isinstance(exc.value, CrcglotError), "must derive from CrcglotError"
        assert isinstance(exc.value, ValueError), "must stay a ValueError"


class TestJsonSerializable:
    """External consumers take the manifest as plain data; asdict must JSON."""

    @pytest.mark.parametrize("verb", _VERB_IDS)
    def test_verbspec_round_trips_through_json(self, verb):
        # Act -- project to a dict, serialize, reload.
        spec = VERBS[verb]
        reloaded = json.loads(json.dumps(dataclasses.asdict(spec)))
        # Assert -- structure survives with names intact.
        assert reloaded["name"] == verb, f"{verb}: name lost in round trip"
        actual_params = [p["name"] for p in reloaded["params"]]
        expected_params = [p.name for p in spec.params]
        assert actual_params == expected_params, (
            f"{verb}: params lost in round trip"
        )
