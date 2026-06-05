"""Target-language metadata for crcglot.

Public registry of every language crcglot can generate code for, with
the file extensions to write and the per-language variant support
matrix.  Downstream callers iterate :data:`LANGUAGES` instead of
hard-coding the list.

Example:

    >>> from crcglot import LANGUAGES, ALGORITHMS
    >>> sorted(LANGUAGES.keys())
    ['c', 'csharp', 'go', 'java', 'python', 'rust', 'typescript', 'verilog', 'vhdl']
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
from typing import TYPE_CHECKING, Callable

from crcglot._helpers import combine_concat

if TYPE_CHECKING:
    from crcglot.comments import StyleInfo
from crcglot.lang.c import combine_c, generate_c, generate_c_from_entry
from crcglot.lang.csharp import (
    combine_csharp,
    generate_csharp,
    generate_csharp_from_entry,
)
from crcglot.lang.go import combine_go, generate_go, generate_go_from_entry
from crcglot.lang.java import (
    combine_java,
    generate_java,
    generate_java_from_entry,
)
from crcglot.lang.python import generate_python, generate_python_from_entry
from crcglot.lang.rust import generate_rust, generate_rust_from_entry
from crcglot.lang.typescript import (
    generate_typescript,
    generate_typescript_from_entry,
)
from crcglot.lang.verilog import generate_verilog, generate_verilog_from_entry
from crcglot.lang.vhdl import generate_vhdl, generate_vhdl_from_entry


VARIANT_ORDER: tuple[str, ...] = ("bitwise", "table", "slice8")
"""Canonical scan / display order for implementation variants.

