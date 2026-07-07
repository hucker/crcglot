"""The verb manifest: every crcglot verb and its parameters as plain data.

``VERBS`` describes the twelve crcglot verbs (detect, reverse, generate, ...)
the way a frontend needs them: name, summary, guidance prose, parameters with
types / defaults / choices / one-line help, mutual-exclusion groups, and the
result-dict fields.  crcglot's own MCP server renders its tool descriptions
from these records, and an external frontend (a CLI wrapper, another MCP
server, a UI) can render typed tools from the same source instead of
hand-rolling parameter metadata that drifts.

Everything here is a frozen dataclass built from str / int / bool / tuple, so
``dataclasses.asdict(spec)`` JSON-serializes directly:

    >>> import dataclasses, json
    >>> from crcglot import VERBS
    >>> _ = json.dumps(dataclasses.asdict(VERBS["detect"]))

Choices for registry-backed parameters (language, variant, naming,
comment_style) are derived from the registries (``LANGUAGES``,
``VARIANT_ORDER``, ``NAMING_ORDER``, ``COMMENT_STYLES``) at import time, so
adding a language or style updates the manifest automatically.  The four
wire-level enums with no registry of their own (endian, match, crc_byte_order,
packet_format) are defined here, their single home.

Defaults are the *tool-surface* defaults (what the MCP tools and any manifest
consumer should present); where a Python function's own default differs
deliberately (``reverse_packets(std_algo_only=True)`` vs the tool's
``std_algo_only=False``), the manifest records the tool surface.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from crcglot.comments import COMMENT_STYLES, style_info
from crcglot.exceptions import UnknownVerbError
from crcglot.targets import (
    LANGUAGES,
    NAMING_ORDER,
    VARIANT_ORDER,
    naming_info,
    variant_info,
)


@dataclass(frozen=True)
class ChoiceInfo:
    """One legal value of an enum parameter, with a one-line description."""

    name: str
    description: str


@dataclass(frozen=True)
class ParamSpec:
    """One parameter of a verb, as rendered on a typed-tool surface.

    ``type`` uses a small closed vocabulary: ``"string"``, ``"integer"``,
    ``"boolean"``, ``"object"``, ``"array[string]"``, or
    ``"string | array[string]"``.  An empty ``choices`` tuple means the value
    is open.
    """

    name: str
    type: str
    required: bool
    help: str
    default: str | int | bool | None = None
    choices: tuple[ChoiceInfo, ...] = ()


@dataclass(frozen=True)
class ExclusiveGroup:
    """Parameters that may not be combined.

    ``required=True`` means exactly one of ``params`` must be supplied;
    ``required=False`` means at most one.
    """

    params: tuple[str, ...]
    required: bool


@dataclass(frozen=True)
class ResultField:
    """One top-level key of the verb's result dict."""

    name: str
    description: str


@dataclass(frozen=True)
class VerbSpec:
    """Everything a frontend needs to render one crcglot verb as a typed tool.

    ``description`` is the full guidance prose the MCP tool ships;
    ``summary`` is the one-line form.  ``mcp_tool`` / ``cli_command`` /
    ``python_api`` cross-reference the same verb on the other surfaces
    (``None`` where a surface has no single equivalent).
    """

    name: str
    summary: str
    description: str
    mcp_tool: str
    cli_command: str | None
    python_api: str | None
    params: tuple[ParamSpec, ...]
    mutually_exclusive: tuple[ExclusiveGroup, ...] = ()
    result_fields: tuple[ResultField, ...] = ()


# ── choice tables ────────────────────────────────────────────────────────────
# Registry-backed choices are derived so they cannot drift from the registry.

_LANGUAGE_CHOICES = tuple(
    ChoiceInfo(code, info.display_name) for code, info in LANGUAGES.items()
)

_VARIANT_CHOICES = (
    ChoiceInfo("auto", "the fastest variant the target supports at this width"),
) + tuple(ChoiceInfo(v, variant_info(v).description) for v in VARIANT_ORDER)

_NAMING_CHOICES = tuple(ChoiceInfo(n, naming_info(n).description) for n in NAMING_ORDER)

_COMMENT_STYLE_CHOICES = tuple(
    ChoiceInfo(s, style_info(s).description) for s in COMMENT_STYLES
)

# Wire-level enums with no registry of their own; this is their single home.

_ENDIAN_CHOICES = (
    ChoiceInfo("big", "most-significant byte first"),
    ChoiceInfo("little", "least-significant byte first"),
    ChoiceInfo("both", "try each byte order and report what matched"),
)

_MATCH_CHOICES = (
    ChoiceInfo("first", "stop at the first matching algorithm"),
    ChoiceInfo("all", "report every matching algorithm (forensic view)"),
    ChoiceInfo("set", "succeed only if exactly one algorithm fits all inputs"),
)

_CRC_BYTE_ORDER_CHOICES = (
    ChoiceInfo("big", "CRC field most-significant byte first"),
    ChoiceInfo("little", "CRC field least-significant byte first"),
)

