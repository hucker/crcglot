# crcglot — Claude Code instructions

## Commenting

- Google Style Docstrings for all public functions
- Docstring sections in this order: Args, Returns, Raises, Examples
- Use `"""` triple-quote style for docstrings, even for one-liners.  This
  is the standard convention in the Python ecosystem and supported by
  all major docstring parsers.  It also allows for easy expansion of
  one-liners into multi-line docstrings without needing to change the
  quoting style.
- Do not be super verbose.  Explain the "why" and any non-obvious "what". The docstring
  should add value.
- For public functions, include an "Examples" section with a minimal usage example for
  non trivial functions
- For private functions, docstrings are optional.  If the function is non-trivial,
  a docstring is recommended; if it's trivial, a well-chosen name may suffice.

## Branch hygiene

Never work directly on `main`.  Branch first, before any edit, using
the form `<kind>/<dashed-slug>`:

| Kind    | Use for                                                |
| ------- | ------------------------------------------------------ |
| `feat`  | New features (e.g. new generator option, new function) |
| `bug`   | Bug fixes                                              |
| `doc`   | README / EXAMPLES / docstring-only changes             |
| `test`  | Test-only changes                                      |
| `chore` | Tooling, dependencies, CI                              |
| `rel`   | Release-related changes                                |
| `ref`   | Refactorings (no behavior change)                      |

Slugs use dashes, not underscores.  Examples:
`feat/python-self-test`, `bug/vhdl-refout`, `doc/readme-cli-section`.

## Quality gates (must all pass before declaring work done)

Run these three checks at the end of every coding task.  Any non-zero
diagnostic blocks "done" — fix the underlying issue rather than
silencing it, unless the suppression is justified and documented inline.

| Check             | Command                    | Pass criterion       |
| ----------------- | -------------------------- | -------------------- |
| Lint              | `uvx ruff check src tests` | `All checks passed!` |
| Type check        | `uvx ty check src tests`   | `All checks passed!` |
| IDE Problems pane | (visual)                   | Zero entries         |

Type-checker suppressions: for tests that deliberately pass an invalid
kwarg to assert `TypeError`, use both pragmas on the same line so the
project stays clean across all checkers:
`# type: ignore[call-arg]  # ty: ignore[unknown-argument]`

## Testing

- `uv run pytest` — full suite (~3 min); use this before commit/merge
- `uv run pytest -m "not slow"` — fast suite (~3 s) for tight iteration.  Skips ~750 subprocess-spawning tests (gcc / rustc / ghdl invocations that compile and run generated code).  Use during dev; ALWAYS run the full suite before pushing.
- `uv run pytest -m slow` — only the slow tests (useful when debugging a specific subprocess test)
- Full suite needs `gcc`, `rustc`, and `ghdl` on PATH.  Rust install is rustup-managed under `%USERPROFILE%\.cargo\bin`; VSCode-spawned shells inherit PATH at editor startup, so a fresh `rustup` install needs a VSCode restart before pytest sees it.
- Tests organized by target language: `test_python_gen.py`, `test_c_gen.py`, `test_rust_gen.py`, `test_vhdl_gen.py`, plus `test_catalogue.py` (cross-cutting) and `test_cli.py`.
- Run tests before commit; full suite before merging to main
- AAA comments (`# Arrange`, `# Act`, `# Assert`) for non-trivial tests
- Assert comments required
- Assert order: `actual == expected`
- Assert messages required — every `assert` must include a message string describing what failed
- Non-trivial: use `actual` / `expected` variables
- Multiple checks: `actual_x == expected_x` pattern

## Test documentation: two questions every test serves

The conventions exist to make two questions cheap to answer: **"how was this feature tested?"** (find the tests from a feature) and **"what is this test testing?"** (understand a test you have landed in).

Finding (feature -> tests):

