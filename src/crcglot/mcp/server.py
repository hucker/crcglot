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
from typing import Any, Literal

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
    reverse,
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

VARIANT_ENUM = Literal["bitwise", "table", "slice8"]
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
            "Use crc_list / crc_info to browse; crc_detect to identify "
            "the CRC of a captured packet against the catalogue; crc_reverse "
            "to recover an UNKNOWN / custom CRC's parameters from message-CRC "
            "pairs; crc_compute for raw integer CRC values; crc_encode to "
            "build a packet; crc_generate to emit verified source code.  For "
            "IEEE crc32 and crc32-jamcrc "
            "specifically, prefer the target language's stdlib (e.g. "
            "Python's zlib.crc32) -- those algorithms run ~30x faster "
            "via CPU CRC instructions than any generated code."
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
            "test fixtures, or send a freshly-CRC'd packet on the wire.\n"
            "\n"
            "crc_byte_order controls the byte order of the appended CRC "
            "bytes only (the data portion is unaffected)."
        ),
    )
    def crc_encode(
        algorithm: str,
        data_text: str | None = None,
        data_b64: str | None = None,
        crc_byte_order: CRC_BYTE_ORDER_ENUM = "big",
        sep: str = " ",
        leader: str = "",
        uppercase: bool = False,
        fmt: str = "{data}{sep}{leader}{crc}",
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        if algorithm not in ALGORITHMS:
            raise ValueError(f"unknown algorithm {algorithm!r}; use crc_list to browse")
        if (data_text is None) == (data_b64 is None):
            raise ValueError("supply exactly one of data_text or data_b64")
        if data_b64 is not None:
            import base64 as _b64

            try:
                raw = _b64.b64decode(data_b64, validate=True)
            except Exception as e:
                raise ValueError(f"data_b64 not valid base64: {e}") from e
            packet = encode(raw, algorithm, endianness=crc_byte_order)
            crc_int = encode_int(raw, algorithm)
            return {
                "packet_b64": _b64.b64encode(packet).decode("ascii"),
                "packet_hex": packet.hex(),
                "crc": crc_int,
                "crc_hex": f"0x{crc_int:0{(ALGORITHMS[algorithm].width + 3) // 4}X}",
            }
        # text branch
        assert data_text is not None
        text = encode_text(
            data_text,
            algorithm,
            sep=sep,
            leader=leader,
            uppercase=uppercase,
            endianness=crc_byte_order,
            encoding=encoding,
            fmt=fmt,
        )
        crc_int = encode_int(data_text, algorithm, encoding=encoding)
        return {
            "packet_text": text,
            "crc": crc_int,
            "crc_hex": f"0x{crc_int:0{(ALGORITHMS[algorithm].width + 3) // 4}X}",
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
            "Python-specific perf note: if algorithm is 'crc32' or "
            "'crc32-jamcrc', the stdlib's zlib.crc32 produces the same "
            "value with one fewer round-trip and is the routine crcglot "
            "delegates to internally anyway."
        ),
    )
    def crc_compute(
        algorithm: str,
        data_text: str | None = None,
        data_b64: str | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        if algorithm not in ALGORITHMS:
            raise ValueError(f"unknown algorithm {algorithm!r}; use crc_list to browse")
        if (data_text is None) == (data_b64 is None):
            raise ValueError("supply exactly one of data_text or data_b64")
        if data_b64 is not None:
            import base64 as _b64

            try:
                raw = _b64.b64decode(data_b64, validate=True)
            except Exception as e:
                raise ValueError(f"data_b64 not valid base64: {e}") from e
            crc = encode_int(raw, algorithm)
        else:
            assert data_text is not None
            crc = encode_int(data_text, algorithm, encoding=encoding)
        width = ALGORITHMS[algorithm].width
        hex_w = (width + 3) // 4
        return {
            "crc": crc,
            "crc_hex": f"0x{crc:0{hex_w}X}",
            "width": width,
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
            "data_b64s for binary payloads."
        ),
    )
    def crc_compute_many(
        algorithm: str,
        data_texts: list[str] | None = None,
        data_b64s: list[str] | None = None,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        if algorithm not in ALGORITHMS:
            raise ValueError(f"unknown algorithm {algorithm!r}; use crc_list to browse")
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

        a = ALGORITHMS[algorithm]
        results = generic_crc_many(
            buffers, a.width, a.poly, a.init, a.refin, a.refout, a.xorout
        )
        hex_w = (a.width + 3) // 4
        return {
            "algorithm": algorithm,
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
            "(message, crc) codewords -- the recovery counterpart to "
            "crc_detect (which only identifies CRCs already in the catalogue). "
            "Use this when a device's CRC is NOT any known algorithm: supply "
            "several codewords and it solves the Rocksoft/Williams parameters "
            "algebraically over GF(2).\n"
            "\n"
            "Each codeword is an object {message_hex | message_b64, crc} -- the "
            "message bytes (one of hex or base64) and the integer CRC it "
            "produced.  Supply SEVERAL VARIED frames: varied in CONTENT (so the "
            "polynomial converges) and in LENGTH (to separate init from "
            "xorout); ~4+ is typical and more is better.  Fix any known "
            "parameter (width / refin / refout / poly / init / xorout) to "
            "reduce how many codewords are needed.\n"
            "\n"
            "Returns 'status': 'catalogue' (matched a known algorithm), "
            "'unique' (recovered, one parameter set), 'equivalent' (recovered "
            "and verified, but several (init, xorout) labellings are "
            "observationally identical -- ALL are returned in 'candidates', a "
            "complete and provably-exhaustive set of size 2**ambiguity_bits; "
            "the polynomial is always unique), 'underdetermined' (supply more "
            "varied frames), or 'none'.  Every returned model is self-verified "
            "against the engine, and 'validated_frames' reports a held-out "
            "generalisation check.  Guarantee: a recovered model is correct on "
            "unseen data, or honestly reports underdetermined -- never "
            "confidently wrong.  std_algo_only=True restricts to the catalogue "
            "tier (identical to crc_detect on these pairs)."
        ),
    )
    def crc_reverse(
        codewords: list[dict[str, Any]],
        std_algo_only: bool = False,
        width: int | None = None,
        refin: bool | None = None,
        refout: bool | None = None,
        poly: int | None = None,
        init: int | None = None,
        xorout: int | None = None,
        validate: bool = True,
    ) -> dict[str, Any]:
        import base64 as _b64

        if not codewords:
            raise ValueError(
                "codewords must be a non-empty list of "
                "{message_hex|message_b64, crc}"
            )
        frames: list[tuple[bytes, int]] = []
        for i, cw in enumerate(codewords):
            if "crc" not in cw:
                raise ValueError(f"codewords[{i}]: missing 'crc'")
            raw_crc = cw["crc"]
            crc = int(raw_crc, 0) if isinstance(raw_crc, str) else int(raw_crc)
            mh, mb = cw.get("message_hex"), cw.get("message_b64")
            if (mh is None) == (mb is None):
                raise ValueError(
                    f"codewords[{i}]: supply exactly one of "
                    "message_hex or message_b64"
                )
            try:
                if mh is not None:
                    msg = bytes.fromhex(mh)
                else:
                    assert mb is not None  # narrowed by the check above
                    msg = _b64.b64decode(mb, validate=True)
            except Exception as e:
                raise ValueError(f"codewords[{i}]: bad message bytes: {e}") from e
            frames.append((msg, crc))

        result = reverse(
            frames, std_algo_only=std_algo_only, width=width,
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

    # ----- crc_generate -----

    @mcp.tool(
        annotations=_READONLY,
        name="crc_generate",
        description=(
            "Generate verified CRC source code for one (language, "
            "algorithm, variant) cell.  Supports C, C#, Go, Java, Python, "
            "Rust, TypeScript, Verilog, VHDL.  Variants: 'bitwise' "
            "(smallest, default), 'table' (256-entry LUT, faster), "
            "'slice8' (8 tables, fastest, width 32/64 only, not on "
            "Python / Verilog / VHDL).  Every emitted file embeds a "
            "_self_test() against the reveng canonical vector for "
            "b'123456789'.\n"
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
            "lists what was generated."
        ),
    )
    def crc_generate(
        language: LANG_ENUM,
        algorithm: str | list[str] | None = None,
        variant: VARIANT_ENUM = "bitwise",
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
        else:
            assert custom_params is not None
            cp = custom_params
            width = int(cp.get("width", 0))
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
            "files": files,
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