_PACKET_FORMAT_CHOICES = (
    ChoiceInfo("hex", "hex string; spaces, commas, colons, 0x prefixes tolerated"),
    ChoiceInfo("base64", "base64-encoded raw bytes"),
    ChoiceInfo(
        "text",
        "'data <sep> hexcrc' line; the trailing hex field is peeled automatically",
    ),
)


# ── shared parameters ────────────────────────────────────────────────────────
# One frozen instance per shared parameter, reused across verbs, so the help
# text cannot diverge between tools.

_P_ENCODING = ParamSpec(
    "encoding", "string", False,
    "text encoding used when a text input is converted to bytes",
    default="utf-8",
)
_P_ALGORITHM = ParamSpec(
    "algorithm", "string", False,
    "catalogue algorithm name (exactly one of algorithm / custom_params)",
)
_P_CUSTOM_PARAMS = ParamSpec(
    "custom_params", "object", False,
    "custom / recovered Rocksoft tuple {width, poly, init?, refin?, refout?, "
    "xorout?, name?, desc?}; width and poly required, numbers may be hex strings",
)
_P_PACKET_HEX = ParamSpec(
    "packet_hex", "string", False,
    "the whole frame as a hex string (spaces, commas, colons, 0x tolerated)",
)
_P_PACKET_TEXT = ParamSpec(
    "packet_text", "string", False,
    "the whole frame as a 'data <sep> hexcrc' text line (or a JSON payload form)",
)
_P_PACKET_B64 = ParamSpec(
    "packet_b64", "string", False,
    "the whole frame as base64-encoded raw bytes",
)
_P_DATA_TEXT = ParamSpec(
    "data_text", "string", False,
    "the message as text (exactly one of data_text / data_b64)",
)
_P_DATA_B64 = ParamSpec(
    "data_b64", "string", False,
    "the message as base64-encoded raw bytes (exactly one of data_text / data_b64)",
)

_G_PACKET = ExclusiveGroup(("packet_hex", "packet_text", "packet_b64"), required=True)
_G_ALGORITHM = ExclusiveGroup(("algorithm", "custom_params"), required=True)
_G_DATA = ExclusiveGroup(("data_text", "data_b64"), required=True)

# Result fields shared by every custom-capable compute-style verb.
_R_CRC = ResultField("crc", "the CRC as a decimal integer")
_R_CRC_HEX = ResultField("crc_hex", "the CRC as a 0x-prefixed hex string")
_R_TRAILER_HINT = ResultField(
    "trailer_hint",
    "when no CRC matched: the likely non-CRC trailer (see identify_trailer), "
    "or null",
)


# ── the manifest ─────────────────────────────────────────────────────────────

VERBS: dict[str, VerbSpec] = {}


def _register(spec: VerbSpec) -> VerbSpec:
    VERBS[spec.name] = spec
    return spec


_register(
    VerbSpec(
        name="list",
        summary="Browse the catalogue of more than 100 named CRC algorithms.",
        description=(
            "Browse the crcglot CRC algorithm catalogue.  Returns more than "
            "100 named algorithms from the reveng catalogue (crc32, "
            "crc16-modbus, crc8-cdma2000, ...).  Use this when the user "
            "mentions a CRC by partial name or family, or to disambiguate "
            "before crc_generate.  Filter with a shell glob like "
            "'crc16-*' to narrow the list.  This is also the best first "
            "tool to call when building a candidate set to filter by width "
            "or description before calling crc_info / crc_generate."
        ),
        mcp_tool="crc_list",
        cli_command="list",
        python_api="crcglot.ALGORITHMS",
        params=(
            ParamSpec(
                "glob", "string", False,
                "shell-style pattern to filter names (e.g. 'crc16-*'); "
                "omit for the whole catalogue",
            ),
        ),
        result_fields=(
            ResultField("algorithms", "matching entries as {name, width, desc}"),
            ResultField("count", "how many algorithms matched"),
        ),
    )
)

_register(
    VerbSpec(
        name="info",
        summary="Full Rocksoft/Williams parameters for one catalogue algorithm.",
        description=(
            "Get the full Rocksoft/Williams parameters (width, poly, "
            "init, refin, refout, xorout, check) for one catalogue "
            "algorithm.  Use after crc_list to confirm parameters before "
            "crc_generate, or to answer 'what polynomial does "
            "crc16-modbus use?'.  Numeric fields are surfaced in both "
            "decimal (poly, init, xorout, check) and hex (poly_hex, "
            "init_hex, xorout_hex, check_hex)."
        ),
        mcp_tool="crc_info",
        cli_command="info",
        python_api="crcglot.ALGORITHMS",
        params=(
            ParamSpec("name", "string", True, "catalogue algorithm name"),
        ),
        result_fields=(
            ResultField(
                "name",
                "algorithm name; parameters follow as width / poly / init / "
                "refin / refout / xorout / check / desc / source, numeric "
                "fields in decimal and *_hex forms",
            ),
        ),
    )
)