- Tests live in the file named for their surface (`test_cli.py`, `test_trailers.py`, `test_<lang>_gen.py`).  A feature's tests are in the file named after where the feature lives, not scattered.
- Test and class names use the feature's **public vocabulary**: the same words the API and docs use (`identify_trailer`, `goldens`, `self_test`).  When a feature is renamed, rename its tests and their vocabulary in the same commit; the contract is that grepping tests/ for the public name lands in the right place.
- Parametrize with meaningful `ids=` (algorithm / variant / input names), so `uv run pytest --collect-only -k <feature>` reads as an inventory of what is covered.

Understanding (test -> meaning):

- The function name states **one claim**, readable as a sentence (`test_one_breaking_frame_drops_the_candidate`).  It is the only documentation pytest shows at collection and in the failure header, so it carries the spec.
- The **class docstring** carries the shared story: why the group exists, where its inputs and oracles come from.  Nearly every test class has one; keep it that way.
- AAA comments give the structure; **assert messages give the failure diagnosis** (which check inside the test diverged, with values).
- A function docstring is added only when the why is not derivable from the name: a regression test citing the original bug, a magic constant that needs justifying, a non-obvious input shape.  Never write one that restates the name.

## Execution tests: batch (default) vs `exhaustive` (opt-in)

The slow tier *executes* generated code (compiles + runs it through gcc /
rustc / go / dotnet / tsx / iverilog / ghdl).  There are **two ways it does
this**, and the difference is the single most important thing to understand
about how these tests run:

1. **Batch (the default).**  `test_<lang>_batch_execution` generates the
   **whole catalogue × every supported variant** under per-symbol names
   (`crc32_t`, `crc32_s8`, …), concatenates it into **one** source unit, and
   compiles + runs it in a **single** toolchain invocation.  A
   **session-scoped fixture** (e.g. `ts_batch_results`) does that one
   build/run and caches a `{"name/variant": "PASS"|"FAIL:phase"}` dict; the
   test is `@pytest.mark.parametrize`d over every case and just looks up the
   dict, so each algorithm is still its own pytest node
   (`test_ts_batch_execution[crc32-table]`).  This is ~40× faster than
   spawning a process per case, and the single combined build is *also* the
   coexistence proof — it only links because tables are per-symbol
   (`crcglot_table_<sym>`); a name collision would fail the build.

2. **`exhaustive` (opt-in isolation).**  The older one-process-per-algorithm
   classes (`TestGenerated<Lang>Executes`, `…Streaming`, `…SliceBy8Executes`)
   still exist, marked `@pytest.mark.exhaustive`.  They are **deselected by
   default** (via `pytest_collection_modifyitems` in `conftest.py` — shown as
   *deselected*, never *skipped*, so a normal run stays green not amber).
   Run them with `--exhaustive` when you need to isolate one algorithm in its
   own translation unit: `uv run pytest --exhaustive -k crc32`.

**Why `@pytest.mark.xdist_group("<lang>_batch")` is on each batch test
(do not remove it):** under `-n auto` a session-scoped fixture runs **once
per xdist worker**.  Without the group pin, all ~16 workers would each
rebuild the batch — re-spending most of the speedup.  `xdist_group` forces
every case of that batch onto a **single** worker, so the build happens
**once** while the other workers run the rest of the suite in parallel.  The
group name must be unique per batch (`ts_batch`, `c_batch`, …).

When adding a new target language, follow this same shape: one session
fixture that builds the whole catalogue once, a parametrized lookup test, an
`xdist_group` pin, and move the old per-algorithm classes behind
`@pytest.mark.exhaustive`.

## Coverage target

Overall ≥ 90% on the full suite.  Per-module floor: 80%.  The fast
suite alone should hit ≥ 95% — the only paths that legitimately need
slow tests for coverage are subprocess invocations themselves.

## Skipped tests are not "passed"

A test run with skips is **amber, not green**.  Never report a suite as
"passing" or "green" while the summary line includes a non-zero
`skipped` count.  Always state the skip count alongside the pass count
("2443 passed, 383 skipped — not green") and treat the skips as a
regression to investigate.

