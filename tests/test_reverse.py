"""Tests for CRC parameter recovery (``crcglot.reverse``).

The headline tests are a *counterexample hunt*: recover parameters for every
catalogue algorithm and for hundreds of random custom CRCs, then assert the
recovered model **predicts the CRC of held-out messages it never trained on**.
That predictive check -- not exact-parameter-match -- is the right correctness
criterion, because a CRC whose generator carries the ``(x+1)`` factor has
several ``(init, xorout)`` labellings that are observationally identical.  The
standing guarantee these tests enforce is: **a recovered model is either
correct on unseen data, or reports no/under-determined -- never
confidently wrong.**
"""

from __future__ import annotations

import random

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from crcglot import Crc, ReverseResult, generic_crc, reverse, reverse_packets
from crcglot.catalogue import ALGORITHMS
from crcglot._reverse import _solve_dials

# A spread of held-out lengths the solver never sees during recovery.
_HELD_LENS = (3, 5, 21, 40, 77)


def _codewords(width, poly, init, refin, refout, xorout, *, seed=0):
    """Varied codewords: enough same-length frames for the poly GCD to converge
    (more differences clear spurious common factors) + mixed lengths to separate
    init from xorout."""
    rng = random.Random(seed)
    lens = [8] * 12 + [9, 11, 13, 17, 23]
    out = []
    for length in lens:
        m = bytes(rng.randrange(256) for _ in range(length))
        out.append((m, generic_crc(m, Crc(width, poly, init, refin, refout, xorout))))
    return out


_Params = tuple  # (width, poly, init, refin, refout, xorout)


def _info_params(info) -> _Params:
    return (info.width, info.poly, info.init, info.refin, info.refout, info.xorout)


def _agree(pa: _Params, pb: _Params, *, seed=99) -> bool:
    """True if two parameter sets give the same CRC on fresh held-out messages."""
    rng = random.Random(seed)
    held = [bytes(rng.randrange(256) for _ in range(L)) for L in _HELD_LENS]
    return all(generic_crc(m, Crc(*pa)) == generic_crc(m, Crc(*pb)) for m in held)


# ---------------------------------------------------------------------------
# Catalogue round-trip -- the algebraic tier against 113 known algorithms
# ---------------------------------------------------------------------------


class TestRecoversEveryCatalogueAlgorithm:
    """For every catalogue entry, the algebraic solver recovers a model that
    predicts held-out messages, and recovers the polynomial exactly."""

    @pytest.mark.parametrize("name", sorted(ALGORITHMS))
    def test_recovers_predictive_model(self, name):
        # Arrange -- codewords from this algorithm; solve with width fixed (the
        # algebra under test, not the width search).
        a = ALGORITHMS[name]
        cws = _codewords(a.width, a.poly, a.init, a.refin, a.refout, a.xorout)

        # Act -- the Tier-2 core (bypasses the catalogue short-circuit).
        solved = _solve_dials(cws, a.width, None, None, None, None, None)

        # Assert
        assert solved is not None, f"{name}: recovered no model"
        w, p, ri, ro, members, _dim = solved
        actual_poly, expected_poly = p, a.poly
        assert actual_poly == expected_poly, (
            f"{name}: poly {actual_poly:#x} != {expected_poly:#x}"
        )
        init, xorout = members[0]
        recovered = (w, p, init, ri, ro, xorout)
        truth = (a.width, a.poly, a.init, a.refin, a.refout, a.xorout)
        assert _agree(recovered, truth), (
            f"{name}: recovered model mispredicts a held-out message"
        )


# ---------------------------------------------------------------------------
# Random adversarial sweep -- the "never confidently wrong" guarantee
# ---------------------------------------------------------------------------


# The widths the property draws over: sub-byte-adjacent (8), non-byte-aligned
# (12, 15), byte-aligned (16, 24, 32).  Wider widths cost solver time without
# reaching new structure -- the ambiguity behaviour is set by the generator's
# factorization, not by width.
_PROPERTY_WIDTHS = (8, 12, 15, 16, 24, 32)


