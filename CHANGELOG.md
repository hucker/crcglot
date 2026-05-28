# Changelog

## v0.8.0 — 2026-05-28

A fast **runtime CRC** engine ships as an optional C extension
(`crcglot._c`), `--small` / `--fast` become the front door for picking an
implementation, and releasing now publishes a cross-platform wheel matrix.

### NEW: fast runtime CRC via `crcglot._c` ⚙️

Until now crcglot only *generated* source code.  It now also computes
CRCs directly — fast — for all 71 catalogue algorithms:

```python
from crcglot import generic_crc

# CRC-32/ISO-HDLC over any bytes-like object
crc = generic_crc(b"123456789", 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)
```

`generic_crc` picks the fastest available path automatically — you don't
choose:

1. **zlib hardware** for IEEE CRC-32 and JAMCRC (PCLMULQDQ on x86, the
   `crc32`/PMULL instructions on ARM),
2. the **C extension** for every other algorithm — bit-by-bit, 256-entry
   table, or slice-by-8 selected by width, with a per-`(width, poly,
   refin)` table cache, releasing the GIL on large buffers,
3. a **pure-Python** fallback when the extension isn't built — identical
   results.

The extension also exposes incremental and batch APIs:

- `CrcStream(...)` — `update()` / `digest()` / `reset()` / `copy()` for
  streaming,
- `c_crc_many(...)` — checksum many buffers in one call.

It ships as a single **abi3 wheel per platform** (one wheel covers
CPython 3.11+, no per-version rebuilds), built and parity-tested across
Linux (x86-64 + aarch64, glibc + musl), Windows (x64 + arm64), and macOS
(arm64).

### NEW: `--small` / `--fast` intent flags 🎛️

Express *what you want*, not which table layout:

```bash
crcglot c crc32 --fast        # fastest the target + width support
crcglot c crc32 --small       # smallest code (bit-by-bit; the default)
```

`--fast` resolves to slice-by-8 for width 32/64 where the language
supports it, else a 256-entry table, else bit-by-bit.  `--table` and
`--slice8` remain as explicit expert overrides, and default output is
unchanged.

### Release + publishing pipeline

- Two-stage release scripts — `scripts/release_prep.py` and
  `scripts/release_publish.py` — with a runbook (`scripts/RELEASE.md`).
- Tagging a release triggers `wheels.yml` to build and publish every
  platform wheel + the sdist to PyPI via OIDC trusted publishing; the old
  single-platform `publish.yml` is gone.

### Benchmarks

`BENCHMARKS.md` gains a cross-language crc32 matrix (1 KiB / 1 MiB,
release builds) plus pure-Python vs C-extension vs `generic_crc`
runtime-engine comparisons.

## v0.7.0 — 2026-05-26

Two new targets (TypeScript and Verilog), display metadata on
`LanguageInfo`, README install section showing `uv tool install`, and
**Zig has been removed** -- see below.

### NEW target: TypeScript 🔷

```bash
crcglot typescript crc32 file=mycrc
```

Emits a single `.ts` module with `init` / `update` / `finalize`
streaming triple, a one-shot wrapper, and a runtime-callable
`_self_test()` returning `boolean`.  Three variants: bitwise, table,
slice-by-8.  State type is `number` for widths 8 / 16 / 32 and
`bigint` for width 64 -- both with native JS bitwise operators (no
external library, no runtime ceiling at 2^53-1).

The emitted module is runtime-agnostic -- pure TypeScript with no
imports, runs under Node, Bun, Deno, browser ES modules, or any
bundler.  Internally, uint32 coercion (`>>> 0`) is applied at the
right points so non-reflected CRC-32 results don't slide into the
negative int32 range and surprise the caller.

Verified end-to-end via `tsx` (Node) across all 71 catalogue
algorithms × bitwise / table / slice8 variants on this dev box.

### NEW target: Verilog 🔧 (SystemVerilog 2012)

```bash
crcglot verilog crc32 file=mycrc
```

Emits a single `.sv` file containing `package <fname>_pkg` with
`automatic` functions for the streaming triple, one-shot wrapper,
and `_self_test()` returning `bit`.  Bit-by-bit only -- like VHDL,
this is a simulator-friendly reference implementation; synthesizable
pipelined RTL is a future enhancement and a different shape
(`always_ff` blocks, not pure functions).

