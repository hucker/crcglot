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
from typing import Any, Literal, cast

from mcp.server import FastMCP
from mcp.types import ToolAnnotations

from crcglot import (
    custom_algorithm,
    ALGORITHMS,
    ATTRIBUTION,
    LANGUAGES,
    AlgorithmInfo,
    detect,
    identify_trailer,
    encode,
    encode_int,
    encode_text,
    generic_crc_many,
    reverse_packets,
    variant_info,
    verify,
)
from crcglot.catalogue import unknown_algorithm_error
from crcglot.verbs import VERBS
from crcglot.mcp._wire import (
    algorithm_to_dict,
    trailer_result_to_dict,
    detect_match_to_dict,
    language_to_dict,
    parse_packet,
    parse_target_crc,
    vectors_to_dict,
)


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


def _as_int(value: Any, field: str) -> int:
    """Coerce a ``custom_params`` numeric field to int.

    Accepts a plain int or a hex / decimal string (``"0x1021"`` / ``"4129"``):
    LLMs routinely quote a polynomial in hex straight from a datasheet, and a
    bare ``int(value)`` rejects ``"0x1021"``.  ``int(s, 0)`` reads the base
    from a ``0x`` / ``0o`` / ``0b`` prefix and defaults to decimal.

    Raises:
        ValueError: ``value`` is a bool, or a string that is not a valid
            integer literal, or some other non-int type.
    """
    if isinstance(value, bool):
        # bool is an int subclass; refin / refout are the boolean fields, so a
        # True / False in a numeric slot is almost certainly a mistake.
        raise ValueError(f"custom_params[{field!r}] must be an integer, not a bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError as e:
            raise ValueError(
                f"custom_params[{field!r}] must be an integer or hex string "
                f"(e.g. 4129 or '0x1021'); got {value!r}"
            ) from e
    raise ValueError(
        f"custom_params[{field!r}] must be an integer or hex string; "
        f"got {type(value).__name__}"
    )


def _parse_custom_params(cp: dict[str, Any]) -> AlgorithmInfo:
    """Validate a ``custom_params`` dict into an :class:`AlgorithmInfo`.

    Requires ``width`` and ``poly``; accepts hex / decimal strings or ints for
    every numeric field (see :func:`_as_int`).  The width range and the rest of
    the Rocksoft validity check live in :func:`crcglot.custom_algorithm` (the
    engine), so the CLI, the Python API, and every MCP tool reject the same
    invalid parameter sets with the same message.

    Raises:
        ValueError: ``width`` or ``poly`` missing, a numeric field
            unparseable, or the engine rejects the parameter set.
    """
    missing = [k for k in ("width", "poly") if k not in cp]
    if missing:
        raise ValueError(
            f"custom_params is missing required field(s) {missing}; supply at "
            "least 'width' and 'poly' (optional: init / refin / refout / "
            "xorout / name / desc)"
        )
    return custom_algorithm(
        width=_as_int(cp["width"], "width"),
        poly=_as_int(cp["poly"], "poly"),
        init=_as_int(cp.get("init", 0), "init"),
        refin=bool(cp.get("refin", False)),
        refout=bool(cp.get("refout", False)),
        xorout=_as_int(cp.get("xorout", 0), "xorout"),
        desc=str(cp.get("desc", "")),
    )


def _resolve_algorithm(
    algorithm: str | None,
    custom_params: dict[str, Any] | None,
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
            raise unknown_algorithm_error(algorithm, surface="mcp")
        return ALGORITHMS[algorithm], algorithm
    assert custom_params is not None
    return _parse_custom_params(custom_params), str(custom_params.get("name", "custom"))


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
        description=_tool_description("info"),
    )
    def crc_info(name: str) -> dict[str, Any]:
        algo = ALGORITHMS.get(name)
        if algo is None:
            raise unknown_algorithm_error(name, surface="mcp")
        return algorithm_to_dict(name, algo)

    # ----- crc_self_test_vectors -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_self_test_vectors",
        description=_tool_description("vectors"),
    )
    def crc_self_test_vectors(algorithm: str) -> dict[str, Any]:
        algo = ALGORITHMS.get(algorithm)
        if algo is None:
            raise unknown_algorithm_error(algorithm, surface="mcp")
        return vectors_to_dict(algorithm, algo)

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
        target = parse_target_crc(target_crc, target_crc_hex)
        # A hex packet keeps its representation (mode="hex" -> form="hex");
        # parse_packet would decode it to bytes, reading as "binary".  base64
        # is a transport encoding (not a form), so it decodes to binary.
        if packet_hex is not None and packet_text is None and packet_b64 is None:
            packet: bytes | str = packet_hex
            detect_mode: Literal["auto", "hex"] = "hex"
        else:
            packet = parse_packet(packet_hex, packet_text, packet_b64)
            detect_mode = "auto"
        result = detect(
            packet,
            mode=detect_mode,
            endian=endian,
            algorithms=algorithms,
            width=width,
            match=match,
            encoding=encoding,
            target_crc=target,
            form=form,
        )
        return {
            "matched": result.matched,
            "candidates": [detect_match_to_dict(m) for m in result.candidates],
            "trailer_hint": (
                trailer_result_to_dict(result.trailer_hint)
                if result.trailer_hint
                else None
            ),
        }

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
        if not packets:
            raise ValueError(
                "packets must be a non-empty list of frames (message followed "
                "by the checksum), each a hex string (or base64 / text per "
                "packet_format)"
            )
        frames_in: list[bytes | str]
        if packet_format == "text":
            frames_in = list(packets)
        else:
            frames_in = []
            for i, p in enumerate(packets):
                try:
                    raw = (
                        parse_packet(None, None, p)
                        if packet_format == "base64"
                        else parse_packet(p, None, None)
                    )
                except ValueError as e:
                    raise ValueError(f"packets[{i}]: {e}") from e
                assert isinstance(raw, bytes)
                frames_in.append(raw)
        mode = "text" if packet_format == "text" else "binary"
        result = identify_trailer(
            frames_in,
            mode=mode,
            endian=endian,
            encoding=encoding,
            trailers=trailers,
        )
        return trailer_result_to_dict(result)

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
        description=_tool_description("compute"),
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
        description=_tool_description("compute_many"),
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

        results = generic_crc_many(buffers, a)
        hex_w = (a.width + 3) // 4
        return {
            "algorithm": label,
            "width": a.width,
            "count": len(results),
            "results": [{"crc": c, "crc_hex": f"0x{c:0{hex_w}X}"} for c in results],
        }

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
                    raw = (
                        parse_packet(None, None, p)
                        if packet_format == "base64"
                        else parse_packet(p, None, None)
                    )
                except ValueError as e:
                    raise ValueError(f"packets[{i}]: {e}") from e
                assert isinstance(raw, bytes)  # hex / base64 forms decode to bytes
                frames_in.append(raw)

        result = reverse_packets(
            frames_in,
            crc_bytes=crc_bytes,
            crc_byte_order=crc_byte_order,
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

        def _model(info: AlgorithmInfo) -> dict[str, Any]:
            hw = (info.width + 3) // 4
            return {
                "width": info.width,
                "poly": info.poly,
                "poly_hex": f"0x{info.poly:0{hw}X}",
                "init": info.init,
                "init_hex": f"0x{info.init:0{hw}X}",
                "refin": info.refin,
                "refout": info.refout,
                "xorout": info.xorout,
                "xorout_hex": f"0x{info.xorout:0{hw}X}",
                "check": info.check,
                "check_hex": f"0x{info.check:0{hw}X}",
            }

        return {
            "status": result.status,
            "catalogue_name": result.catalogue_name,
            "ambiguity_bits": result.ambiguity_bits,
            "validated_frames": result.validated_frames,
            "candidates": [_model(c) for c in result.candidates],
            "note": result.note,
            "trailer_hint": (
                trailer_result_to_dict(result.trailer_hint)
                if result.trailer_hint
                else None
            ),
        }

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
        info, label = _resolve_algorithm(algorithm, custom_params)
        if sum(p is not None for p in (packet_hex, packet_text, packet_b64)) != 1:
            raise ValueError(
                "supply exactly one of packet_hex / packet_text / packet_b64"
            )
        if packet_text is not None:
            result = verify(
                packet_text, info, endianness=crc_byte_order, encoding=encoding
            )
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
            requested = (
                algorithm.split() if isinstance(algorithm, str) else list(algorithm)
            )
            names = list(dict.fromkeys(requested))
            if not names:
                raise ValueError(
                    "algorithm is empty; supply one or more catalogue names"
                )
            unknown = [n for n in names if n not in ALGORITHMS]
            if unknown:
                raise unknown_algorithm_error(unknown[0], surface="mcp")
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
            generated = names
            advised_algos: list[str | AlgorithmInfo] = list(names)
        else:
            assert custom_params is not None
            # Validate + parse once (requires width / poly, accepts hex
            # strings, range-checks width in the engine) before any
            # variant logic, so width is known-good here.
            algo_info = _parse_custom_params(custom_params)
            cust_name = str(custom_params.get("name", "crc_custom"))
            width = algo_info.width
            if variant == "auto":
                variant = cast(VARIANT_ENUM, info.fastest_variant_for_width(width))
            valid_variants = info.variants_for_width(width)
            if variant not in valid_variants:
                raise ValueError(
                    f"variant={variant!r} is not valid for language={language!r} "
                    f"at width={width}; valid variants for this cell: "
                    f"{list(valid_variants)}"
                )
            generated = [name or cust_name]
            advised_algos = [algo_info]

        # crcglot owns naming + filenames: one call returns ready-to-write,
        # correctly-named files (Java's class == file, C's .h/.c pair).  Pass the
        # already-resolved concrete variant so the reported value matches.
        gfiles = info.generate_files(
            algorithm=(names if algorithm is not None else None),
            custom=(algo_info if custom_params is not None else None),
            variant=variant,
            comment_style=comment_style,
            naming=naming_resolved,
            name=(name if algorithm is not None else (name or cust_name)),
            symbol=symbol,
        )
        files = [
            {
                "filename": f.filename,
                "extension": f.filename[f.filename.rfind(".") :],
                "content": f.content,
                "role": f.role,
            }
            for f in gfiles
        ]
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
        description=_tool_description("credits"),
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