@st.composite
def _crc_parameters(draw) -> _Params:
    """Draw a Rocksoft/Williams parameter set for an off-catalogue CRC.

    ``poly`` is forced odd because a CRC generator always is (an even
    polynomial has ``x`` as a factor, which ``reverse`` strips).  Everything
    else is unconstrained over its width, so the draw reaches the boundary
    values a uniform random sweep effectively never produces: ``poly=1``
    (whose generator ``x**w + 1`` factors as ``(x+1)**w``, the maximal
    ``(init, xorout)`` ambiguity), ``init=0``, ``xorout=0``, and the
    all-ones forms.
    """
    width = draw(st.sampled_from(_PROPERTY_WIDTHS))
    hi = (1 << width) - 1
    return (
        width,
        draw(st.integers(min_value=1, max_value=hi)) | 1,
        draw(st.integers(min_value=0, max_value=hi)),
        draw(st.booleans()),
        draw(st.booleans()),
        draw(st.integers(min_value=0, max_value=hi)),
    )


class TestNoConfidentlyWrongRecovery:
    """The standing guarantee, searched rather than sampled.

    ``reverse`` may always answer "no model" or "underdetermined"; what it
    may never do is return a model that mispredicts data it did not train
    on.  Hypothesis searches the parameter space for a counterexample and
    shrinks any it finds to a minimal one, which is the property this
    replaces a fixed seeded sweep to get: a failure here names a small,
    reproducible parameter set instead of one arbitrary draw out of 240.

    The guarantee has been broken before (``4cd0842``: an ``(init, xorout)``
    class enumerated from the observed lengths only, plus a width overfit,
    together produced confidently-wrong models on ~6% of random trials), so
    the search is pointed at exactly the surface that failed.
    """

    @given(params=_crc_parameters(), seed=st.integers(min_value=0, max_value=2**30))
    # The degenerate corners, pinned so they run in every profile rather than
    # only when the search happens to reach them.
    @example(params=(16, 0x0001, 0x0000, False, False, 0x0000), seed=0)
    @example(params=(16, 0x0001, 0xFFFF, True, True, 0xFFFF), seed=0)
    @example(params=(8, 0x0001, 0x0000, False, False, 0x0000), seed=0)
    def test_recovered_model_is_correct_or_absent(self, params, seed):
        # Arrange -- codewords from a CRC that is (almost surely) in no catalogue.
        width, poly, init, refin, refout, xorout = params
        codewords = _codewords(width, poly, init, refin, refout, xorout, seed=seed)

        # Act -- algebraic tier, width supplied (the blind search is pinned
        # separately by test_full_blind_search_recovers).
        result = reverse(codewords, std_algo_only=False, width=width)

        # Assert -- no model is always an acceptable answer; a model is not
        # allowed to be wrong on data it never saw.
        if result.info is None:
            return
        actual = _info_params(result.info)
        assert _agree(actual, params), (
            f"confidently WRONG recovery: reverse returned {actual} for a CRC "
            f"with parameters {params}; the two disagree on held-out messages"
        )


class TestRandomCustomCrcs:
    """Specific recovery capabilities, pinned with fixed parameters.

    The general guarantee is searched in
    :class:`TestNoConfidentlyWrongRecovery`; what stays here is the
    documented full-blind-search capability, which needs a known-good
    parameter set rather than a drawn one.
    """

    def test_full_blind_search_recovers(self):
        # Arrange -- a custom CRC, recovered with ALL dials searched (no hints).
        width, poly, init, ri, ro, xorout = 16, 0x1009, 0x1234, False, True, 0x5678
        cws = _codewords(width, poly, init, ri, ro, xorout)

        # Act -- width/refin/refout all None.
        r = reverse(cws, std_algo_only=False)

        # Assert -- predicts held-out (the model is correct on unseen data).
        assert r.info is not None, "full-blind recovery returned no model"
        assert r.info.width == width, f"width {r.info.width} != {width}"
        assert _agree(_info_params(r.info), (width, poly, init, ri, ro, xorout)), (
            "full-blind recovered model mispredicts held-out"
        )


# ---------------------------------------------------------------------------
# The (init, xorout) equivalence class
# ---------------------------------------------------------------------------


