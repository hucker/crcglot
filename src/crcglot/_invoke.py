"""The verb bodies and the public :func:`call_verb` invoker.

One implementation per verb in :data:`crcglot.VERBS`, taking the manifest's
parameter names (``packet_hex``, ``crc_byte_order``, ``custom_params``, ...)
and returning the JSON-ready wire dict the manifest's ``result_fields``
describe.  crcglot's own MCP tools are thin wrappers over these same
functions, and an external frontend calls them uniformly through
:func:`call_verb`, so every renderer of the manifest shares one code path.

Stdlib + crcglot core only; the MCP SDK is never imported here (it stays in
``crcglot.mcp.server``, behind the ``[mcp]`` extra).
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from crcglot import (
    ALGORITHMS,
    ATTRIBUTION,
    LANGUAGES,
    AlgorithmInfo,
    custom_algorithm,
    detect,
    encode,
    encode_int,
    encode_text,
    generic_crc_many,
    identify_trailer,
    reverse_packets,
    verify,
)
from crcglot._wire import (
    algorithm_to_dict,
    detect_match_to_dict,
    parse_packet,
    parse_target_crc,
    trailer_result_to_dict,
    vectors_to_dict,
)
from crcglot.catalogue import unknown_algorithm_error
from crcglot.exceptions import UnknownParamError, UnknownVerbError
from crcglot.verbs import VERBS, verb_info


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
    *,
    surface: str = "python",
) -> tuple[AlgorithmInfo, str]:
    """Resolve a catalogue name OR a custom Rocksoft tuple to ``(info, label)``.

    ``custom_params`` is ``{width, poly, init?, refin?, refout?, xorout?, name?,
    desc?}`` (``width`` and ``poly`` required); its ``check`` is computed.  This
    lets the compute / encode / verify verbs work with a custom or *recovered*
    polynomial -- e.g. the parameter set the reverse verb returns -- not just a
    catalogue entry.  ``surface`` picks the where-to-look-next hint on an
    unknown-algorithm error (``"python"`` here, ``"mcp"`` from the MCP tools).
    """
    if (algorithm is None) == (custom_params is None):
        raise ValueError("supply exactly one of algorithm or custom_params")
    if algorithm is not None:
        if algorithm not in ALGORITHMS:
            raise unknown_algorithm_error(algorithm, surface=surface)
        return ALGORITHMS[algorithm], algorithm
    assert custom_params is not None
    return _parse_custom_params(custom_params), str(custom_params.get("name", "custom"))


# ── the verb bodies ──────────────────────────────────────────────────────────
# Signatures mirror the VERBS ParamSpecs (same names, same tool-surface
# defaults); each returns the wire dict its VerbSpec.result_fields describe.


def _verb_list(glob: str | None = None) -> dict[str, Any]:
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


def _verb_info(name: str, *, surface: str = "python") -> dict[str, Any]:
    algo = ALGORITHMS.get(name)
    if algo is None:
        raise unknown_algorithm_error(name, surface=surface)
    return algorithm_to_dict(name, algo)


def _verb_vectors(algorithm: str, *, surface: str = "python") -> dict[str, Any]:
    algo = ALGORITHMS.get(algorithm)
    if algo is None:
        raise unknown_algorithm_error(algorithm, surface=surface)
    return vectors_to_dict(algorithm, algo)


def _verb_detect(
    packet_hex: str | None = None,
    packet_text: str | None = None,
    packet_b64: str | None = None,
    target_crc: int | None = None,
    target_crc_hex: str | None = None,
    endian: str = "both",
    algorithms: str | None = None,
    width: int | None = None,
    match: str = "first",
    encoding: str = "utf-8",
    form: str | None = None,
) -> dict[str, Any]:
    target = parse_target_crc(target_crc, target_crc_hex)
    # A hex packet keeps its representation (mode="hex" -> form="hex");
    # parse_packet would decode it to bytes, reading as "binary".  base64
    # is a transport encoding (not a form), so it decodes to binary.
    if packet_hex is not None and packet_text is None and packet_b64 is None:
        packet: bytes | str = packet_hex
        detect_mode = "hex"
    else:
        packet = parse_packet(packet_hex, packet_text, packet_b64)
        detect_mode = "auto"
    result = detect(
        packet,
        mode=detect_mode,
        endian=endian,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # detect validates the value
        algorithms=algorithms,
        width=width,
        match=match,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # detect validates the value
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


def _verb_identify_trailer(
    packets: list[str],
    packet_format: str = "hex",
    endian: str = "both",
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
        endian=endian,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # identify_trailer validates the value
        encoding=encoding,
        trailers=trailers,
    )
    return trailer_result_to_dict(result)


def _verb_reverse(
    packets: list[str],
    crc_bytes: int | None = None,
    crc_byte_order: str = "big",
    packet_format: str = "hex",
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
        crc_byte_order=crc_byte_order,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # reverse_packets validates the value
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


def _verb_verify(
    algorithm: str | None = None,
    custom_params: dict[str, Any] | None = None,
    packet_hex: str | None = None,
    packet_text: str | None = None,
    packet_b64: str | None = None,
    crc_byte_order: str = "big",
    encoding: str = "utf-8",
    *,
    surface: str = "python",
) -> dict[str, Any]:
    info, label = _resolve_algorithm(algorithm, custom_params, surface=surface)
    if sum(p is not None for p in (packet_hex, packet_text, packet_b64)) != 1:
        raise ValueError(
            "supply exactly one of packet_hex / packet_text / packet_b64"
        )
    if packet_text is not None:
        result = verify(
            packet_text,
            info,
            endianness=crc_byte_order,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # verify validates the value
            encoding=encoding,
        )
    else:
        packet = parse_packet(packet_hex, None, packet_b64)
        assert isinstance(packet, bytes)  # hex / base64 forms decode to bytes
        result = verify(
            packet,
            info,
            endianness=crc_byte_order,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # verify validates the value
        )
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


def _verb_compute(
    algorithm: str | None = None,
    custom_params: dict[str, Any] | None = None,
    data_text: str | None = None,
    data_b64: str | None = None,
    encoding: str = "utf-8",
    *,
    surface: str = "python",
) -> dict[str, Any]:
    info, _label = _resolve_algorithm(algorithm, custom_params, surface=surface)
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


def _verb_compute_many(
    algorithm: str | None = None,
    custom_params: dict[str, Any] | None = None,
    data_texts: list[str] | None = None,
    data_b64s: list[str] | None = None,
    encoding: str = "utf-8",
    *,
    surface: str = "python",
) -> dict[str, Any]:
    a, label = _resolve_algorithm(algorithm, custom_params, surface=surface)
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


def _verb_encode(
    algorithm: str | None = None,
    custom_params: dict[str, Any] | None = None,
    data_text: str | None = None,
    data_b64: str | None = None,
    crc_byte_order: str = "big",
    sep: str = " ",
    leader: str = "",
    uppercase: bool = False,
    fmt: str = "{data}{sep}{leader}{crc}",
    encoding: str = "utf-8",
    *,
    surface: str = "python",
) -> dict[str, Any]:
    info, _label = _resolve_algorithm(algorithm, custom_params, surface=surface)
    hex_w = (info.width + 3) // 4
    if (data_text is None) == (data_b64 is None):
        raise ValueError("supply exactly one of data_text or data_b64")
    if data_b64 is not None:
        import base64 as _b64

        try:
            raw = _b64.b64decode(data_b64, validate=True)
        except Exception as e:
            raise ValueError(f"data_b64 not valid base64: {e}") from e
        packet = encode(raw, info, endianness=crc_byte_order)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # encode validates the value
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
        endianness=crc_byte_order,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # encode_text validates the value
        encoding=encoding,
        fmt=fmt,
    )
    crc_int = encode_int(data_text, info, encoding=encoding)
    return {
        "packet_text": text,
        "crc": crc_int,
        "crc_hex": f"0x{crc_int:0{hex_w}X}",
    }


def _verb_generate(
    language: str,
    algorithm: str | list[str] | None = None,
    variant: str = "auto",
    symbol: str | None = None,
    name: str | None = None,
    custom_params: dict[str, Any] | None = None,
    comment_style: str = "plain",
    naming: str | None = None,
    *,
    surface: str = "python",
) -> dict[str, Any]:
    if language not in LANGUAGES:
        # The MCP schema enforces the language enum before this runs; this
        # check is for call_verb, where the value arrives unconstrained.
        raise ValueError(
            f"unknown language {language!r}; valid languages: "
            f"{', '.join(LANGUAGES)}"
        )
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
            raise unknown_algorithm_error(unknown[0], surface=surface)
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
            variant = common[-1]
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
            variant = info.fastest_variant_for_width(width)
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
        # not a snippet to abridge.  See the generate verb's OUTPUT HANDLING note.
        "note": (
            "Each files[].content is a COMPLETE drop-in source file -- write "
            "it whole to a file (never truncate tables or omit functions); "
            "paste it inline only if the user asked to see the code, and "
            "then in full."
        ),
    }


def _verb_credits() -> dict[str, str]:
    return {"attribution": ATTRIBUTION}


_DISPATCH: dict[str, Callable[..., dict[str, Any]]] = {
    "list": _verb_list,
    "info": _verb_info,
    "vectors": _verb_vectors,
    "detect": _verb_detect,
    "identify_trailer": _verb_identify_trailer,
    "reverse": _verb_reverse,
    "verify": _verb_verify,
    "compute": _verb_compute,
    "compute_many": _verb_compute_many,
    "encode": _verb_encode,
    "generate": _verb_generate,
    "credits": _verb_credits,
}


def call_verb(name: str, /, **params: Any) -> dict[str, Any]:
    """Invoke a crcglot verb by its :data:`crcglot.VERBS` name.

    The uniform execution half of the verb manifest: a frontend renders a
    typed tool from ``VERBS[name]`` and calls this in the handler with the
    manifest's parameter names.  Returns the JSON-ready dict the spec's
    ``result_fields`` describe, identical to the corresponding MCP tool's
    structured output (both run the same implementation).

    Args:
        name: A ``VERBS`` key such as ``"detect"`` or ``"generate"`` (the
            frontend-neutral verb name, not the ``crc_``-prefixed tool name).
        **params: The verb's parameters, by manifest name.  Omitted optional
            parameters take the manifest defaults.

    Returns:
        The verb's wire dict, per ``VERBS[name].result_fields``.

    Raises:
        UnknownVerbError: ``name`` is not a verb (did-you-mean included).
        UnknownParamError: a parameter name is not in the verb's manifest.
        CrcglotError: bad values raise the same errors the MCP tools raise
            (``ValueError`` subclasses; unknown algorithms carry the Python
            surface's where-to-look hint).

    Examples:
        >>> call_verb("compute", algorithm="crc16-modbus", data_text="123456789")
        {'crc': 19255, 'crc_hex': '0x4B37', 'width': 16}
    """
    if name.startswith("crc_") and name[4:] in VERBS:
        raise UnknownVerbError(
            f"unknown verb {name!r}; that is the MCP tool name -- pass the "
            f"verb name {name[4:]!r} (VerbSpec.mcp_tool maps between them)"
        )
    spec = verb_info(name)
    declared = {p.name for p in spec.params}
    unknown = sorted(set(params) - declared)
    if unknown:
        close = difflib.get_close_matches(unknown[0], declared, n=1)
        hint = f"did you mean {close[0]!r}?  " if close else ""
        valid = ", ".join(p.name for p in spec.params) or "(none)"
        raise UnknownParamError(
            f"unknown parameter {unknown[0]!r} for verb {name!r}; {hint}"
            f"valid parameters: {valid}"
        )
    return _DISPATCH[name](**params)
