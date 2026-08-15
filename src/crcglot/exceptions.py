"""Exception hierarchy for crcglot.

Every error crcglot raises on purpose derives from :class:`CrcglotError`, so a
consumer can ``except CrcglotError`` to catch "crcglot rejected this" apart from
any other error in their own code.  Each concrete error *also* derives from the
conventional standard-library type it has always been (``ValueError`` /
``TypeError``), so existing ``except ValueError`` / ``except TypeError`` handlers
keep working unchanged -- adopting the base is additive, never breaking.

Examples:
    >>> from crcglot import compute, CrcglotError, UnknownAlgorithmError
    >>> try:
    ...     compute(b"123456789", "crc16")
    ... except CrcglotError as e:        # catches anything crcglot rejects
    ...     kind = type(e).__name__
    >>> kind
    'UnknownAlgorithmError'
    >>> issubclass(UnknownAlgorithmError, ValueError)   # old handlers still catch it
    True
"""

from __future__ import annotations


class CrcglotError(Exception):
    """Base class for every error crcglot raises deliberately."""


class UnknownAlgorithmError(CrcglotError, ValueError):
    """An algorithm name is not in the catalogue.

    Also a ``ValueError`` for backward compatibility.  The message carries a
    best-effort suggestion built by :func:`crcglot.catalogue.suggest_algorithms`
    (a ``crc<width>`` family hint, a close-match "did you mean", or a pointer to
    browse the catalogue).
    """


class UnknownVerbError(CrcglotError, ValueError):
    """A verb name is not in the :data:`crcglot.VERBS` manifest.

    Also a ``ValueError`` by convention.  The message suggests a close match
    when one exists and lists the full verb vocabulary (built by
    :func:`crcglot.verbs.verb_info`, the vocabulary's owner).
    """


class MixedFormatError(CrcglotError, ValueError):
    """A format record describes packets that did not share one surface shape.

    Raised by :func:`crcglot.encode_match` when the :class:`~crcglot.TextFormat`
    or :class:`~crcglot.HexFormat` it was handed has a non-empty ``mixed``, so
    its ``separator`` / ``prefix`` are one packet's values rather than every
    packet's.  Rebuilding from such a record would emit a frame shape some of
    the input never had, so it raises instead.  Also a ``ValueError``.
    """


class UnknownTerminatorError(CrcglotError, ValueError):
    """A frame terminator name is not in the :data:`crcglot.TERMINATORS` registry.

    Also a ``ValueError`` by convention.  The vocabulary is small and closed,
    so the message lists all of it rather than guessing at a near match.
    """


class UnknownParamError(CrcglotError, TypeError):
    """A :func:`crcglot.call_verb` parameter is not in the verb's manifest.

    A ``TypeError`` because that is Python's convention for an unexpected
    keyword argument.  The message suggests a close match when one exists and
    lists the verb's valid parameters.
    """
