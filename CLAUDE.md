# crcglot — Claude Code instructions

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
  