class TestEquivalenceClass:
    """The complete set of observationally-identical parameter sets."""

    def test_x_plus_1_free_is_unique(self):
        # Arrange -- poly 0x8001's generator has no (x+1) factor -> unique.
        cws = _codewords(16, 0x8001, 0x1234, False, False, 0x5678)
        # Act
        r = reverse(cws, std_algo_only=False)
        # Assert
        assert r.status == "unique", f"expected unique, got {r.status}"
        assert r.ambiguity_bits == 0, f"ambiguity_bits {r.ambiguity_bits} != 0"
        assert len(r.candidates) == 1, f"{len(r.candidates)} candidates, expected 1"

    def test_x_plus_1_factor_yields_full_class(self):
        # Arrange -- poly 0x8005's generator has one (x+1) factor -> 2 sets.
        cws = _codewords(16, 0x8005, 0x1234, False, False, 0x5678)
        # Act
        r = reverse(cws, std_algo_only=False)
        # Assert -- exactly 2 members, all observationally identical.
        assert r.status == "equivalent", f"expected equivalent, got {r.status}"
        assert r.ambiguity_bits == 1, f"ambiguity_bits {r.ambiguity_bits} != 1"
        assert len(r.candidates) == 2, f"{len(r.candidates)} members, expected 2"
        rng = random.Random(7)
        for _ in range(200):
            m = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
            values = {
                generic_crc(
                    m, Crc(c.width, c.poly, c.init, c.refin, c.refout, c.xorout)
                )
                for c in r.candidates
            }
            assert len(values) == 1, f"class members disagree on {m!r}: {values}"

    def test_fixed_init_resolves_to_unique(self):
        # Arrange / Act -- pinning init collapses the class.
        cws = _codewords(16, 0x8005, 0x1234, False, False, 0x5678)
        r = reverse(cws, std_algo_only=False, init=0x1234)
        # Assert
        assert r.status == "unique", f"expected unique, got {r.status}"
        assert r.info is not None, "unique result must carry a model"
        assert r.info.init == 0x1234, f"init {r.info.init:#x} != 0x1234"
        assert r.info.xorout == 0x5678, f"xorout {r.info.xorout:#x} != 0x5678"


# ---------------------------------------------------------------------------
# Held-out validation + catalogue tier + edges
# ---------------------------------------------------------------------------


class TestBehaviour:
    def test_held_out_validation_runs(self):
        # Act
        r = reverse(_codewords(16, 0x8001, 0, False, False, 0), std_algo_only=False)
        # Assert -- cross-validation ran and every held-out fold was predicted
        # (validated_frames counts the leave-one-out folds that passed).
        assert r.validated_frames >= 1, (
            f"expected >=1 validated held-out fold, got {r.validated_frames}"
        )
        assert r.status == "unique", (
            f"a well-determined recovery should stay confident, got {r.status}"
        )

    def test_tier1_matches_catalogue(self):
        # Arrange -- codewords from a known algorithm.
        a = ALGORITHMS["crc16-modbus"]
        cws = _codewords(a.width, a.poly, a.init, a.refin, a.refout, a.xorout)
        # Act -- default (std_algo_only=True).
        r = reverse(cws)
        # Assert
        assert r.status == "catalogue", f"expected catalogue, got {r.status}"
        assert r.catalogue_name == "crc16-modbus", f"named {r.catalogue_name!r}"

    def test_custom_under_std_algo_only_returns_none(self):
        # A custom CRC with std_algo_only=True (default) -> no catalogue match.
        cws = _codewords(16, 0x1009, 0x1234, False, False, 0x5678)
        r = reverse(cws)
        assert r.status == "none", f"expected none, got {r.status}"
        assert not r, "custom CRC under std_algo_only should be falsy"

    def test_empty_frames_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            reverse([])

    def test_too_few_frames_is_honest_not_wrong(self):
        # A custom (non-catalogue) poly with just 2 same-length frames gives a
        # single difference -> can't pin the polynomial -> underdetermined, NOT
        # a wrong answer.  (Uses a custom poly so the catalogue tier can't match.)
        two = _codewords(16, 0x1009, 0x1234, False, False, 0x5678)[:2]
        r = reverse(two, std_algo_only=False)
        assert r.status in ("underdetermined", "none"), (
            f"too-few frames gave {r.status} (should be honest, not a guess)"
        )

    def test_all_distinct_lengths_guidance_names_same_length_fix(self):
        # Every frame a distinct length -> the poly GCD (which works on
        # same-length differences) has no pair to chew on -> underdetermined.
        # The note must steer to the real fix (>=2 same-length frames), NOT the
        # misleading "vary the length" that this exact situation invites.
        rng = random.Random(0)
        cws = []
        for length in (5, 6, 7, 8, 9, 10):  # all distinct; custom poly
            m = bytes(rng.randrange(256) for _ in range(length))
            cws.append(
                (m, generic_crc(m, Crc(16, 0x1009, 0x1234, False, False, 0x5678)))
            )

        # Act
        r = reverse(cws, std_algo_only=False)

        # Assert -- status + actionable, correct guidance.
        assert r.status == "underdetermined", (
            f"all-distinct-length frames should be underdetermined, got {r.status}"
        )
        note = r.note.lower()
        assert "same length" in note, (
            f"note must point at the same-length fix, got: {r.note!r}"
        )
        assert "distinct lengths" in note, (
            f"note should name the all-distinct-length situation, got: {r.note!r}"
        )

    def test_result_is_reverseresult(self):
        r = reverse(_codewords(16, 0x8001, 0, False, False, 0), std_algo_only=False)
        assert isinstance(r, ReverseResult), "reverse() must return a ReverseResult"
        assert bool(r) is True, "a recovered model should be truthy"


