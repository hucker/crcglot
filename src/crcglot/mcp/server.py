"""FastMCP server for crcglot.

Exposes the existing CLI surface as MCP tools + resources so an LLM
client (Claude Desktop, Cursor, mcp-cli, etc.) can call into crcglot
in natural-language workflows::

    User: "Here's a Modbus packet, give me C code for the CRC."
    LLM  -> crc_detect(...)       -> ("crc16-modbus", "little")
    LLM  -> crc_generate(...)     -> (.h + .c)

Every tool wraps an existing public Python function from ``crcglot``;
the MCP layer is purely transport adaptation and adds no CRC logic.
Correctness of the underlying engines is asserted by the 2,930-test
suite in ``tests/``.

Entry point: ``crcglot-mcp`` (registered in ``pyproject.toml`` under
``[project.scripts]``).  The server speaks stdio JSON-RPC -- standard
for MCP -- which makes it composable with any MCP client.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Literal, cast

from mcp.server import FastMCP
from mcp.types import ToolAnnotations

from crcglot import (
    ALGORITHMS,
    ATTRIBUTION,
    LANGUAGES,
    AlgorithmInfo,
    detect,
    encode,
    encode_int,
    encode_text,
    generic_crc,
    generic_crc_many,
    reverse_packets,
    variant_info,
    verify,
)
from crcglot.mcp._wire import (
    algorithm_to_dict,
    detect_match_to_dict,
    language_to_dict,
    parse_packet,
    parse_target_crc,
)


# Catalogue width set -- used both by ``crc_generate`` validation and
# the ``variants.json`` resource cross-product.
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
# Comment / documentation style.  ``plain`` is the only one implemented
# today; the doc-tool styles are accepted by the schema but raise an
# informative ValueError until shipped (see crcglot.comments).
COMMENT_STYLE_ENUM = Literal[
    "plain", "doxygen", "google", "numpy", "rest", "rustdoc", "godoc", "docfx",
    "javadoc", "jsdoc",
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


def _resolve_algorithm(
    algorithm: str | None, custom_params: dict[str, Any] | None,
) -> tuple[AlgorithmInfo, str]:
    """Resolve a catalogue name OR a custom Rocksoft tuple to ``(info, label)``.

    ``custom_params`` is ``{width, poly, init?, refin?, refout?, xorout?, name?,
    desc?}`` (``width`` and ``poly`` required); its ``check`` is computed.  This
    lets the compute / encode / verify tools work with a custom or *recovered*
    polynomial -- e.g. the parameter set ``crc_reverse`` returns -- not just a
    catalogue entry, the same way ``crc_generate`` already accepts custom_params.
    """
    if (algorithm is None) == (custom_params is None):
        raise ValueError("supply exactly one of algorithm or custom_params")
    if algorithm is not None:
        if algorithm not in ALGORITHMS:
            raise ValueError(f"unknown algorithm {algorithm!r}; use crc_list to browse")
        return ALGORITHMS[algorithm], algorithm
    cp = custom_params
    assert cp is not None
    if "width" not in cp or "poly" not in cp:
        raise ValueError(
            "custom_params requires at least 'width' and 'poly' "
            "(plus optional init / refin / refout / xorout)")
    width, poly = int(cp["width"]), int(cp["poly"])
    init = int(cp.get("init", 0))
    refin, refout = bool(cp.get("refin", False)), bool(cp.get("refout", False))
    xorout = int(cp.get("xorout", 0))
    check = generic_crc(b"123456789", width, poly, init, refin, refout, xorout)
    info = AlgorithmInfo(
        width=width, poly=poly, init=init, refin=refin, refout=refout,
        xorout=xorout, check=check, desc=str(cp.get("desc", "")), source="custom",
    )
    return info, str(cp.get("name", "custom"))


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
            "a multi-language code generator (C / C# / Go / Python / Rust "
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
    )
    def crc_list(glob: str | None = None) -> dict[str, Any]:
        import fnmatch as _fnmatch

        pat = glob or "*"
        names = sorted(n for n in ALGORITHMS if _fnmatch.fnmatch(n, pat))
        return {
            "algorithms": [
                {
                    "name": n,
                    "width": ALGORITHMS[n].width,
                    "desc": ALGORITHMS[n].desc,
                }
                for n in names
            ],
            "count": len(names),
        }

    # ----- crc_info -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_info",
        description=(
            "Get the full Rocksoft/Williams parameters (width, poly, "
            "init, refin, refout, xorout, check) for one catalogue "
            "algorithm.  Use after crc_list to confirm parameters before "
            "crc_generate, or to answer 'what polynomial does "
            "crc16-modbus use?'.  Numeric fields are surfaced in both "
            "decimal (poly, init, xorout, check) and hex (poly_hex, "
            "init_hex, xorout_hex, check_hex)."
        ),
    )
    def crc_info(name: str) -> dict[str, Any]:
        algo = ALGORITHMS.get(name)
        if algo is None:
            raise ValueError(f"unknown algorithm {name!r}; use crc_list to browse")
        return algorithm_to_dict(name, algo)

    # ----- crc_detect -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_detect",
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
            "and pass the data-only bytes as the packet."
        ),
    )
    def crc_detect(
        packet_hex: str | None = None,
        packet_text: str | None = None,
        packet_b64: str | None = None,
        target_crc: int | None = None,
        target_crc_hex: str | None = None,
        endian: ENDIAN_ENUM = "both",
        algorithms: str | None = None,
        match: MATCH_ENUM = "first",
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        packet = parse_packet(packet_hex, packet_text, packet_b64)
        target = parse_target_crc(target_crc, target_crc_hex)
        result = detect(
            packet,
            endian=endian,
            algorithms=algorithms,
            match=match,
            encoding=encoding,
            target_crc=target,
        )
        return {
            "matched": result.matched,
            "candidates": [detect_match_to_dict(m) for m in result.candidates],
        }

    # ----- crc_encode -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_encode",
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
        info, _label = _resolve_algorithm(algorithm, custom_params)
        hex_w = (info.width + 3) // 4
        if (data_text is None) == (data_b64 is None):
            raise ValueError("supply exactly one of data_text or data_b64")
        if data_b64 is not None:
            import base64 as _b64

            try:
                raw = _b64.b64decode(data_b64, validate=True)
            except Exception as e:
                raise ValueError(f"data_b64 not valid base64: {e}") from e
            packet = encode(raw, info, endianness=crc_byte_order)
            crc_int = encode_int(raw, info)
            return {
                "packet_b64": _b64.b64encode(packet).decode("ascii"),
                "packet_hex": packet.hex(),
                "crc": crc_int,
                "crc_hex": f"0x{crc_int:0{hex_w}X}",
            }
        # text branch
        assert data_text is not None
        text = encode_text(
            data_text,
            info,
            sep=sep,
            leader=leader,
            uppercase=uppercase,
            endianness=crc_byte_order,
            encoding=encoding,
            fmt=fmt,
        )
        crc_int = encode_int(data_text, info, encoding=encoding)
        return {
            "packet_text": text,
            "crc": crc_int,
            "crc_hex": f"0x{crc_int:0{hex_w}X}",
        }

    # ----- crc_compute -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_compute",
        description=(
            "Compute the raw CRC integer for data without packaging or "
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
    )
    def crc_compute(
        algorithm: str | None = None,
        custom_params: dict[str, Any] | None = None,
        data_text: str | None = None,
        data_b64: str | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        info, _label = _resolve_algorithm(algorithm, custom_params)
        if (data_text is None) == (data_b64 is None):
            raise ValueError("supply exactly one of data_text or data_b64")
        if data_b64 is not None:
            import base64 as _b64

            try:
                raw = _b64.b64decode(data_b64, validate=True)
            except Exception as e:
                raise ValueError(f"data_b64 not valid base64: {e}") from e
            crc = encode_int(raw, info)
        else:
            assert data_text is not None
            crc = encode_int(data_text, info, encoding=encoding)
        hex_w = (info.width + 3) // 4
        return {
            "crc": crc,
            "crc_hex": f"0x{crc:0{hex_w}X}",
            "width": info.width,
        }

    # ----- crc_compute_many -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_compute_many",
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
    )
    def crc_compute_many(
        algorithm: str | None = None,
        custom_params: dict[str, Any] | None = None,
        data_texts: list[str] | None = None,
        data_b64s: list[str] | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        a, label = _resolve_algorithm(algorithm, custom_params)
        if (data_texts is None) == (data_b64s is None):
            raise ValueError("supply exactly one of data_texts or data_b64s")

        if data_b64s is not None:
            import base64 as _b64

            buffers: list[bytes] = []
            for i, item in enumerate(data_b64s):
                try:
                    buffers.append(_b64.b64decode(item, validate=True))
                except Exception as e:
                    raise ValueError(f"data_b64s[{i}] not valid base64: {e}") from e
        else:
            assert data_texts is not None
            buffers = [t.encode(encoding) for t in data_texts]

        results = generic_crc_many(
            buffers, a.width, a.poly, a.init, a.refin, a.refout, a.xorout
        )
        hex_w = (a.width + 3) // 4
        return {
            "algorithm": label,
            "width": a.width,
            "count": len(results),
            "results": [
                {"crc": c, "crc_hex": f"0x{c:0{hex_w}X}"} for c in results
            ],
        }

    # ----- crc_reverse -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_reverse",
        description=(
            "Reverse-engineer the parameters of an UNKNOWN / custom CRC from "
            "captured packets -- the recovery counterpart to crc_detect (which "
            "only identifies CRCs already in the catalogue).  Takes the SAME "
            "input shape as crc_detect: whole frames with the CRC as the "
            "trailing field.  Use this when a device's CRC is NOT any known "
            "algorithm; it solves the Rocksoft/Williams parameters algebraically "
            "over GF(2).\n"
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
            "Guarantee: a recovered model is correct on unseen data, or honestly "
            "reports underdetermined -- never confidently wrong.  "
            "std_algo_only=True restricts to the catalogue tier (identical to "
            "crc_detect)."
        ),
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
        if not packets:
            raise ValueError(
                "packets must be a non-empty list of frames (message followed "
                "by the CRC), each a hex string (or base64 / text per "
                "packet_format)"
            )
        frames_in: list[bytes | str]
        if packet_format == "text":
            frames_in = list(packets)  # text frames pass through to _parse_text
        else:
            frames_in = []
            for i, p in enumerate(packets):
                try:
                    raw = (parse_packet(None, None, p) if packet_format == "base64"
                           else parse_packet(p, None, None))
                except ValueError as e:
                    raise ValueError(f"packets[{i}]: {e}") from e
                assert isinstance(raw, bytes)  # hex / base64 forms decode to bytes
                frames_in.append(raw)

        result = reverse_packets(
            frames_in, crc_bytes=crc_bytes, crc_byte_order=crc_byte_order,
            encoding=encoding, std_algo_only=std_algo_only, width=width,
            refin=refin, refout=refout, poly=poly, init=init, xorout=xorout,
            validate=validate,
        )

        def _model(info: AlgorithmInfo) -> dict[str, Any]:
            hw = (info.width + 3) // 4
            return {
                "width": info.width,
                "poly": info.poly, "poly_hex": f"0x{info.poly:0{hw}X}",
                "init": info.init, "init_hex": f"0x{info.init:0{hw}X}",
                "refin": info.refin, "refout": info.refout,
                "xorout": info.xorout, "xorout_hex": f"0x{info.xorout:0{hw}X}",
                "check": info.check, "check_hex": f"0x{info.check:0{hw}X}",
            }

        return {
            "status": result.status,
            "catalogue_name": result.catalogue_name,
            "ambiguity_bits": result.ambiguity_bits,
            "validated_frames": result.validated_frames,
            "candidates": [_model(c) for c in result.candidates],
            "note": result.note,
        }

    # ----- crc_verify -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_verify",
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
        info, label = _resolve_algorithm(algorithm, custom_params)
        if sum(p is not None for p in (packet_hex, packet_text, packet_b64)) != 1:
            raise ValueError(
                "supply exactly one of packet_hex / packet_text / packet_b64")
        if packet_text is not None:
            result = verify(
                packet_text, info, endianness=crc_byte_order, encoding=encoding)
        else:
            packet = parse_packet(packet_hex, None, packet_b64)
            assert isinstance(packet, bytes)  # hex / base64 forms decode to bytes
            result = verify(packet, info, endianness=crc_byte_order)
        hw = (result.width + 3) // 4
        return {
            "valid": result.valid,
            "expected": result.expected,
            "expected_hex": f"0x{result.expected:0{hw}X}",
            "actual": result.actual,
            "actual_hex": f"0x{result.actual:0{hw}X}",
            "width": result.width,
            "algorithm": label,
        }

    # ----- crc_generate -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_generate",
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
            "other 68 catalogue algorithms and for embedded targets "
            "without a zlib equivalent.\n"
            "\n"
            "Supply algorithm OR custom_params (Rocksoft/Williams tuple); "
            "they are mutually exclusive.  'algorithm' accepts a single "
            "catalogue name, several names as a list, or a space-separated "
            "string (e.g. 'crc32 crc16-modbus crc8') -- multiple names "
            "bundle into ONE file (one .h + one .c for C), each keeping its "
            "catalogue-derived function names; per-symbol tables keep the "
            "bundle collision-free.  'symbol' renames the single emitted "
            "function and is rejected with more than one algorithm.  The "
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
            "<returns> XML doc comments for C#.  The returned 'algorithms' "
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
    )
    def crc_generate(
        language: LANG_ENUM,
        algorithm: str | list[str] | None = None,
        variant: VARIANT_ENUM = "auto",
        symbol: str | None = None,
        custom_params: dict[str, Any] | None = None,
        comment_style: COMMENT_STYLE_ENUM = "plain",
        naming: NAMING_ENUM | None = None,
    ) -> dict[str, Any]:
        info = LANGUAGES[language]
        if (algorithm is None) == (custom_params is None):
            raise ValueError("supply exactly one of algorithm or custom_params")

        # Resolve naming: None -> the language's idiomatic default; reject a
        # convention the language doesn't offer (e.g. pascal for Rust).
        naming_resolved = naming or info.default_naming
        if naming_resolved not in info.naming:
            raise ValueError(
                f"naming={naming_resolved!r} is not valid for "
                f"language={language!r}; this language offers "
                f"{sorted(info.naming)} (default {info.default_naming!r})"
            )

        if algorithm is not None:
            # Accept one name, a space-separated string, or a list -- no
            # catalogue name contains a space, so splitting is unambiguous.
            # Several names bundle into one file (one .h + one .c for C);
            # per-symbol tables keep the merge collision-free.  Dedup,
            # order-preserving.
            requested = algorithm.split() if isinstance(algorithm, str) else list(algorithm)
            names = list(dict.fromkeys(requested))
            if not names:
                raise ValueError("algorithm is empty; supply one or more catalogue names")
            unknown = [n for n in names if n not in ALGORITHMS]
            if unknown:
                raise ValueError(
                    f"unknown algorithm {unknown[0]!r}; use crc_list to browse"
                )
            if symbol is not None and len(names) > 1:
                raise ValueError(
                    "symbol names a single function; omit it when generating "
                    "multiple algorithms (each uses its catalogue name)"
                )
            # "auto" -> the fastest variant valid for EVERY algorithm in the
            # bundle (the intersection's fastest; variants_for_width is ordered
            # slowest-to-fastest and always includes bitwise, so it's non-empty).
            if variant == "auto":
                per = [info.variants_for_width(ALGORITHMS[n].width) for n in names]
                common = [v for v in per[0] if all(v in p for p in per)]
                variant = cast(VARIANT_ENUM, common[-1])
            # variant must be legal for EVERY algorithm's width (slice8 is
            # 32/64-only), so a mixed-width bundle can't silently break one.
            for n in names:
                w = ALGORITHMS[n].width
                valid_variants = info.variants_for_width(w)
                if variant not in valid_variants:
                    raise ValueError(
                        f"variant={variant!r} is not valid for {n!r} (width {w}) "
                        f"in language={language!r}; valid here: {list(valid_variants)}"
                    )
            outputs = [
                info.generator(  # type: ignore[call-arg]
                    n,
                    symbol=(symbol if len(names) == 1 else None),
                    variant=variant,
                    comment_style=comment_style,
                    naming=naming_resolved,
                )
                for n in names
            ]
            result = (
                outputs[0] if len(names) == 1
                else info.combiner(outputs, symbol or "crcglot")  # type: ignore[call-arg]
            )
            generated = names
            advised_algos: list[str | AlgorithmInfo] = list(names)
        else:
            assert custom_params is not None
            cp = custom_params
            width = int(cp.get("width", 0))
            if variant == "auto":
                variant = cast(VARIANT_ENUM, info.fastest_variant_for_width(width))
            valid_variants = info.variants_for_width(width)
            if variant not in valid_variants:
                raise ValueError(
                    f"variant={variant!r} is not valid for language={language!r} "
                    f"at width={width}; valid variants for this cell: "
                    f"{list(valid_variants)}"
                )
            poly = int(cp["poly"])
            init = int(cp.get("init", 0))
            refin = bool(cp.get("refin", False))
            refout = bool(cp.get("refout", False))
            xorout = int(cp.get("xorout", 0))
            desc = str(cp.get("desc", ""))
            cust_name = str(cp.get("name", "crc_custom"))
            check = generic_crc(
                b"123456789",
                width,
                poly,
                init,
                refin,
                refout,
                xorout,
            )
            algo_info = AlgorithmInfo(
                width=width,
                poly=poly,
                init=init,
                refin=refin,
                refout=refout,
                xorout=xorout,
                check=check,
                desc=desc,
                source="custom",
            )
            result = info.generator_from_entry(  # type: ignore[call-arg]
                cust_name,
                algo_info,
                symbol=symbol,
                variant=variant,
                comment_style=comment_style,
                naming=naming_resolved,
            )
            generated = [cust_name]
            advised_algos = [algo_info]

        files: list[dict[str, str]]
        if isinstance(result, tuple):
            # C: (header, source).
            files = [
                {"extension": info.extensions[0], "content": result[0]},
                {"extension": info.extensions[1], "content": result[1]},
            ]
        else:
            files = [{"extension": info.extensions[0], "content": result}]
        return {
            "language": language,
            "variant": variant,
            "comment_style": comment_style,
            "naming": naming_resolved,
            "algorithms": generated,
            # Advisory is a dataclass; JSON-project it for the wire (the MCP
            # result is serialized to JSON over stdio).  asdict keeps the dict
            # in lockstep with the dataclass's fields.
            "advisories": [asdict(a) for a in info.advisories_for(advised_algos)],
            "files": files,
            # Reinforce at the point of use (the result is what's in context when
            # the model decides how to present it): the content is a whole file,
            # not a snippet to abridge.  See the tool's OUTPUT HANDLING note.
            "note": (
                "Each files[].content is a COMPLETE drop-in source file -- write "
                "it whole to a file (never truncate tables or omit functions); "
                "paste it inline only if the user asked to see the code, and "
                "then in full."
            ),
        }

    # ----- crc_credits -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_credits",
        description=(
            "Return the projects crcglot stands on (reveng catalogue, "
            "zlib, Rocksoft/Williams parameterization)."
        ),
    )
    def crc_credits() -> dict[str, str]:
        return {"attribution": ATTRIBUTION}

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
            "language, and/or crc_encode / crc_verify to build and check frames."
            + ctx
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
                if len(namings) > 1 else f"naming {namings[0]} (only)")
            style_part = (
                f"comment styles {styles}"
                if len(styles) > 1 else f"comment style {styles[0]} (only)")
            rows.append(
                f"- {code} ({info.display_name}): {naming_part}; {style_part}")
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
