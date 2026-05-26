"""Target-language metadata for crcglot.

Public registry of every language crcglot can generate code for, with
the file extensions to write and the per-language variant support
matrix.  Downstream callers iterate :data:`LANGUAGES` instead of
hard-coding the list.

Example:

    >>> from crcglot import LANGUAGES, ALGORITHMS
    >>> sorted(LANGUAGES.keys())
    ['c', 'csharp', 'go', 'python', 'rust', 'vhdl', 'zig']
    >>> LANGUAGES["c"].extensions
    ('.h', '.c')
    >>> "slice8" in LANGUAGES["vhdl"].variants
    False
    >>> code, _ = LANGUAGES["c"].generator("crc32")  # type: ignore[misc]
    >>> ALGORITHMS["crc32"].check == 0xCBF43926
    True
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from crcglot.c import generate_c, generate_c_from_entry
from crcglot.csharp import generate_csharp, generate_csharp_from_entry
from crcglot.go import generate_go, generate_go_from_entry
from crcglot.python import generate_python, generate_python_from_entry
from crcglot.rust import generate_rust, generate_rust_from_entry
from crcglot.vhdl import generate_vhdl, generate_vhdl_from_entry
from crcglot.zig import generate_zig, generate_zig_from_entry


@dataclass(frozen=True)
class LanguageInfo:
    """Typed metadata for one target language.

    ``variants`` reports which implementation shapes the generator
    accepts as a flag, not which (language x width) cells are valid.
    The width-32/64 constraint on ``"slice8"`` is enforced inside the
    generator itself, which raises ``ValueError`` for incompatible
    widths.  Callers that want to filter strictly by capability should
    consult the algorithm width too.

    Attributes:
        code: CLI identifier and dispatch key ("c", "csharp", "go",
            "python", "rust", "vhdl", "zig").
        extensions: File extension tuple.  ``(".h", ".c")`` for C
            (header + source); single-element tuple for every other
            language.
        variants: Subset of ``{"bitwise", "table", "slice8"}``.  Every
            language supports ``"bitwise"``.
        generator: Name-lookup generator -- ``generator(name, ...)``.
        generator_from_entry: Entry-dispatch generator --
            ``generator_from_entry(name, AlgorithmInfo, ...)``.
    """

    code: str
    extensions: tuple[str, ...]
    variants: frozenset[str]
    generator: Callable
    generator_from_entry: Callable


_BITWISE_TABLE = frozenset({"bitwise", "table"})
_BITWISE_TABLE_SLICE8 = frozenset({"bitwise", "table", "slice8"})
_BITWISE_ONLY = frozenset({"bitwise"})


LANGUAGES: dict[str, LanguageInfo] = {
    "c": LanguageInfo(
        code="c",
        extensions=(".h", ".c"),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_c,
        generator_from_entry=generate_c_from_entry,
    ),
    "csharp": LanguageInfo(
        code="csharp",
        extensions=(".cs",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_csharp,
        generator_from_entry=generate_csharp_from_entry,
    ),
    "go": LanguageInfo(
        code="go",
        extensions=(".go",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_go,
        generator_from_entry=generate_go_from_entry,
    ),
    "python": LanguageInfo(
        code="python",
        extensions=(".py",),
        variants=_BITWISE_TABLE,
        generator=generate_python,
        generator_from_entry=generate_python_from_entry,
    ),
    "rust": LanguageInfo(
        code="rust",
        extensions=(".rs",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_rust,
        generator_from_entry=generate_rust_from_entry,
    ),
    "vhdl": LanguageInfo(
        code="vhdl",
        extensions=(".vhd",),
        variants=_BITWISE_ONLY,
        generator=generate_vhdl,
        generator_from_entry=generate_vhdl_from_entry,
    ),
    "zig": LanguageInfo(
        code="zig",
        extensions=(".zig",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_zig,
        generator_from_entry=generate_zig_from_entry,
    ),
}
