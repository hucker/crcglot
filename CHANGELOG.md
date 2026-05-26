# Changelog

## v0.5.0 — 2026-05-26

Public helper `generic_crc` for field-defined CRCs, plus a verification
section in the README.  No generator code changed; emitted source is
byte-identical to v0.4.0.

### `generic_crc` is now public API

The Rocksoft/Williams CRC engine that powers `--custom` and populates
the catalogue's `check` values has been promoted from `_generic_crc`
to `generic_crc` and is now exported from the package root.  Use it
to compute the canonical check value for a CRC that isn't in the
reveng catalogue -- e.g. a vendor-defined poly -- without going
through the CLI:

```python
from crcglot import AlgorithmInfo, LANGUAGES, generic_crc

check = generic_crc(b"123456789", 16, 0x1234, 0xFFFF, True, True, 0x0000)
algo = AlgorithmInfo(
    name="my_crc16", width=16, poly=0x1234, init=0xFFFF,
    refin=True, refout=True, xorout=0x0000, check=check,
    desc="Vendor-defined CRC-16",
)
code = LANGUAGES["rust"].generator_from_entry("my_crc16", algo, table=True)
```

Non-breaking: the underscore-prefixed name was documented as private
in the v0.4.0 module docstring, so no legitimate public consumer was
importing it.  Internal callers (`cli.py`, `tests/test_catalogue.py`)
updated in lockstep.

### README: "How it's verified" section

New section between "What you get per language" and "CLI reference"
spells out the two-tier verification strategy:

- The Python suite (run by CI on every push) checks every algorithm
  against its **hardcoded** reveng vector -- not the catalogue's own
  `check` field, so a silent engine regression can't hide -- and
  also runs the Python generator end-to-end (generated, exec'd,
  called) against the same hardcoded vectors.
- The slow tier on top of that compiles and executes the generated
  source for every algorithm in C, Rust, Go, C#, Zig, and VHDL via
  `gcc` / `rustc` / `go` / `dotnet` / `zig` / `ghdl`.

The section also recommends calling `_self_test()` once in your build
environment for every target except Python -- compiler version,
optimization flags, target endianness, and integer widths can each
subtly disagree with the reference toolchain.

### Verification on this release

Run on a partially equipped box (gcc / rustc / dotnet / ghdl
present; go and zig absent): 1676 passed, 483 skipped (the skips are
the go/zig slow-tier execution tests), 0 failed, 97% coverage.  Fast
tier in isolation: 928 passed, 97%.  Since v0.5.0 changes no
generator code, the go/zig emitted source is bit-identical to v0.4.0
(which was verified end-to-end with all six toolchains).

## v0.4.0 — 2026-05-26

Typed introspection API + slice-by-8 for every compiled target.
**Breaking change** — replaces the loose-dict public surface
(`CRC_CATALOGUE`, `GENERATORS`, `GENERATORS_FROM_ENTRY`) with frozen
dataclasses (`AlgorithmInfo`, `LanguageInfo`) and typed registries
(`ALGORITHMS`, `LANGUAGES`).

### Slice-by-8 expansion

Go, C#, and Zig now emit slice-by-8 implementations on demand
(`crcglot <lang> <algo> --slice8`).  Previously only C and Rust did;
the three new targets in v0.3.0 shipped with bit-by-bit and
`--table` only.  All five compiled languages now offer the same
three implementation shapes: bit-by-bit, table-driven, slice-by-8.

Verification: each new slice-by-8 generator is checked by an
execution-equivalence test that compiles both the bit-by-bit and the
slice-by-8 forms under disjoint symbol names, runs them on inputs of
varying lengths (0, 1, 7, 8, 9, 15, 16, 100 bytes), and asserts every
result matches.  Since the bit-by-bit forms are reveng-verified,
equivalence proves slice-by-8 is correct.

Python remains bit-by-bit + table only (CPython per-int overhead
measurably negates the speedup; measured 0.79x).  VHDL remains
bit-by-bit only (simulator reference, not synthesizable throughput).

### CI verification model clarified

`.github/workflows/tests.yml` now runs `pytest -m "not slow"` only.
The slow tests shell out to six different compilers to verify the
*generated* code; that's a developer-machine concern, not a CI one.
The verification crcglot actually ships is the embedded
`_self_test()` function the end user calls on their toolchain.

### New introspection API

```python
from crcglot import LANGUAGES, ALGORITHMS, LanguageInfo, AlgorithmInfo

# Iterate target languages and their metadata.
for code, info in LANGUAGES.items():
    print(code, info.extensions, sorted(info.variants))
    # info.generator(name, ...) and info.generator_from_entry(name, algo, ...)

# Iterate algorithms.
for name, algo in ALGORITHMS.items():
    print(name, algo.width, algo.check, algo.desc)
```

`LanguageInfo` carries the file extensions (`(".h", ".c")` for C; a
single-element tuple for every other language), the supported variants
(subset of `{"bitwise", "table", "slice8"}`), and references to the
two generator callables.  `AlgorithmInfo` carries the full Rocksoft /
Williams parameters plus the canonical reveng `check` value and a
human-readable `desc`.

### Removed (breaking)

- `CRC_CATALOGUE: dict[str, dict]` is no longer exported.  The raw
  data still lives in `crcglot.catalogue._REVENG_CATALOGUE` but is
  now private; consumers must move to `ALGORITHMS`.
- `GENERATORS: dict[str, Callable]` is no longer exported.  Move to
  `LANGUAGES[code].generator`.
- `GENERATORS_FROM_ENTRY: dict[str, Callable]` is no longer exported.
  Move to `LANGUAGES[code].generator_from_entry`, and pass an
  `AlgorithmInfo` instance (not a dict) as the second argument.

### Migration notes

Mechanical search-and-replace for downstream callers:

| Old                            | New                                       |
| ------------------------------ | ----------------------------------------- |
| `CRC_CATALOGUE`                | `ALGORITHMS`                              |
| `entry["width"]`               | `algo.width` (etc.)                       |
| `GENERATORS[lang]`             | `LANGUAGES[lang].generator`               |
| `GENERATORS_FROM_ENTRY[lang]`  | `LANGUAGES[lang].generator_from_entry`    |
| `entry = {"width": ..., ...}`  | `AlgorithmInfo(name=..., width=..., ...)` |

### Internal

- All seven generator modules (`c.py`, `csharp.py`, `go.py`,
  `python.py`, `rust.py`, `vhdl.py`, `zig.py`) now consume
  `ALGORITHMS` and `AlgorithmInfo` directly.
- `cli.py`: dropped private `_CRC_FILE_EXTENSIONS` / `_LANGS`
  constants; everything derives from `LANGUAGES`.
- New module `src/crcglot/targets.py` holds `LanguageInfo` and
  `LANGUAGES`; `AlgorithmInfo` and `ALGORITHMS` live in
  `src/crcglot/catalogue.py` next to the raw data.

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
