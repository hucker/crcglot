"""Wire-format helpers for the crcglot MCP server.

Small set of functions used by every tool / resource handler:

* Packet decoding -- pick the right input form (``packet_hex`` /
  ``packet_text`` / ``packet_b64``) and resolve to bytes-or-str for
  :func:`crcglot.detect`.
* Dataclass serialization -- :class:`AlgorithmInfo` /
  :class:`LanguageInfo` to JSON-friendly dicts with poly / init / xorout
  surfaced in both decimal and hex (LLMs misread bare integers as
  decimal when the source quoted hex).
* Endianness relabel -- the public ``DetectMatch.endianness`` field
  becomes ``crc_byte_order`` on the wire so LLMs don't conflate "the
  CRC bytes are little-endian" with "the protocol is little-endian."
* ``target_crc`` resolution -- mutually-exclusive decimal-int /
  hex-string forms get unified into one ``int | None`` for ``detect``.

Pure helpers, no MCP SDK imports.  Server-side glue lives in
``server.py``.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from crcglot import AlgorithmInfo, LanguageInfo


_HEX_CLEAN = re.compile(r"0[xX]|[\s,:]+")


def parse_target_crc(
    target_crc: int | None,
    target_crc_hex: str | None,
) -> int | None:
    """Pick the single ``target_crc`` int from the two input forms.

    Args:
        target_crc: Decimal-int form (preferred when the value came from
            a struct field).
        target_crc_hex: Hex-string form (``"0xCBF43926"`` or ``"CBF43926"``;
            preferred when the value came from a doc quote).

    Returns:
        The resolved integer, or ``None`` when neither was supplied.

    Raises:
        ValueError: Both forms supplied (mutually exclusive), or the
            hex form is unparsable.
    """
    if target_crc is not None and target_crc_hex is not None:
        raise ValueError(
            "target_crc and target_crc_hex are mutually exclusive; pass one"
        )
    if target_crc is not None:
        return target_crc
    if target_crc_hex is None:
        return None
    s = target_crc_hex.strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    try:
        return int(s, 16)
    except ValueError as e:
        raise ValueError(
            f"target_crc_hex must be a hex integer; got {target_crc_hex!r}"
        ) from e


def parse_packet(
    packet_hex: str | None,
    packet_text: str | None,
    packet_b64: str | None,
) -> bytes | str:
    """Resolve the three packet-input forms to a single value for ``detect``.

    Args:
        packet_hex: Hex-encoded bytes with any common formatting --
            spaces, commas, colons, ``0x`` prefixes are all tolerated.
        packet_text: Text packet (``"data <sep> hex"``).
        packet_b64: Base64-encoded raw bytes (the wire-safe binary form).

    Returns:
        ``bytes`` for the hex / base64 cases, ``str`` for text.  Pass
        the result directly to :func:`crcglot.detect`.

    Raises:
        ValueError: None of the three were supplied, or more than one
            was supplied, or the encoding is malformed.
    """
    supplied = [p for p in (packet_hex, packet_text, packet_b64) if p]
    if not supplied:
        raise ValueError(
            "must supply exactly one of packet_hex / packet_text / packet_b64"
        )
    if len(supplied) > 1:
        raise ValueError("packet_hex / packet_text / packet_b64 are mutually exclusive")
    if packet_b64 is not None:
        try:
            return base64.b64decode(packet_b64, validate=True)
        except Exception as e:
            raise ValueError(f"packet_b64 is not valid base64: {e}") from e
    if packet_hex is not None:
        cleaned = _HEX_CLEAN.sub("", packet_hex)
        if not cleaned or len(cleaned) % 2 != 0:
            raise ValueError(
                f"packet_hex must be an even-length hex string; got {packet_hex!r}"
            )
        try:
            return bytes.fromhex(cleaned)
        except ValueError as e:
            raise ValueError(f"packet_hex contains non-hex characters: {e}") from e
    # packet_text -- always a str by the supplied check above.
    assert packet_text is not None
    return packet_text


def algorithm_to_dict(name: str, algo: AlgorithmInfo) -> dict[str, Any]:
    """Serialize one :class:`AlgorithmInfo` for JSON output.

    Surface every numeric Rocksoft field in both decimal and hex so the
    LLM doesn't have to convert.  Field naming preserves the original
    Python names; ``name`` is added explicitly because it lives on the
    dict key, not on the dataclass.
    """
    w = algo.width
    hex_w = (w + 3) // 4

    def hexfmt(v: int) -> str:
        return f"0x{v:0{hex_w}X}"

    return {
        "name": name,
        "width": w,
        "poly": algo.poly,
        "poly_hex": hexfmt(algo.poly),
        "init": algo.init,
        "init_hex": hexfmt(algo.init),
        "refin": algo.refin,
        "refout": algo.refout,
        "xorout": algo.xorout,
        "xorout_hex": hexfmt(algo.xorout),
        "check": algo.check,
        "check_hex": hexfmt(algo.check),
        "desc": algo.desc,
        "source": algo.source,
    }


def language_to_dict(code: str, info: LanguageInfo) -> dict[str, Any]:
    """Serialize one :class:`LanguageInfo` for JSON output.

    Drops the two ``generator`` callables (not JSON-serializable);
    keeps everything else.  ``variants`` is sorted into a list so the
    output order is deterministic across runs.  ``comment_styles`` lists the
    documentation styles valid for this language as ``{name, label,
    description}`` records -- enough for a UI to build a dropdown (show the
    label, submit the name) without hardcoding the matrix.
    """
    # Imported lazily to keep this module free of comment-subsystem imports
    # at module load (it is otherwise pure wire helpers).
    from crcglot.comments import comment_styles_for_language

    return {
        "code": code,
        "display_name": info.display_name,
        "extensions": list(info.extensions),
        "variants": sorted(info.variants),
        "comment_styles": [
            {"name": s.name, "label": s.label, "description": s.description}
            for s in comment_styles_for_language(code)
        ],
        "naming": [
            {"name": n.name, "label": n.label, "description": n.description}
            for n in info.naming_infos
        ],
        "default_naming": info.default_naming,
        "emoji": info.emoji,
    }


# ---------------------------------------------------------------------------
# DetectMatch / Attempt -> wire format (with the endianness relabel)
# ---------------------------------------------------------------------------


def detect_match_to_dict(match: Any) -> dict[str, Any]:
    """Serialize one :class:`crcglot.DetectMatch` for JSON output.

    Wire-format relabel: ``DetectMatch.endianness`` becomes
    ``crc_byte_order`` -- LLMs misread "endianness=little" as "the
    protocol is little-endian" when the field actually means "the CRC
    bytes are little-endian within the packet."  The relabel happens
    only at the JSON boundary; the internal field name is untouched.

    ``padding`` (TextFormat / HexFormat / None) is also flattened into a
    discriminated dict so the consumer doesn't need to know the
    dataclass types.
    """
    out: dict[str, Any] = {
        "algorithm": match.algorithm,
        "width": match.info.width,
        "crc_byte_order": match.endianness,
    }
    pad = match.padding
    if pad is None:
        out["padding_kind"] = "binary"
    else:
        # TextFormat or HexFormat: surface every attr as a dict.
        out["padding_kind"] = type(pad).__name__
        out["padding"] = {k: v for k, v in vars(pad).items() if not k.startswith("_")}
    return out


def trailer_match_to_dict(match: Any) -> dict[str, Any]:
    """Serialize one :class:`crcglot.TrailerMatch` for JSON output.

    Mirrors :func:`detect_match_to_dict`'s ``endianness -> crc_byte_order``
    relabel.  An 8-bit checksum is byte-order-invariant (always ``"big"``),
    as is any digest (a byte string has no byte order); 16/32-bit checksums
    respect the field's byte order.  ``truncated_to`` is the trailer's byte
    length for a leading-truncated digest match, else ``None``.
    """
    return {
        "trailer": match.name,
        "kind": match.info.kind,
        "label": match.info.label,
        "width": match.info.width,
        "crc_byte_order": match.endianness,
        "truncated_to": match.truncated_to,
    }


def trailer_result_to_dict(result: Any) -> dict[str, Any]:
    """Serialize a :class:`crcglot.TrailerResult` (the non-CRC heads-up).

    ``frames_agreed`` is the confidence signal -- one frame is weak (an 8-bit
    checksum matches a random frame ~1/256); more agreeing frames make it
    trustworthy.  ``note`` carries the MAC heads-up when a digest-sized field
    matched nothing.
    """
    return {
        "matched": result.matched,
        "frames_agreed": result.frames_agreed,
        "candidates": [trailer_match_to_dict(m) for m in result.candidates],
        "note": result.note,
    }