# ---------------------------------------------------------------------------
# reverse_packets -- the packet-oriented entry (CRC at the tail of each frame)
# ---------------------------------------------------------------------------


def _byte_packets(width, poly, init, refin, refout, xorout, *, crc_bytes, order="big"):
    """Codewords reshaped into binary frames: message + CRC at the tail."""
    return [
        m
        + generic_crc(m, Crc(width, poly, init, refin, refout, xorout)).to_bytes(
            crc_bytes, order
        )
        for m, _c in _codewords(width, poly, init, refin, refout, xorout)
    ]


def _text_packets(width, poly, init, refin, refout, xorout, *, nibbles=4, sep=" "):
    """Codewords reshaped into 'data <sep> hexcrc' text frames."""
    out = []
    for i, (m, _c) in enumerate(_codewords(width, poly, init, refin, refout, xorout)):
        # Use printable, varied data so the text regex has a clean data/sep/hex split.
        data = f"frame{i:02d}-{m.hex()}"
        crc = generic_crc(data.encode(), Crc(width, poly, init, refin, refout, xorout))
        out.append(f"{data}{sep}{crc:0{nibbles}x}")
    return out


class TestReversePackets:
    """`reverse_packets` splits the CRC off the tail (binary) or the trailing
    hex field (text), then recovers -- the detect-shaped entry to `reverse`."""

    def test_binary_with_known_field_size(self):
        # Arrange -- custom poly, 2-byte big-endian CRC field.
        pkts = _byte_packets(16, 0x1009, 0xFFFF, True, True, 0, crc_bytes=2)
        # Act
        r = reverse_packets(pkts, crc_bytes=2, std_algo_only=False)
        # Assert
        assert r.info is not None and r.info.poly == 0x1009, "poly recovered"

    def test_binary_autodetects_field_size(self):
        # crc_bytes omitted -> the largest consistent cut (2) is chosen.
        pkts = _byte_packets(16, 0x1009, 0xFFFF, True, True, 0, crc_bytes=2)
        r = reverse_packets(pkts, std_algo_only=False)
        assert r.info is not None and r.info.poly == 0x1009, "poly recovered"
        assert "2 byte" in r.note, f"chosen field size not reported: {r.note!r}"

    def test_binary_little_endian_field(self):
        pkts = _byte_packets(
            16, 0x8005, 0xFFFF, True, True, 0, crc_bytes=2, order="little"
        )
        # 'both' must discover the little-endian split and name the catalogue entry.
        r = reverse_packets(pkts, crc_byte_order="both", std_algo_only=False)
        assert r.status == "catalogue", f"status {r.status}"
        assert r.catalogue_name == "crc16-modbus", r.catalogue_name
        assert "little-endian" in r.note, f"byte order not reported: {r.note!r}"

    def test_feedback_false_positive_avoided(self):
        # A reflected 16-bit CRC's low byte algebraically predicts its high byte,
        # so crc_bytes=1 is *also* consistent -- the largest-cut rule must still
        # pick the true 2-byte field, not the 1-byte feedback artifact.
        pkts = _byte_packets(16, 0x8005, 0xFFFF, True, True, 0, crc_bytes=2)
        r = reverse_packets(pkts, std_algo_only=False)
        assert "2 byte" in r.note, f"should pick the 2-byte field, got {r.note!r}"

    def test_text_frames(self):
        # Custom poly recovered from 'data hexcrc' text lines (no size hint).
        pkts = _text_packets(16, 0x1009, 0xFFFF, True, True, 0)
        r = reverse_packets(pkts, std_algo_only=False)
        assert r.info is not None and r.info.poly == 0x1009, "poly recovered from text"
        assert "text hex" not in r.note, "single-order search shouldn't tag the note"

    def test_text_catalogue_passthrough(self):
        a = ALGORITHMS["crc16-xmodem"]
        pkts = _text_packets(a.width, a.poly, a.init, a.refin, a.refout, a.xorout)
        r = reverse_packets(pkts)  # std_algo_only=True default
        assert r.status == "catalogue", f"status {r.status}"
        assert r.catalogue_name == "crc16-xmodem", r.catalogue_name

    def test_mixed_text_and_binary_rejected(self):
        with pytest.raises(ValueError, match="all text or all binary"):
            reverse_packets([b"\x00\x01\x02", "frame 1234"])

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="at least one"):
            reverse_packets([])

    def test_short_binary_packet_rejected(self):
        with pytest.raises(ValueError, match="too short"):
            reverse_packets([b"\x01", b"\x02"], crc_bytes=2)

    def test_str_frame_that_is_neither_text_nor_hex_is_rejected(self):
        """A str frame has two readings; the error has to name both.

        Renamed from ``test_non_text_frame_rejected`` when the hex-byte-string
        reading was added: the input set widened, so a message naming only the
        text shape would send a caller looking for the wrong fix.
        """
        with pytest.raises(ValueError, match="neither a text frame.*nor a hex"):
            reverse_packets(["no-trailing-hex-here!"], std_algo_only=False)

    @staticmethod
    def _pairs(width, poly, init, refin, refout, xorout):
        """`(message, crc-value)` pairs for a CRC -- the out-of-band shape."""
        algo = Crc(width, poly, init, refin, refout, xorout)
        msgs = [
            bytes((i * 37 + j * 53 + 17) & 0xFF for j in range(8))
            for i in range(8)
        ] + [b"a longer frame", b"and one more!!!"]
        return [(m, generic_crc(m, algo)) for m in msgs]

    def test_pairs_recover_custom_poly(self):
        # Arrange -- message bytes + the CRC as an integer value (no field to peel).
        pairs = self._pairs(16, 0x1009, 0xFFFF, True, True, 0)
        # Act
        r = reverse_packets(pairs, std_algo_only=False)
        # Assert
        assert r.info is not None and r.info.poly == 0x1009, "poly recovered from pairs"

    def test_pairs_match_the_binary_frame_form(self):
        # Arrange -- the identical data as value-pairs and as binary frames.
        pairs = self._pairs(16, 0x1009, 0xFFFF, True, True, 0)
        frames = [m + c.to_bytes(2, "big") for m, c in pairs]
        # Act
        by_pair = reverse_packets(pairs, std_algo_only=False)
        by_frame = reverse_packets(frames, crc_bytes=2, std_algo_only=False)
        # Assert -- the value form is the frame form minus the peel step.
        assert by_pair.info is not None and by_frame.info is not None, "both recover"
        actual, expected = by_pair.info.poly, by_frame.info.poly
        assert actual == expected, f"pairs {actual:#x} != frames {expected:#x}"

    def test_pairs_catalogue_passthrough(self):
        # Arrange
        a = ALGORITHMS["crc16-xmodem"]
        pairs = self._pairs(a.width, a.poly, a.init, a.refin, a.refout, a.xorout)
        # Act -- std_algo_only=True default
        r = reverse_packets(pairs)
        # Assert
        assert r.status == "catalogue", f"status {r.status}"
        assert r.catalogue_name == "crc16-xmodem", r.catalogue_name

    def test_mixed_pairs_and_frames_rejected(self):
        # Act / Assert
        with pytest.raises(ValueError, match="pairs or all frames"):
            reverse_packets([(b"\x00\x01", 0x1234), b"\x00\x01\x02\x03"])

    @pytest.mark.parametrize(
        "bad",
        [
            (b"msg", "not-an-int"),
            ("msg", 0x1234),
            (b"msg", True),
            (b"msg", 1, 2),
        ],
        ids=["crc-not-int", "message-not-bytes", "crc-is-bool", "wrong-arity"],
    )
    def test_bad_pair_shape_rejected(self, bad):
        # Act / Assert -- each malformed pair names the required shape.
        with pytest.raises(ValueError, match="message: bytes, crc: int"):
            reverse_packets([bad])


