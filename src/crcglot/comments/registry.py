"""Style registry + (language, style) compatibility, derived from the styles.

Each :class:`~crcglot.comments.base.CommentStyle` subclass is self-describing
(it declares its own ``name`` and ``languages``), so this module only lists the
classes -- the compatibility matrix is **derived**, never duplicated.  CLI and
MCP query it (:func:`styles_for_language`) instead of hardcoding which styles
fit which language.  Adding a style is one class (its own module) plus one
entry in :data:`_STYLES`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import CommentStyle, PlainStyle
from .docfx import DocfxStyle
from .doxygen import DoxygenStyle
from .godoc import GodocStyle
from .google import GoogleStyle
from .javadoc import JavadocStyle
from .jsdoc import JsdocStyle
from .numpy import NumpyStyle
from .rest import RestStyle
from .rustdoc import RustdocStyle

#: Every style class, in display order.  Each carries its own ``name`` and
#: ``languages``; the maps and queries below are derived from these.
_STYLES: tuple[type[CommentStyle], ...] = (
    PlainStyle,
    DoxygenStyle,
    GoogleStyle,
    NumpyStyle,
    RestStyle,
    RustdocStyle,
    GodocStyle,
    DocfxStyle,
    JavadocStyle,
    JsdocStyle,
)

_BY_NAME: dict[str, type[CommentStyle]] = {cls.name: cls for cls in _STYLES}

#: Every comment style crcglot can emit (all are implemented).
COMMENT_STYLES: tuple[str, ...] = tuple(_BY_NAME)


@dataclass(frozen=True)
class StyleInfo:
    """A comment style's metadata, for building UIs (dropdowns, help text).

    ``name`` is the machine-readable code passed to the generator / CLI / MCP
    (the dropdown's value); ``label`` and ``description`` are human-readable
    for display; ``languages`` is where the style applies.
    """

    name: str
    label: str
    description: str
    languages: frozenset[str]


def _info(cls: type[CommentStyle]) -> StyleInfo:
    return StyleInfo(cls.name, cls.label, cls.description, cls.languages)


def comment_style_for(language: str, style: str) -> CommentStyle:
    """Resolve a :class:`CommentStyle` for a (language, style) pair.

    Args:
        language: Target-language code (``"c"``, ``"java"``, ...).
        style: Comment style (``"plain"`` plus the doc-tool styles).

    Returns:
        A ready-to-use :class:`CommentStyle`.

    Raises:
        ValueError: Unknown style, or a style that does not apply to this
            language (e.g. ``doxygen`` for ``go``).

    Examples:
        >>> comment_style_for("c", "plain").language
        'c'
    """
    cls = _BY_NAME.get(style)
    if cls is None:
        raise ValueError(
            f"unknown comment style {style!r}; valid: {sorted(_BY_NAME)}"
        )
    if language not in cls.languages:
        raise ValueError(
            f"comment style {style!r} is not valid for language {language!r}; "
            f"it applies to {sorted(cls.languages)} (use 'plain' for {language!r})"
        )
    return cls(language)


def styles_for_language(language: str) -> tuple[str, ...]:
    """The comment styles valid for ``language``, in display order.

    Lets the CLI and MCP offer exactly the compatible styles per language
    without hardcoding the matrix -- it is derived from each style's own
    ``languages``.

    Examples:
        >>> "doxygen" in styles_for_language("c")
        True
        >>> "doxygen" in styles_for_language("go")
        False
        >>> styles_for_language("python")
        ('plain', 'google')
    """
    return tuple(
        name for name, cls in _BY_NAME.items() if language in cls.languages
    )


def languages_for_style(style: str) -> frozenset[str]:
    """The languages ``style`` renders correctly for.

    Raises:
        KeyError: Unknown style.

    Examples:
        >>> sorted(languages_for_style("doxygen"))
        ['c', 'csharp', 'java']
    """
    return _BY_NAME[style].languages


def style_info(style: str) -> StyleInfo:
    """Display metadata (name / label / description / languages) for one style.

    Raises:
        KeyError: Unknown style.

    Examples:
        >>> style_info("google").label
        'Google'
    """
    return _info(_BY_NAME[style])


def comment_styles_for_language(language: str) -> tuple[StyleInfo, ...]:
    """Rich metadata for every style valid for ``language``, in display order.

    The UI-facing companion to :func:`styles_for_language`: a front end picks a
    language, calls this, and builds a dropdown showing each ``label`` /
    ``description`` while submitting the ``name``.  No matrix is hardcoded --
    it is derived from each style's own ``languages``.

    Examples:
        >>> [s.name for s in comment_styles_for_language("python")]
        ['plain', 'google', 'numpy', 'rest']
        >>> comment_styles_for_language("python")[1].label
        'Google'
    """
    return tuple(_info(cls) for cls in _STYLES if language in cls.languages)
