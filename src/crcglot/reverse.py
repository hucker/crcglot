"""Reverse-engineer an unknown CRC's parameters from codewords.

Given a handful of ``(message, crc)`` pairs produced by some unknown CRC,
recover its Rocksoft/Williams parameters -- the capability ``detect``
(catalogue lookup) can't provide for a *custom* polynomial.

This is an **independent, clean-room implementation derived from the linearity
of CRCs over GF(2)** -- the same public mathematics CRC RevEng implements, but
written from first principles, NOT from reveng's source (reveng is GPLv3+;
crcglot is MIT).  It reuses only crcglot's own engine
(:func:`crcglot.generic_crc`).  The technique predates reveng and is not
ownable; we attribute it to Greg Cook / reveng as a courtesy, no more.

Two tiers:

1. **Catalogue tier** -- first check whether the codewords match a known
   catalogue algorithm (fast, the common case).  ``std_algo_only=True`` (the
   default) stops here, mirroring :func:`crcglot.detect`.
2. **Algebraic tier** (``std_algo_only=False``) -- when nothing in the
   catalogue fits, solve for the parameters:

   * **poly** = GCD over GF(2) of *equal-length difference codewords* (in a
     same-length difference, ``init``/``xorout`` cancel, leaving a multiple of
     the generator; the GCD of several recovers it exactly).  The polynomial is
     always uniquely determined.
   * **init/xorout** = a GF(2) linear solve, using the engine as a black box to
     build the length-dependent contribution map (so it's reflection-agnostic).
   * **width / refin / refout** are searched, or fixed when you pass them.

Honest about ambiguity.  A CRC whose generator carries the ``(x+1)`` factor
(most well-made ones do -- it's what detects all odd-bit errors) admits several
``(init, xorout)`` labellings that produce **identical** output on every input.
That set is a coset of a null space, so it is *finite and completely
enumerable*: exactly ``2 ** ambiguity_bits`` members, where ``ambiguity_bits``
is the multiplicity of ``(x+1)`` in the generator.  :func:`reverse` returns the
**whole class** -- all observationally-identical sets -- with a canonical
representative first; ``status`` is ``"unique"`` (one set) or ``"equivalent"``
(several).  Optionally it also validates the recovered model against held-out
frames it didn't train on.

Scope: the CRC is the trailing field of each codeword; byte-aligned messages;
width <= 64.  A CRC bit-packed at an unknown offset mid-frame is out of scope.

:func:`reverse` takes the ``(message, crc)`` split directly.  When you instead
have whole captured frames with the CRC appended at the tail -- the same shape
:func:`detect` and :func:`verify` consume -- use :func:`reverse_packets`, which
splits the trailing CRC field off for you (searching the field size when you
don't know it).
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, cast

from crcglot.catalogue import ALGORITHMS, AlgorithmInfo, _reflect, generic_crc
from crcglot.detect import _parse_text, _read_hex_crc

Codeword = tuple[bytes, int]
Status = Literal["catalogue", "unique", "equivalent", "underdetermined", "none"]

# Cap class enumeration so a pathological generator (many (x+1) factors) can't
# ask us to build an astronomically large coset.
_MAX_CLASS = 64


# ---------------------------------------------------------------------------
# GF(2) polynomial arithmetic (bit i = coefficient of x**i)
# ---------------------------------------------------------------------------


def _deg(a: int) -> int:
    """Degree of a GF(2) polynomial (``-1`` for zero)."""
    return a.bit_length() - 1


def _polymod(a: int, m: int) -> int:
    """Remainder of ``a`` divided by ``m`` over GF(2)."""
    dm = _deg(m)
    while a and _deg(a) >= dm:
        a ^= m << (_deg(a) - dm)
    return a


def _polygcd(a: int, b: int) -> int:
    """GCD of two GF(2) polynomials (Euclid)."""
    while b:
        a, b = b, _polymod(a, b)
    return a


def _gf2_solve(rows: list[int], nvars: int) -> tuple[int, int, list[int]] | None:
    """Solve a GF(2) linear system, returning the full solution structure.

    Each row packs ``nvars`` coefficient bits (0..nvars-1) and the right-hand
    side at bit ``nvars``.

    Returns:
        ``(particular, rank, null_basis)`` -- one particular solution (free
        variables set to 0), the system rank, and a basis for the null space
        (one vector per free variable).  The full solution set is
        ``particular`` XORed with every combination of ``null_basis`` vectors,
        i.e. ``2 ** (nvars - rank)`` solutions.  ``None`` if inconsistent.
    """
    pivots: dict[int, int] = {}  # pivot column -> reduced row (coeff + rhs)
    for row in rows:
        r = row
        for b in range(nvars):
            if (r >> b) & 1:
                if b in pivots:
                    r ^= pivots[b]
                else:
                    if r & ((1 << nvars) - 1):
                        pivots[b] = r
                    break
        else:
            if (r >> nvars) & 1:
                return None  # 0 == 1
    rank = len(pivots)
    free = [b for b in range(nvars) if b not in pivots]

    def back_substitute(seed: int, use_rhs: bool) -> int:
        sol = seed
        for b in sorted(pivots, reverse=True):
            r = pivots[b]
            val = (r >> nvars) & 1 if use_rhs else 0
            for b2 in range(b + 1, nvars):
                if (r >> b2) & 1:
                    val ^= (sol >> b2) & 1
            if val:
                sol |= 1 << b
        return sol

    particular = back_substitute(0, use_rhs=True)
    null_basis = [back_substitute(1 << f, use_rhs=False) for f in free]
    return particular, rank, null_basis


# ---------------------------------------------------------------------------
# Algebraic recovery for one fixed (width, refin, refout) candidate
# ---------------------------------------------------------------------------


def _recover_poly(
    codewords: Sequence[Codeword], width: int, refin: bool, refout: bool,
) -> int | None:
    """Recover the polynomial via GCD of equal-length difference codewords.

    Reflected algorithms reduce to the non-reflected case by bit-reflecting each
    input byte (``refin``) and the output CRC (``refout``); the polynomial then
    falls out directly.

    Returns:
        The ``width``-bit polynomial, or ``None`` if it can't be pinned (too few
        codewords, or all same-content/structured -> the GCD keeps a spurious
        factor and ``deg(g) != width``).
    """
    def tx_msg(m: bytes) -> int:
        if refin:
            m = bytes(_reflect(b, 8) for b in m)
        return int.from_bytes(m, "big")

    def tx_crc(c: int) -> int:
        return _reflect(c, width) if refout else c

    by_len: dict[int, list[Codeword]] = {}
    for msg, crc in codewords:
        by_len.setdefault(len(msg), []).append((msg, crc))

    g = 0
    for group in by_len.values():
        base_msg, base_crc = group[0]
        base_m, base_c = tx_msg(base_msg), tx_crc(base_crc)
        for msg, crc in group[1:]:
            diff = ((tx_msg(msg) ^ base_m) << width) | (tx_crc(crc) ^ base_c)
            g = _polygcd(g, diff)

    # A CRC generator is always odd (it has an x**0 term -- the polynomial's low
    # bit is set), so any factor of x the GCD picked up is spurious; strip it.
    while g and not (g & 1):
        g >>= 1
    if _deg(g) != width:
        return None
    return g ^ (1 << width)


def _recover_iox_class(
    codewords: Sequence[Codeword], width: int, poly: int, refin: bool, refout: bool,
) -> tuple[list[tuple[int, int]], int] | None:
    """Recover the complete ``(init, xorout)`` equivalence class.

    With the polynomial known, ``crc(M) ^ crc0(M)`` is linear in ``init`` plus a
    constant ``xorout``; the per-length contribution columns are measured by
    probing :func:`generic_crc` (so this is reflection-agnostic).  The solution
    set is a coset of the system's null space -- the full set of
    observationally-identical labellings -- which we enumerate.

    Returns:
        ``(members, ambiguity_bits)`` where ``members`` is every ``(init,
        xorout)`` in the class (capped at ``_MAX_CLASS``) and ``ambiguity_bits``
        is the class dimension (``0`` -> unique).  ``None`` if inconsistent.
    """
    mask = (1 << width) - 1
    col_cache: dict[int, list[int]] = {}

    def columns(n: int) -> list[int]:
        if n not in col_cache:
            z = b"\x00" * n
            base = generic_crc(z, width, poly, 0, refin, refout, 0)
            col_cache[n] = [
                generic_crc(z, width, poly, 1 << j, refin, refout, 0) ^ base
                for j in range(width)
            ]
        return col_cache[n]

    rows: list[int] = []
    for msg, crc in codewords:
        cols = columns(len(msg))
        y = crc ^ generic_crc(msg, width, poly, 0, refin, refout, 0)
        for k in range(width):
            coeff = 0
            for j in range(width):
                if (cols[j] >> k) & 1:
                    coeff |= 1 << j           # init bit j
            coeff |= 1 << (width + k)          # xorout bit k
            if (y >> k) & 1:
                coeff |= 1 << (2 * width)      # rhs
            rows.append(coeff)

    solved = _gf2_solve(rows, 2 * width)
    if solved is None:
        return None
    particular, rank, null_basis = solved
    dim = 2 * width - rank

    def split(v: int) -> tuple[int, int]:
        return v & mask, (v >> width) & mask

    if dim == 0:
        return [split(particular)], 0
    if (1 << dim) > _MAX_CLASS:
        # Too many to enumerate; return the representative only.
        return [split(particular)], dim
    members = set()
    for take in itertools.product((0, 1), repeat=dim):
        v = particular
        for t, bv in zip(take, null_basis):
            if t:
                v ^= bv
        members.add(split(v))
    return sorted(members), dim


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReverseResult:
    """Outcome of :func:`reverse`.

    Attributes:
        status: ``"catalogue"`` (matched a known algorithm), ``"unique"``
            (recovered, one parameter set), ``"equivalent"`` (recovered and
            verified, but several ``(init, xorout)`` labellings are
            observationally identical -- all returned), ``"underdetermined"``
            (couldn't pin the polynomial; supply more varied frames), or
            ``"none"`` (no model reproduces the codewords).
        candidates: Every model consistent with the codewords -- the full
            equivalence class, all observationally identical.  The first is the
            canonical representative (a catalogue match where one exists).
        catalogue_name: Name of the catalogue entry a member matches, if any.
        ambiguity_bits: ``log2`` of the class size (``0`` -> unique).
        validated_frames: How many held-out frames (not used for recovery) the
            model correctly predicted, or ``-1`` if held-out validation didn't
            run.  Empirical confidence, no math required.
        note: Human-readable summary and guidance.
    """

    status: Status
    candidates: tuple[AlgorithmInfo, ...] = ()
    catalogue_name: str | None = None
    ambiguity_bits: int = 0
    validated_frames: int = -1
    note: str = ""

    def __bool__(self) -> bool:
        return self.status not in ("none", "underdetermined")

    @property
    def info(self) -> AlgorithmInfo | None:
        """The canonical representative (first candidate), or ``None``."""
        return self.candidates[0] if self.candidates else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_info(width: int, poly: int, init: int, refin: bool, refout: bool,
             xorout: int, desc: str, source: str) -> AlgorithmInfo:
    check = generic_crc(b"123456789", width, poly, init, refin, refout, xorout)
    return AlgorithmInfo(width, poly, init, refin, refout, xorout, check, desc, source)


def _catalogue_name(width: int, poly: int, init: int, refin: bool, refout: bool,
                    xorout: int) -> str | None:
    """The catalogue entry with exactly these parameters, if one exists."""
    for name, a in ALGORITHMS.items():
        if (a.width, a.poly, a.init, a.refin, a.refout, a.xorout) == (
                width, poly, init, refin, refout, xorout):
            return name
    return None


def _catalogue_match(codewords: Sequence[Codeword]) -> list[str]:
    """Names of catalogue algorithms that reproduce every codeword."""
    return [
        name for name, a in ALGORITHMS.items()
        if all(
            generic_crc(m, a.width, a.poly, a.init, a.refin, a.refout, a.xorout) == c
            for m, c in codewords
        )
    ]


def _solve_dials(
    codewords: Sequence[Codeword], width: int | None, refin: bool | None,
    refout: bool | None, poly: int | None, init: int | None, xorout: int | None,
) -> tuple[int, int, bool, bool, list[tuple[int, int]], int] | None:
    """Search the cheap dials and solve; return the winning model + class.

    Returns ``(width, poly, refin, refout, members, ambiguity_bits)`` for the
    first ``(width, refin, refout)`` whose recovered model reproduces every
    codeword, preferring a fully-determined (``ambiguity_bits == 0``) solution.
    ``None`` if nothing fits.
    """
    max_bits = max(c.bit_length() for _, c in codewords)
    widths = [width] if width is not None else list(range(max(1, max_bits), 65))
    refin_opts = [refin] if refin is not None else [False, True]
    refout_opts = [refout] if refout is not None else [False, True]

    best: tuple[int, int, bool, bool, list[tuple[int, int]], int] | None = None
    for w in widths:
        if w < max_bits:
            continue
        for ri in refin_opts:
            for ro in refout_opts:
                p = poly if poly is not None else _recover_poly(codewords, w, ri, ro)
                if p is None:
                    continue
                got = _recover_iox_class(codewords, w, p, ri, ro)
                if got is None:
                    continue
                members, dim = got
                # Honour any fixed init/xorout by filtering the class.
                if init is not None:
                    members = [(i, x) for i, x in members if i == init]
                if xorout is not None:
                    members = [(i, x) for i, x in members if x == xorout]
                if not members:
                    continue
                # Final arbiter: the representative must reproduce every codeword.
                ri0, xo0 = members[0]
                if not all(
                    generic_crc(m, w, p, ri0, ri, ro, xo0) == c for m, c in codewords
                ):
                    continue
                dim = 0 if (init is not None or xorout is not None) else dim
                found = (w, p, ri, ro, members, dim)
                if dim == 0:
                    return found
                best = best or found
    return best


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reverse(
    frames: Sequence[Codeword],
    *,
    std_algo_only: bool = True,
    width: int | None = None,
    refin: bool | None = None,
    refout: bool | None = None,
    poly: int | None = None,
    init: int | None = None,
    xorout: int | None = None,
    validate: bool = True,
) -> ReverseResult:
    """Recover the CRC parameters that produced a set of codewords.

    Args:
        frames: The codewords as ``(message_bytes, crc_int)`` pairs.  For the
            algebraic tier, supply **several varied** frames -- varied in
            *content* (so the polynomial GCD converges) and in *length* (to
            separate ``init`` from ``xorout``); a single frame suffices only for
            a catalogue match.
        std_algo_only: When ``True`` (default), only attempt the catalogue match
            (identical to :func:`detect` on these pairs).  ``False`` falls
            through to algebraic recovery of a custom algorithm.
        width: Fix the CRC width, or ``None`` to search ``[1, 64]``.  A known
            parameter is a free constraint -- it reduces the codewords needed.
        refin: Fix input reflection, or ``None`` to try both.
        refout: Fix output reflection, or ``None`` to try both.
        poly: Fix the polynomial (skip its recovery), or ``None`` to solve it.
        init: Fix the initial value, or ``None`` to solve it.  Fixing it also
            resolves the ``(init, xorout)`` labelling ambiguity.
        xorout: Fix the final XOR, or ``None`` to solve it.
        validate: When ``True`` (default), additionally recover from all-but-one
            frame and confirm the model predicts the held-out frame -- an
            empirical generalisation check reported in ``validated_frames``.

    Returns:
        A :class:`ReverseResult`.  Truthy when a model was found.  For an
        ``"equivalent"`` result, ``candidates`` holds the *complete* set of
        observationally-identical models.

    Raises:
        ValueError: ``frames`` is empty.

    Examples:
        >>> from crcglot import generic_crc, reverse
        >>> msgs = [bytes((i * 37 + j * 53 + 17) & 0xFF for j in range(8))
        ...         for i in range(8)] + [b"a longer frame", b"and another one!!"]
        >>> cws = [(m, generic_crc(m, 16, 0x1021, 0xFFFF, False, False, 0))
        ...        for m in msgs]
        >>> r = reverse(cws, std_algo_only=False)
        >>> r.info.poly
        4129
    """
    codewords = [(bytes(m), int(c)) for m, c in frames]
    if not codewords:
        raise ValueError("reverse() needs at least one (message, crc) frame")

    # ----- Tier 1: catalogue -----
    names = _catalogue_match(codewords)
    if names:
        return ReverseResult(
            status="catalogue",
            candidates=tuple(
                _as_info(a.width, a.poly, a.init, a.refin, a.refout, a.xorout,
                         a.desc, "reveng")
                for n in names for a in (ALGORITHMS[n],)),
            catalogue_name=names[0],
            note=f"matched catalogue: {', '.join(names)}",
        )
    if std_algo_only:
        return ReverseResult(
            status="none",
            note="no catalogue algorithm matches; pass std_algo_only=False to "
                 "attempt algebraic recovery of a custom algorithm",
        )

    # ----- Tier 2: algebraic recovery -----
    solved = _solve_dials(codewords, width, refin, refout, poly, init, xorout)
    if solved is None:
        return ReverseResult(
            status="underdetermined",
            note="could not pin the polynomial -- supply more frames, varied in "
                 "content (and in length to separate init/xorout).",
        )
    w, p, ri, ro, members, dim = solved

    # Canonical representative: prefer a member that names a catalogue entry.
    cat_name: str | None = None
    ordered = list(members)
    for idx, (mi, mx) in enumerate(members):
        nm = _catalogue_name(w, p, mi, ri, ro, mx)
        if nm:
            cat_name = nm
            ordered.insert(0, ordered.pop(idx))
            break

    # ----- held-out validation -----
    validated = -1
    if validate and len(codewords) >= 4:
        sub = _solve_dials(codewords[:-1], w, ri, ro, None, None, None)
        if sub is not None:
            sw, sp, _, _, sub_members, _ = sub
            hi, hx = sub_members[0]
            hm, hc = codewords[-1]
            validated = int(
                generic_crc(hm, sw, sp, hi, ri, ro, hx) == hc)
        else:
            validated = 0

    candidates = tuple(
        _as_info(w, p, mi, ri, ro, mx,
                 (f"matches {cat_name}" if i == 0 and cat_name else "recovered (custom)"),
                 "recovered")
        for i, (mi, mx) in enumerate(ordered))
    status: Status = "unique" if dim == 0 else "equivalent"
    if status == "unique":
        note = "parameters fully determined"
    else:
        note = (f"{len(members)} (init, xorout) labellings reproduce all "
                f"codewords identically -- a complete (x+1)-factor equivalence "
                f"class ({dim} bit(s)). All predict the same CRC for every "
                f"input; supply a known init/xorout (or a catalogue match) to "
                f"pick the canonical one.")
    if validated == 0:
        note += "  WARNING: model did not predict a held-out frame -- treat as " \
                "low-confidence and supply more varied frames."
    return ReverseResult(
        status=status,
        candidates=candidates,
        catalogue_name=cat_name,
        ambiguity_bits=dim,
        validated_frames=validated,
        note=note,
    )


# Trailing CRC field sizes to try when ``crc_bytes`` is unknown -- one per
# common width (8, 16, 24, 32, 48, 64 bits).
_CRC_FIELD_SIZES: tuple[int, ...] = (1, 2, 3, 4, 6, 8)

# When several cuts fit, rank by confidence: a catalogue hit beats a bare
# algebraic recovery, and a unique recovery beats an ambiguous class.
_CONFIDENT_RANK = {"catalogue": 0, "unique": 1, "equivalent": 2}


def reverse_packets(
    packets: Sequence[bytes | str],
    *,
    crc_bytes: int | None = None,
    crc_byte_order: Literal["big", "little", "both"] = "big",
    encoding: str = "utf-8",
    std_algo_only: bool = True,
    width: int | None = None,
    refin: bool | None = None,
    refout: bool | None = None,
    poly: int | None = None,
    init: int | None = None,
    xorout: int | None = None,
    validate: bool = True,
) -> ReverseResult:
    """Recover a CRC from whole packets (message followed by its CRC).

    The packet-oriented entry to :func:`reverse`, taking the same frame shapes
    :func:`detect` accepts so you can hand it raw captures.  Each packet is a
    message with its CRC as the trailing field; supply them as **binary** frames
    (``bytes``: the CRC is the trailing ``crc_bytes`` bytes) or **text** frames
    (``str`` ``"data <sep> hexcrc"``: the trailing hex field is peeled
    structurally).  Don't mix the two.  Each frame is split into a
    ``(message, crc)`` pair and handed to :func:`reverse`.

    For binary frames, when ``crc_bytes`` is ``None`` the field size is unknown,
    so each plausible size (1-8 bytes) is tried -- largest first, since CRC
    register feedback can make a smaller cut look consistent too -- and the
    first that yields a confident recovery wins.  ``crc_byte_order="both"``
    additionally tries each field byte order.  Supplying ``crc_bytes`` (you
    usually know "it's the last two bytes") is faster.  Text frames need no size
    search: the hex field is already delimited; ``crc_bytes`` is ignored.

    Args:
        packets: The captured frames -- all binary (``bytes``) or all text
            (``str``).  Supply several varied frames for the algebraic tier (see
            :func:`reverse`).
        crc_bytes: Binary frames only -- size of the trailing CRC field in
            bytes, or ``None`` to search ``1..8``.
        crc_byte_order: Byte order of the CRC field -- ``"big"`` (default),
            ``"little"``, or ``"both"`` to try each.  For text frames this reads
            the hex digits big-endian or byte-swapped.
        encoding: Text frames only -- encoding for the data portion.  Default
            ``"utf-8"``.
        std_algo_only: Forwarded to :func:`reverse` (catalogue-only when True).
        width: Forwarded to :func:`reverse`.
        refin: Forwarded to :func:`reverse`.
        refout: Forwarded to :func:`reverse`.
        poly: Forwarded to :func:`reverse`.
        init: Forwarded to :func:`reverse`.
        xorout: Forwarded to :func:`reverse`.
        validate: Forwarded to :func:`reverse`.

    Returns:
        A :class:`ReverseResult`, exactly as :func:`reverse` returns.  When the
        field size or byte order was searched, the winning split is appended to
        ``note`` so you can confirm the boundary it chose.

    Raises:
        ValueError: ``packets`` is empty, mixes binary and text frames, a binary
            packet is too short for the given ``crc_bytes``, or a text frame
            isn't ``"data <sep> hexcrc"``.

    Examples:
        >>> from crcglot import generic_crc, reverse_packets
        >>> msgs = [bytes((i * 37 + j * 53 + 17) & 0xFF for j in range(8))
        ...         for i in range(8)] + [b"a longer frame", b"and one more!!!"]
        >>> pkts = [m + generic_crc(m, 16, 0x1021, 0xFFFF, False, False, 0)
        ...                .to_bytes(2, "big") for m in msgs]
        >>> r = reverse_packets(pkts, crc_bytes=2, std_algo_only=False)
        >>> r.info.poly
        4129
    """
    items = list(packets)
    if not items:
        raise ValueError("reverse_packets() needs at least one packet")

    orders: tuple[Literal["big", "little"], ...] = (
        ("big", "little") if crc_byte_order == "both" else (crc_byte_order,))
    searching_order = crc_byte_order == "both"

    def solve(frames: Sequence[Codeword]) -> ReverseResult:
        return reverse(
            frames, std_algo_only=std_algo_only, width=width, refin=refin,
            refout=refout, poly=poly, init=init, xorout=xorout, validate=validate)

    # ----- text frames: the hex field is already delimited (no size search) -----
    if any(isinstance(p, str) for p in items):
        if not all(isinstance(p, str) for p in items):
            raise ValueError(
                "packets must be all text or all binary frames, not a mix")
        parsed: list[tuple[bytes, str]] = []
        for i, text in enumerate(cast("list[str]", items)):
            pr = _parse_text(text, encoding)
            if pr is None:
                raise ValueError(
                    f"packets[{i}] is not a text frame ('data <sep> hexcrc'): "
                    f"{text!r}")
            data, _tf, _hex_len, hex_str = pr
            parsed.append((data, hex_str))

        fallback: ReverseResult | None = None
        for order in orders:
            frames: list[Codeword] = []
            applies = True
            for data, hex_str in parsed:
                crc = _read_hex_crc(hex_str, order)
                if crc is None:  # little-endian asked of an odd-nibble field
                    applies = False
                    break
                frames.append((data, crc))
            if not applies:
                continue
            res = solve(frames)
            if res.status in _CONFIDENT_RANK:
                if searching_order:
                    res = replace(
                        res,
                        note=f"{res.note}  [CRC field: {order}-endian text hex]")
                return res
            fallback = fallback or res
        if fallback is not None:
            return fallback
        return ReverseResult(
            status="none",
            note="no consistent model for these text frames; try crc_byte_order, "
                 "or supply more varied frames.")

    # ----- binary frames: split the trailing CRC field off (size search) -----
    pkts = [bytes(p) for p in cast("list[bytes]", items)]
    sizes = (crc_bytes,) if crc_bytes is not None else _CRC_FIELD_SIZES
    searching = crc_bytes is None or searching_order

    byte_fallback: ReverseResult | None = None
    # Largest field first: CRC register feedback can make a *smaller* cut look
    # consistent too (a 16-bit reflected CRC's low byte algebraically predicts
    # its high byte), so the true field is the largest cut that still fits.
    for n in sorted(sizes, reverse=True):
        short = next((p for p in pkts if len(p) <= n), None)
        if short is not None:
            if crc_bytes is not None:
                raise ValueError(
                    f"packet of length {len(short)} is too short for a {n}-byte "
                    f"CRC plus a message")
            continue  # this size can't apply to every packet; skip in a search
        hits: list[tuple[Literal["big", "little"], ReverseResult]] = []
        for order in orders:
            byte_frames = [(p[:-n], int.from_bytes(p[-n:], order)) for p in pkts]
            res = solve(byte_frames)
            if res.status in _CONFIDENT_RANK:
                hits.append((order, res))
            else:
                byte_fallback = byte_fallback or res
        if hits:
            order, res = min(hits, key=lambda h: _CONFIDENT_RANK[h[1].status])
            if searching:
                res = replace(
                    res, note=f"{res.note}  [CRC field: {n} byte(s), "
                              f"{order}-endian]")
            return res
    if byte_fallback is not None:
        return byte_fallback
    return ReverseResult(
        status="none",
        note="no CRC field size in 1..8 bytes yielded a consistent model; "
             "specify crc_bytes / crc_byte_order, or supply more varied frames.")