_register(
    VerbSpec(
        name="vectors",
        summary="The four independently-generated self-test vectors for one algorithm.",
        description=(
            "Get the canonical self-test vectors for one catalogue "
            "algorithm: the CRC this algorithm must produce for four fixed "
            "inputs (empty message, the check string '123456789', all 256 "
            "byte values, and a 1 KiB pattern).  Use these to verify a CRC "
            "implementation (hand-written or from elsewhere) against a "
            "known-good answer instead of trusting it.  The values are "
            "independently generated (two engines, anycrc + crccheck, that "
            "had to agree; the check input anchored to reveng).  Each vector "
            "carries the input bytes (hex) and the expected CRC (decimal + "
            "hex), so the check is runnable."
        ),
        mcp_tool="crc_self_test_vectors",
        cli_command="vectors",
        python_api="crcglot.self_test_vectors",
        params=(
            ParamSpec("algorithm", "string", True, "catalogue algorithm name"),
        ),
        result_fields=(
            ResultField("algorithm", "the algorithm the vectors grade"),
            ResultField("width", "CRC width in bits"),
            ResultField("provenance", "where the expected values come from"),
            ResultField(
                "vectors",
                "one entry per fixed input: {input, input_hex, input_len, "
                "expected, expected_hex}",
            ),
        ),
    )
)

_register(
    VerbSpec(
        name="detect",
        summary="Name the catalogue CRC on a packet's trailing bytes.",
        description=(
            "Identify which catalogue CRC matches a packet whose tail "
            "is a CRC.  Accepts the packet as packet_hex (any common "
            "formatting -- spaces, commas, colons, 0x prefixes all "
            "tolerated), packet_text ('data <sep> hex'), or packet_b64 "
            "(base64-encoded raw bytes).  Exactly one must be supplied.\n"
            "\n"
            "IMPORTANT: 'crc_byte_order' in the output describes the "
            "byte order of the CRC field within the packet -- NOT the "
            "byte order of the surrounding protocol.  A big-endian "
            "protocol can serialize its CRC little-endian (and vice "
            "versa); the two are independent.\n"
            "\n"
            "If you already know the CRC value but not its algorithm "
            "(e.g. user pasted 'expected CRC: 0xCBF43926'), pass it as "
            "target_crc (decimal int) or target_crc_hex (hex string) "
            "and pass the data-only bytes as the packet.\n"
            "\n"
            "Narrow the scan with 'width' (e.g. 16 for a 2-byte CRC field) "
            "and/or 'algorithms' (an fnmatch glob like 'crc16-*').\n"
            "\n"
            "Every candidate reports 'form' -- the input representation the CRC "
            "was found in: 'binary', 'hex', 'text', or 'json'.\n"
            "\n"
            "For a CRC wrapped in a text/JSON frame rather than a bare tail -- "
            "such as a crclink JSON frame {\"t\":1234,\"v\":42,\"crc\":\"1352\"} "
            "-- pass packet_text; it reports form='json' and a 'form_detail' "
            "with the embedded crc and the covered message.  The 'form' "
            "argument (distinct from the result field) is an fnmatch glob "
            "selecting which named payload forms to try."
        ),
        mcp_tool="crc_detect",
        cli_command="detect",
        python_api="crcglot.detect",
        params=(
            _P_PACKET_HEX,
            _P_PACKET_TEXT,
            _P_PACKET_B64,
            ParamSpec(
                "target_crc", "integer", False,
                "known CRC value as a decimal integer (packet is then data only)",
            ),
            ParamSpec(
                "target_crc_hex", "string", False,
                "known CRC value as a hex string (packet is then data only)",
            ),
            ParamSpec(
                "endian", "string", False,
                "byte order(s) of the trailing CRC field to test",
                default="both", choices=_ENDIAN_CHOICES,
            ),
            ParamSpec(
                "algorithms", "string", False,
                "fnmatch glob narrowing the scan to a family (e.g. 'crc16-*')",
            ),
            ParamSpec(
                "width", "integer", False,
                "restrict candidates to this CRC width in bits",
            ),
            ParamSpec(
                "match", "string", False,
                "scan strategy",
                default="first", choices=_MATCH_CHOICES,
            ),
            _P_ENCODING,
            ParamSpec(
                "form", "string", False,
                "fnmatch glob over named payload forms to try (e.g. JSON frames)",
            ),
        ),
        mutually_exclusive=(
            _G_PACKET,
            ExclusiveGroup(("target_crc", "target_crc_hex"), required=False),
        ),
        result_fields=(
            ResultField("matched", "whether any catalogue algorithm fit"),
            ResultField(
                "candidates",
                "matches as {algorithm, width, crc_byte_order, form, ...}",
            ),
            _R_TRAILER_HINT,
        ),
    )
)

