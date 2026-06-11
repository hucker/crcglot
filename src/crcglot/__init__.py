"""crcglot -- a multi-language CRC toolkit: generate, compute, detect, reverse.

Generate ready-to-compile CRC source code in C, C#, Go, Java, Python,
Rust, TypeScript, Verilog, or VHDL for any of the reveng catalogue's
more than 100 named algorithms, or any custom Rocksoft/Williams polynomial.  Three
implementation shapes per target (where supported): bit-by-bit
(smallest), table-driven (4-8x faster), and slice-by-8 (another
5-10x faster, CRC-32/64 only).

Public API:
    Typed registries (recommended for downstream tooling):
        - LANGUAGES: dict[str, LanguageInfo] -- one entry per target.
          Each LanguageInfo carries the code, file extensions, the
          set of supported variants, and references to the generator
          callables.
        - ALGORITHMS: dict[str, AlgorithmInfo] -- one entry per
          algorithm in the reveng catalogue.

    Dataclasses:
        - LanguageInfo, Crc, AlgorithmInfo (all frozen; AlgorithmInfo is a
          named, check-carrying Crc).

    Individual generators (also reachable via LANGUAGES[code].generator):
        - generate_c / generate_c_from_entry
        - generate_csharp / generate_csharp_from_entry
        - generate_go / generate_go_from_entry
        - generate_java / generate_java_from_entry
        - generate_python / generate_python_from_entry
        - generate_rust / generate_rust_from_entry
        - generate_typescript / generate_typescript_from_entry
        - generate_verilog / generate_verilog_from_entry
        - generate_vhdl / generate_vhdl_from_entry

    Engine utilities:
        - generic_crc: public helper to compute a check value for any
          Rocksoft/Williams parameter set (used by the --custom CLI
          path and available for field use when defining a one-off
          CRC).
        - _reflect: internal bit-reversal helper.

    Trailer identification (non-CRC, heads-up only -- no code generation):
        - identify_trailer / TrailerResult: spot a common non-CRC trailing
          field in a packet -- a checksum (8-bit sum / LRC / XOR, Adler-32,
          Fletcher, Internet checksum) or a cryptographic digest (MD5, SHA-1,
          SHA-2/3, BLAKE2; full or truncated); also surfaced as
          detect()/reverse() ``.trailer_hint`` when no CRC matched.
          TRAILERS / TrailerInfo / trailer_info mirror the ALGORITHMS
          metadata pattern.

Import cost: ``import crcglot`` eagerly loads only the compute core
(the catalogue + engine and the streaming API).  Everything else --
the per-language generators, detection, reverse-engineering, checksum
identification -- resolves on first attribute access (PEP 562), so a
consumer that only computes CRCs never pays for the rest.  The public
surface is identical either way; the one observable difference is that
an ``ImportError`` from an unused layer would surface at first use
rather than at ``import crcglot``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Eager core: computing a CRC is the package's base requirement, so the
# engine + catalogue and the streaming API always load.  Everything else
# is resolved lazily through __getattr__ below.
from crcglot.catalogue import (
    ALGORITHMS,
    AlgorithmInfo,
    Crc,
    _reflect,
    generic_crc,
    generic_crc_many,
    has_faster_alternative,
)
from crcglot.stream import CrcStream, crc_stream

if TYPE_CHECKING:
    # Static mirror of the lazy names so type checkers and IDEs resolve
    # them without executing __getattr__.  Keep in sync with _LAZY.
    from crcglot._detect import (
        Attempt,
        DetectMatch,
        DetectResult,
        HexFormat,
        TextFormat,
        detect,
        detect_iter,
    )
    from crcglot._encode import (
        VerifyResult,
        encode,
        encode_int,
        encode_match,
        encode_text,
        verify,
    )
    from crcglot._reverse import ReverseResult, reverse, reverse_packets
    from crcglot.attribution import ACKNOWLEDGMENTS, ATTRIBUTION
    from crcglot._trailers import (
        TRAILERS,
        TrailerInfo,
        TrailerMatch,
        TrailerResult,
        identify_trailer,
        trailer_info,
    )
    from crcglot.lang.c import generate_c, generate_c_from_entry
    from crcglot.lang.csharp import generate_csharp, generate_csharp_from_entry
    from crcglot.lang.go import generate_go, generate_go_from_entry
    from crcglot.lang.java import generate_java, generate_java_from_entry
    from crcglot.lang.python import generate_python, generate_python_from_entry
    from crcglot.lang.rust import generate_rust, generate_rust_from_entry
    from crcglot.lang.typescript import (
        generate_typescript,
        generate_typescript_from_entry,
    )
    from crcglot.lang.verilog import generate_verilog, generate_verilog_from_entry
    from crcglot.lang.vhdl import generate_vhdl, generate_vhdl_from_entry
    from crcglot.targets import (
        LANGUAGES,
        NAMING_ORDER,
        VARIANT_ORDER,
        Advisory,
        GeneratedFile,
        LanguageInfo,
        NamingInfo,
        VariantInfo,
        default_stem,
        generate_files,
        naming_info,
        variant_info,
    )

def _lazy_map() -> dict[str, str]:
    """Build the name -> defining-module map for the lazy layers."""
    by_module = {
        "crcglot.attribution": ("ACKNOWLEDGMENTS", "ATTRIBUTION"),
        "crcglot._trailers": (
            "TRAILERS",
            "TrailerInfo",
            "TrailerMatch",
            "TrailerResult",
            "identify_trailer",
            "trailer_info",
        ),
        "crcglot._detect": (
            "Attempt",
            "DetectMatch",
            "DetectResult",
            "HexFormat",
            "TextFormat",
            "detect",
            "detect_iter",
        ),
        "crcglot._encode": (
            "VerifyResult",
            "encode",
            "encode_int",
            "encode_match",
            "encode_text",
            "verify",
        ),
        "crcglot._reverse": ("ReverseResult", "reverse", "reverse_packets"),
        "crcglot.lang.c": ("generate_c", "generate_c_from_entry"),
        "crcglot.lang.csharp": ("generate_csharp", "generate_csharp_from_entry"),
        "crcglot.lang.go": ("generate_go", "generate_go_from_entry"),
        "crcglot.lang.java": ("generate_java", "generate_java_from_entry"),
        "crcglot.lang.python": ("generate_python", "generate_python_from_entry"),
        "crcglot.lang.rust": ("generate_rust", "generate_rust_from_entry"),
        "crcglot.lang.typescript": (
            "generate_typescript",
            "generate_typescript_from_entry",
        ),
        "crcglot.lang.verilog": ("generate_verilog", "generate_verilog_from_entry"),
        "crcglot.lang.vhdl": ("generate_vhdl", "generate_vhdl_from_entry"),
        "crcglot.targets": (
            "LANGUAGES",
            "NAMING_ORDER",
            "VARIANT_ORDER",
            "Advisory",
            "GeneratedFile",
            "LanguageInfo",
            "NamingInfo",
            "VariantInfo",
            "default_stem",
            "generate_files",
            "naming_info",
            "variant_info",
        ),
    }
    return {name: mod for mod, names in by_module.items() for name in names}


_LAZY: dict[str, str] = _lazy_map()

# Submodules the eager __init__ used to bind onto the package; keep them
# reachable as attributes (``crcglot.targets`` etc.) without a separate
# ``import crcglot.targets`` statement.
_LAZY_SUBMODULES = frozenset(
    {"attribution", "comments", "lang", "targets"}
)


def __getattr__(name: str) -> object:
    """Resolve a lazy-layer name on first access (PEP 562).

    Imports the defining module, caches the attribute in the package
    namespace (so subsequent access is a plain dict hit), and returns it.

    Raises:
        AttributeError: ``name`` is not part of the public surface.
    """
    from importlib import import_module

    source = _LAZY.get(name)
    if source is not None:
        value = getattr(import_module(source), name)
        globals()[name] = value
        return value
    if name in _LAZY_SUBMODULES:
        return import_module(f"crcglot.{name}")
    if name == "__version__":
        # Resolved on demand: importlib.metadata scans every dist-info in
        # site-packages (~70 ms here) -- by far the most expensive part of
        # the old eager import, and most consumers never read it.
        try:
            from importlib.metadata import version

            ver = version("crcglot")
        except Exception:  # pragma: no cover - source tree without metadata
            ver = "0.0.0+unknown"
        globals()["__version__"] = ver
        return ver
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Advertise the full public surface, loaded or not."""
    return sorted(set(globals()) | set(_LAZY) | _LAZY_SUBMODULES)


