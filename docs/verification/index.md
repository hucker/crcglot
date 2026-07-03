# Verification reports

Every adversarial verification pass of crcglot is recorded here, one file per pass, so the series has a permanent home instead of accumulating one-off filenames.

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