_register(
    VerbSpec(
        name="identify_trailer",
        summary="Name a non-CRC trailing field: checksum or cryptographic digest.",
        description=(
            "Identify a NON-CRC trailing field in a packet -- the heads-up for "
            "when crc_detect / crc_reverse find no CRC.  Recognises simple "
            "checksums (8-bit sum / LRC (two's-complement) / one's-complement / "
            "XOR, 16-bit sum, the Internet checksum (RFC 1071), Fletcher-16, "
            "Fletcher-32, Adler-32) AND cryptographic digests (MD5, SHA-1, "
            "SHA-2 and SHA-3 families, BLAKE2, double SHA-256 -- full length or "
            "the common 4/8-byte leading truncations).  IDENTIFICATION ONLY: "
            "crcglot does not generate code for these (checksums are "
            "one-liners; digests live in every stdlib).  The result exists to "
            "give you -- or the human you are helping -- the next move with an "
            "unfamiliar packet: it ends the CRC parameter hunt, names the "
            "likely protocol family, and says whether verification is even "
            "possible without a key.\n"
            "\n"
            "Keyed MACs (HMAC / CMAC) are undetectable without the key; when a "
            "delimited digest-sized field matches nothing, 'note' says so.\n"
            "\n"
            "Pass SEVERAL frames (same shape as crc_reverse: a list, hex by "
            "default, or base64 / text per packet_format).  Reliability comes "
            "from corroboration, not a single packet: an 8-bit checksum matches "
            "a random frame about 1 in 256, so 'frames_agreed' (how many frames "
            "a candidate fits) is the confidence signal -- one frame is weak, "
            "several agreeing frames make a hit trustworthy.  'crc_byte_order' "
            "(endian) only affects the 16/32-bit checksums."
        ),
        mcp_tool="crc_identify_trailer",
        cli_command="identify",
        python_api="crcglot.identify_trailer",
        params=(
            ParamSpec(
                "packets", "array[string]", True,
                "frames (message + trailing field), encoded per packet_format",
            ),
            ParamSpec(
                "packet_format", "string", False,
                "how each frame string is encoded",
                default="hex", choices=_PACKET_FORMAT_CHOICES,
            ),
            ParamSpec(
                "endian", "string", False,
                "byte order(s) tried for the 16/32-bit checksums",
                default="both", choices=_ENDIAN_CHOICES,
            ),
            ParamSpec(
                "trailers", "string", False,
                "fnmatch glob narrowing the candidate trailers (e.g. 'sha*')",
            ),
            _P_ENCODING,
        ),
        result_fields=(
            ResultField("matched", "whether any known trailer fit"),
            ResultField("frames_agreed", "how many frames the best candidate fit"),
            ResultField(
                "candidates",
                "matches as {trailer, kind, label, width, crc_byte_order, "
                "truncated_to}",
            ),
            ResultField("note", "diagnostic hint (e.g. a probable keyed MAC)"),
        ),
    )
)

