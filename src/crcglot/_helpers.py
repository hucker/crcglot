"""Language-agnostic helpers shared by every target generator.

Function-name sanitization, hex formatting, bit masks, and CRC table
pre-computation are identical across Python / C / Rust / VHDL output
because they're math, not syntax.  Each language module imports what
it needs from here; per-language helpers (table formatters,
self-test scaffolds, header builders) stay local to their target.

Underscore-prefixed; the package's public API is ``__init__.py``.
"""

from __future__ import annotations

from crcglot.catalogue import _reflect


def _func_name(algo_name: str) -> str:
    """Convert a CRC algorithm name into a valid identifier.

    Algorithm names from the reveng catalogue use ``-`` and ``.``
    which aren't valid in C / Python / Rust / VHDL identifiers;
    swap them for underscores.  Same mangling is applied
    consistently across all four target languages.
    """
    return algo_name.replace("-", "_").replace(".", "_")


# The five public functions every generator emits, with the role-suffix
# tokens appended to the algorithm-name tokens.  ``oneshot`` has no suffix
# (its name is the bare algorithm), so snake ``oneshot`` equals the ``base``
# stem -- a property the C ``#include`` and per-symbol table rewrites rely on.
_ROLE_TOKENS: dict[str, tuple[str, ...]] = {
    "oneshot": (),
    "init": ("init",),
    "update": ("update",),
    "finalize": ("finalize",),
    "self_test": ("self", "test"),
}


def _title(token: str) -> str:
    """Title-case one token: ``crc16`` -> ``Crc16`` (matches `_cs_pascal_class`)."""
    return token[:1].upper() + token[1:].lower()


def _join_naming(tokens: list[str], convention: str) -> str:
    """Join identifier tokens under a casing convention.

    Snake preserves token text verbatim (so an explicit ``symbol=`` keeps its
    case); pascal/camel re-case per token.  Catalogue stems are already
    lowercase, so snake is a no-op for them.

    Raises:
        KeyError: Unknown convention.
    """
    if convention == "snake":
        return "_".join(tokens)
    if convention == "pascal":
        return "".join(_title(t) for t in tokens)
    if convention == "camel":
        if not tokens:
            return ""
        return tokens[0].lower() + "".join(_title(t) for t in tokens[1:])
    raise KeyError(
        f"unknown naming convention {convention!r}; valid: 'snake', 'camel', 'pascal'"
    )


def crc_function_names(
    base_snake: str, convention: str, *, is_override: bool = False
) -> dict[str, str]:
    """Build the five public function identifiers under a naming convention.

    ``base_snake`` is the snake-case stem (``crc16_modbus``).  The result maps
    each role (``oneshot|init|update|finalize|self_test``) to its emitted
    identifier.  An explicit ``symbol=`` override (``is_override``) is always
    joined snake-style and emitted verbatim, regardless of ``convention`` --
    the user named it, so we honor it.

    Args:
        base_snake: The snake-case identifier stem.
        convention: ``"snake"``, ``"camel"``, or ``"pascal"``.
        is_override: True when ``base_snake`` came from a user ``symbol=``.

    Returns:
        Role -> identifier, e.g. ``{"update": "Crc16ModbusUpdate", ...}``.

    Examples:
        >>> crc_function_names("crc16_modbus", "pascal")["update"]
        'Crc16ModbusUpdate'
        >>> crc_function_names("crc16_modbus", "camel")["self_test"]
        'crc16ModbusSelfTest'
        >>> crc_function_names("my_check", "pascal", is_override=True)["oneshot"]
        'my_check'
    """
    join = "snake" if is_override else convention
    tokens = base_snake.split("_")
    return {
        role: _join_naming(tokens + list(suffix), join)
        for role, suffix in _ROLE_TOKENS.items()
    }


def combine_concat(outputs: list[str], stem: str | None = None) -> str:
    """Combine several single-file generator outputs by concatenation.

    For languages whose output is self-contained top-level items (Rust,
    TypeScript, Python) or per-unit-guarded packages (Verilog include
    guards, VHDL per-package ``library``/``use`` clauses), several
    algorithms' modules sit in one file unchanged -- per-symbol table names
    already prevent collisions.  ``stem`` is accepted for a uniform combiner
    signature but unused (only the C combiner needs it).

    Args:
        outputs: Individual ``generate_<lang>`` results, one per algorithm.
        stem: Unused here; present for signature parity with ``combine_c``.

    Returns:
        The outputs joined with a blank line between them.

    Examples:
        >>> combine_concat(["fn a() {}", "fn b() {}"])
        'fn a() {}\\n\\nfn b() {}'
    """
    del stem  # only C's combiner needs the output stem
    return "\n\n".join(outputs)


def _hex(value: int, width: int) -> str:
    """Format an integer as a ``0xHEX`` literal sized for ``width`` bits.

    The ``0x``-prefixed form is identical in C / Python / Rust source
    (and acceptable in VHDL comments), so callers across all target
    languages share this helper.  VHDL *code* uses :func:`_vhdl_lit`
    from the vhdl module because hex literals there have a different
    syntax for arithmetic contexts.
    """
    hex_w = (width + 3) // 4
    return f"0x{value:0{hex_w}X}"


def _mask(width: int) -> str:
    """Format ``(1 << width) - 1`` as a hex literal of matching width."""
    return _hex((1 << width) - 1, width)