Most skips on this project are caused by `pytest.mark.skipif(not
HAS_<tool>, ...)` evaluating false at test-module import time.  That
test-module-time evaluation is the trap: if conftest sets up PATH or
toolchains via fixtures (which fire *after* collection), the `HAS_<tool>`
flags freeze in their pre-fixup state and every dependent test silently
skips.  Use `pytest_configure` (a real pytest hook, runs *before*
collection) for any environment setup that controls test discovery; keep
session-scope autouse fixtures for things that only affect throughput.

The only acceptable skipped tests are ones the user has **explicitly
confirmed** as expected (e.g. "yes those Windows-arm tests skip on Mac").
After getting that confirmation, immediately reconfirm: **"Are you sure
those tests should not be skipped?"** -- the double-check exists because
silent skips have already cost us real regressions (the conftest fixture
refactor, the Go toolchain detection).

## Precommit

- Update README.md (and the badge counts at the top if test count or coverage % changed)
- Run `uv run pytest` (full suite) + coverage review
- **`uvx ruff check src tests` must be 0**
- **`uvx ty check src tests` must be 0** (the README badge tracks this and turns yellow/red on regression)
- **Run `uv run python scripts/regenerate_examples.py`** if any generator changed, a new target landed, or the variant matrix changed.  EXAMPLES.md is auto-generated; never hand-edit it.  Always re-run the script before tagging a release so the published gallery matches the shipped generators.
- **Run `/humanizer` over end-user-facing markdown (see below).**
- **Run the cruft audit (below).**

## Humanizing end-user prose