_register(
    VerbSpec(
        name="reverse",
        summary="Recover the parameters of an unknown / custom CRC from captured frames.",
        description=(
            "Reverse-engineer the parameters of an UNKNOWN / custom CRC from "
            "captured packets -- the recovery counterpart to crc_detect (which "
            "only identifies CRCs already in the catalogue).  Takes the SAME "
            "input shape as crc_detect: whole frames with the CRC as the "
            "trailing field.  Use this when a device's CRC is NOT any known "
            "algorithm; it solves the Rocksoft/Williams parameters algebraically "
            "over GF(2).  A hand-written searcher gets this subtly wrong and "
            "fails silently, so delegate it: this returns a deterministic answer, "
            "or 'underdetermined' when the frames cannot pin one, never a guess.\n"
            "\n"
            "'packets' is a list of frames.  packet_format selects how each is "
            "encoded: 'hex' (default; any common formatting -- spaces, colons, "
            "0x prefixes tolerated), 'base64' (raw bytes), or 'text' for a "
            "'data <sep> hexcrc' line where the CRC is appended as hex after a "
            "separator (the trailing hex field is peeled automatically, like "
            "crc_detect).  Supply SEVERAL frames -- and crucially, at least TWO "
            "of the SAME length (their difference is what pins the polynomial), "
            "each varied in CONTENT, PLUS some frames of OTHER lengths (to "
            "separate init from xorout).  Frames that are all different lengths "
            "CANNOT recover the polynomial, so if the user only has differently "
            "sized captures, ask them for a few more at one size.  ~6+ is "
            "typical and more is better.\n"
            "\n"
            "'crc_bytes' is the size of the trailing CRC field for binary frames "
            "(e.g. 2 for a 16-bit CRC); leave it null to auto-detect, and it's "
            "ignored for text frames (the hex field is already delimited).  "
            "'crc_byte_order' is that field's byte order ('big' default, "
            "'little', or 'both' to try each).  Fix any known parameter (width / "
            "refin / refout / poly / init / xorout) to reduce how many frames "
            "are needed.\n"
            "\n"
            "Returns 'status': 'catalogue' (matched a known algorithm), "
            "'unique' (recovered, one parameter set), 'equivalent' (recovered "
            "and verified, but several (init, xorout) labellings are "
            "observationally identical -- ALL are returned in 'candidates', a "
            "complete and provably-exhaustive set of size 2**ambiguity_bits; "
            "the polynomial is always unique), 'underdetermined' (couldn't pin "
            "the polynomial -- usually means no two frames share a length; ask "
            "for >=2 same-length captures), or 'none'.  Every returned model is "
            "self-verified "
            "against the engine, and 'validated_frames' reports a held-out "
            "generalisation check; when the field size / byte order was "
            "auto-detected, 'note' records the split that was chosen.  "
            "Guarantee: a recovered model is correct on unseen data, or "
            "reports underdetermined -- never confidently wrong.  "
            "std_algo_only=True restricts to the catalogue tier (identical to "
            "crc_detect)."
        ),
        mcp_tool="crc_reverse",
        cli_command="reverse",
        python_api="crcglot.reverse_packets",
        params=(
            ParamSpec(
                "packets", "array[string]", True,
                "frames (message + trailing CRC), encoded per packet_format; "
                "include at least two same-length frames with different content",
            ),
            ParamSpec(
                "crc_bytes", "integer", False,
                "trailing CRC field size in bytes for binary frames; "
                "null auto-detects",
            ),
            ParamSpec(
                "crc_byte_order", "string", False,
                "byte order of the trailing CRC field",
                default="big", choices=_ENDIAN_CHOICES,
            ),
            ParamSpec(
                "packet_format", "string", False,
                "how each frame string is encoded",
                default="hex", choices=_PACKET_FORMAT_CHOICES,
            ),
            _P_ENCODING,
            ParamSpec(
                "std_algo_only", "boolean", False,
                "restrict to the catalogue tier; no algebraic recovery",
                default=False,
            ),
            ParamSpec(
                "width", "integer", False,
                "fix the CRC width in bits when known",
            ),
            ParamSpec(
                "refin", "boolean", False,
                "fix input reflection when known",
            ),
            ParamSpec(
                "refout", "boolean", False,
                "fix output reflection when known",
            ),
            ParamSpec(
                "poly", "integer", False,
                "fix the polynomial when known",
            ),
            ParamSpec(
                "init", "integer", False,
                "fix the initial register value when known",
            ),
            ParamSpec(
                "xorout", "integer", False,
                "fix the final XOR when known",
            ),
            ParamSpec(
                "validate", "boolean", False,
                "hold out one frame to check the recovered model generalizes",
                default=True,
            ),
        ),
        result_fields=(
            ResultField(
                "status",
                "catalogue / unique / equivalent / underdetermined / none",
            ),
            ResultField("catalogue_name", "the matched catalogue algorithm, or null"),
            ResultField(
                "ambiguity_bits",
                "the equivalent set has size 2**ambiguity_bits",
            ),
            ResultField("validated_frames", "frames the held-out check confirmed"),
            ResultField(
                "candidates",
                "recovered parameter sets as {width, poly, init, refin, refout, "
                "xorout, check} with *_hex forms",
            ),
            ResultField("note", "e.g. the auto-detected field size / byte order"),
            _R_TRAILER_HINT,
        ),
    )
)

_register(
    VerbSpec(
        name="verify",
        summary="Check a frame's trailing CRC against a named or custom algorithm.",
        description=(
            "Check whether a packet's trailing CRC is valid -- the inverse of "
            "crc_encode (which builds the packet) and the natural follow-up to "
            "crc_detect (which names the algorithm).  Splits the trailing CRC "
            "field off the frame, recomputes the CRC over the message, and "
            "compares.\n"
            "\n"
            "Identify the CRC with 'algorithm' (a KNOWN catalogue name) OR "
            "'custom_params' (a custom / recovered Rocksoft tuple -- e.g. what "
            "crc_reverse returns, so you can validate further frames against a "
            "recovered CRC).  Supply the frame as packet_hex (binary, any common "
            "formatting tolerated), packet_b64 (binary, base64), or packet_text "
            "('data <sep> hexcrc' -- the trailing hex CRC is peeled "
            "automatically, like crc_detect); exactly one.  crc_byte_order is "
            "the byte order of the trailing CRC field ('big' default / "
            "'little').\n"
            "\n"
            "Returns 'valid' (bool), 'expected' (the CRC the message should "
            "carry) and 'actual' (the value read from the field), in decimal and "
            "hex -- comparing the two shows HOW a bad frame is wrong."
        ),
        mcp_tool="crc_verify",
        cli_command="verify",
        python_api="crcglot.verify",
        params=(
            _P_ALGORITHM,
            _P_CUSTOM_PARAMS,
            _P_PACKET_HEX,
            _P_PACKET_TEXT,
            _P_PACKET_B64,
            ParamSpec(
                "crc_byte_order", "string", False,
                "byte order of the trailing CRC field",
                default="big", choices=_CRC_BYTE_ORDER_CHOICES,
            ),
            _P_ENCODING,
        ),
        mutually_exclusive=(_G_ALGORITHM, _G_PACKET),
        result_fields=(
            ResultField("valid", "whether the frame's CRC matches"),
            ResultField("expected", "the CRC the message should carry (decimal)"),
            ResultField("expected_hex", "expected, as 0x-prefixed hex"),
            ResultField("actual", "the value read from the CRC field (decimal)"),
            ResultField("actual_hex", "actual, as 0x-prefixed hex"),
            ResultField("width", "CRC width in bits"),
            ResultField("algorithm", "the algorithm label used"),
        ),
    )
)

