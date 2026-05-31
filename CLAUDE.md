# crcglot — Claude Code instructions

## Commenting

- Google Style Docstrings for all public funcitons
- Docstring sections in this order: Args, Returns, Raises, Examples
- Use `"""` triple-quote style for docstrings, even for one-liners.  This
  is the standard convention in the Python ecosystem and supported by
  all major docstring parsers.  It also allows for easy expansion of
  one-liners into multi-line docstrings without needing to change the
  quoting style.
- Do not be super verbose.  Explain the "why" and any non-obvious "what". The docstring 
  should add value.
- For public functions, include an "Examples" section with a minimal usage example for
  non trivial functinons
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

## Coverage target

Overall ≥ 90% on the full suite.  Per-module floor: 80%.  The fast
suite alone should hit ≥ 95% — the only paths that legitimately need
slow tests for coverage are subprocess invocations themselves.

## Precommit

- Update README.md (and the badge counts at the top if test count or coverage % changed)
- Run `uv run pytest` (full suite) + coverage review
- **`uvx ruff check src tests` must be 0**
- **`uvx ty check src tests` must be 0** (the README badge tracks this and turns yellow/red on regression)
- **Run `uv run python scripts/regenerate_examples.py`** if any generator changed, a new target landed, or the variant matrix changed.  EXAMPLES.md is auto-generated; never hand-edit it.  Always re-run the script before tagging a release so the published gallery matches the shipped generators.

## Project shape

- Pure-stdlib package (no runtime dependencies) — keep it that way
  unless there's a very good reason.
- Tests are organized **by target language**, not by phase:
  `test_python_gen.py`, `test_c_gen.py`, `test_rust_gen.py`,
  `test_vhdl_gen.py`, plus `test_catalogue.py` for cross-cutting
  concerns and `test_cli.py` for the command-line interface.
- The `slow` marker is applied per-class on execution-verified test
  classes (the ones that shell out to a compiler/simulator).

## Readme

- Update the badge counts at the top if test count or coverage % changed
- Update the "what you get per language" table if the API changed
- Update the "CLI reference" section if the CLI changed
- Ensure that there are no auto-fixable markdown lint issues (run `uvx ruff check README.md` to verify)

## EXAMPLES.md

- Auto-generated by `scripts/regenerate_examples.py`.  Never hand-edit; re-run the script and commit the regenerated file.
- One collapsible `<details>` block per (language × variant) cell.  Default collapsed; expandable on GitHub render.
- Quick links TOC at the top uses explicit `<a id="example-{lang}-{variant}">` anchors emitted by the script -- safe against `C#`/`C` anchor collisions.
- The script reads `LANGUAGES` and walks the variant set for each language.  Adding a new language to `crcglot/targets.py` automatically picks it up on the next regeneration -- no separate maintenance of EXAMPLES.md required.
  