``"bitwise"`` first (always supported, smallest code size), then
``"table"`` (the throughput-for-memory trade-off), then ``"slice8"``
(highest throughput at width 32 / 64 only).  Downstream UIs (dropdowns,
help text, generated examples) use this order so the simplest option
appears first.
"""


@dataclass(frozen=True)
class VariantInfo:
    """Display metadata for one implementation variant, for building UIs.

    Mirrors :class:`crcglot.comments.StyleInfo`: ``name`` is the
    machine-readable code passed to the generator / CLI (the dropdown's
    value); ``label`` and ``description`` are human-readable; ``widths`` is
    the set of CRC widths the variant applies to, or ``None`` for "any width".

    Examples:
        >>> from crcglot import variant_info
        >>> variant_info("slice8").label
        'Slice-by-8'
        >>> variant_info("slice8").widths
        frozenset({32, 64})
        >>> variant_info("table").widths is None
        True
    """

    name: str
    label: str
    description: str
    widths: frozenset[int] | None


_VARIANT_INFO: dict[str, VariantInfo] = {
    "bitwise": VariantInfo(
        "bitwise", "Bit-by-bit",
        "Smallest code, no lookup table.  Works for any width.",
        None,
    ),
    "table": VariantInfo(
        "table", "Table-driven",
        "One 256-entry lookup table; ~10x faster than bit-by-bit.",
        None,
    ),
    "slice8": VariantInfo(
        "slice8", "Slice-by-8",
        "Eight tables; fastest, for width 32 / 64 on compiled targets.",
        frozenset({32, 64}),
    ),
}


def variant_info(variant: str) -> VariantInfo:
    """Display metadata (name / label / description / widths) for one variant.

    The variant analogue of :func:`crcglot.comments.style_info`, so UIs read
    one canonical label/description instead of hardcoding their own.

    Raises:
        KeyError: Unknown variant.

    Examples:
        >>> variant_info("bitwise").name
        'bitwise'
    """
    return _VARIANT_INFO[variant]


@dataclass(frozen=True)
class LanguageInfo:
    """Typed metadata for one target language.

    ``variants`` reports which implementation shapes the generator
    accepts as a flag, not which (language x width) cells are valid.
    Use :meth:`variants_for_width` when you need the width-filtered
    subset -- it applies the "slice8 requires width 32 or 64" rule so
    callers don't have to encode that magic number themselves.

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
        combiner: Merge several generator outputs into one file --
            ``combiner(outputs, stem)``.  To bundle multiple algorithms into
            one file, call ``generator`` once per algorithm and pass the list
            of results to ``combiner``: e.g. ``combiner([generator("crc32"),
            generator("crc16-modbus")], "mycrcs")``.  Per-symbol table names
            make the merged unit collision-free.  C takes/returns
            ``(header, source)`` pairs; others take/return strings.
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
    combiner: Callable
    emoji: str
    display_name: str

    def variants_for_width(self, width: int) -> tuple[str, ...]:
        """Implementation variants this language supports at a given width.

        Identical to ``self.variants`` ordered by :data:`VARIANT_ORDER`,
        except ``"slice8"`` is filtered out when ``width`` is anything
        other than 32 or 64 -- the slice-by-8 implementation chunks the
        input 8 bytes at a time and only makes sense at those widths,
        so the generator raises ``ValueError`` for narrower CRCs.
        Surfacing the rule here lets dropdowns and other UI code avoid
        offering options that would error.

        Args:
            width: CRC width in bits (e.g. ``8``, ``16``, ``32``, ``64``).

        Returns:
            Supported variants in canonical order.

        Examples:
            >>> from crcglot import LANGUAGES
            >>> LANGUAGES["c"].variants_for_width(32)
            ('bitwise', 'table', 'slice8')
            >>> LANGUAGES["c"].variants_for_width(16)
            ('bitwise', 'table')
            >>> LANGUAGES["python"].variants_for_width(32)
            ('bitwise', 'table')
        """
        return tuple(
            v for v in VARIANT_ORDER
            if v in self.variants
            and not (v == "slice8" and width not in (32, 64))
        )

    def variant_infos_for_width(self, width: int) -> tuple[VariantInfo, ...]:
        """:meth:`variants_for_width` as rich :class:`VariantInfo` records.

        The UI-facing companion: a front end can show each variant's
        ``label`` / ``description`` and submit its ``name``, with no hardcoded
        variant metadata.

        Examples:
            >>> [v.name for v in LANGUAGES["c"].variant_infos_for_width(32)]
            ['bitwise', 'table', 'slice8']
            >>> LANGUAGES["c"].variant_infos_for_width(32)[2].label
            'Slice-by-8'
        """
        return tuple(variant_info(v) for v in self.variants_for_width(width))

    @property
    def styles(self) -> tuple[StyleInfo, ...]:
        """The comment styles valid for this language, as rich records.

        Mirrors :attr:`variants` on the documentation axis, so everything a UI
        needs about a target lives on :class:`LanguageInfo`:
        ``LANGUAGES[code].styles`` instead of reaching into
        :mod:`crcglot.comments`.  Each :class:`~crcglot.comments.StyleInfo`
        carries ``name`` / ``label`` / ``description``.

        Examples:
            >>> [s.name for s in LANGUAGES["python"].styles]
            ['plain', 'google', 'numpy', 'rest']
        """
        # Lazy import: comments depends on nothing here, but targets is
        # imported early, so keep the comment subsystem off the import path.
        from crcglot.comments import comment_styles_for_language

        return comment_styles_for_language(self.code)


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
        combiner=combine_c,
        emoji="⚙️",  # gear
        display_name="C / C++",
    ),
    "csharp": LanguageInfo(
        code="csharp",
        extensions=(".cs",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_csharp,
        generator_from_entry=generate_csharp_from_entry,
        combiner=combine_csharp,
        emoji="\U0001F4A0",  # diamond with a dot
        display_name="C#",
    ),
    "go": LanguageInfo(
        code="go",
        extensions=(".go",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_go,
        generator_from_entry=generate_go_from_entry,
        combiner=combine_go,
        emoji="\U0001F6A6",  # vertical traffic light
        display_name="Go",
    ),
    "java": LanguageInfo(
        code="java",
        extensions=(".java",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_java,
        generator_from_entry=generate_java_from_entry,
        combiner=combine_java,
        emoji="☕",  # hot beverage (coffee)
        display_name="Java",
    ),
    "python": LanguageInfo(
        code="python",
        extensions=(".py",),
        variants=_BITWISE_TABLE,
        generator=generate_python,
        generator_from_entry=generate_python_from_entry,
        combiner=combine_concat,
        emoji="\U0001F40D",  # snake
        display_name="Python",
    ),
    "rust": LanguageInfo(
        code="rust",
        extensions=(".rs",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_rust,
        generator_from_entry=generate_rust_from_entry,
        combiner=combine_concat,
        emoji="\U0001F980",  # crab
        display_name="Rust",
    ),
    "typescript": LanguageInfo(
        code="typescript",
        extensions=(".ts",),
        variants=_BITWISE_TABLE_SLICE8,
        generator=generate_typescript,
        generator_from_entry=generate_typescript_from_entry,
        combiner=combine_concat,
        emoji="\U0001F537",  # large blue diamond
        display_name="TypeScript",
    ),
    "verilog": LanguageInfo(
        code="verilog",
        extensions=(".sv",),
        variants=_BITWISE_ONLY,
        generator=generate_verilog,
        generator_from_entry=generate_verilog_from_entry,
        combiner=combine_concat,
        emoji="\U0001F527",  # wrench
        display_name="Verilog",
    ),
    "vhdl": LanguageInfo(
        code="vhdl",
        extensions=(".vhd",),
        variants=_BITWISE_ONLY,
        generator=generate_vhdl,
        generator_from_entry=generate_vhdl_from_entry,
        combiner=combine_concat,
        emoji="\U0001F50C",  # electric plug
        display_name="VHDL",
    ),
}
