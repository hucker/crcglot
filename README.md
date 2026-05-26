# crcglot

![tests](https://img.shields.io/badge/tests-2179%20passed-brightgreen)
![coverage](https://img.shields.io/badge/coverage-98%25-brightgreen)
![ruff](https://img.shields.io/badge/ruff-passing-brightgreen)
![ty](https://img.shields.io/badge/ty-passing-brightgreen)

**Verified CRC source code for C / C++ ⚙️, Rust 🦀, Go 🚦, C# 💠, Python 🐍, TypeScript 🔷, Verilog 🔧, and VHDL 🔌.**  Catalogue-driven, self-test embedded, multi-language by design.  **Pure-stdlib package — zero runtime dependencies.**

LLMs will gladly write you CRC code.  It might even be right.  `crcglot` guarantees the generated code matches the canonical [reveng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm) test vector (`crc("123456789") == <check value>`) and ships a self-test you can run on your toolchain to prove it.

## Quick start

```bash
uv tool install crcglot         # or: pip install crcglot
crcglot c crc32 file=mycrc
```

That's it.  You now have `mycrc.h` and `mycrc.c` — drop-in CRC-32 with a built-in `_self_test()` you can call to verify it matches the canonical [reveng](https://reveng.sourceforge.io/crc-catalogue/all.htm) check value.

Different language?  Swap `c` for `python` / `rust` / `vhdl` / `verilog` / `go` / `csharp` / `typescript`.  Different algorithm?  Run `crcglot list` to browse all 71.

### Installation

| Tool | Command | Use when |
| ---- | ------- | -------- |
| **uv (recommended)** | `uv tool install crcglot` | You just want the `crcglot` CLI on PATH.  Isolated install, no global pollution. |
| **uv (as a library)** | `uv add crcglot` | You're calling the generators from Python (e.g. a build script that emits CRC code into your repo). |
| **pip** | `pip install crcglot` | You don't have `uv`.  Identical package, slower install. |
| **pipx** | `pipx install crcglot` | Same isolation story as `uv tool`, if pipx is what you have. |

Python 3.11+, no other runtime dependencies — `crcglot` itself is pure stdlib.  Per-target toolchains (`gcc`, `rustc`, `tsx`, `iverilog`, etc.) only matter if you want to *run* the generated code; the generator produces source either way.

Or use it from Python code:

```python
from crcglot import LANGUAGES
header, source = LANGUAGES["c"].generator("crc32")
```

Both surfaces are documented in detail below.

## What you get per language

| Function                                 | Purpose                                                 |
| ---------------------------------------- | ------------------------------------------------------- |
| `<fname>_init` / `_update` / `_finalize` | Streaming triple — feed data chunk by chunk             |
| `<fname>`                                | One-shot wrapper that calls the streaming triple        |
| `<fname>_self_test`                      | Verify against the reveng check value on your toolchain |

Every target ships a runtime-callable `_self_test()`: C returns 0/1; Rust / Go / C# / TypeScript / Python / Verilog / VHDL return `bool` / `boolean` / `bit`.  No `#[cfg(test)]` gating — call it from your release build, a boot self-check, or a startup assertion.

## How it's verified

CI runs the Python-level suite on every push: every algorithm in the reveng catalogue is checked against its **hardcoded** canonical check value — not the catalogue's own `check` field, so a silent regression in the engine can't hide — and the Python generator is run end-to-end (generated, exec'd, and called on `b"123456789"`) against the same hardcoded vectors.  The slow tier on top of that compiles and executes the generated source for **every** algorithm in C, Rust, Go, C#, TypeScript, Verilog, and VHDL via `gcc` / `rustc` / `go` / `dotnet` / `tsx` (Node) / `iverilog` / `ghdl` and re-checks the runtime result — same algorithm coverage, exercised through each real toolchain.

Every generated file also ships its own `_self_test()` carrying that same canonical vector.  **For every target except Python, you should call `_self_test()` once in your build environment** — wire it into a unit test, a startup assertion, or your boot self-check.  Our CI proves the generator emits correct code on our reference toolchain; only running `_self_test()` on yours proves your compiler version, optimization flags, target endianness, and integer widths haven't introduced a subtle disagreement.  Python is the exception: the interpreter that ran the CI suite is the one running your code, so the in-environment check would be redundant.

## CLI reference

```text
crcglot <command> [options...]
```

### `crcglot list [GLOB]`

Browse the catalogue.  Optional `GLOB` filters by shell-style pattern (e.g. `crc16-*`).  Exit code 1 if nothing matches.

```bash
crcglot list                # all 71 algorithms
crcglot list 'crc32-*'      # just the CRC-32 family
```

### `crcglot info <name>`

Print parameters (width, poly, init, refin, refout, xorout, check, desc) for one algorithm.  Exit 1 on unknown name.

```bash
crcglot info crc64-xz
```

### `crcglot {c | csharp | go | python | rust | typescript | verilog | vhdl} <algorithm> [options...] [tokens...]`

Generate source code for the chosen target language.

| Option / token       | Effect                                                                                              |
| -------------------- | --------------------------------------------------------------------------------------------------- |
| (default) bit-by-bit | Smallest code, zero RAM table, slowest.  All widths.                                                |
| `--table`            | 256-entry lookup table, 4-8× faster.  All widths.                                                   |
| `--slice8`           | 8 lookup tables, 5-10× faster than `--table`.  CRC-32 / CRC-64 only.  Compiled-software targets.    |
| `--custom`           | Use raw Rocksoft/Williams params instead of a catalogue lookup (see below).                         |
| `file=STEM`          | Write to disk (extension picked per language; see below).  Omit for stdout.                         |
| `symbol=NAME`        | Override the emitted function name.  Default: derived from algorithm, or from `file=STEM` if given. |

File extensions per language: C emits `STEM.h` + `STEM.c`; Python `.py`; Rust `.rs`; VHDL `.vhd`; Verilog `.sv` (SystemVerilog 2012); Go `.go`; C# `.cs`; TypeScript `.ts`.

Rules:

- `--table` and `--slice8` are mutually exclusive (exit 2 if both given).
- `--slice8 python` silently falls back to `--table` (CPython's per-int overhead eats the slice-by-8 speedup; stderr warns).
- Without `file=`, output goes to stdout.  For C, header is emitted first, then source.
- C / Rust / VHDL files embed `<symbol>_self_test()` returning 0 on success.  In constrained embedded targets, standard toolchain flags (`-Wl,--gc-sections` for C, LTO for Rust) strip whatever you don't call.

### `--custom` (raw Rocksoft/Williams parameters)

For algorithms not in the catalogue:

```bash
crcglot c --custom width=16 poly=0x1234 init=0xFFFF \
         refin=true refout=true xorout=0x0000 file=mycustom
```

| Param       | Required | Notes                                                                   |
| ----------- | -------- | ----------------------------------------------------------------------- |
| `width=N`   | yes      | 8, 16, 32, or 64 only                                                   |
| `poly=X`    | yes      | Hex (`0x...`) or decimal                                                |
| `init=X`    | no       | Default 0.  Hex or decimal.                                             |
| `refin=B`   | no       | Default `false`.  Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`. |
| `refout=B`  | no       | Default `false`.  Same boolean syntax.                                  |
| `xorout=X`  | no       | Default 0.                                                              |
| `name=NAME` | no       | Default `crc_custom`.  Used in generated comments.                      |
| `desc=TEXT` | no       | Free-form description in comments.                                      |

The check value for the custom parameters is computed automatically (`generic_crc(b"123456789", ...)`) and embedded into the generated `_self_test()`.

## Catalogue

64+ algorithms covering everything from CRC-8 (ATM, AUTOSAR, Bluetooth, Maxim 1-Wire) through CRC-16 (Modbus, XMODEM, CCITT, IBM SDLC) through CRC-32 (Ethernet, bzip2, iSCSI, AUTOSAR) to CRC-64 (XZ, ECMA-182, NVMe, Redis).  Browse with `crcglot list`.

## Programmatic API

Two registries, both keyed by short code:

### `LANGUAGES` — supported target languages

```python
from crcglot import LANGUAGES

for code, info in LANGUAGES.items():
    print(f"{info.emoji} {info.display_name:<10}  {info.extensions}  "
          f"{sorted(info.variants)}")
    # → ⚙️ C / C++       ('.h', '.c')  ['bitwise', 'slice8', 'table']
    # → 💠 C#            ('.cs',)      ['bitwise', 'slice8', 'table']
    # → 🚦 Go            ('.go',)      ['bitwise', 'slice8', 'table']
    # → 🐍 Python        ('.py',)      ['bitwise', 'table']
    # → 🦀 Rust          ('.rs',)      ['bitwise', 'slice8', 'table']
    # → 🔷 TypeScript    ('.ts',)      ['bitwise', 'slice8', 'table']
    # → 🔧 Verilog       ('.sv',)      ['bitwise']
    # → 🔌 VHDL          ('.vhd',)     ['bitwise']
```

Each entry is a frozen `LanguageInfo` dataclass with:

- `code` — dispatch key (`"c"`, `"csharp"`, ..., `"typescript"`, `"verilog"`)
- `extensions` — file extension tuple (`(".h", ".c")` for C; single-element for the rest)
- `variants` — subset of `{"bitwise", "table", "slice8"}` that the generator accepts
- `generator(name, ...)` — name-lookup callable (returns source string, or `(header, source)` tuple for C)
- `generator_from_entry(name, algo, ...)` — bypass the catalogue with a custom `AlgorithmInfo`
- `emoji` — single-grapheme pictographic identifier for terminals / docs
- `display_name` — human-readable name (e.g. `"C / C++"`, `"TypeScript"`) — distinct from `code`

### `ALGORITHMS` — the reveng CRC catalogue

```python
from crcglot import ALGORITHMS

modbus = ALGORITHMS["crc16-modbus"]
print(modbus.width, hex(modbus.check), modbus.desc)
# → 16 0x4b37 Modbus RTU serial protocol

# Filter to CRC-32 only.
crc32_family = [a for a in ALGORITHMS.values() if a.width == 32]
```

Each entry is a frozen `AlgorithmInfo` dataclass with the full Rocksoft / Williams parameter set: `name`, `width`, `poly`, `init`, `refin`, `refout`, `xorout`, `check`, `desc`.

### Custom polynomials

```python
from crcglot import AlgorithmInfo, LANGUAGES, generic_crc

# Compute the canonical check value for a custom poly.
check = generic_crc(b"123456789", 16, 0x1234, 0xFFFF, True, True, 0x0000)

# Build an AlgorithmInfo and feed it to any generator.
algo = AlgorithmInfo(
    name="my_crc16", width=16, poly=0x1234, init=0xFFFF,
    refin=True, refout=True, xorout=0x0000, check=check,
    desc="My custom CRC-16",
)
code = LANGUAGES["rust"].generator_from_entry("my_crc16", algo, table=True)
```

## Example output

See [EXAMPLES.md](EXAMPLES.md) for the actual generated source for `crc32` across every language × implementation combination (C / Rust / Python / VHDL / Verilog / Go / C# / TypeScript crossed with bit-by-bit, table-driven, and slice-by-8 where supported).  Every block is reproducible with one CLI command.

## Acknowledgments

CRC catalogue data is derived from Greg Cook's [reveng project](https://reveng.sourceforge.io/) — the canonical source for CRC algorithm parameters since 1999.

## License

MIT