class TestReverseFrameShapeValidation:
    """``reverse()`` rejects malformed frames with a pointer to reverse_packets.

    Regression for Finding 6 of the Fable verification report
    (docs/verification/2026-07-02-fable.md): raw packet bytes (the CRC still
    attached) passed to ``reverse()`` leaked a bare "too many values to
    unpack (expected 2)" instead of naming the expected shape.  The element
    rules and message now mirror ``reverse_packets``' pairs validation.
    """

    def test_raw_frame_bytes_point_at_reverse_packets(self):
        # Act / Assert -- the most likely misuse names the right function.
        with pytest.raises(ValueError, match="reverse_packets"):
            reverse([b"raw-packet-with-crc-attached"])  # type: ignore[list-item]  # ty: ignore[invalid-argument-type]

    def test_bool_crc_rejected(self):
        # Act / Assert -- same rule as reverse_packets: a bool is not a CRC.
        with pytest.raises(
            ValueError,
            match=r"frames\[0\] must be a \(message: bytes, crc: int\) pair",
        ):
            reverse([(b"x", True)])

    def test_str_crc_echoes_the_offending_element(self):
        # Act / Assert
        with pytest.raises(ValueError, match=r"got \(b'x', '5'\)"):
            reverse([(b"x", "5")])  # type: ignore[list-item]  # ty: ignore[invalid-argument-type]

    def test_single_frame_note_reads_singular(self):
        """The underdetermined note used to say "all 1 frames have distinct
        lengths" for one frame (Fable report, Finding 7)."""
        # Act -- one junk frame cannot pin anything.
        r = reverse([(b"x" * 20, 0xDEADBEEF)], std_algo_only=False)

        # Assert
        assert r.status == "underdetermined", (
            f"one junk frame must stay underdetermined, got {r.status}"
        )
        assert "single frame" in r.note, (
            f"singular wording missing from the note: {r.note!r}"
        )


