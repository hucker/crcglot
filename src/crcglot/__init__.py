"""crcglot -- multi-language CRC code generator.

Generate ready-to-compile CRC source code in C, Rust, VHDL, or Python
for any of 64+ named algorithms (reveng catalogue) or any custom
Rocksoft/Williams polynomial.  Three implementation shapes per target:
bit-by-bit (smallest), table-driven (4-8x faster), and slice-by-8
(another 5-10x faster, CRC-32/64 only).

Public API:
    - CRC_CATALOGUE: dict of named algorithm parameters.
    - generate_c, generate_python, generate_rust, generate_vhdl:
      name-lookup generators.
    - generate_c_from_entry, etc.: generate from a custom entry dict
      (Rocksoft/Williams params without catalogue lookup).
    - GENERATORS, GENERATORS_FROM_ENTRY: dicts of the two forms
      keyed by language code, for parameterized callers.
"""

from __future__ import annotations

from typing import Callable

from crcglot.c import generate_c, generate_c_from_entry
from crcglot.catalogue import CRC_CATALOGUE, _generic_crc, _reflect
from crcglot.csharp import generate_csharp, generate_csharp_from_entry
from crcglot.go import generate_go, generate_go_from_entry
from crcglot.python import generate_python, generate_python_from_entry
from crcglot.rust import generate_rust, generate_rust_from_entry
from crcglot.vhdl import generate_vhdl, generate_vhdl_from_entry
from crcglot.zig import generate_zig, generate_zig_from_entry


# Language code -> name-lookup generator callable.
GENERATORS: dict[str, Callable] = {
    "c": generate_c,
    "csharp": generate_csharp,
    "go": generate_go,
    "python": generate_python,
    "rust": generate_rust,
    "vhdl": generate_vhdl,
    "zig": generate_zig,
}


# Language code -> entry-dict generator callable (custom-params path).
GENERATORS_FROM_ENTRY: dict[str, Callable] = {
    "c": generate_c_from_entry,
    "csharp": generate_csharp_from_entry,
    "go": generate_go_from_entry,
    "python": generate_python_from_entry,
    "rust": generate_rust_from_entry,
    "vhdl": generate_vhdl_from_entry,
    "zig": generate_zig_from_entry,
}


__all__ = [
    "CRC_CATALOGUE",
    "GENERATORS",
    "GENERATORS_FROM_ENTRY",
    "_generic_crc",
    "_reflect",
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
    "generate_vhdl",
    "generate_vhdl_from_entry",
    "generate_zig",
    "generate_zig_from_entry",
]