Verified end-to-end via Icarus Verilog (`iverilog -g2012` + `vvp`)
across all 71 catalogue algorithms.

### NEW: display metadata on `LanguageInfo`

Two new fields on the frozen `LanguageInfo` dataclass:

- `emoji: str` -- single-grapheme pictographic identifier (e.g.
  `"🦀"` for Rust, `"🔷"` for TypeScript)
- `display_name: str` -- human-readable name (e.g. `"C / C++"`,
  `"TypeScript"`).  Distinct from `code`, which is the dispatch key.

Useful for terminal output, generated documentation, and the
auto-EXAMPLES script (which now derives section headings from
`display_name` instead of a hardcoded dict, so a new language only
needs to land in `LANGUAGES` to show up everywhere).

### BREAKING: Zig target removed

`crcglot zig <algo>` no longer exists, `generate_zig` and
`generate_zig_from_entry` are no longer importable, and the `"zig"`
entry has been dropped from `LANGUAGES`.

Migration: pick another compiled target (`c`, `rust`, `go`, `csharp`,
`typescript`) for the same algorithm and recompile.  If you were
distributing crcglot-generated Zig source in a build pipeline, pin
to `crcglot==0.6.0` until you migrate.

Why removed: Zig 0.13 → 0.16 changed enough that the existing
generator's CRC-64 slice-by-8 output became flaky under parallel
test execution, and re-validating it wasn't the right use of this
release's scope.  Zig may return as a separate generator branch
later -- the design lives in git history at v0.6.0.

### NEW: README install section

`uv tool install crcglot` is now the recommended install path
alongside `pip install`.  Section covers `uv tool` (isolated CLI),
`uv add` (library use), `pip`, and `pipx`.

### Registry shape and test infra

- `LANGUAGES` now has 8 entries (TypeScript + Verilog added; Zig
  removed; net +1 vs v0.6.0).
- `scripts/regenerate_examples.py` is data-driven on `display_name`
  / `extensions` -- no per-language hardcoding.  A new language
  landing in `LANGUAGES` shows up in the regenerated gallery
  automatically.
- `tests/conftest.py` learns a Windows PATH-fixup pass for install
  dirs that don't propagate to already-open shells:
  `C:\iverilog\bin`, `C:\Program Files\nodejs`,
  `C:\Program Files\Go\bin`, `%LOCALAPPDATA%\Microsoft\WinGet\Links`,
  `%APPDATA%\npm`.  Lets the slow tier pick up freshly-installed
  toolchains without restarting VS Code.

## v0.6.0 — 2026-05-26

Rust generator: `_self_test()` is now a runtime-callable `pub fn`,
not a `#[cfg(test)]` test block.  Brings Rust in line with every
other target (C / Go / C# / Zig / Python / VHDL) and makes the v0.5.0
README claim about calling `_self_test()` in your build environment
actually true for Rust output.

### Generated Rust changed shape

Old emission (v0.5.0 and earlier):

```rust
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn check_value_matches_reveng() {
        assert_eq!(crc32(b"123456789"), 0xCBF43926);
    }
}
```

New emission:

```rust
pub fn crc32_self_test() -> bool {
    crc32(b"123456789") == 0xCBF43926_u32
}
```

The old block was only compiled under `cargo test` / `rustc --test`,
so a release-build caller couldn't wire `_self_test()` into a boot
self-check or startup assertion -- contradicting what the README
recommends.  The new shape compiles in every build configuration and
returns a `bool` you can branch on.

### Migration for v0.5.0 callers

If you were relying on `cargo test` discovering the embedded test,
wrap the new function in your own `#[test]`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn check_value_matches_reveng() {
        assert!(crc32_self_test());
    }
}
```

If you wanted `_self_test()` callable from release code -- you
couldn't on v0.5.0; now you can.

### Test harness rewire

`tests/test_rust_gen.py::TestGeneratedRustExecutes` switched from
`rustc --test` to compiling with an injected `main()` that calls
`_self_test()` and exits 0 iff it returned `true`.  Same algorithm
coverage, exercised through the same path a downstream consumer
would actually use.  README "What you get per language" caption and
`rust.py` module docstring updated; `EXAMPLES.md` regenerated.

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