# ---------------------------------------------------------------------------
# Guarantee: never confidently wrong -- correct on unseen data, or
# underdetermined.  Regression for the over-reported (init, xorout) class and
# the width overfit, both of which used to return confident-but-wrong models.
# ---------------------------------------------------------------------------


class TestNeverConfidentlyWrong:
    """The recovery returns a confident model only when the frames pin it.

    These guard the two ways thin data used to slip through: an (init, xorout)
    class enumerated from the seen lengths only (members diverged off them),
    and a width overfit where the polynomial GCD was a multiple of the true
    generator.  Verified at scale by the oracle/brute-force harness; these pin
    the specific behaviours.
    """

    def test_all_same_length_is_underdetermined(self):
        # One length: init and xorout are provably inseparable, so a confident
        # answer would have to be (partly) guessed.
        crc = Crc(16, 0x1021, 0x1234, False, False, 0x5678)
        cw = [
            (m, generic_crc(m, crc))
            for m in (bytes((i, i * 7 & 0xFF, i * 13 & 0xFF, 0x5A, 0xA5, i ^ 0x33,
                             0xC3, i)) for i in range(10))
        ]
        r = reverse(cw, std_algo_only=False)
        assert r.status == "underdetermined", (
            f"all-same-length frames cannot pin init/xorout, got {r.status}"
        )

    def test_ambiguity_bits_match_structural_formula(self):
        # The genuine (init, xorout) ambiguity is deg(gcd(generator, x**8 + 1)),
        # computed independently here -- the solver must agree exactly.  Solve
        # via _solve_dials to exercise the algebraic tier regardless of whether
        # a (poly, init) pair happens to be a catalogue entry.
        from crcglot._reverse import _deg, _polygcd
        for poly in (0x1021, 0x8005, 0xA097, 0x8BB7, 0x3D65):
            cws = _codewords(16, poly, 0xFFFF, False, False, 0)
            solved = _solve_dials(cws, 16, None, None, None, None, None)
            assert solved is not None, f"poly {poly:#06x}: recovered no model"
            dim = solved[5]
            expected = _deg(_polygcd((1 << 16) | poly, (1 << 8) | 1))
            assert dim == expected, (
                f"poly {poly:#06x}: ambiguity {dim} != structural {expected}"
            )

    def test_six_frames_underdetermined_ten_frames_recovers(self):
        # The docs capture: six frames admit more than one width (a smaller-width
        # model also fits), so they must NOT yield a confident answer; the full
        # ten pin CRC-16 poly 0xA097.
        six = [bytes.fromhex(h) for h in (
            "5057523a31322e3430569771", "544d503a34382e31433d4d",
            "52504d3a303031343530da2e", "5354413a4f4bea3b",
            "5057523a31322e333856b10d", "544d503a34382e3343bde8",
        )]
        extra = [bytes.fromhex(h) for h in (
            "52504d3a303031343438eebc", "5354413a52554e0492",
            "5057523a31322e3431565723", "4552523a4e4f4e458030",
        )]
        r6 = reverse_packets(six, crc_bytes=2, crc_byte_order="both",
                             std_algo_only=False)
        assert r6.status == "underdetermined", (
            f"six frames do not pin the width, got {r6.status}"
        )
        r10 = reverse_packets(six + extra, crc_bytes=2, crc_byte_order="both",
                              std_algo_only=False)
        assert r10.status in ("unique", "equivalent"), (
            f"ten frames should recover, got {r10.status}"
        )
        polys = {c.poly for c in r10.candidates}
        assert 0xA097 in polys, f"expected poly 0xA097 among {polys}"