__all__ = [
    "ACKNOWLEDGMENTS",
    "ALGORITHMS",
    "ATTRIBUTION",
    "TRAILERS",
    "Advisory",
    "AlgorithmInfo",
    "Attempt",
    "TrailerInfo",
    "TrailerMatch",
    "TrailerResult",
    "Crc",
    "CrcStream",
    "DetectMatch",
    "DetectResult",
    "GeneratedFile",
    "HexFormat",
    "LANGUAGES",
    "LanguageInfo",
    "NAMING_ORDER",
    "NamingInfo",
    "ReverseResult",
    "TextFormat",
    "VerifyResult",
    "VARIANT_ORDER",
    "VariantInfo",
    "__version__",
    "_reflect",
    "default_stem",
    "naming_info",
    "variant_info",
    "detect",
    "detect_iter",
    "identify_trailer",
    "trailer_info",
    "encode",
    "encode_int",
    "encode_match",
    "encode_text",
    "generate_c",
    "generate_c_from_entry",
    "generate_files",
    "generate_csharp",
    "generate_csharp_from_entry",
    "generate_go",
    "generate_go_from_entry",
    "generate_java",
    "generate_java_from_entry",
    "generate_python",
    "generate_python_from_entry",
    "generate_rust",
    "generate_rust_from_entry",
    "generate_typescript",
    "generate_typescript_from_entry",
    "generate_verilog",
    "generate_verilog_from_entry",
    "generate_vhdl",
    "generate_vhdl_from_entry",
    "crc_stream",
    "generic_crc",
    "generic_crc_many",
    "has_faster_alternative",
    "reverse",
    "reverse_packets",
    "verify",
]