_register(
    VerbSpec(
        name="compute",
        summary="The raw CRC integer of some data, no packet framing.",
        description=(
            "The deterministic CRC value for this data; call this instead "
            "of computing a CRC yourself (the bitwise math is easy to get "
            "subtly wrong).  Returns the raw integer, without packaging or "
            "framing.  Use when you need the bare number (e.g. compare "
            "against a captured value, fill in a struct field).  Supply "
            "exactly one of data_text or data_b64.\n"
            "\n"
            "Identify the CRC with 'algorithm' (a catalogue name) OR "
            "'custom_params' for a custom / recovered polynomial -- "
            "{width, poly, init, refin, refout, xorout} (width + poly "
            "required), e.g. the parameter set crc_reverse returns.\n"
            "\n"
            "Python-specific perf note: if algorithm is 'crc32' or "
            "'crc32-jamcrc', the stdlib's zlib.crc32 produces the same "
            "value with one fewer round-trip and is the routine crcglot "
            "delegates to internally anyway."
        ),
        mcp_tool="crc_compute",
        cli_command="compute",
        python_api="crcglot.compute",
        params=(
            _P_ALGORITHM,
            _P_CUSTOM_PARAMS,
            _P_DATA_TEXT,
            _P_DATA_B64,
            _P_ENCODING,
        ),
        mutually_exclusive=(_G_ALGORITHM, _G_DATA),
        result_fields=(
            _R_CRC,
            _R_CRC_HEX,
            ResultField("width", "CRC width in bits"),
        ),
    )
)

_register(
    VerbSpec(
        name="compute_many",
        summary="CRC many messages with one algorithm in a single batch call.",
        description=(
            "Compute the CRC of MANY messages with one algorithm in a "
            "single call -- the batch form of crc_compute.  Each message is "
            "CRC'd independently (not concatenated); results come back in "
            "order.  Use this instead of calling crc_compute in a loop: it "
            "builds the lookup table once for the whole batch (via the C "
            "extension) and pays the Python<->C transition once, so it is "
            "dramatically faster for many small messages of the same "
            "algorithm (packet streams, framed protocols, bulk validation). "
            "Supply exactly one of data_texts or data_b64s (a list); use "
            "data_b64s for binary payloads.  Identify the CRC with 'algorithm' "
            "(catalogue name) OR 'custom_params' (a custom / recovered "
            "Rocksoft tuple), as in crc_compute."
        ),
        mcp_tool="crc_compute_many",
        cli_command=None,
        python_api="crcglot.generic_crc_many",
        params=(
            _P_ALGORITHM,
            _P_CUSTOM_PARAMS,
            ParamSpec(
                "data_texts", "array[string]", False,
                "messages as text (exactly one of data_texts / data_b64s)",
            ),
            ParamSpec(
                "data_b64s", "array[string]", False,
                "messages as base64-encoded raw bytes "
                "(exactly one of data_texts / data_b64s)",
            ),
            _P_ENCODING,
        ),
        mutually_exclusive=(
            _G_ALGORITHM,
            ExclusiveGroup(("data_texts", "data_b64s"), required=True),
        ),
        result_fields=(
            ResultField("algorithm", "the algorithm label used"),
            ResultField("width", "CRC width in bits"),
            ResultField("count", "how many messages were CRC'd"),
            ResultField("results", "one {crc, crc_hex} per message, in order"),
        ),
    )
)

