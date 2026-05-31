"""crcglot -- multi-language CRC code generator.

Generate ready-to-compile CRC source code in C, C#, Go, Python, Rust,
TypeScript, Verilog, or VHDL for any of 71 named algorithms (reveng
catalogue) or any custom Rocksoft/Williams polynomial.  Three
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
        - LanguageInfo, AlgorithmInfo (both frozen).

    Individual generators (also reachable via LANGUAGES[code].generator):
        - generate_c / generate_c_from_entry
        - generate_csharp / generate_csharp_from_entry
        - generate_go / generate_go_from_entry
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
"""

from __future__ import annotations

from crcglot.attribution import ACKNOWLEDGMENTS, ATTRIBUTION
from crcglot.catalogue import (
    ALGORITHMS,
    AlgorithmInfo,
    _reflect,
    generic_crc,
)
from crcglot.detect import (
    Attempt,
    DetectMatch,
    DetectResult,
    TextFormat,
    detect,
    detect_iter,
)
from crcglot.encode import encode, encode_int, encode_match, encode_text
from crcglot.lang.c import generate_c, generate_c_from_entry
from crcglot.lang.csharp import generate_csharp, generate_csharp_from_entry
from crcglot.lang.go import generate_go, generate_go_from_entry
from crcglot.lang.python import generate_python, generate_python_from_entry
from crcglot.lang.rust import generate_rust, generate_rust_from_entry
from crcglot.lang.typescript import (
    generate_typescript,
    generate_typescript_from_entry,
)
from crcglot.lang.verilog import generate_verilog, generate_verilog_from_entry
from crcglot.lang.vhdl import generate_vhdl, generate_vhdl_from_entry
from crcglot.targets import LANGUAGES, LanguageInfo


__all__ = [
    "ACKNOWLEDGMENTS",
    "ALGORITHMS",
    "ATTRIBUTION",
    "AlgorithmInfo",
    "Attempt",
    "DetectMatch",
    "DetectResult",
    "LANGUAGES",
    "LanguageInfo",
    "TextFormat",
    "_reflect",
    "detect",
    "detect_iter",
    "encode",
    "encode_int",
    "encode_match",
    "encode_text",
    "generate_c",
    "generate_c_from_entry",
    "generate_csharp",
    "generate_csharp_from_entry",
    "generate_go",
    "generate_go_from_entry",
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
    "generic_crc",
]
