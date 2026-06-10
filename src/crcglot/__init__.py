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

    Checksum identification (non-CRC, heads-up only -- no code generation):
        - identify_checksum / ChecksumResult: spot a common non-CRC checksum
          (8-bit sum / LRC / XOR, Adler-32, Fletcher, Internet checksum) in a
          packet; also surfaced as detect()/reverse() ``.checksum_hint`` when no
          CRC matched.  CHECKSUMS / ChecksumInfo / checksum_info mirror the
          ALGORITHMS metadata pattern.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from crcglot.attribution import ACKNOWLEDGMENTS, ATTRIBUTION
from crcglot.catalogue import (
    ALGORITHMS,
    AlgorithmInfo,
    Crc,
    _reflect,
    generic_crc,
    generic_crc_many,
    has_faster_alternative,
)
from crcglot.checksums import (
    CHECKSUMS,
    ChecksumInfo,
    ChecksumMatch,
    ChecksumResult,
    checksum_info,
    identify_checksum,
)
from crcglot.detect import (
    Attempt,
    DetectMatch,
    DetectResult,
    HexFormat,
    TextFormat,
    detect,
    detect_iter,
)
from crcglot.encode import (
    VerifyResult,
    encode,
    encode_int,
    encode_match,
    encode_text,
    verify,
)
from crcglot.reverse import ReverseResult, reverse, reverse_packets
from crcglot.stream import CrcStream, crc_stream
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

try:
    __version__ = version("crcglot")
except PackageNotFoundError:  # pragma: no cover - source tree without metadata
    __version__ = "0.0.0+unknown"


__all__ = [
    "ACKNOWLEDGMENTS",
    "ALGORITHMS",
    "ATTRIBUTION",
    "CHECKSUMS",
    "Advisory",
    "AlgorithmInfo",
    "Attempt",
    "ChecksumInfo",
    "ChecksumMatch",
    "ChecksumResult",
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
    "identify_checksum",
    "checksum_info",
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
