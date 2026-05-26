# Changelog

## v0.3.0 — 2026-05-25

Three new language targets and a Python self-test.  No breaking
changes to existing targets.

### New targets

- **Go** (`crcglot go <algo>`) -- emits a `package crc` file with the
  streaming triple, one-shot wrapper, and `_self_test() bool`.
  Supports bit-by-bit and `--table`.
- **C#** (`crcglot csharp <algo>`) -- emits a single `.cs` file
  declaring a `public static class` (named in PascalCase from the
  algorithm) with the streaming triple, one-shot wrapper, and
  `_self_test() bool`.  Supports bit-by-bit and `--table`.
- **Zig** (`crcglot zig <algo>`) -- emits a `.zig` file with `pub fn`
  exports for the streaming triple, one-shot wrapper, and
  `_self_test() bool`.  Supports bit-by-bit and `--table`.

### Python self-test

The Python generator now emits `<fname>_self_test() -> bool`, matching
the convention of the other targets.  Previously Python only signalled
correctness via the docstring `check:` line.

### Verification coverage

Every algorithm in the catalogue × every shipped variant × every
target compiles and runs on the real toolchain, verifying four
patterns per algorithm: one-shot vs reveng check value, split-at-4
streaming, empty-chunk-first streaming, empty-chunk-last streaming.
Slow execution tests on the new targets are gated on `go`,
`dotnet` (with SDK -- runtime alone is not enough), and `zig` being
on PATH; structural tests always run.

Full-suite tests: 1994 collected, fast-suite coverage ≥ 99%.

### Tooling

- `CLAUDE.md`: codified branch-naming convention
  (`feat/<dashed-slug>` etc.).
- `cspell.json`: terms from the new generators.

## v0.2.0 — 2026-05-25

Developer-experience release.  No public API changes; existing
generated code is byte-identical to v0.1.0.

### Testing infrastructure

- **Tests reorganized by target language**: `test_python_gen.py`,
  `test_c_gen.py`, `test_rust_gen.py`, `test_vhdl_gen.py`, plus
  `test_catalogue.py` (cross-cutting) and `test_cli.py`.  Replaces
  the previous phase-based layout where each language's tests were
  spread across two files.
- **New `test_cli.py`** (88 tests): `crcglot.cli` coverage 0% -> 99%,
  exercising every subcommand, flag, error path, and exit code.
- **New per-variant structural tests** for C and Rust generators
  raise fast-suite coverage on `c.py` and `rust.py` from 91% / 74%
  to 100%.
- **Overall fast-suite coverage 39% -> 99%**, full-suite 1082 ->
  1187 tests.

### Tooling and docs

- `CLAUDE.md`: codified quality gates (ruff + ty + IDE Problems
  pane all zero), test commands, coverage targets, precommit ritual.
- `cspell.json`: project-root spell-check dictionary for the
  reveng / Rocksoft / toolchain terminology used throughout.
- README: four status badges (tests, coverage, ruff, ty); new
  "CLI reference" section documents every subcommand, flag, and
  `--custom` parameter in one place.
- New `EXAMPLES.md`: the actual generated code for `crc32` across
  all 9 language × implementation combinations (C / Rust / Python /
  VHDL crossed with bit-by-bit / table-driven / slice-by-8 where
  supported).  Readers can compare output shapes side by side
  without installing.
- `uvx ruff check src tests` and `uvx ty check src tests` both
  pass clean.

## v0.1.0 — 2026-05-25

Initial release.

### What's in

- **Catalogue:** 64+ named CRC algorithms from the reveng catalogue
  (CRC-8 through CRC-64), each with verified Rocksoft/Williams
  parameters and a canonical `crc("123456789")` check value.
- **Generators:** C, Rust, VHDL, Python source code per algorithm.
  Three implementation shapes per language: bit-by-bit, table-driven,
  slice-by-8 (CRC-32 / CRC-64 only).
- **Custom polynomials:** generate from raw Rocksoft/Williams
  parameters via the `from_entry` API or the `--custom` CLI flag.
- **Embedded self-tests:** every C / Rust / VHDL file ships with a
  `<fname>_self_test` function asserting the catalogue check value.
- **CLI:** `crcglot c crc32 --slice8 file=mycrc` (and equivalents
  for python / rust / vhdl).
- **Streaming API:** `init / update / finalize` for chunked data,
  plus a one-shot wrapper.

### Verification

Tests live in `tests/`.  Run with `uv run pytest`.  Verification
strategy:

- Python output is exec'd against the reveng check value for every
  algorithm.
- C / Rust / VHDL output is compiled by gcc / rustc / ghdl,
  executed, and asserted against the same check value.  Each
  toolchain is auto-skipped if not on PATH (so the suite still
  partially runs without a full polyglot install).