_register(
    VerbSpec(
        name="encode",
        summary="Build a packet by computing the CRC and appending it.",
        description=(
            "Build a complete packet by computing the CRC of the data "
            "and appending it.  Pairs round-trip with crc_detect.  For "
            "binary data pass data_b64; for text use data_text plus "
            "optional sep / leader / uppercase / fmt formatting.  Use "
            "this to generate test vectors, write expected values into "
            "test fixtures, or send a freshly-CRC'd packet on the wire.  "
            "Identify the CRC with 'algorithm' (catalogue name) OR "
            "'custom_params' (a custom / recovered Rocksoft tuple).\n"
            "\n"
            "crc_byte_order controls the byte order of the appended CRC "
            "bytes only (the data portion is unaffected)."
        ),
        mcp_tool="crc_encode",
        cli_command="encode",
        python_api="crcglot.encode",
        params=(
            _P_ALGORITHM,
            _P_CUSTOM_PARAMS,
            _P_DATA_TEXT,
            _P_DATA_B64,
            ParamSpec(
                "crc_byte_order", "string", False,
                "byte order of the appended CRC bytes",
                default="big", choices=_CRC_BYTE_ORDER_CHOICES,
            ),
            ParamSpec(
                "sep", "string", False,
                "text mode: separator between data and the hex CRC",
                default=" ",
            ),
            ParamSpec(
                "leader", "string", False,
                "text mode: hex leader before the CRC ('', '0x', or '0X')",
                default="",
            ),
            ParamSpec(
                "uppercase", "boolean", False,
                "text mode: uppercase hex digits",
                default=False,
            ),
            ParamSpec(
                "fmt", "string", False,
                "text mode: str.format template over {data} {sep} {leader} {crc}",
                default="{data}{sep}{leader}{crc}",
            ),
            _P_ENCODING,
        ),
        mutually_exclusive=(_G_ALGORITHM, _G_DATA),
        result_fields=(
            ResultField(
                "packet_hex",
                "binary branch: the whole packet as hex (with packet_b64 "
                "alongside); text branch returns packet_text instead",
            ),
            ResultField("packet_b64", "binary branch: the whole packet as base64"),
            ResultField("packet_text", "text branch: the formatted packet line"),
            _R_CRC,
            _R_CRC_HEX,
        ),
    )
)

