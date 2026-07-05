# Verification reports

Every adversarial verification pass of crcglot is recorded here, one file per pass, so the series has a permanent home instead of accumulating one-off filenames.

## The model: evidence instead of review

crcglot is a working example of shifting the basis of software correctness from human inspection of code to evidence of behavioral conformance.  The traditional chain (spec, implementation, a few unit tests, a reviewer's sign-off) locates trust in the review: someone read the code and vouched for it.  crcglot locates trust in a body of evidence built from sources that fail independently:

- **An exhaustively enumerated conformance matrix.**  Every catalogue algorithm, in every variant, in every target language, executed rather than sampled.  CRC correctness is a finite, enumerable space, and an enumerable space gets enumerated.
- **Vectors that cover regions, not points.**  The reference inputs (empty message, canonical check string, all 256 byte values, bulk pseudo-random data) stake out structural regions of the input space: the null path, the published anchor, the full byte table, the long-run mixing behavior.  Each vector class covers a distinct region of behavioral certainty, not another point on the happy path.
- **Expected values the code under test never computed.**  Two independent engines that had to agree, anchored to reveng's published constants.  A tool grading itself establishes nothing.
- **Cross-language execution as differential validation.**  Nine toolchains must interpret the same parameter set identically.  Agreement constrains the interpretation of the spec itself, not just any one implementation.
- **Adversarial passes (this series).**  Each pass rebuilds its oracle from scratch and tries to break the results: boundary conditions, parameterization ambiguity, the known failure modes of CRC bit handling.

Under this model the test suite is not a QA gate bolted onto development; it is the correctness argument.  Human review still happens, but the claim does not rest on it: a reviewer can vouch for what they read, while the matrix vouches for what the code does, on toolchains and inputs no reviewer holds in their head.  Trust comes from the density, diversity, and independence of the evidence, which is also why the suite is yours to re-run.

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
