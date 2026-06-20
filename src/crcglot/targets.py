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

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal, cast

from crcglot._helpers import _func_name, _join_naming, combine_concat
from crcglot.catalogue import (
    ALGORITHMS,
    AlgorithmInfo,
    has_faster_alternative,
    unknown_algorithm_error,
)

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


NAMING_ORDER: tuple[str, ...] = ("snake", "camel", "pascal")
"""Canonical scan / display order for naming conventions.

``"snake"`` first (the universal lowest-common-denominator and the default for
C / Rust / Python / Verilog / VHDL), then ``"camel"``, then ``"pascal"``.  UIs
list conventions in this order so the simplest reads first.
"""


@dataclass(frozen=True)
class NamingInfo:
    """Display metadata for one naming convention, for building UIs.

    The naming analogue of :class:`VariantInfo` / :class:`crcglot.comments.StyleInfo`:
    ``name`` is the machine-readable code passed to the generator / CLI (the
    dropdown's value); ``label`` and ``description`` are human-readable.  Which
    conventions a given language offers lives on :attr:`LanguageInfo.naming`,
    not here, because the same convention applies across many languages.

    Examples:
        >>> from crcglot import naming_info
        >>> naming_info("pascal").label
        'PascalCase'
    """

    name: str
    label: str
    description: str


_NAMING_INFO: dict[str, NamingInfo] = {
    "snake": NamingInfo(
        "snake", "snake_case",
        "lower_snake_case identifiers (crc16_modbus_update).",
    ),
    "camel": NamingInfo(
        "camel", "camelCase",
        "camelCase identifiers (crc16ModbusUpdate).",
    ),
    "pascal": NamingInfo(
        "pascal", "PascalCase",
        "PascalCase identifiers (Crc16ModbusUpdate).",
    ),
}


def naming_info(naming: str) -> NamingInfo:
    """Display metadata (name / label / description) for one naming convention.

    The naming analogue of :func:`variant_info` / :func:`crcglot.comments.style_info`,
    so UIs read one canonical label/description instead of hardcoding their own.

    Raises:
        KeyError: Unknown naming convention.

    Examples:
        >>> naming_info("camel").name
        'camel'
    """
    return _NAMING_INFO[naming]


def naming_convention_for(language: str, naming: str) -> str:
    """Validate that ``language`` offers naming convention ``naming``; return it.

    The naming analogue of :func:`crcglot.comments.comment_style_for`'s
    validation -- generators call this so a bad (language, convention) pair is
    rejected before any code is emitted (e.g. PascalCase for Rust).

    Raises:
        ValueError: Unknown convention, or one the language does not offer.

    Examples:
        >>> naming_convention_for("go", "pascal")
        'pascal'
    """
    if naming not in _NAMING_INFO:
        raise ValueError(
            f"unknown naming convention {naming!r}; valid: {list(NAMING_ORDER)}"
        )
    info = LANGUAGES[language]
    if naming not in info.naming:
        offered = [n for n in NAMING_ORDER if n in info.naming]
        raise ValueError(
            f"naming convention {naming!r} is not valid for language "
            f"{language!r}; it offers {offered} (default "
            f"{info.default_naming!r})"
        )
    return naming


@dataclass(frozen=True)
class Advisory:
    """A per-target informational note about a generated CRC.

    Produced by :meth:`LanguageInfo.advisories_for` so every generation surface
    (the CLI, the MCP ``crc_generate`` tool, a downstream UI) shows the same
    guidance instead of re-deriving it.

    Attributes:
        severity: ``"warning"`` (the emitted code is a genuine second-best here)
            or ``"info"`` (it's fine, but a faster path exists).  Maps to a UI
            affordance, e.g. Streamlit's ``st.warning`` / ``st.info``.
        kind: Stable machine-readable tag (``"python-runtime"`` /
            ``"stdlib-crc32"``) for callers that style or filter by kind.
        message: One ready-to-render sentence.  Plain text with backticks for
            code and no other markup, so it reads cleanly in a terminal and
            renders correctly in a Markdown UI.
    """

    severity: Literal["info", "warning"]
    kind: str
    message: str


