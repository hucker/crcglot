"""Target-language metadata for crcglot.

Public registry of every language crcglot can generate code for, with
the file extensions to write and the per-language variant support
matrix.  Downstream callers iterate :data:`LANGUAGES` instead of
hard-coding the list.

Example:

    >>> from crcglot import LANGUAGES, ALGORITHMS
    >>> sorted(LANGUAGES.keys())
    ['c', 'csharp', 'go', 'python', 'rust', 'typescript', 'verilog', 'vhdl']
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
            "python", "rust", "typescript", "verilog", "vhdl").
        extensions: File extension tuple.  ``(".h", ".c")`` for C
            (header + source); single-element tuple for every other
            language.
        variants: Subset of ``{"bitwise", "table", "slice8"}``.  Every
            language supports ``"bitwise"``.
        generator: Name-lookup generator -- ``generator(name, ...)``.
        generator_from_entry: Entry-dispatch generator --
            ``generator_from_entry(name, AlgorithmInfo, ...)``.
        emoji: Short pictographic identifier for terminals / docs
            (e.g. "\U0001F980" for Rust).  One grapheme cluster.
        display_name: Human-readable name for documentation and CLI
            output (e.g. "C / C++", "Rust", "TypeScript").  Distinct
            from ``code`` -- the latter is the dispatch key, this is
            for humans.
    """

    code: str
    extensions: tuple[str, ...]
    variants: frozenset[str]
    generator: Callable
    generator_from_entry: Callable
    emoji: str
    display_name: str


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
        emoji="⚙️",  # gear
        display_name="C / C++",
    ),
    "csharp": LanguageInfo(
        code="csharp",
        extensions=(".cs",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_csharp,
        generator_from_entry=generate_csharp_from_entry,
        emoji="\U0001F4A0",  # diamond with a dot
        display_name="C#",
    ),
    "go": LanguageInfo(
        code="go",
        extensions=(".go",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_go,
        generator_from_entry=generate_go_from_entry,
        emoji="\U0001F6A6",  # vertical traffic light
        display_name="Go",
    ),
    "python": LanguageInfo(
        code="python",
        extensions=(".py",),
        variants=_BITWISE_TABLE,
        generator=generate_python,
        generator_from_entry=generate_python_from_entry,
        emoji="\U0001F40D",  # snake
        display_name="Python",
    ),
    "rust": LanguageInfo(
        code="rust",
        extensions=(".rs",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_rust,
        generator_from_entry=generate_rust_from_entry,
        emoji="\U0001F980",  # crab
        display_name="Rust",
    ),
    "typescript": LanguageInfo(
        code="typescript",
        extensions=(".ts",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_typescript,
        generator_from_entry=generate_typescript_from_entry,
        emoji="\U0001F537",  # large blue diamond
        display_name="TypeScript",
    ),
    "verilog": LanguageInfo(
        code="verilog",
        extensions=(".sv",),
        variants=_BITWISE_ONLY,
        generator=generate_verilog,
        generator_from_entry=generate_verilog_from_entry,
        emoji="\U0001F527",  # wrench
        display_name="Verilog",
    ),
    "vhdl": LanguageInfo(
        code="vhdl",
        extensions=(".vhd",),
        variants=_BITWISE_ONLY,
        generator=generate_vhdl,
        generator_from_entry=generate_vhdl_from_entry,
        emoji="\U0001F50C",  # electric plug
        display_name="VHDL",
    ),
}
