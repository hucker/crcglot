# Changelog

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