@dataclass(frozen=True)
class GeneratedFile:
    """One emitted source file, ready to write verbatim.

    The unit :meth:`LanguageInfo.generate_files` returns -- a complete file with
    the name it should be saved as, so a CLI / MCP / UI just writes ``content``
    to ``filename`` without re-deriving any per-language naming rule.

    Attributes:
        filename: Name to save as, extension included (e.g. ``"crc16_xmodem.rs"``,
            ``"Crc16Xmodem.java"``, ``"crc16_xmodem.h"``).  For Java / C# this is
            the public class name -- the file *must* be called this.
        content: The complete source, never abridged.
        role: ``""`` for a sole file, or ``"header"`` / ``"source"`` to label
            C's two-file output for a UI.
    """

    filename: str
    content: str
    role: str = ""


# Java reserves these; a container class can't be named one of them.  Used by
# :meth:`LanguageInfo.validate_symbol` for the strict (filename == class) targets.
_JAVA_KEYWORDS: frozenset[str] = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "goto", "if", "implements",
    "import", "instanceof", "int", "interface", "long", "native", "new",
    "package", "private", "protected", "public", "return", "short", "static",
    "strictfp", "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while", "true", "false", "null",
})


def _is_legal_class_identifier(s: str) -> bool:
    """True iff ``s`` is usable as a Java/C# class name (and not a keyword)."""
    if not s or not (s[0].isalpha() or s[0] in "_$"):
        return False
    if not all(c.isalnum() or c in "_$" for c in s):
        return False
    return s not in _JAVA_KEYWORDS


def _sanitize_base(s: str) -> str:
    """Mangle an arbitrary stem into a snake-style identifier base.

    Basename only (drop any path), then ``-`` / ``.`` -> ``_`` -- the same rule
    the CLI's ``file=`` shortcut has always used.
    """
    from pathlib import PurePath

    return PurePath(s).name.replace("-", "_").replace(".", "_")


def _pascal_base(base_snake: str) -> str:
    """PascalCase a snake base: ``crc16_xmodem`` -> ``Crc16Xmodem``.

    Matches ``crc_function_names(base, "pascal")["oneshot"]`` and the C#
    generator's ``_cs_pascal_class`` so the filename agrees with the class.
    """
    return "".join(t[:1].upper() + t[1:].lower() for t in base_snake.split("_") if t)


