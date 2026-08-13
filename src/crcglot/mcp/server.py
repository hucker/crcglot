"""MCP server for crcglot (mcp 2.0 ``MCPServer``).

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

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from crcglot import ALGORITHMS, LANGUAGES, variant_info
from crcglot._wire import algorithm_to_dict, language_to_dict
from crcglot.mcp._synth import synthesize_tool
from crcglot.verbs import VERBS


# Byte-aligned catalogue widths -- the rows of the ``variants.json``
# resource cross-product (one variants-by-language map per width).
_CATALOGUE_WIDTHS = (8, 16, 32, 64)

# Every crcglot tool is a pure, deterministic, offline read: it lists /
# computes / generates and never mutates external state or touches the
# network (crc_generate only *returns* source).  These hints let a client
# auto-approve the calls instead of prompting per invocation.
_READONLY = ToolAnnotations(
    read_only_hint=True,
    idempotent_hint=True,
    destructive_hint=False,
    open_world_hint=False,
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


def build_server() -> MCPServer:
    """Construct the configured FastMCP server.

    Factored out of ``main`` so tests can instantiate the server in-process
    and call ``server.call_tool(name, args)`` / ``server.read_resource(uri)``
    without spawning the stdio loop.
    """
    mcp = MCPServer(
        "crcglot",
        instructions=(
            "crcglot exposes the reveng CRC catalogue (more than 100 algorithms), "
            "a multi-language code generator (C / C# / Go / Java / Lua / Python / "
            "Rust / TypeScript / Zig / Verilog / VHDL), and a runtime CRC engine.  "
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

    # ----- Tools: registered from the verb manifest -----
    #
    # Each callable is synthesized from crcglot.VERBS (see _synth.py), so
    # the wire schema is derived from the manifest by construction -- a new
    # verb or a new enum value reaches the MCP surface with no server edit.
    # tests/goldens/mcp_wire.json pins the derived schemas to the shapes the
    # last mcp 1.x build shipped.
    for verb, spec in VERBS.items():
        mcp.add_tool(
            synthesize_tool(verb),
            name=spec.mcp_tool,
            description=_tool_description(verb),
            annotations=_READONLY,
            structured_output=True,
        )

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

    Runs the MCP stdio loop forever -- the process is owned by the
    MCP client (Claude Desktop, mcp-cli, etc.), which manages
    lifecycle.  Exiting cleanly when the client closes stdin / stdout
    is the SDK's responsibility.
    """
    server = build_server()
    server.run()