_MODBUS_PAYLOADS = [
    b"\x01\x03\x00\x00\x00\x01",
    b"\x01\x03\x02\x00\x2a",
    b"\x02\x06\x00\x10\x12\x34",
    b"\x11\x03\x00\x6b\x00\x03",
    b"\x0a\x01\x00\x13\x00\x25",
    b"\x04\x04\x00\x08\x00\x01",
]


def _modbus_frames(order="little", trail=b"", payloads=None):
    """Whole frames: payload, then the CRC-16/Modbus, then optional delimiter."""
    algo = ALGORITHMS["crc16-modbus"]
    return [
        p + generic_crc(p, algo).to_bytes(2, order) + trail
        for p in (payloads if payloads is not None else _MODBUS_PAYLOADS)
    ]


class TestReversePacketsAcceptsWhatDetectAccepts:
    """A caller reaches ``reverse_packets`` when ``detect`` found nothing, so
    an input shape ``detect`` takes has to be one ``reverse_packets`` takes.

    Whole-frame hex used to raise ``ValueError`` here while detecting fine,
    which put an exception on the exact step the workflow moves to next.
    """

    @pytest.mark.parametrize("order", ["little", "big"], ids=["le", "be"])
    def test_a_whole_frame_hex_string_is_accepted(self, order):
        # Arrange -- the same frames detect() takes, hex-encoded.
        frames = [f.hex() for f in _modbus_frames(order)]

        # Act
        result = reverse_packets(frames)

        # Assert
        actual = result.catalogue_name
        assert actual == "crc16-modbus", f"recovered {actual!r} from hex frames"

    def test_text_frames_still_take_the_text_reading(self):
        """Text is tried first, so nothing that resolved before resolves differently."""
        # Arrange -- "data <sep> hexcrc", the shape that already worked.
        algo = ALGORITHMS["crc16-xmodem"]
        msgs = [b"HELLO", b"WORLD", b"THIRD", b"FOURTH", b"FIFTH!"]
        frames = [f"{m.decode()} {generic_crc(m, algo):04X}" for m in msgs]

        # Act
        result = reverse_packets(frames)

        # Assert
        actual = result.catalogue_name
        assert actual == "crc16-xmodem", f"text frames recovered {actual!r}"