Always run `/humanizer` over the **end-user-facing** markdown after editing it —
`README.md`, the `docs/` files, `ARCHITECTURE.md`, `BENCHMARKS.md`,
`CHANGELOG.md` (not `EXAMPLES.md`, which is generated).  These files are read
by people evaluating the package, and AI tells (em-dash pile-ups, "it's not
just X, it's Y" constructions, overlong hedged sentences) read as
machine-written.  Humanize *after* the substantive edits land, so a later
content change doesn't reintroduce the tells.  Code comments / docstrings are
exempt; this is about prose docs.

Two crutches this project has specifically overused — grep for them on every
prose pass:

- **"honest" / "honestly".**  Don't advertise candor; state the limitation
  and let it speak.  "Performance, stated honestly:" becomes "Performance:".
  "An honest pointer" is just a pointer.  If a sentence stops working without
  the word, the sentence was padding.
- **Em dashes.**  AT MOST one per file; zero is the norm (Chuck never uses
  them in his own writing).  They convert cleanly to a comma, a colon, a
  period, or parentheses.  List-item separators (`- **Thing** — description`)
  use a colon instead.

## Cruft audit (every release, minimum)

Cruft accumulates because **tests assert behaviour, not labels** — when a
feature's implementation is removed or changed, correctness assertions keep
passing while the names, docstrings, and comments around them silently rot.
The green suite hides it.  So a passing test run is *not* evidence the prose
is current.  Before each release, sweep for:

- **Stale counts.**  Any hardcoded algorithm count in a comment, docstring,
  section header (`# ---- CRC-8 (21 algorithms) ----`), README prose, or test
  docstring.  Prefer age-proof phrasing ("every catalogue entry", "100+") over
  a brittle exact number.  Quick scan: `grep -rnE "[0-9]+ (algorithms|entries)" src tests README.md docs`.
- **References to removed/renamed things.**  Comments or test names citing a
  mechanism that no longer exists (e.g. the removed C-extension table cache:
  `CACHE_CAP`, `TableCache`, "cache-hit/overflow"; a test named
  `..._use_cache` that no longer exercises a cache).  When you delete a
  feature, grep the tests/docs for its vocabulary and rename what survives.
- **Dead install extras / flags / paths.**  Cross-check README/docs install
  commands against `pyproject.toml` (e.g. `crcglot[fast]` referenced an extra
  that was never defined).  `grep -rnE "crcglot\[[a-z]+\]"` and confirm each
  extra exists.
- **Docstrings that contradict the code.**  Width lists that say "8/16/32/64"
  where sub-byte/non-byte-aligned widths now exist; examples that no longer run.
- **Unnecessary suppressions.**  Audit every `# type: ignore`, `# ty: ignore`,
  `# noqa`, `# pragma: no cover` — is it still needed?  (Keep the paired
  `# type: ignore[...]  # ty: ignore[...]` form: ruff/mypy and `ty` are
  separate checkers and the project must stay clean under both.)

## Project shape

- Pure-stdlib package (no runtime dependencies) — keep it that way
  unless there's a very good reason.
- Tests are organized **by target language**, not by phase:
  `test_python_gen.py`, `test_c_gen.py`, `test_rust_gen.py`,
  `test_vhdl_gen.py`, plus `test_catalogue.py` for cross-cutting
  concerns and `test_cli.py` for the command-line interface.
- The `slow` marker is applied per-class on execution-verified test
  classes (the ones that shell out to a compiler/simulator).

## Public API ergonomics & stability

These are load-bearing conventions for the public surface (anything an
integrator imports).  They exist because real consumers have hit friction
when we broke them — honor them when touching public API.

- **Every metadata axis ships a record + a lookup, never a bare name tuple.**
  Languages, variants, and comment styles each expose a frozen
  `*Info(name, label, description, …)` dataclass, a `*_info(name)` lookup, and
  a per-language accessor.  Follow the established shape — `StyleInfo` /
  `style_info` / `comment_styles_for_language`, `VariantInfo` / `variant_info`
  — when adding a new axis.  Do **not** ship a bare `tuple[str, …]` and force
  callers to hardcode their own `{name: (label, description)}` map.
- **Everything about a target lives on `LanguageInfo`.**  Per-target
  capabilities are accessors/properties on `LanguageInfo`
  (`.variants_for_width`, `.variant_infos_for_width`, `.styles`), so a UI
  reaches one object instead of stitching two namespaces together.  A new
  per-language capability gets a `LanguageInfo` member, not a free function
  in another module.
- **No silent breaking changes.**  Never remove or rename a public field,
  function, or CLI flag without a one-release `DeprecationWarning` cycle:
  release N keeps the old name working but warns ("use X instead"); release
  N+1 removes it.  Prefer additive changes (a new field/alias) over edits.  A
  field that simply vanishes (as `AlgorithmInfo.name` did pre-0.11) turns a
  consumer's upgrade into a runtime `AttributeError` instead of a visible,
  pre-upgrade warning their test suite can catch.
- **`crcglot.__version__`** is the supported way for apps to assert a minimum
  version at import time; keep it exported and accurate.

## Readme and docs/

- Markdown prose uses LONG lines: one line per paragraph / list item, no
  hard-wrapping at 80 columns (let the editor soft-wrap).  Code fences and
  tables are exempt.
- README.md is the short overview (~175 lines); the reference lives in
  `docs/` (one file per section: `cli.md`, `api.md`, `generated-code.md`,
  `MCP.md`, indexed by `docs/index.md`).  Keep it that way -- new reference
  detail goes in a docs/ file, with at most a summary row/teaser in README.
- Update the badge counts at the top if test count or coverage % changed
- Update the "what you get per language" table if the API changed
- Update **docs/cli.md** (and the README "CLI at a glance" table row) if the
  CLI changed; **docs/api.md** if the public API changed
- Ensure that there are no auto-fixable markdown lint issues (run `uvx ruff check README.md` to verify)

## EXAMPLES.md

- Auto-generated by `scripts/regenerate_examples.py`.  Never hand-edit; re-run the script and commit the regenerated file.
- One collapsible `<details>` block per (language × variant) cell.  Default collapsed; expandable on GitHub render.
- Quick links TOC at the top uses explicit `<a id="example-{lang}-{variant}">` anchors emitted by the script -- safe against `C#`/`C` anchor collisions.
- The script reads `LANGUAGES` and walks the variant set for each language.  Adding a new language to `crcglot/targets.py` automatically picks it up on the next regeneration -- no separate maintenance of EXAMPLES.md required.
  