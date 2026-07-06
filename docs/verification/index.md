# Verification reports

Every adversarial verification pass of crcglot is recorded here, one file per pass, so the series has a permanent home instead of accumulating one-off filenames.

## The model: review by evidence, not by reading

crcglot is a working example of shifting the basis of software correctness from human inspection of code to evidence of behavioral conformance.  The traditional chain (spec, implementation, a few unit tests, a reviewer's sign-off) locates trust in the review: someone read the code and vouched for it.  crcglot locates trust in a body of evidence built from sources that fail independently:

- **An exhaustively enumerated conformance matrix.**  Every catalogue algorithm, in every variant, in every target language, executed rather than sampled.  CRC correctness is a finite, enumerable space, and an enumerable space gets enumerated.
- **Vectors that cover regions, not points.**  The reference inputs (empty message, canonical check string, all 256 byte values, bulk pseudo-random data) stake out structural regions of the input space: the null path, the published anchor, the full byte table, the long-run mixing behavior.  Each vector class covers a distinct region of behavioral certainty, not another point on the happy path.
- **Expected values the code under test never computed.**  Two independent engines that had to agree, anchored to reveng's published constants.  A tool grading itself establishes nothing.
- **Cross-language execution as differential validation.**  Nine toolchains must interpret the same parameter set identically.  Agreement constrains the interpretation of the spec itself, not just any one implementation.
- **Adversarial passes (this series).**  Each pass rebuilds its oracle from scratch and tries to break the results: boundary conditions, parameterization ambiguity, the known failure modes of CRC bit handling.

Under this model the test suite is not a QA gate bolted onto development; it is the correctness argument.  The code is still reviewed, but the review is performed by evidence rather than by reading: a review by reading vouches for what the reviewer saw, while this one vouches for what the code does, on toolchains and inputs no reader holds in their head.  Trust comes from the density, diversity, and independence of the evidence, which is also why the suite is yours to re-run.

### The verification matrix

The evidence sources above organize into ten categories: the first nine are checked by the test suite on every run, and the tenth is the review series this directory records.  The README carries the one-line version of this table; this is the full mapping from category to the tests (or reports) that carry it.

| # | Category | What it checks | Where it lives |
| - | -------- | -------------- | -------------- |
| 1 | Reference vectors | reveng's published check value plus four goldens computed by two engines that had to agree, in the engine and in every generated file's embedded self-test | `tests/test_external_vectors.py`, `tests/test_independent_vectors.py`, `tests/test_verification_vectors.py` (engine, fast); the `_self_test()` embedded in every generated file (generated, slow) |
| 2 | Extended vectors | all 256 byte values, a 1 KiB pseudo-random pattern, boundary lengths 0/1/7/8/9/15/16/100 | the `all_bytes` / `binary_1k` goldens across the catalogue (fast + embedded); slice-by-8 vs bit-by-bit equivalence at boundary lengths in each `tests/test_<lang>_gen.py` (slow) |
| 3 | Random vectors | seeded random inputs, computed live by anycrc and crccheck, which must agree before the engine is graded | `tests/test_differential_random.py` (engine, fast); the committed goldens' two-oracle provenance re-run in `tests/test_vectors_provenance.py` (slow) |
| 4 | Cross-language equivalence | the whole catalogue × every supported variant, generated and executed in nine languages, all pinned to the same references | the batch execution tests in each `tests/test_<lang>_gen.py` (slow) |
| 5 | Streaming | init/update/finalize fed in chunks equals the one-shot result | `tests/test_stream.py`, `tests/test_independent_vectors.py` (engine, fast); the split-streaming phase in every software batch driver (generated, slow) |
| 6 | Segmentation | every split position of the check string and of a 33-byte message digests identically, plus curated boundary-hazard framings | `tests/test_stream.py::TestSegmentation` on both engine backends (fast); `tests/test_independent_vectors.py::TestChunkingInvariance` (fast) |
| 7 | Byte-at-a-time | the fully segmented feed, one byte per update, equals the one-shot CRC | `tests/test_stream.py` (engine, fast); `tests/test_python_gen.py` (generated Python, fast); the `bytewise` phase in every software batch driver (slow); Verilog/VHDL update one byte per invocation by construction |
| 8 | Toolchain execution | generated source is compiled and run through gcc, rustc, go, dotnet, javac + java, tsx, iverilog, and ghdl; acceptance is the execution result | batch (default) and `--exhaustive` classes in each `tests/test_<lang>_gen.py` (slow) |
| 9 | Parameter edge cases | sub-byte and non-byte-aligned widths, init/xorout edge values, asymmetric reflection in both orders, reverse-engineering ambiguity | catalogue-wide parametrization carries the odd widths everywhere (crc12-umts is the catalogue's one refin != refout entry); `tests/test_differential_random.py::TestAsymmetricReflectionDifferential` (fast); `TestAsymmetricCustomExecution` in each software `tests/test_<lang>_gen.py` (slow); `tests/test_reverse.py` (fast) |
| 10 | Adversarial review | independent agents run separate verification passes: each rebuilds its own oracle from scratch, validates it against references outside the package, and tries to break the engine, the generators, and the reverse-engineering | the report series in this directory, one dated file per pass; harnesses are deliberately not archived (see the conventions below), so each pass re-earns its independence |

A row-10 finding is traceable end to end in the git history, not just in the report.  Each one leaves three permanent artifacts: the as-reviewed report entry, a fix commit that names it, and a regression test that cites it.  Examples: `aea296d` ("range-check CRC width at Crc construction (Finding 3)") closed the width guard from the first series' 0.25.0 re-run; the seven findings of the Fable pass drove `ee73c59` (boundary validation), `b2ccee2` (`--custom` width range), `0d61a3b` (custom-polynomial comment scoping), and `b9d9809` (stale citations and README scoping), closed by the resolution note in `de0fe1c`; and `tests/test_stream.py` carries regression tests citing Fable Findings 1 and 4 in their docstrings.

Scoping notes, so the claim stays exactly as strong as it is.  This works as well as it does because a CRC's behavior space is closed and enumerable; the same approach on a sprawling behavior space buys evidence density, not exhaustiveness.  No finite test set can prove an implementation over an infinite input space, which is why these pages say "verified" and "checked", never "proven": the claim is convergent evidence, deliberately not proof.  And the harness was designed by the same development process it grades; the independent passes in this series exist to attack precisely that residual assumption.

## The series

| Report | Reviewer | Target | Outcome |
| ------ | -------- | ------ | ------- |
| [2026-06-18-independent.md](2026-06-18-independent.md) | Claude (external check) | 0.23.0, re-run against 0.25.0 | 3 findings (C# compile, HDL self-test scope, width guard); all resolved by 0.25.1 |
| [2026-07-02-fable.md](2026-07-02-fable.md) | Claude Fable 5 | 0.25.1 | 7 boundary / accuracy findings, none touching computed values; all resolved same day |

## Conventions

- **One file per pass**, named `YYYY-MM-DD-<reviewer>.md` (date the pass ran, then a short reviewer slug). Date-first keeps the directory listing chronological.
- **The report is written as-reviewed and never edited after the fact.** When findings close, a Resolution section is appended below the original text, so the as-reviewed record stands next to how each finding ended. A re-run against a later version may also be appended to the same file (the first report carries its 0.25.0 re-verification this way).
- **Update the table above in the same commit** that adds a report or appends a resolution.
- **Harnesses are not archived.** Each pass builds its oracle and harnesses from scratch and validates the oracle against references outside the package (zlib, published check constants) before grading anything; that independence is the method, so re-using a prior pass's harness would weaken the next pass. The report documents the method well enough to rebuild it.

## What a pass covers

The method contract, common to every report so far: an independently-built oracle that must earn its role before grading; generated code checked by execution through real toolchains, never by inspection; no reliance on the bundled test suite; and every correctness claim graded against references that do not come from crcglot.