def _build_table(width: int, poly: int, refin: bool) -> list[int]:
    """Pre-compute the 256-entry CRC lookup table for an algorithm.

    Returns the table as a list of ``width``-bit integers, one per
    possible byte value.  Caller renders this list to its target
    language's array syntax via per-language formatters.

    The reflected-input case uses the reflected polynomial and
    right-shifts; the normal case left-shifts.  Both are textbook
    Sarwate's algorithm.
    """
    table = []
    if refin:
        ref_poly = _reflect(poly, width)
        for i in range(256):
            crc = i
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ ref_poly
                else:
                    crc >>= 1
            table.append(crc & ((1 << width) - 1))
    else:
        for i in range(256):
            crc = i << (width - 8)
            for _ in range(8):
                if crc & (1 << (width - 1)):
                    crc = (crc << 1) ^ poly
                else:
                    crc <<= 1
                crc &= (1 << width) - 1
            table.append(crc)
    return table


def _build_slice8_tables(
    width: int, poly: int, refin: bool,
) -> list[list[int]]:
    """Pre-compute the 8 tables used by slice-by-8 CRC.

    Returns ``[T0, T1, ..., T7]``: each Tk is a 256-entry list of
    width-bit ints.  Tk[i] is the CRC of ``[i] + [0] * k`` -- i.e.
    the contribution to the running CRC of a byte at position k.

    The recurrence: ``T(k+1)[i] = T0[low_byte(Tk[i])] ^ shifted_rest(Tk[i])``
    where the shift direction matches the polynomial direction.  This
    is what powers Intel's slice-by-8 (5-10x throughput over standard
    table-driven for CRC-32 / CRC-64 on big buffers).
    """
    mask = (1 << width) - 1
    t0 = _build_table(width, poly, refin)
    tables = [t0]
    for _ in range(7):
        prev = tables[-1]
        nxt: list[int] = []
        if refin:
            # Reflected: low byte feeds next lookup, rest shifts right.
            for i in range(256):
                v = prev[i]
                nxt.append((t0[v & 0xFF] ^ (v >> 8)) & mask)
        else:
            # Normal: high byte feeds next lookup, rest shifts left.
            for i in range(256):
                v = prev[i]
                top = (v >> (width - 8)) & 0xFF
                nxt.append((t0[top] ^ ((v << 8) & mask)) & mask)
        tables.append(nxt)
    return tables


def resolve_variant(language: str, width: int, variant: str) -> str:
    """Resolve the ``"auto"`` variant sentinel to a concrete implementation.

    ``"auto"`` -- the shared default of the CLI, the MCP ``crc_generate`` tool,
    and every library generator -- means "the fastest variant valid for this
    (language, width)", so a caller who doesn't choose gets fast code rather than
    the smallest.  An explicit ``"bitwise"`` / ``"table"`` / ``"slice8"`` is
    returned unchanged.

    Args:
        language: Target language code (e.g. ``"rust"``).
        width: CRC width in bits, used to pick the fastest valid variant.
        variant: ``"auto"`` or an explicit variant name.

    Returns:
        A concrete variant name.

    Examples:
        >>> resolve_variant("rust", 32, "table")
        'table'
        >>> resolve_variant("rust", 32, "auto")
        'slice8'
    """
    if variant != "auto":
        return variant
    # Lazy import: ``targets`` imports the generators that import this module, so
    # resolving at call time (after both are loaded) sidesteps the cycle.
    from crcglot.targets import LANGUAGES

    return LANGUAGES[language].fastest_variant_for_width(width)


def _variant_to_flags(
    variant: str,
    *,
    allow_table: bool = True,
    allow_slice8: bool = True,
) -> tuple[bool, bool]:
    """Map the public ``variant`` string to the internal ``(table, slice8)`` flags.

    Each generator's body still keys off the two booleans because the
    different implementation paths live behind ``if table:`` / ``if
    slice8:`` branches in helper calls.  Translation happens once at the
    top of the public entry point so the deeper code stays unchanged.

    Args:
        variant: One of ``"bitwise"``, ``"table"``, ``"slice8"``.
        allow_table: ``False`` for generators with no table-driven
            variant (none today; reserved).
        allow_slice8: ``False`` for generators that don't ship a
            slice-by-8 variant (Python: per-int overhead eats the win;
            Verilog / VHDL: synth-time generated, not runtime).

    Returns:
        ``(table, slice8)`` -- exactly one ``True`` for non-bitwise, or
        both ``False`` for bitwise.

    Raises:
        ValueError: ``variant`` isn't one of the three known names, or
            the caller asked for a variant this generator doesn't ship.
    """
    if variant == "bitwise":
        return False, False
    if variant == "table":
        if not allow_table:
            raise ValueError(
                "variant='table' is not supported by this generator; it offers "
                + ", ".join(["bitwise"] + (["slice8"] if allow_slice8 else []))
            )
        return True, False
    if variant == "slice8":
        if not allow_slice8:
            raise ValueError(
                "variant='slice8' is not supported by this generator; it offers "
                + ", ".join(["bitwise"] + (["table"] if allow_table else []))
            )
        return False, True
    raise ValueError(
        f"variant must be 'bitwise', 'table', or 'slice8'; got {variant!r}"
    )


# Note: a Python reference for slice-by-8 was considered but dropped.
# It would have served as a test oracle for the generated C / Rust
# code, but Python doesn't benefit from slice-by-N at runtime (per-int
# overhead eats the win), and using it as an oracle adds a third
# implementation that itself needs verification.  Better verification:
# generate both bit-by-bit and slice-by-8 in the target language,
# compile both, run both on the same inputs, assert they agree.
# Bit-by-bit is reveng-verified, so equivalence means slice-by-8 is
# correct.  Tests live in the per-language files (tests/test_c_gen.py,
# tests/test_rust_gen.py, ...).