def default_stem(algorithm: str | Sequence[str]) -> str:
    """The default filename / identifier stem for a generation.

    The stem :func:`generate_files` uses when no ``name`` / ``symbol``
    override is given: the algorithm's own name for a single CRC, or the
    neutral ``"crc_bundle"`` when several are bundled into one file.
    Returns the raw (snake) stem -- pass it through
    :meth:`LanguageInfo.format_filename` / :meth:`LanguageInfo.format_name` to
    case it for a target.

    Owning this here keeps a UI's "default name" field in lockstep with the file
    crcglot writes, instead of the app re-deriving ``name.replace("-", "_")``.

    Args:
        algorithm: One catalogue name, or several to bundle.

    Returns:
        ``_func_name(name)`` for a single algorithm; ``"crc_bundle"`` for a
        bundle (two or more distinct names).

    Examples:
        >>> default_stem("crc16-xmodem")
        'crc16_xmodem'
        >>> default_stem(["crc32"])
        'crc32'
        >>> default_stem(["crc32", "crc8"])
        'crc_bundle'
    """
    if isinstance(algorithm, str):
        return _func_name(algorithm)
    names = list(dict.fromkeys(algorithm))
    return _func_name(names[0]) if len(names) == 1 else "crc_bundle"


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
        naming: Subset of ``{"snake", "camel", "pascal"}`` the language
            offers for its public function/method names.  Each language is
            "a mess" (C: all three) or "clean" (Rust / Python: snake only).
        default_naming: The idiomatic convention emitted when ``--naming``
            is not given -- snake for C / Rust / Python / Verilog / VHDL,
            pascal for Go / C#, camel for Java / TypeScript.
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
        stdlib_crc32: How to reach this target's stdlib / canonical-package
            IEEE CRC-32, on CPU CRC instructions (e.g. Python's
            ``zlib.crc32``, Rust's ``crc32fast``), or ``None`` when there
            isn't one (Verilog / VHDL: the emitted RTL is the implementation).
            Drives the ``"stdlib-crc32"`` advisory; see
            :meth:`advisories_for`.
    """

    code: str
    extensions: tuple[str, ...]
    variants: frozenset[str]
    naming: frozenset[str]
    default_naming: str
    generator: Callable
    generator_from_entry: Callable
    combiner: Callable
    emoji: str
    display_name: str
    stdlib_crc32: str | None = None
    #: How a stem becomes the output filename (and, for ``"pascal"``, the public
    #: class).  ``"snake"`` for most targets; ``"pascal"`` for C# / Java, whose
    #: file is named after a PascalCase class.
    filename_case: Literal["snake", "pascal"] = "snake"

    def variants_for_width(self, width: int) -> tuple[str, ...]:
        """Implementation variants this language supports at a given width.

        Identical to ``self.variants`` ordered by :data:`VARIANT_ORDER`,
        with two width-based exclusions:

        * ``"slice8"`` is filtered out when ``width`` is anything other
          than 32 or 64 -- the slice-by-8 implementation chunks the input
          8 bytes at a time and only makes sense at those widths.
        * ``"table"`` is filtered out for sub-byte widths (``width < 8``).
          A 256-entry lookup to checksum the tiny payloads these CRCs run
          on (USB tokens, MMC commands, RFID, CAN control fields) is pure
          overhead -- the table build dwarfs the CRC -- and the byte-wise
          table update has no well-defined form when the register is
          narrower than a byte.  Sub-byte CRCs are bit-by-bit only.

        The generators raise ``ValueError`` for an excluded variant, so
        surfacing the rule here lets dropdowns and other UI code avoid
        offering options that would error.

        Args:
            width: CRC width in bits (e.g. ``5``, ``8``, ``16``, ``32``).

        Returns:
            Supported variants in canonical order.

        Examples:
            >>> from crcglot import LANGUAGES
            >>> LANGUAGES["c"].variants_for_width(32)
            ('bitwise', 'table', 'slice8')
            >>> LANGUAGES["c"].variants_for_width(16)
            ('bitwise', 'table')
            >>> LANGUAGES["c"].variants_for_width(5)
            ('bitwise',)
        """
        return tuple(
            v for v in VARIANT_ORDER
            if v in self.variants
            and not (v == "slice8" and width not in (32, 64))
            and not (v == "table" and width < 8)
        )

    def variants_for_widths(self, widths: Iterable[int]) -> tuple[str, ...]:
        """Implementation variants valid across *all* the given widths.

        For a multi-algorithm bundle the combiner emits a single
        implementation shared by every member, so the offered variant set is
        the **intersection** of :meth:`variants_for_width` over each member's
        width -- e.g. bundling a 32-bit with a 16-bit CRC drops ``"slice8"``
        (valid at 32, not 16).  Surfacing the rule here keeps a UI from having
        to intersect the per-width sets itself.

        Args:
            widths: The CRC widths in the bundle (e.g. ``[32, 16, 8]``).  An
                empty iterable applies no width constraint (the language's
                full variant set).

        Returns:
            Variants valid for every width, in canonical order.

        Examples:
            >>> from crcglot import LANGUAGES
            >>> LANGUAGES["c"].variants_for_widths([32, 64])
            ('bitwise', 'table', 'slice8')
            >>> LANGUAGES["c"].variants_for_widths([32, 16])
            ('bitwise', 'table')
            >>> LANGUAGES["c"].variants_for_widths([32, 5])
            ('bitwise',)
        """
        widths = list(widths)
        if not widths:
            return tuple(v for v in VARIANT_ORDER if v in self.variants)
        common = set(self.variants_for_width(widths[0]))
        for w in widths[1:]:
            common &= set(self.variants_for_width(w))
        return tuple(v for v in VARIANT_ORDER if v in common)

    def fastest_variant_for_width(self, width: int) -> str:
        """The fastest implementation variant valid at this width.

        This is the default the CLI, MCP, and generators resolve ``"auto"`` to:
        slice-by-8 where the language and width allow it (32 / 64), else
        table-driven, else bit-by-bit (sub-byte widths, or a language that only
        ships bitwise).  Since :meth:`variants_for_width` is ordered
        slowest-to-fastest and always includes ``"bitwise"``, this is just its
        last element.

        Args:
            width: CRC width in bits.

        Returns:
            One of ``"bitwise"`` / ``"table"`` / ``"slice8"``.

        Examples:
            >>> from crcglot import LANGUAGES
            >>> LANGUAGES["c"].fastest_variant_for_width(32)
            'slice8'
            >>> LANGUAGES["c"].fastest_variant_for_width(16)
            'table'
            >>> LANGUAGES["python"].fastest_variant_for_width(32)
            'table'
        """
        return self.variants_for_width(width)[-1]

    def validate_symbol(self, stem: str) -> str:
        """Sanitize a desired name/stem to this target's identifier base.

        Returns the snake-style base (``-`` / ``.`` -> ``_``, path stripped).
        For targets whose file is named after a class (C# / Java,
        ``filename_case == "pascal"``), it also verifies the resulting
        PascalCase class is a legal identifier, raising ``ValueError`` if not --
        so a UI can validate a field before generating.

        Examples:
            >>> from crcglot import LANGUAGES
            >>> LANGUAGES["rust"].validate_symbol("my-crc")
            'my_crc'
        """
        base = _sanitize_base(stem)
        if self.filename_case == "pascal" and not _is_legal_class_identifier(
            _pascal_base(base)
        ):
            raise ValueError(
                f"{stem!r} yields class {_pascal_base(base)!r}, not a legal "
                f"{self.display_name} class name (start with a letter; use "
                f"letters / digits / _; not a reserved word)"
            )
        return base

    def format_name(self, stem: str, kind: str = "filename") -> str:
        """Case ``stem`` to this target's convention for a filename or identifier.

        The casing crcglot itself applies, exposed so a UI's name field agrees
        with the generated output instead of reimplementing per-language rules:

        * ``kind="filename"`` -- the output basename (minus extension): path
          stripped + ``-`` / ``.`` -> ``_`` for snake targets; additionally
          PascalCased for C# / Java, whose file is named after the public class.
        * ``kind="identifier"`` -- the public function / method base, cased to
          the language's idiomatic convention (snake / camel / pascal): e.g.
          ``crc_bundle`` -> ``crcBundle`` in TypeScript, ``CrcBundle`` in Go.

        Pure and total: whitespace-only / empty input returns unchanged (the
        caller guards empty before generating).  The casing is not a round-trip
        (``my_crcs`` and ``MyCrcs`` both yield ``Mycrcs``), so pass the raw stem,
        not a previously-formatted one.

        Args:
            stem: Desired stem, without extension.
            kind: ``"filename"`` (default) or ``"identifier"``.

        Returns:
            ``stem`` cased for this target and ``kind``.

        Raises:
            ValueError: ``kind`` is not ``"filename"`` or ``"identifier"``.

        Examples:
            >>> from crcglot import LANGUAGES
            >>> LANGUAGES["go"].format_name("crc_bundle", "filename")
            'crc_bundle'
            >>> LANGUAGES["go"].format_name("crc_bundle", "identifier")
            'CrcBundle'
            >>> LANGUAGES["typescript"].format_name("crc-bundle", "identifier")
            'crcBundle'
        """
        if not stem.strip():
            return stem
        base = _sanitize_base(stem)
        if kind == "filename":
            return _pascal_base(base) if self.filename_case == "pascal" else base
        if kind == "identifier":
            convention = naming_convention_for(self.code, self.default_naming)
            return _join_naming(base.split("_"), convention)
        raise ValueError(f"kind must be 'filename' or 'identifier'; got {kind!r}")

    def format_filename(self, stem: str) -> str:
        """Case ``stem`` to this target's filename convention.

        Convenience for :meth:`format_name` with ``kind="filename"`` -- the exact
        basename :meth:`generate_files` writes for ``name=stem`` (minus
        extension).  See :meth:`format_name` for the casing rules and the
        non-round-trip caveat.

        Examples:
            >>> from crcglot import LANGUAGES
            >>> LANGUAGES["rust"].format_filename("crc_bundle")
            'crc_bundle'
            >>> LANGUAGES["java"].format_filename("crc32")
            'Crc32'
            >>> LANGUAGES["csharp"].format_filename("crc-bundle")
            'CrcBundle'
        """
        return self.format_name(stem, "filename")

    def generate_files(
        self,
        algorithm: str | Sequence[str] | None = None,
        *,
        custom: AlgorithmInfo | None = None,
        variant: str = "auto",
        comment_style: str = "plain",
        naming: str | None = None,
        name: str | None = None,
        symbol: str | None = None,
    ) -> tuple[GeneratedFile, ...]:
        """Generate complete, correctly-named source file(s) for this target.

        The one call a CLI / MCP / UI needs: pass configuration, get back
        ready-to-write :class:`GeneratedFile`s.  crcglot owns every naming
        decision -- the filename(s) and the in-code class/module renamed to
        match (Java's class *must* equal the file; C is a ``.h`` / ``.c`` pair).

        Args:
            algorithm: A catalogue name, or several to bundle into one file.
                Mutually exclusive with ``custom``.
            custom: A custom :class:`AlgorithmInfo` (a recovered / Rocksoft
                tuple) instead of a catalogue entry.
            variant: ``"auto"`` (fastest the target + width supports) or an
                explicit ``"bitwise"`` / ``"table"`` / ``"slice8"``.
            comment_style: Forwarded to the generator.
            naming: Forwarded to the generator; defaults to the language's
                idiomatic convention.
            name: The one naming knob -- sets the filename stem AND the in-code
                identifier / class, **cased per target**.  Valid for a single
                CRC and for a bundle (where it names the file / module / class;
                each member keeps its own function name).  Defaults to the
                algorithm name (single) or a neutral bundle stem.
            symbol: Emit the in-code identifier **verbatim** (escape hatch) --
                the filename still follows ``name``.  Single CRC, not valid for
                Java.  Combine ``name=`` + ``symbol=`` to make the file and the
                identifier differ.

        Returns:
            One :class:`GeneratedFile` (two for C: header + source).

        Raises:
            ValueError: bad ``algorithm`` / ``custom`` combination, ``symbol``
                with a bundle, ``symbol`` for Java, or a ``name`` that can't be
                a legal class name for a strict target.

        Examples:
            >>> from crcglot import LANGUAGES
            >>> [f.filename for f in LANGUAGES["c"].generate_files("crc16-xmodem")]
            ['crc16_xmodem.h', 'crc16_xmodem.c']
            >>> LANGUAGES["java"].generate_files("crc16-xmodem")[0].filename
            'Crc16Xmodem.java'
            >>> LANGUAGES["rust"].generate_files("crc32", name="my-widget")[0].filename
            'my_widget.rs'
            >>> bundle = LANGUAGES["rust"].generate_files(["crc32", "crc8"], name="checks")
            >>> bundle[0].filename
            'checks.rs'
        """
        if (algorithm is None) == (custom is None):
            raise ValueError("supply exactly one of algorithm or custom")
        naming_resolved = naming_convention_for(
            self.code, naming or self.default_naming
        )

        # Work list: [(display_name, AlgorithmInfo), ...].
        if custom is not None:
            items = [(_sanitize_base(name) if name else "crc_custom", custom)]
        else:
            assert algorithm is not None  # guaranteed by the xor check above
            names = [algorithm] if isinstance(algorithm, str) else list(algorithm)
            names = list(dict.fromkeys(names))
            unknown = [n for n in names if n not in ALGORITHMS]
            if unknown:
                raise unknown_algorithm_error(unknown[0])
            items = [(n, ALGORITHMS[n]) for n in names]
        multi = len(items) > 1
        if multi and symbol is not None:
            raise ValueError("symbol= names one function; omit it for a bundle")
        if self.code == "java" and symbol is not None:
            raise ValueError(
                "symbol= is not used for Java (methods are named after the "
                "algorithm, the class after name=); use name="
            )

        # `name` is the one knob: it sets the filename stem AND the in-code
        # base (identifier / class), cased per target.  `symbol` overrides only
        # the in-code identifier (verbatim); the filename still follows `name`
        # -- so `name=out symbol=foo` writes out.* with a foo() inside.
        display0 = items[0][0]
        if name is not None:
            file_base = _sanitize_base(name)
        elif symbol is not None:
            file_base = _sanitize_base(symbol)
        elif multi:
            file_base = default_stem(names)
        else:
            file_base = default_stem(display0)

        def _gen(disp, algo, *, sym=None, nm=None):
            # ``disp`` (the catalogue / display name) is always the algorithm
            # label -- it drives the header title, the ``reveng/`` line, and the
            # provenance ``algorithm`` field.  ``nm`` (the file stem from
            # ``name=`` / ``file=``) only retargets the in-code identifier, via
            # ``stem``; it must not relabel the algorithm.
            return self.generator_from_entry(
                disp, algo, symbol=sym, stem=nm,
                variant=variant, comment_style=comment_style, naming=naming_resolved,
            )

        if multi:
            outputs = [_gen(d, a) for d, a in items]
            if self.code == "java":
                result = combine_java(outputs, stem=_pascal_base(file_base))
            else:
                result = self.combiner(outputs, file_base)
        else:
            disp, algo = items[0]
            if self.code == "java":
                # Methods follow name= when given, else the algorithm; the class
                # (and file) is the PascalCase file_base via the combiner.
                result = combine_java(
                    [_gen(disp, algo, nm=name)], stem=_pascal_base(file_base)
                )
            elif symbol is not None:
                result = _gen(disp, algo, sym=symbol)
            elif name is not None:
                result = _gen(disp, algo, nm=name)
            else:
                result = _gen(disp, algo)

        # The generator fields are typed only as ``Callable``, so the result is
        # untyped here; it is a string (or a header/source pair for C).
        result = cast("str | tuple[str, str]", result)
        stem = _pascal_base(file_base) if self.filename_case == "pascal" else file_base
        if self.filename_case == "pascal" and not _is_legal_class_identifier(stem):
            raise ValueError(
                f"{(name or symbol or display0)!r} yields class {stem!r}, "
                f"not a legal {self.display_name} class name"
            )
        exts = self.extensions
        if isinstance(result, tuple):  # C: (header, source)
            return tuple(
                GeneratedFile(f"{stem}{ext}", content, role)
                for content, ext, role in zip(result, exts, ("header", "source"))
            )
        return (GeneratedFile(f"{stem}{exts[0]}", result),)

    def advisories_for(
        self, algorithms: Sequence[str | AlgorithmInfo],
    ) -> tuple[Advisory, ...]:
        """Informational notes about faster alternatives for a generation.

        Two mutually-exclusive triggers, mirroring the runtime engine's own
        fast paths:

        * **Python target** (``"warning"``): the emitted Python is interpreted;
          crcglot's own runtime dispatches to a C extension (and ``zlib.crc32``
          for IEEE CRC-32), so the package itself is much faster than the
          generated file.  The note frames the file as the port / no-dependency
          answer, not the first choice.
        * **A CRC-32-equivalent algorithm on a compiled target** (``"info"``):
          the language has a stdlib / canonical-package CRC-32 (typically on CPU
          CRC instructions; :attr:`stdlib_crc32`).  The emitted code is fine for
          small messages, but the library wins on large or streaming data.

        Eligibility is by parameter tuple
        (:func:`crcglot.catalogue.has_faster_alternative`), so it covers
        ``crc32``, ``crc32-jamcrc``, and custom CRC-32-equivalents alike.  HDL
        targets, and selections with no fast-path algorithm, get ``()``.

        Args:
            algorithms: The algorithms being generated -- catalogue names
                (``str``) and/or custom :class:`AlgorithmInfo` records.

        Returns:
            Zero or one :class:`Advisory` (the two triggers are exclusive).

        Examples:
            >>> from crcglot import LANGUAGES
            >>> [a.kind for a in LANGUAGES["c"].advisories_for(["crc32"])]
            ['stdlib-crc32']
            >>> [a.kind for a in LANGUAGES["python"].advisories_for(["crc16-modbus"])]
            ['python-runtime']
            >>> LANGUAGES["vhdl"].advisories_for(["crc32"])
            ()
        """
        infos = [ALGORITHMS[a] if isinstance(a, str) else a for a in algorithms]
        names = [a for a in algorithms if isinstance(a, str)]
        eligible = [i for i in infos if has_faster_alternative(i)]

        if self.code == "python":
            if names:
                head = ", ".join(f"`'{n}'`" for n in names[:3])
                tail = "" if len(names) <= 3 else f" (+{len(names) - 3} more)"
                call = f"`crcglot.encode_int(data, name)` with name = {head}{tail}"
            else:
                call = (
                    "`crcglot.generic_crc(data, crcglot.Crc(width=..., poly=..., "
                    "init=..., refin=..., refout=..., xorout=...))`"
                )
            speed = (
                "it dispatches to `zlib.crc32` (CPU CRC instructions), far faster "
                "than the emitted pure-Python code"
                if eligible else
                "it uses the fastest available implementation (a C extension), "
                "close to C speed for any catalogue algorithm"
            )
            message = (
                f"For Python use cases, prefer the `crcglot` package itself: "
                f"`pip install crcglot`, then {call}, and {speed}.  Generate the "
                f"file below only when you need a self-contained Python module "
                f"(no crcglot install on the target, a locked-down environment)."
            )
            return (Advisory("warning", "python-runtime", message),)

        if eligible and self.stdlib_crc32 is not None:
            extra = (
                "  (crc32-jamcrc is that value XOR 0xFFFFFFFF.)"
                if any(i.xorout == 0 for i in eligible) else ""
            )
            message = (
                f"Faster CRC-32 path on {self.display_name}: {self.stdlib_crc32}.  "
                f"The generated code is fine for small messages, but for large "
                f"files or streaming throughput prefer that library; it uses CPU "
                f"CRC instructions where the processor supports them.{extra}"
            )
            return (Advisory("info", "stdlib-crc32", message),)

        return ()

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

    @property
    def naming_infos(self) -> tuple[NamingInfo, ...]:
        """The naming conventions valid for this language, as rich records.

        The naming companion to :attr:`styles` / :meth:`variant_infos_for_width`:
        ``LANGUAGES[code].naming_infos`` gives a UI each convention's
        ``label`` / ``description``, ordered by :data:`NAMING_ORDER`, with the
        idiomatic :attr:`default_naming` first in spirit (a UI can preselect it).

        Examples:
            >>> [n.name for n in LANGUAGES["go"].naming_infos]
            ['camel', 'pascal']
            >>> [n.name for n in LANGUAGES["python"].naming_infos]
            ['snake']
        """
        return tuple(
            naming_info(n) for n in NAMING_ORDER if n in self.naming
        )


_BITWISE_TABLE = frozenset({"bitwise", "table"})
_BITWISE_TABLE_SLICE8 = frozenset({"bitwise", "table", "slice8"})
_BITWISE_ONLY = frozenset({"bitwise"})

# Naming-convention sets.  "Clean" languages enforce one convention; the
# "mess" of C/C++ admits all three.  Defaults are the idiomatic choice.
_SNAKE_ONLY = frozenset({"snake"})
_ALL_CASES = frozenset({"snake", "camel", "pascal"})
_PASCAL_CAMEL = frozenset({"pascal", "camel"})
_CAMEL_PASCAL = frozenset({"camel", "pascal"})


LANGUAGES: dict[str, LanguageInfo] = {
    "c": LanguageInfo(
        code="c",
        extensions=(".h", ".c"),
        variants=_BITWISE_TABLE_SLICE8,
        naming=_ALL_CASES,
        default_naming="snake",
        generator=generate_c,
        generator_from_entry=generate_c_from_entry,
        combiner=combine_c,
        emoji="⚙️",  # gear
        display_name="C / C++",
        stdlib_crc32="zlib's `crc32()` (`<zlib.h>`)",
    ),
    "csharp": LanguageInfo(
        code="csharp",
        extensions=(".cs",),
        variants=_BITWISE_TABLE_SLICE8,
        naming=_PASCAL_CAMEL,
        default_naming="pascal",
        generator=generate_csharp,
        generator_from_entry=generate_csharp_from_entry,
        combiner=combine_csharp,
        emoji="\U0001F4A0",  # diamond with a dot
        display_name="C#",
        stdlib_crc32="`System.IO.Hashing.Crc32` (.NET 6+)",
        filename_case="pascal",  # file is named after the public class
    ),
    "go": LanguageInfo(
        code="go",
        extensions=(".go",),
        variants=_BITWISE_TABLE_SLICE8,
        naming=_PASCAL_CAMEL,
        default_naming="pascal",
        generator=generate_go,
        generator_from_entry=generate_go_from_entry,
        combiner=combine_go,
        emoji="\U0001F6A6",  # vertical traffic light
        display_name="Go",
        stdlib_crc32="the `hash/crc32` stdlib (`crc32.ChecksumIEEE`)",
    ),
    "java": LanguageInfo(
        code="java",
        extensions=(".java",),
        variants=_BITWISE_TABLE_SLICE8,
        naming=_CAMEL_PASCAL,
        default_naming="camel",
        generator=generate_java,
        generator_from_entry=generate_java_from_entry,
        combiner=combine_java,
        emoji="☕",  # hot beverage (coffee)
        display_name="Java",
        stdlib_crc32="`java.util.zip.CRC32`",
        filename_case="pascal",  # file MUST be named after the public class
    ),
    "python": LanguageInfo(
        code="python",
        extensions=(".py",),
        variants=_BITWISE_TABLE,
        naming=_SNAKE_ONLY,
        default_naming="snake",
        generator=generate_python,
        generator_from_entry=generate_python_from_entry,
        combiner=combine_concat,
        emoji="\U0001F40D",  # snake
        display_name="Python",
        stdlib_crc32="`zlib.crc32`",
    ),
    "rust": LanguageInfo(
        code="rust",
        extensions=(".rs",),
        variants=_BITWISE_TABLE_SLICE8,
        naming=_SNAKE_ONLY,
        default_naming="snake",
        generator=generate_rust,
        generator_from_entry=generate_rust_from_entry,
        combiner=combine_concat,
        emoji="\U0001F980",  # crab
        display_name="Rust",
        stdlib_crc32="the `crc32fast` crate (`crc32fast::hash`)",
    ),
    "typescript": LanguageInfo(
        code="typescript",
        extensions=(".ts",),
        variants=_BITWISE_TABLE_SLICE8,
        naming=_CAMEL_PASCAL,  # snake_case is non-idiomatic in TS (ESLint flags it)
        default_naming="camel",
        generator=generate_typescript,
        generator_from_entry=generate_typescript_from_entry,
        combiner=combine_concat,
        emoji="\U0001F537",  # large blue diamond
        display_name="TypeScript",
        stdlib_crc32="the `crc-32` npm package (`CRC32.buf`)",
    ),
    "verilog": LanguageInfo(
        code="verilog",
        extensions=(".sv",),
        variants=_BITWISE_ONLY,
        naming=_SNAKE_ONLY,
        default_naming="snake",
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
        naming=_SNAKE_ONLY,
        default_naming="snake",
        generator=generate_vhdl,
        generator_from_entry=generate_vhdl_from_entry,
        combiner=combine_concat,
        emoji="\U0001F50C",  # electric plug
        display_name="VHDL",
    ),
}


def generate_files(
    language: str,
    algorithm: str | Sequence[str] | None = None,
    *,
    custom: AlgorithmInfo | None = None,
    variant: str = "auto",
    comment_style: str = "plain",
    naming: str | None = None,
    name: str | None = None,
    symbol: str | None = None,
) -> tuple[GeneratedFile, ...]:
    """Generate complete, correctly-named source file(s) for ``language``.

    The consumer-facing front door to :meth:`LanguageInfo.generate_files`:
    configure once, read finished files out.  crcglot owns the filename and the
    in-code class/module naming; the caller just writes each
    :class:`GeneratedFile`'s ``content`` to its ``filename``.

    Args:
        language: A key of :data:`LANGUAGES` (e.g. ``"rust"``).
        algorithm: A catalogue name or list of names; see
            :meth:`LanguageInfo.generate_files` for the full keyword set
            (``custom`` / ``variant`` / ``comment_style`` / ``naming`` /
            ``name`` / ``symbol``).

    Returns:
        One :class:`GeneratedFile` (two for C: header + source).

    Raises:
        ValueError: unknown ``language``, or any error from
            :meth:`LanguageInfo.generate_files`.

    Examples:
        >>> from crcglot import generate_files
        >>> generate_files("rust", "crc16-xmodem")[0].filename
        'crc16_xmodem.rs'
        >>> generate_files("java", "crc32", name="my-widget")[0].filename
        'MyWidget.java'
    """
    if language not in LANGUAGES:
        raise ValueError(
            f"unknown language {language!r}; one of {sorted(LANGUAGES)}"
        )
    return LANGUAGES[language].generate_files(
        algorithm, custom=custom, variant=variant, comment_style=comment_style,
        naming=naming, name=name, symbol=symbol,
    )