class TestReversePacketsTrailingDelimiter:
    """Delimiter bytes after the CRC move the field reverse is looking for.

    The guidance the failure used to emit ("supply more varied frames", "specify
    crc_bytes") pointed away from the cause, which is worse for an agent than
    for a person: an agent acts on the note.
    """

    @pytest.mark.parametrize("order", ["little", "big"], ids=["le", "be"])
    def test_frames_with_a_delimiter_still_recover(self, order):
        # Act
        result = reverse_packets(_modbus_frames(order, b"\r\n"))

        # Assert
        actual = result.catalogue_name
        assert actual == "crc16-modbus", f"recovered {actual!r} despite a CRLF"

    def test_the_delimiter_is_named_in_the_note(self):
        # Act
        result = reverse_packets(_modbus_frames(trail=b"\r\n"))

        # Assert -- a fact about the frame layout the caller has to build to.
        assert "terminator" in result.note, f"note must name it: {result.note!r}"
        assert "0D 0A" in result.note, f"note must show the bytes: {result.note!r}"

    def test_hex_frames_with_a_delimiter_recover(self):
        # Arrange -- captured as hex, delimiter bytes included.
        frames = [f.hex() for f in _modbus_frames(trail=b"\r\n")]

        # Act
        result = reverse_packets(frames)

        # Assert
        actual = result.catalogue_name
        assert actual == "crc16-modbus", f"recovered {actual!r} from dirty hex"

    def test_clean_frames_gain_no_terminator_note(self):
        # Assert -- the as-given reading wins and says nothing about delimiters.
        result = reverse_packets(_modbus_frames())
        assert "terminator" not in result.note, (
            f"clean frames must not mention a terminator: {result.note!r}"
        )

    def test_a_delimiter_on_only_some_frames_is_not_recovered(self):
        # Arrange -- frames that disagree describe no single layout.
        frames = [
            f + (b"\r\n" if i % 2 else b"\x00")
            for i, f in enumerate(_modbus_frames())
        ]

        # Act
        result = reverse_packets(frames)

        # Assert
        assert result.catalogue_name != "crc16-modbus", (
            "frames disagreeing on their delimiter must not resolve"
        )

    def test_below_the_frame_floor_no_delimiter_is_guessed(self):
        # Arrange -- two frames is under MIN_TRAIL_FRAMES.
        frames = _modbus_frames(trail=b"\r\n", payloads=_MODBUS_PAYLOADS[:2])

        # Act
        result = reverse_packets(frames)

        # Assert
        assert result.catalogue_name != "crc16-modbus", (
            "must not search for a delimiter below the frame floor"
        )

    def test_too_few_frames_says_what_more_frames_would_buy(self):
        """Below the floor the caller gets an instruction, not a shrug.

        A caller driving a live link can execute "capture one more frame"; it
        cannot act on "no consistent model".  The count has to be concrete and
        it has to say the payloads must differ, since the same reply captured
        twice is one observation, not two.
        """
        # Arrange
        frames = _modbus_frames(trail=b"\r\n", payloads=_MODBUS_PAYLOADS[:2])

        # Act
        note = reverse_packets(frames).note

        # Assert -- names the delimiter, the shortfall, and the payload caveat.
        assert "CRLF" in note, f"note must name the delimiter seen: {note!r}"
        assert "capture 1 more" in note, f"note must give the shortfall: {note!r}"
        assert "DIFFERENT payloads" in note, (
            f"note must say repeat frames do not count: {note!r}"
        )

    def test_frames_sharing_no_delimiter_get_no_such_advice(self):
        # Arrange -- nothing in common at the tail, so there is nothing to test.
        frames = [b"\x01\x02\x03\x04\x05", b"\x06\x07\x08\x09\x0a"]

        # Act
        note = reverse_packets(frames).note

        # Assert -- advice only when it names something concrete.
        assert "terminator" not in note, (
            f"must not ask for frames to test a delimiter that is not there: {note!r}"
        )

    def test_random_frames_carrying_no_crc_stay_unrecovered(self):
        """The false-positive guard.

        Every extra hypothesis costs accuracy on the catalogue's narrow
        entries, so the search must not start finding CRCs in frames that have
        none.  These end in a common CRLF, so the terminator retry does run.
        """
        # Arrange -- deterministic noise, CRLF-terminated, no CRC anywhere.
        rng = random.Random(20260814)
        frames = [
            bytes(rng.randrange(256) for _ in range(12)) + b"\r\n"
            for _ in range(6)
        ]

        # Act
        result = reverse_packets(frames)

        # Assert
        assert result.status == "none", (
            f"noise must not resolve, got {result.status}/{result.catalogue_name}"
        )
