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
