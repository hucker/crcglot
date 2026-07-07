"""FastMCP server for crcglot.

Exposes the existing CLI surface as MCP tools + resources so an LLM
client (Claude Desktop, Cursor, mcp-cli, etc.) can call into crcglot
in natural-language workflows::

    User: "Here's a Modbus packet, give me C code for the CRC."
    LLM  -> crc_detect(...)       -> ("crc16-modbus", "little")
    LLM  -> crc_generate(...)     -> (.h + .c)

Every tool wraps an existing public Python function from ``crcglot``;
the MCP layer is purely transport adaptation and adds no CRC logic.
Correctness of the underlying engines is asserted by the project test
suite in ``tests/``.

Entry point: ``crcglot-mcp`` (registered in ``pyproject.toml`` under
``[project.scripts]``).  The server speaks stdio JSON-RPC -- standard
for MCP -- which makes it composable with any MCP client.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Literal

from mcp.server import FastMCP
from mcp.types import ToolAnnotations

from crcglot import ALGORITHMS, LANGUAGES, variant_info
from crcglot._invoke import (
    _verb_compute,
    _verb_compute_many,
    _verb_credits,
    _verb_detect,
    _verb_encode,
    _verb_generate,
    _verb_identify_trailer,
    _verb_info,
    _verb_list,
    _verb_reverse,
    _verb_vectors,
    _verb_verify,
)
from crcglot._wire import algorithm_to_dict, language_to_dict
from crcglot.verbs import VERBS


# Byte-aligned catalogue widths -- the rows of the ``variants.json``
# resource cross-product (one variants-by-language map per width).
_CATALOGUE_WIDTHS = (8, 16, 32, 64)

# Language enum -- single source of truth for ``crc_generate``.
LANG_ENUM = Literal[
    "c",
    "csharp",
    "go",
    "java",
    "python",
    "rust",
    "typescript",
    "verilog",
    "vhdl",
]

VARIANT_ENUM = Literal["auto", "bitwise", "table", "slice8"]
# Naming convention for the generated public function / method names.
# Which conventions a language offers (and its default) lives on
# ``LanguageInfo.naming`` / ``.default_naming``; the schema accepts all three
# and the tool rejects a pair the language doesn't offer.
NAMING_ENUM = Literal["snake", "camel", "pascal"]
# Comment / documentation style.  ``plain`` plus the per-language doc-tool
# styles (doxygen, google, numpy, rest, rustdoc, godoc, docfx, javadoc,
# jsdoc) are all implemented; the generator rejects a style a given
# language doesn't offer (see crcglot.comments).
COMMENT_STYLE_ENUM = Literal[
    "plain",
    "doxygen",
    "google",
    "numpy",
    "rest",
    "rustdoc",
    "godoc",
    "docfx",
    "javadoc",
    "jsdoc",
]
ENDIAN_ENUM = Literal["big", "little", "both"]
MATCH_ENUM = Literal["first", "all", "set"]
CRC_BYTE_ORDER_ENUM = Literal["big", "little"]
# How the per-packet strings in crc_reverse are encoded.
PACKET_FORMAT_ENUM = Literal["hex", "base64", "text"]

# Every crcglot tool is a pure, deterministic, offline read: it lists /
# computes / generates and never mutates external state or touches the
# network (crc_generate only *returns* source).  These hints let a client
# auto-approve the calls instead of prompting per invocation.
_READONLY = ToolAnnotations(
    readOnlyHint=True,
    idempotentHint=True,
    destructiveHint=False,
    openWorldHint=False,
)


def _tool_description(verb: str) -> str:
    """Render a tool's MCP description from the verb manifest.

    The guidance prose lives on ``VerbSpec.description`` (the manifest is the
    single home for it); this appends a rendered per-parameter block, so an
    MCP client sees the same choices / defaults / help an importing consumer
    reads from :data:`crcglot.VERBS`.  ``test_mcp.py::TestVerbManifestDrift``
    holds the live schemas to the same manifest.
    """
    spec = VERBS[verb]
    if not spec.params:
        return spec.description
    lines = []
    for p in spec.params:
        suffix = ""
        if p.choices:
            suffix += " (choices: " + " / ".join(c.name for c in p.choices) + ")"
        if p.default is not None:
            suffix += f" (default {p.default!r})"
        lines.append(f"- {p.name}: {p.help}{suffix}")
    for g in spec.mutually_exclusive:
        rule = "exactly one" if g.required else "at most one"
        lines.append(f"- supply {rule} of: " + " / ".join(g.params))
    return spec.description + "\n\nParameters:\n" + "\n".join(lines)


def build_server() -> FastMCP:
    """Construct the configured FastMCP server.

    Factored out of ``main`` so tests can instantiate the server in-process
    and call ``server.call_tool(name, args)`` / ``server.read_resource(uri)``
    without spawning the stdio loop.
    """
    mcp = FastMCP(
        "crcglot",
        instructions=(
            "crcglot exposes the reveng CRC catalogue (more than 100 algorithms), "
            "a multi-language code generator (C / C# / Go / Java / Python / Rust "
            "/ TypeScript / Verilog / VHDL), and a runtime CRC engine.  "
            "Use crc_list / crc_info to browse.  The packet tools all take the "
            "same shape -- whole frames with the CRC as the trailing field: "
            "crc_detect identifies a KNOWN CRC, crc_reverse recovers an UNKNOWN "
            "/ custom one, and crc_verify checks a frame against a named "
            "algorithm.  crc_compute gives raw integer CRC values; crc_encode "
            "builds a packet (the inverse of crc_verify); crc_generate emits "
            "verified source code -- it defaults to the FASTEST implementation "
            "the target supports, so when the user hasn't said, ask whether "
            "they want smallest (variant='bitwise') or fastest, rather than "
            "silently picking.\n"
            "\n"
            "CHOOSING vs MATCHING: if the CRC crosses a boundary you don't "
            "control -- an existing device, wire protocol, or file format -- you "
            "must MATCH it (crc_detect, or crc_reverse for a custom one); a "
            "guessed CRC silently fails to interoperate.  You only get to CHOOSE "
            "when both ends are yours (a new protocol), and then SIZE the CRC to "
            "the job rather than reaching for one by reflex: crc32 when overhead "
            "is cheap and payloads are large or hardware-accelerated (a solid "
            "general-purpose default); crc16 for small fixed blocks or framed "
            "serial / field-bus protocols where two bytes per frame matters "
            "(this is why XMODEM, Modbus, and CAN are 16-bit); crc8 for tiny or "
            "deeply constrained payloads; or a specific width to match an HDL "
            "bus.  Wider = stronger detection but more overhead per frame; size "
            "it to the data you're protecting.  Never pick an arbitrary "
            "algorithm -- the choice fixes both interoperability and "
            "error-detection strength.  (The design-a-crc prompt walks this.)\n"
            "\n"
            "For IEEE crc32 and crc32-jamcrc specifically, prefer the target "
            "language's stdlib (e.g. Python's zlib.crc32) -- those algorithms "
            "run ~30x faster via CPU CRC instructions than any generated code."
        ),
    )

    # ----- crc_list -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_list",
        description=_tool_description("list"),
    )
    def crc_list(glob: str | None = None) -> dict[str, Any]:
        return _verb_list(glob=glob)

    # ----- crc_info -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_info",
        description=_tool_description("info"),
    )
    def crc_info(name: str) -> dict[str, Any]:
        return _verb_info(name, surface="mcp")

    # ----- crc_self_test_vectors -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_self_test_vectors",
        description=_tool_description("vectors"),
    )
    def crc_self_test_vectors(algorithm: str) -> dict[str, Any]:
        return _verb_vectors(algorithm, surface="mcp")

    # ----- crc_detect -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_detect",
        description=_tool_description("detect"),
    )
    def crc_detect(
        packet_hex: str | None = None,
        packet_text: str | None = None,
        packet_b64: str | None = None,
        target_crc: int | None = None,
        target_crc_hex: str | None = None,
        endian: ENDIAN_ENUM = "both",
        algorithms: str | None = None,
        width: int | None = None,
        match: MATCH_ENUM = "first",
        encoding: str = "utf-8",
        form: str | None = None,
    ) -> dict[str, Any]:
        return _verb_detect(
            packet_hex=packet_hex,
            packet_text=packet_text,
            packet_b64=packet_b64,
            target_crc=target_crc,
            target_crc_hex=target_crc_hex,
            endian=endian,
            algorithms=algorithms,
            width=width,
            match=match,
            encoding=encoding,
            form=form,
        )

    # ----- crc_identify_trailer -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_identify_trailer",
        description=_tool_description("identify_trailer"),
    )
    def crc_identify_trailer(
        packets: list[str],
        packet_format: PACKET_FORMAT_ENUM = "hex",
        endian: ENDIAN_ENUM = "both",
        trailers: str | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return _verb_identify_trailer(
            packets=packets,
            packet_format=packet_format,
            endian=endian,
            trailers=trailers,
            encoding=encoding,
        )

    # ----- crc_encode -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_encode",
        description=_tool_description("encode"),
    )
    def crc_encode(
        algorithm: str | None = None,
        custom_params: dict[str, Any] | None = None,
        data_text: str | None = None,
        data_b64: str | None = None,
        crc_byte_order: CRC_BYTE_ORDER_ENUM = "big",
        sep: str = " ",
        leader: str = "",
        uppercase: bool = False,
        fmt: str = "{data}{sep}{leader}{crc}",
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return _verb_encode(
            algorithm=algorithm,
            custom_params=custom_params,
            data_text=data_text,
            data_b64=data_b64,
            crc_byte_order=crc_byte_order,
            sep=sep,
            leader=leader,
            uppercase=uppercase,
            fmt=fmt,
            encoding=encoding,
            surface="mcp",
        )

    # ----- crc_compute -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_compute",
        description=_tool_description("compute"),
    )
    def crc_compute(
        algorithm: str | None = None,
        custom_params: dict[str, Any] | None = None,
        data_text: str | None = None,
        data_b64: str | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return _verb_compute(
            algorithm=algorithm,
            custom_params=custom_params,
            data_text=data_text,
            data_b64=data_b64,
            encoding=encoding,
            surface="mcp",
        )

    # ----- crc_compute_many -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_compute_many",
        description=_tool_description("compute_many"),
    )
    def crc_compute_many(
        algorithm: str | None = None,
        custom_params: dict[str, Any] | None = None,
        data_texts: list[str] | None = None,
        data_b64s: list[str] | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return _verb_compute_many(
            algorithm=algorithm,
            custom_params=custom_params,
            data_texts=data_texts,
            data_b64s=data_b64s,
            encoding=encoding,
            surface="mcp",
        )

    # ----- crc_reverse -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_reverse",
        description=_tool_description("reverse"),
    )
    def crc_reverse(
        packets: list[str],
        crc_bytes: int | None = None,
        crc_byte_order: ENDIAN_ENUM = "big",
        packet_format: PACKET_FORMAT_ENUM = "hex",
        encoding: str = "utf-8",
        std_algo_only: bool = False,
        width: int | None = None,
        refin: bool | None = None,
        refout: bool | None = None,
        poly: int | None = None,
        init: int | None = None,
        xorout: int | None = None,
        validate: bool = True,
    ) -> dict[str, Any]:
        return _verb_reverse(
            packets=packets,
            crc_bytes=crc_bytes,
            crc_byte_order=crc_byte_order,
            packet_format=packet_format,
            encoding=encoding,
            std_algo_only=std_algo_only,
            width=width,
            refin=refin,
            refout=refout,
            poly=poly,
            init=init,
            xorout=xorout,
            validate=validate,
        )

    # ----- crc_verify -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_verify",
        description=_tool_description("verify"),
    )
    def crc_verify(
        algorithm: str | None = None,
        custom_params: dict[str, Any] | None = None,
        packet_hex: str | None = None,
        packet_text: str | None = None,
        packet_b64: str | None = None,
        crc_byte_order: CRC_BYTE_ORDER_ENUM = "big",
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        return _verb_verify(
            algorithm=algorithm,
            custom_params=custom_params,
            packet_hex=packet_hex,
            packet_text=packet_text,
            packet_b64=packet_b64,
            crc_byte_order=crc_byte_order,
            encoding=encoding,
            surface="mcp",
        )

    # ----- crc_generate -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_generate",
        description=_tool_description("generate"),
    )
    def crc_generate(
        language: LANG_ENUM,
        algorithm: str | list[str] | None = None,
        variant: VARIANT_ENUM = "auto",
        symbol: str | None = None,
        name: str | None = None,
        custom_params: dict[str, Any] | None = None,
        comment_style: COMMENT_STYLE_ENUM = "plain",
        naming: NAMING_ENUM | None = None,
    ) -> dict[str, Any]:
        return _verb_generate(
            language=language,
            algorithm=algorithm,
            variant=variant,
            symbol=symbol,
            name=name,
            custom_params=custom_params,
            comment_style=comment_style,
            naming=naming,
            surface="mcp",
        )

    # ----- crc_credits -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_credits",
        description=_tool_description("credits"),
    )
    def crc_credits() -> dict[str, str]:
        return _verb_credits()

    # ----- Prompts -----

    @mcp.prompt(
        name="design-a-crc",
        title="Design / choose a CRC",
        description=(
            "Guide the user to the right CRC for a data link -- match an "
            "existing one, or choose (default crc32) and generate code.  Use "
            "this for an open-ended 'I need a CRC' / 'add a checksum to my "
            "protocol' request."
        ),
    )
    def design_a_crc(use_case: str = "") -> str:
        """Return a guided prompt that walks the match-vs-choose decision.

        Args:
            use_case: Optional free-text description of what the user is
                building (a device, a file format, a new protocol, …).

        Returns:
            A user-message string steering the model through the workflow.
        """
        ctx = f"\n\nWhat I'm building: {use_case}" if use_case.strip() else ""
        # Per-variant facts come from the VariantInfo records so this prompt
        # never restates a speed/size claim that lives in crcglot proper.
        bitwise_desc = variant_info("bitwise").description.rstrip(".")
        table_desc = variant_info("table").description.rstrip(".")
        return (
            "Help me choose and set up a CRC. Work through this in order:\n"
            "\n"
            "1. MATCH vs CHOOSE. Am I interoperating with something I do NOT "
            "control -- an existing device, an on-the-wire protocol, or a file "
            "format? If yes, I must MATCH its CRC, not invent one: use "
            "crc_detect on a captured frame, or crc_reverse if the CRC is custom "
            "/ unknown. A guessed CRC will not interoperate.\n"
            "2. CHOOSE THE ALGORITHM (only if both ends are mine -- a new "
            "protocol). Size the CRC to the job, not by reflex: crc32 when "
            "overhead is cheap and payloads are large or hardware-accelerated (a "
            "solid general default); crc16 for small fixed blocks or framed "
            "serial / field-bus links where two bytes per frame matters (XMODEM, "
            "Modbus, and CAN are 16-bit for exactly this reason); crc8 for tiny "
            "or constrained payloads; or a specific width to match an HDL bus. "
            "Wider detects more but costs more overhead per frame -- size it to "
            "the data I'm protecting.\n"
            "3. CHOOSE THE IMPLEMENTATION (bitwise / table / external). This is a "
            "speed-vs-size call that's independent of the algorithm above -- "
            "every variant computes the same CRC value, so it never affects "
            "interop. Size it to payload x frequency:\n"
            f"   - bitwise (variant='bitwise'): {bitwise_desc}. Pick it for tiny "
            "or infrequent payloads, or a code-size-constrained target (MCU / "
            "bootloader) where the table's footprint isn't worth it.\n"
            f"   - the default (leave variant unset = auto, the fastest the "
            f"target supports): {table_desc}, or slice-by-8 on a 32/64-bit "
            "compiled target. This is the right call once throughput matters.\n"
            "   - external (very large data AND both ends are mine): prefer "
            "crc32 and the target language's stdlib / hardware-CRC path "
            "(zlib.crc32, a CPU CRC intrinsic, the crc32fast crate) -- ~30x "
            "faster than any generated code. crc_generate emits an advisory "
            "pointing to it when the algorithm qualifies.\n"
            "4. Then act: crc_generate to emit verified code in my target "
            "language, and/or crc_encode / crc_verify to build and check frames." + ctx
        )

    @mcp.prompt(
        name="generate-crc-code",
        title="Generate CRC code (pick language, naming, comment style)",
        description=(
            "Walk the user through emitting CRC source code: choose the target "
            "language, then -- only when the language offers more than one -- the "
            "naming convention and the comment style, then call crc_generate.  "
            "Use this for 'give me code for <CRC> in <language>' requests."
        ),
    )
    def generate_crc_code(algorithm: str = "") -> str:
        """Return a guided prompt that walks the language/naming/style picker.

        Args:
            algorithm: Optional catalogue name (or custom-CRC description) the
                code is for; folded into the prompt when given.

        Returns:
            A user-message string.  The per-language option lists are built from
            :data:`crcglot.LANGUAGES` so the "ask only when there's a choice"
            gating stays accurate as targets gain or lose conventions -- it is
            never hardcoded here.
        """
        # Per-language picker map, derived from the metadata so the gating below
        # can't drift from what crc_generate actually accepts.
        rows = []
        for code, info in LANGUAGES.items():
            namings = [n.name for n in info.naming_infos]
            styles = [s.name for s in info.styles]
            naming_part = (
                f"naming {namings} (default {info.default_naming})"
                if len(namings) > 1
                else f"naming {namings[0]} (only)"
            )
            style_part = (
                f"comment styles {styles}"
                if len(styles) > 1
                else f"comment style {styles[0]} (only)"
            )
            rows.append(f"- {code} ({info.display_name}): {naming_part}; {style_part}")
        catalogue = "\n".join(rows)
        for_algo = f" for {algorithm}" if algorithm.strip() else ""

        return (
            f"Generate CRC source code{for_algo}. Work with the user IN ORDER, "
            "and ask only when there's a real choice -- when an axis offers a "
            "single option, use it silently rather than asking:\n"
            "\n"
            "0. ALGORITHM -- if which CRC to use isn't settled yet, settle it "
            "first: crc_detect / crc_reverse to MATCH an existing one, or the "
            "design-a-crc prompt to CHOOSE a new one.\n"
            "1. LANGUAGE -- ask which target they want:\n"
            f"{catalogue}\n"
            "2. NAMING -- for the chosen language, ask which convention only if "
            "it lists more than one above (show the human labels from "
            "crcglot://languages.json); if it lists one, use it without asking.\n"
            "3. COMMENT STYLE -- likewise: ask only if the language offers more "
            "than one; otherwise use its single style.\n"
            "4. GENERATE -- call crc_generate(language=..., algorithm=..., and "
            "the chosen naming / comment_style). Leave variant unset (the "
            "fastest the target supports) unless the user wants the smallest "
            "code (variant='bitwise').\n"
            "\n"
            "The lists above come from crcglot's own metadata; "
            "crcglot://languages.json carries the labels and descriptions for "
            "each option."
        )

    # ----- Resources -----

    @mcp.resource(
        "crcglot://catalogue.json",
        name="catalogue",
        description=(
            "All catalogue algorithms with full Rocksoft/Williams "
            "parameters and reveng-canonical check values.  Numeric "
            "fields surface as both decimal and hex."
        ),
        mime_type="application/json",
    )
    def catalogue_resource() -> str:
        payload = {
            "algorithms": {
                name: algorithm_to_dict(name, algo) for name, algo in ALGORITHMS.items()
            },
            "count": len(ALGORITHMS),
        }
        return json.dumps(payload, indent=2)

    @mcp.resource(
        "crcglot://languages.json",
        name="languages",
        description=(
            "Per-target metadata: code, display_name, extensions, "
            "supported variants, and emoji.  Generator callables are "
            "not included (not JSON-serializable; use crc_generate "
            "instead)."
        ),
        mime_type="application/json",
    )
    def languages_resource() -> str:
        payload = {
            "languages": {
                code: language_to_dict(code, info) for code, info in LANGUAGES.items()
            },
        }
        return json.dumps(payload, indent=2)

    @mcp.resource(
        "crcglot://variants.json",
        name="variants_by_width",
        description=(
            "Cross-product of variants_for_width(width) for the four "
            "catalogue widths.  Use this to pick a valid (language, "
            "variant) combination before calling crc_generate, instead "
            "of risking a structured error from the tool.  Example: "
            "Python supports {bitwise, table} at every width; "
            "Verilog / VHDL support {bitwise} only; the slice8 variant "
            "appears only on the compiled-software languages and only "
            "for widths 32 and 64."
        ),
        mime_type="application/json",
    )
    def variants_resource() -> str:
        by_width: dict[str, dict[str, list[str]]] = {}
        for w in _CATALOGUE_WIDTHS:
            by_width[str(w)] = {
                code: list(info.variants_for_width(w))
                for code, info in LANGUAGES.items()
            }
        return json.dumps({"variants_by_width": by_width}, indent=2)

    @mcp.resource(
        "crcglot://verbs.json",
        name="verbs",
        description=(
            "The verb manifest: every crcglot verb with its parameters "
            "(types, defaults, choices, one-line help), mutual-exclusion "
            "groups, and result fields.  The same data as crcglot.VERBS; "
            "render typed tools from it instead of hand-rolling parameter "
            "metadata."
        ),
        mime_type="application/json",
    )
    def verbs_resource() -> str:
        payload = {"verbs": {name: asdict(spec) for name, spec in VERBS.items()}}
        return json.dumps(payload, indent=2)

    return mcp


def main() -> None:
    """Entry point for the ``crcglot-mcp`` script.

    Runs the FastMCP stdio loop forever -- the process is owned by the
    MCP client (Claude Desktop, mcp-cli, etc.), which manages
    lifecycle.  Exiting cleanly when the client closes stdin / stdout
    is FastMCP's responsibility.
    """
    server = build_server()
    server.run()