_register(
    VerbSpec(
        name="generate",
        summary="Emit verified CRC source code for one (language, algorithm, variant) cell.",
        description=(
            "Generate verified CRC source code for one (language, "
            "algorithm, variant) cell.  Supports C, C#, Go, Java, Python, "
            "Rust, TypeScript, Verilog, VHDL.  Variants: 'auto' (DEFAULT -- "
            "the fastest the target + width support), 'bitwise' (smallest "
            "code, zero RAM table), 'table' (256-entry LUT), 'slice8' (8 "
            "tables, fastest, width 32/64 only, not on Python / Verilog / "
            "VHDL).  The default is fast, not small: pass variant='bitwise' "
            "for the smallest code (embedded / tiny MCUs).  When the user "
            "hasn't said, size the choice to payload x frequency: bitwise for "
            "tiny / infrequent payloads or code-constrained targets, the fast "
            "default once throughput matters, and for very large data on an "
            "algorithm both ends control prefer crc32 via the stdlib hardware "
            "path (see advisories / PERFORMANCE STEER below); ask if unsure.  "
            "The design-a-crc prompt walks this.  "
            "Every emitted file embeds a _self_test() against the reveng "
            "canonical vector for b'123456789'.\n"
            "\n"
            "PERFORMANCE STEER: for IEEE crc32 -- and crc32-jamcrc "
            "which crcglot routes through the same zlib path -- prefer "
            "the target language's stdlib (Python: zlib.crc32; C: zlib "
            "or hardware-CRC intrinsic; Rust: crc32fast crate) over "
            "generated code.  The stdlib path is ~30x faster than even "
            "slice-by-8 because it uses CPU CRC instructions (PCLMULQDQ "
            "on x86 since Westmere/2010; PMULL or crc32 instructions "
            "on ARMv8).  Generated code is the right answer for the "
            "rest of the catalogue and for embedded targets "
            "without a zlib equivalent.\n"
            "\n"
            "Supply algorithm OR custom_params (Rocksoft/Williams tuple); "
            "they are mutually exclusive.  'algorithm' accepts a single "
            "catalogue name, several names as a list, or a space-separated "
            "string (e.g. 'crc32 crc16-modbus crc8') -- multiple names "
            "bundle into ONE file (one .h + one .c for C), each keeping its "
            "catalogue-derived function names; per-symbol tables keep the "
            "bundle collision-free.  'name' renames a single CRC -- it replaces "
            "the algorithm name as the base and is CASED per target (Rust "
            "my_widget, Java class MyWidget + MyWidget.java, C# MyWidget); this "
            "is the usual 'call it X' knob (single CRC only).  'symbol' is the "
            "escape hatch: emit that identifier VERBATIM, un-recased (single "
            "CRC, not for Java).  Each returned file carries a ready 'filename' "
            "(crcglot owns the per-target naming; Java/C# name the file after "
            "the class) -- write content to filename as-is.  The "
            "chosen 'variant' must be legal for every algorithm's width "
            "(slice8 is width 32/64 only).  'comment_style' selects the "
            "documentation style of the emitted comments: 'plain' (default) "
            "is professional human-readable comments in each language's "
            "native syntax; 'doxygen' emits /** @brief @param */ markup for "
            "C / C# / Java; Python has 'google' (Args / Returns), 'numpy' "
            "(underlined Parameters / Returns) and 'rest' (Sphinx :param: "
            "field lists); 'rustdoc' emits /// Markdown for "
            "Rust; 'godoc' emits identifier-led // docs for Go; 'javadoc' "
            "emits /** @param @return */ for Java; 'jsdoc' emits TSDoc "
            "markup for TypeScript; 'docfx' emits /// <summary> <param> "
            "<returns> XML doc comments for C#.  Every file header also carries "
            "a 'Reproduce with crcglot' block (the resolved algorithm, target, "
            "variant, comment style, symbol, naming); C additionally emits a "
            "linkable const provenance record for runtime introspection, dropped "
            "by --gc-sections when unused or via -DCRCGLOT_NO_PROVENANCE.  The "
            "returned 'algorithms' "
            "lists what was generated; 'advisories' carries any "
            "{severity, kind, message} notes about a faster path (e.g. a "
            "stdlib CRC-32 for the target, or 'use the crcglot package' for a "
            "Python target) -- surface these to the user.\n"
            "\n"
            "OUTPUT HANDLING: each 'files' entry is a COMPLETE, drop-in source "
            "file -- 'content' is the whole file (header comment, EVERY table "
            "row, all functions, the embedded self-test).  Never truncate it: "
            "do not elide table rows, omit functions, or summarise the body.  "
            "Prefer WRITING the full content to a file and reporting the path "
            "rather than pasting it into the chat; only paste it inline when the "
            "user wants to read or copy it directly, and then IN FULL.  Name the "
            "file by the language's convention -- the algorithm name for C / "
            "Rust / Go / Python / TypeScript / Verilog / VHDL; the public class "
            "name shown in the emitted code for Java / C#.  Large variants make "
            "this matter: slice8 emits eight 256-entry tables, so write it to a "
            "file instead of dumping the whole table into the conversation."
        ),
        mcp_tool="crc_generate",
        cli_command=None,
        python_api="crcglot.generate_files",
        params=(
            ParamSpec(
                "language", "string", True,
                "target language",
                choices=_LANGUAGE_CHOICES,
            ),
            ParamSpec(
                "algorithm", "string | array[string]", False,
                "catalogue name(s); a list or space-separated string bundles "
                "several into one file (exactly one of algorithm / custom_params)",
            ),
            _P_CUSTOM_PARAMS,
            ParamSpec(
                "variant", "string", False,
                "implementation shape",
                default="auto", choices=_VARIANT_CHOICES,
            ),
            ParamSpec(
                "symbol", "string", False,
                "emit this identifier verbatim, bypassing naming "
                "(single algorithm; not for Java)",
            ),
            ParamSpec(
                "name", "string", False,
                "rename the CRC; cased per target language (single algorithm)",
            ),
            ParamSpec(
                "comment_style", "string", False,
                "documentation style of the emitted comments",
                default="plain", choices=_COMMENT_STYLE_CHOICES,
            ),
            ParamSpec(
                "naming", "string", False,
                "casing of the public function / method names; "
                "null uses the language's idiomatic default",
                choices=_NAMING_CHOICES,
            ),
        ),
        mutually_exclusive=(_G_ALGORITHM,),
        result_fields=(
            ResultField("language", "the target language generated"),
            ResultField("variant", "the resolved concrete variant"),
            ResultField("comment_style", "the documentation style used"),
            ResultField("naming", "the resolved naming convention"),
            ResultField("algorithms", "what was generated"),
            ResultField(
                "advisories",
                "{severity, kind, message} notes about a faster path; "
                "surface these to the user",
            ),
            ResultField(
                "files",
                "complete drop-in source files as {filename, extension, "
                "content, role}; never truncate content",
            ),
            ResultField("note", "output-handling reminder for the caller"),
        ),
    )
)

_register(
    VerbSpec(
        name="credits",
        summary="The projects crcglot builds on.",
        description=(
            "Return the projects crcglot stands on (reveng catalogue, "
            "zlib, Rocksoft/Williams parameterization)."
        ),
        mcp_tool="crc_credits",
        cli_command="credits",
        python_api="crcglot.ATTRIBUTION",
        params=(),
        result_fields=(
            ResultField("attribution", "the acknowledgments text"),
        ),
    )
)


def verb_info(name: str) -> VerbSpec:
    """Look up one verb's :class:`VerbSpec` by its frontend-neutral name.

    Args:
        name: A ``VERBS`` key such as ``"detect"`` or ``"generate"``.

    Returns:
        The frozen :class:`VerbSpec` record.

    Raises:
        UnknownVerbError: ``name`` is not a crcglot verb.  The message
            suggests a close match when one exists and lists the full
            vocabulary (it is small enough to show whole).

    Examples:
        >>> verb_info("detect").mcp_tool
        'crc_detect'
    """
    spec = VERBS.get(name)
    if spec is not None:
        return spec
    close = difflib.get_close_matches(name, VERBS, n=1)
    hint = f"did you mean {close[0]!r}?  " if close else ""
    raise UnknownVerbError(
        f"unknown verb {name!r}; {hint}valid verbs: {', '.join(VERBS)}"
    )
