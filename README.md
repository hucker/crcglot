# crcglot

![tests](https://img.shields.io/badge/tests-6513%20passed-brightgreen)
![coverage](https://img.shields.io/badge/coverage-95%25-brightgreen)
![ruff](https://img.shields.io/badge/ruff-passing-brightgreen)
![ty](https://img.shields.io/badge/ty-passing-brightgreen)

**A multi-language CRC toolkit.**  Generate verified code for C / C++ ⚙️, Rust 🦀, Go 🚦, C# 💠, Java ☕, Python 🐍, TypeScript 🔷, Verilog 🔧, and VHDL 🔌 — and compute, detect, and reverse-engineer CRCs, from Python or over MCP.  Catalogue-driven, execution-verified, self-test embedded.  **Zero runtime dependencies — stdlib only** (an optional bundled C accelerator speeds up runtime computation; everything works without it).

LLMs will gladly write you CRC code.  It might even be right.  `crcglot` doesn't ask you to trust the generator; it proves the output by *running* it: every algorithm, in every variant, in every language, is generated, compiled, and executed against the **hardcoded** canonical [reveng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm) vector (`crc("123456789") == <check value>`).  More than 100 algorithms across nine languages, verified by execution rather than inspection, and every generated file embeds a self-test over four independent reference vectors so you can re-prove it on your own toolchain.

## Quick start

```bash
uv tool install crcglot         # or: pip install crcglot
crcglot c crc32 file=mycrc
```

That's it.  You now have `mycrc.h` and `mycrc.c`: a drop-in CRC-32 with a built-in `_self_test()` you can call to verify it reproduces four independent reference CRCs, anchored to the canonical [reveng](https://reveng.sourceforge.io/crc-catalogue/all.htm) check value.

**The whole model is three choices:** which **algorithm** (`crc32`, `crc16-modbus`, … ; `crcglot list` shows the more than 100), which **language** (`c` / `python` / `rust` / `vhdl` / `verilog` / `go` / `csharp` / `java` / `typescript`), and whether you want it **`--fast`** (fastest the target supports, and the default) or **`--small`** (smallest code).  crcglot figures out the implementation details, so you never have to know what "slice-by-8" is.

```bash
crcglot rust crc32 file=mycrc            # fastest Rust crc32 (the default) to mycrc.rs
crcglot c crc8 --small                   # smallest C crc8, to stdout
```

### Installation

| Tool                  | Command                   | Use when                                                                                            |
| --------------------- | ------------------------- | --------------------------------------------------------------------------------------------------- |
| **uv (recommended)**  | `uv tool install crcglot` | You just want the `crcglot` CLI on PATH.  Isolated install, no global pollution.                    |
| **uv (as a library)** | `uv add crcglot`          | You're calling the generators from Python (e.g. a build script that emits CRC code into your repo). |
| **pip**               | `pip install crcglot`     | You don't have `uv`.  Identical package, slower install.                                            |
| **pipx**              | `pipx install crcglot`    | Same isolation story as `uv tool`, if pipx is what you have.                                        |

Python 3.11+, zero runtime dependencies: `crcglot` imports nothing beyond the standard library.  (The prebuilt wheels bundle an optional C accelerator for runtime CRC computation — see [Fast runtime CRC](#fast-runtime-crc-optional-c-extension) — so the package is not "pure Python" in the packaging sense, but it runs fully, on any platform, without it.)  Per-target toolchains (`gcc`, `rustc`, `tsx`, `iverilog`, etc.) only matter if you want to *run* the generated code; the generator produces source either way.

Or use it from Python code:

```python
from crcglot import LANGUAGES
header, source = LANGUAGES["c"].generator("crc32")
```

Both surfaces are documented in detail below.

## What you get per language

| Function                                 | Purpose                                                 |
| ---------------------------------------- | ------------------------------------------------------- |
| `<fname>_init` / `_update` / `_finalize` | Streaming triple; feed data chunk by chunk              |
| `<fname>`                                | One-shot wrapper that calls the streaming triple        |
| `<fname>_self_test`                      | Verify against four independent reference CRCs on your toolchain |

Every target ships a runtime-callable `_self_test()`: C returns 0/1; Rust / Go / C# / Java / TypeScript / Python / Verilog / VHDL return `bool` / `boolean` / `bit`.  No `#[cfg(test)]` gating, so you can call it from your release build, a boot self-check, or a startup assertion.

## How it's verified

**The guarantee is behavioral, not structural.**  crcglot doesn't lint the generated code, it runs it.  Three axes, fully crossed: every one of the **more than 100 algorithms**, in **every variant** the target supports (bit-by-bit, table-driven, slice-by-8), in **every one of the nine languages**, is executed and its output checked against the hardcoded canonical vector.  Nothing ships on "the generator looks correct."

CI runs the Python-level suite on every push: every algorithm in the reveng catalogue is checked against its **hardcoded** canonical check value (not the catalogue's own `check` field, so a silent regression in the engine can't hide), and the Python generator is run end-to-end (generated, exec'd, and called on `b"123456789"`) against the same hardcoded vectors.  The slow tier on top of that compiles and executes the generated source for **every** algorithm in C, Rust, Go, C#, Java, TypeScript, Verilog, and VHDL via `gcc` / `rustc` / `go` / `dotnet` / `javac`+`java` / `tsx` (Node) / `iverilog` / `ghdl` and re-checks the runtime result: the same algorithm coverage, exercised through each real toolchain.

Every generated file also ships its own `_self_test()`.  For a catalogue algorithm it now checks **four** fixed inputs — the empty string, `"123456789"`, all 256 byte values, and a 1 KiB pseudo-random pattern — so the byte-table and the high-bit handling get exercised, not just the one short check string.  The two large inputs are regenerated inside the self-test with a byte-at-a-time loop, so the embedded code carries no big array (it stays friendly to flash- and RAM-constrained targets).  Those four reference CRCs are not computed by crcglot — using the engine to grade itself would be circular.  They come from two independent implementations ([anycrc](https://pypi.org/project/anycrc/) and [crccheck](https://pypi.org/project/crccheck/)) that had to agree, anchored to reveng's published value at the check string; both are dev-only tools, so the shipped package keeps its zero-dependency footprint.  A custom (non-catalogue) polynomial has no independent reference, so it falls back to a single check value crcglot computed itself — a weaker check that still catches a toolchain mismatch but, unlike a catalogue algorithm, can't catch an error shared by the generator and the generated code.

**For every target except Python, you should call `_self_test()` once in your build environment**, wired into a unit test, a startup assertion, or your boot self-check.  Our CI proves the generator emits correct code on our reference toolchain; only running `_self_test()` on yours proves your compiler version, optimization flags, target endianness, and integer widths haven't introduced a subtle disagreement.  Python is the exception: the interpreter that ran the CI suite is the one running your code, so the in-environment check would be redundant.

### What the embedded self-test buys you beyond correctness

- **A boot-time integrity check.**  A table-driven CRC carries ~1 KiB of constants in flash, and a corrupted table entry produces silently wrong CRCs forever.  The all-bytes and 1 KiB vectors drive over a thousand lookups through the table, so calling `_self_test()` at startup doubles as a flash-corruption tripwire — not just a build-time sanity check.
- **A self-evidencing artifact.**  An auditor holding the generated file needs no access to crcglot, its CI, or the internet: the file states its claim ("this is CRC-16/CCITT-FALSE") and carries executable acceptance criteria for it, derived from references crcglot didn't compute.  Years later, when nobody remembers how the file was generated, it still proves itself.
- **Tamper-evidence for well-meaning edits.**  Any later hand-edit to the algorithm either keeps the self-test passing or visibly deletes the assertions — both auditable events in a diff.  Silent drift becomes loud drift.
- **A cleaner story for regulated builds.**  Certification frameworks ask whether your code generator is qualified; the standard alternative is independently verified *output*.  Vectors computed by two independent engines, anchored to a published catalogue, embedded as re-runnable assertions next to the implementation, are that evidence — attached to the artifact, not the tool.

## Documentation comments

The generated code is correct, and it's also **documented**.  Every file gets a header (algorithm parameters, a copy-paste streaming example, the self-test contract) and a doc comment above each of the five functions, so a reader learns the `init → update* → finalize` streaming contract from the source, not from the tests.  Pick the convention with `--comment=<style>`; `plain` (clean human-readable comments in each language's native syntax) is the default, and every language also has its idiomatic doc-tool style:

| Language           | `--comment` styles                                    |
| ------------------ | ----------------------------------------------------- |
| C / C++ ⚙️          | `plain`, `doxygen`                                    |
| C# 💠               | `plain`, `doxygen`, `docfx` (XML `/// <summary>`)     |
| Java ☕             | `plain`, `doxygen`, `javadoc`                         |
| Python 🐍           | `plain`, `google`, `numpy`, `rest` (Sphinx `:param:`) |
| Rust 🦀             | `plain`, `rustdoc` (`///` + `# Arguments`)            |
| Go 🚦               | `plain`, `godoc`                                      |
| TypeScript 🔷       | `plain`, `jsdoc` (TSDoc)                              |
| Verilog 🔧 / VHDL 🔌 | `plain`                                               |

```bash
crcglot c crc32 --comment=doxygen        # /** @brief @param @return */
crcglot python crc32 --comment=numpy     # numpydoc underlined Parameters / Returns
crcglot rust crc32 --comment=rustdoc     # /// with # Arguments markdown
```

crcglot offers each language only the styles its doc-tool actually understands: `crcglot rust --comment=doxygen` is rejected, because Doxygen doesn't read Rust.  The matrix is derived from the styles themselves; nothing hardcodes it.

**Building a UI?**  The matrix is queryable, so a front end can populate a language → style dropdown with no hardcoding.  Each record carries a machine `name` (the dropdown value, handed back to the generator), a human `label`, and a `description`:

```python
from crcglot.comments import comment_styles_for_language
for s in comment_styles_for_language("python"):
    print(s.name, "|", s.label, "|", s.description)
# plain  | Plain            | Human-readable comments in the language's native syntax
# google | Google           | Google-style docstrings (Args / Returns / Note)
# numpy  | NumPy            | NumPy (numpydoc) docstrings, underlined Parameters / Returns
# rest   | reStructuredText | Sphinx field-list docstrings (:param: / :returns:)
```

The generators take the chosen name directly (`LANGUAGES["python"].generator("crc32", comment_style="numpy")`), and the same `{name, label, description}` records are served over MCP in the `crcglot://languages.json` resource (each language's `comment_styles`).

### Why generate the docs instead of asking an LLM?

You still can: point an LLM at the output and let it write whatever prose you like; nothing here stops you.  But the generated code is **fully known**: the parameters, the API contract, and the streaming semantics are deterministic facts, so the *documentation* can be deterministic too.  That buys three things an LLM pass can't:

- **Reproducible.**  The same request produces the same comment, byte for byte.  Everyone who generates `crc32` gets the *identical* documentation: no drift, no "it phrased it differently this time," no diff churn between two runs.
- **Correct by construction, or wrong in exactly one place.**  The comment is rendered from the same source of truth as the code, so it can't hallucinate a parameter or misdescribe the API.  And if a description *is* wrong, it's wrong *uniformly*: caught once, fixed once in the generator, and the fix reaches every output everywhere.  Per-invocation LLM wrongness is the opposite: subtly different each time, and far harder to audit.
- **Free, offline, auditable.**  No API call, no token cost, no network; it runs in CI and on an air-gapped build.  A reviewer (the class-III-medical-device kind) audits the comment generator *once* and can then trust every file it emits.

Layer an LLM on top when you want richer prose.  The point is that the *baseline* everyone ships by default is deterministic, uniform, and reviewable.

## Naming conventions

The generated public functions read like hand-written code in each target: Go and C# get `PascalCase` (`Crc16ModbusUpdate`), Java and TypeScript get `camelCase` (`crc16ModbusUpdate`), and C, Rust, Python, Verilog, and VHDL get `snake_case` (`crc16_modbus_update`).  Those are the **defaults**, so a linter (`govet`, StyleCop, ESLint, …) won't flag the output.  Override with `--naming=<convention>`; each language offers only the conventions its ecosystem actually uses (C is a free-for-all, Python and Rust are snake-only):

| Language           | default  | `--naming` choices         |
| ------------------ | -------- | -------------------------- |
| C / C++ ⚙️          | `snake`  | `snake`, `camel`, `pascal` |
| C# 💠               | `pascal` | `pascal`, `camel`          |
| Go 🚦               | `pascal` | `pascal`, `camel`          |
| Java ☕             | `camel`  | `camel`, `pascal`          |
| TypeScript 🔷       | `camel`  | `camel`, `pascal`          |
| Rust 🦀             | `snake`  | `snake`                    |
| Python 🐍           | `snake`  | `snake`                    |
| Verilog 🔧 / VHDL 🔌 | `snake`  | `snake`                    |

```bash
crcglot go crc32 --naming camel          # crc32Update instead of Crc32Update
crcglot c crc32 --naming pascal          # Crc32Update; the CRC32_H guard stays SCREAMING_SNAKE
```

Only the public function/method names are re-cased; header guards, table symbols, package names, and class names keep their own fixed idiom.  An explicit `name=NAME` renames the CRC and is cased per language (so it follows `--naming`); `symbol=NAME` is the escape hatch, emitted verbatim and bypassing `--naming`.  As with `--comment`, `crcglot rust crc32 --naming=pascal` is rejected (Rust is snake-only); the matrix is derived from `LANGUAGES[code].naming`, nothing hardcodes it.  The same `{name, label, description}` records are served over MCP in `crcglot://languages.json` (each language's `naming` + `default_naming`).

## CLI reference

```text
crcglot <command> [options...]
```

### `crcglot list [GLOB] [--json]`

Browse the catalogue.  Optional `GLOB` filters by shell-style pattern (e.g. `crc16-*`).  Exit code 1 if nothing matches.

```bash
crcglot list                # more than 100 algorithms
crcglot list 'crc32-*'      # just the CRC-32 family
crcglot list --json         # machine-readable list with full parameters
```

### `crcglot info <name>`

Print parameters (width, poly, init, refin, refout, xorout, check, desc) for one algorithm.  Exit 1 on unknown name.

```bash
crcglot info crc64-xz
```

### `crcglot detect [INPUTS...]`

Brute-force identify which catalogue CRC matches a packet whose tail is the CRC.  Useful for reverse-engineering unfamiliar protocols, debugging captured frames, or confirming a sample really uses the CRC you expect.

```bash
crcglot detect packet.bin                            # binary file (or '-' for stdin)
crcglot detect a.bin b.bin c.bin                     # multi-packet (intersected)
crcglot detect --text "123456789 cbf43926"           # text mode, inline
crcglot detect --text -                              # text mode, one packet per line on stdin
crcglot detect --hex "313233343536373839cbf43926"    # hex-encoded bytes
crcglot detect --algorithms 'crc16-*' packet.bin     # narrow the scan to a family
crcglot detect --match all packet.bin                # forensic: every candidate
crcglot detect --match set a.bin b.bin               # strict: succeed only on a single algorithm
```

`--match` selects the strategy: `first` (default; early-stop on the first hit, priority order is `crc32`, `crc32-jamcrc`, `crc32-iscsi`, then the rest of the catalogue), `all` (exhaustive forensic view), `set` (strict singleton: succeed only if exactly one algorithm survives across all packets).  Exit 0 on match, 1 otherwise.  For text packets the inferred separator + hex leader + case are reported so you can reproduce the same format via `crcglot encode`.

When no CRC matches, `detect` (and `reverse`) also report a `checksum_hint` if the trailing field looks like a common **non-CRC** checksum — see `crcglot checksum` below.

### `crcglot checksum [INPUTS...]`

Identify a common **non-CRC** checksum in a packet's trailing field: 8-bit sum / LRC / one's-complement / XOR, 16-bit sum, Internet checksum, Fletcher-16, Fletcher-32, Adler-32.  Identification only — crcglot doesn't generate code for these; the value is knowing your "mystery CRC" isn't a CRC at all before you burn an afternoon on `detect`.

```bash
crcglot checksum packet.bin                          # binary file (or '-' for stdin)
crcglot checksum --hex "74656c656d65...4b8806d2"     # hex-encoded packet
crcglot checksum --text "data 1f2a"                  # text packet
crcglot checksum --checksums 'fletcher*' a.bin       # narrow the candidates
crcglot checksum --endian little a.bin b.bin         # fix byte order (default: try both)
```

```text
$ crcglot checksum --hex "74656c656d657472792d6672616d652d3030314b8806d2"
adler32  width=32  endianness=big  frames_agreed=1  (Adler-32)
```

Confidence scales with `frames_agreed`: one frame is a hint, several corroborating frames are a finding.  Exit 0 on a match, 1 otherwise.

### `crcglot encode <algorithm> [<data>]`

Build a packet by appending the CRC.  Round-trip partner to `detect`: feed `detect`'s `(algorithm, endianness, padding)` shape back to `encode` to rebuild a packet in the same format.

```bash
crcglot encode crc32 "123456789"                                # → "123456789 cbf43926"
crcglot encode crc32 "123456789" --sep $'\t' --leader 0x --upper # tab + "0x" + uppercase
crcglot encode crc32 --binary < data.bin > packet.bin           # binary, big-endian
crcglot encode crc32-iscsi --binary --little < data.bin         # binary, little-endian
```

| Option         | Default                      | Effect                                                 |
| -------------- | ---------------------------- | ------------------------------------------------------ |
| `--binary`     | off                          | Read stdin as bytes; write packet bytes to stdout.     |
| `--little`     | off                          | Little-endian CRC byte order (default: big).           |
| `--sep STR`    | `" "`                        | Text separator between data and hex.                   |
| `--leader STR` | `""`                         | Text hex leader: `""`, `"0x"`, or `"0X"`.              |
| `--upper`      | off                          | Uppercase hex digits.                                  |
| `--fmt STR`    | `"{data}{sep}{leader}{crc}"` | str.format template; the four tokens may be reordered. |

### `crcglot compute <algorithm> [<data>]`

Compute the raw CRC integer of some data — no packet framing, just the value.  The quick check when you have data in one hand and an expected CRC in the other.

```bash
crcglot compute crc16-modbus "123456789"        # → 0x4B37
crcglot compute crc32 "123456789" --dec         # decimal instead of hex
crcglot compute crc64-xz --binary < data.bin    # bytes from stdin
```

### `crcglot credits`

Print acknowledgments for the upstream work crcglot builds on (also exported as `crcglot.ATTRIBUTION` / `crcglot.ACKNOWLEDGMENTS`).  See [Acknowledgments](#acknowledgments).

### `crcglot {c | csharp | go | java | python | rust | typescript | verilog | vhdl} <algorithm> [<algorithm>...] [options...] [tokens...]`

Generate source code for the chosen target language.  Pick your intent; crcglot picks the implementation:

| Option / token        | Effect                                                                                                                                                                        |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--small`             | Smallest code, zero RAM table (bit-by-bit).  Works for any width.                                                                                                              |
| `--fast`              | Fastest the target supports: slice-by-8 for width 32/64 on compiled targets, table-driven otherwise.  **The default** when no variant flag is given.                          |
| `--custom`            | Use raw Rocksoft/Williams params instead of a catalogue lookup (see below).                                                                                                   |
| `--comment=STYLE`     | Documentation style for the generated comments (default `plain`).  See [Documentation comments](#documentation-comments).                                                     |
| `--naming=CONVENTION` | Casing of the public function/method names (`snake` / `camel` / `pascal`).  Defaults to each language's idiomatic convention.  See [Naming conventions](#naming-conventions). |
| `file=STEM`           | Write to disk (extension picked per language; see below).  Omit for stdout.                                                                                                   |
| `name=NAME`           | Rename the CRC: replaces the algorithm name as the base for the functions / class / filename, **cased per language** (`name=my-widget` → `my_widget.rs`, `MyWidget.java`, `MyWidget.cs`).  Single algorithm only.                |
| `symbol=NAME`         | Escape hatch: emit this exact identifier **verbatim**, bypassing `--naming`.  Single algorithm; not for Java.  Prefer `name=` for the usual "call it X" case.                  |

File extensions per language: C emits `STEM.h` + `STEM.c`; Python `.py`; Rust `.rs`; VHDL `.vhd`; Verilog `.sv` (SystemVerilog 2012); Go `.go`; C# `.cs`; Java `.java`; TypeScript `.ts`.  For Java and C# the file is named after the public class (PascalCase of `name=` / the algorithm, or of `STEM`), so the stem must yield a legal class identifier; `STEM` is otherwise sanitized to a valid identifier (`file=my-crc` → `my_crc.rs`).

**Bundle several algorithms into one file** by naming more than one: `crcglot c crc32 crc16-modbus crc8 file=mycrcs` writes a single `mycrcs.h` / `mycrcs.c` containing all three (one `.go` / `.rs` / `.cs` / … for the other languages).  Each algorithm keeps its own catalogue-derived function names (`crc32`, `crc16_modbus`, …) and the tables are namespaced per symbol, so they never collide.  `name=` and `symbol=` rename a single CRC, so both are rejected with more than one algorithm; duplicates are de-duplicated; an unknown name aborts the whole bundle.

**Expert overrides** (you usually don't need these, since `--fast` chooses for you): `--table` forces the 256-entry single-table form, and `--slice8` forces the 8-table form.  They exist for the rare case where you want the *middle* of the size/speed curve explicitly, e.g. a RAM-constrained target where the 1 KiB table is fine but slice-by-8's 8 KiB isn't.  `--slice8` is CRC-32/64 + compiled targets only.

Rules:

- The variant selectors `--small` / `--fast` / `--table` / `--slice8` are mutually exclusive: pick at most one (exit 2 otherwise).  No selector = `--fast` (the fastest the target supports); pass `--small` for the smallest code.
- `--slice8 python` silently falls back to `--table` (CPython's per-int overhead eats the slice-by-8 speedup; stderr warns).  `--fast` never needs this fallback; it only picks slice-by-8 where it actually applies.
- Without `file=`, output goes to stdout.  For C, header is emitted first, then source.
- Every target embeds `<symbol>_self_test()` (C returns 0 on success; the rest return `bool` / `boolean` / `bit`).  In constrained embedded targets, standard toolchain flags (`-Wl,--gc-sections` for C, LTO for Rust) strip whatever you don't call.

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
| `name=NAME` | no       | Default `crc_custom`.  Names the functions / class / filename (cased per language) and labels the comments. |
| `desc=TEXT` | no       | Free-form description in comments.                                      |

The check value for the custom parameters is computed automatically (`generic_crc(b"123456789", ...)`) and embedded into the generated `_self_test()`.

## Catalogue

More than 100 algorithms covering everything from CRC-8 (ATM, AUTOSAR, Bluetooth, Maxim 1-Wire) through CRC-16 (Modbus, XMODEM, CCITT, IBM SDLC) through CRC-32 (Ethernet, bzip2, iSCSI, AUTOSAR) to CRC-64 (XZ, ECMA-182, NVMe, Redis), plus the non-byte-aligned families: CAN (CRC-15), CAN FD (CRC-17/21), FlexRay (CRC-11/24), LTE/BLE/OpenPGP (CRC-24), and the GSM/UMTS/CDMA2000 telecom set.  Browse with `crcglot list`.

## Programmatic API

`import crcglot` loads only the compute core (the engine, the catalogue, and the streaming API — 4 modules in ~30 ms); detection, reverse-engineering, checksum identification, and the nine generators load on first use.  The public surface is identical either way.

Two registries, both keyed by short code:

### `LANGUAGES`: supported target languages

```python
from crcglot import LANGUAGES

for code, info in LANGUAGES.items():
    print(f"{info.emoji} {info.display_name:<10}  {info.extensions}  "
          f"{sorted(info.variants)}")
    # → ⚙️ C / C++       ('.h', '.c')  ['bitwise', 'slice8', 'table']
    # → 💠 C#            ('.cs',)      ['bitwise', 'slice8', 'table']
    # → 🚦 Go            ('.go',)      ['bitwise', 'slice8', 'table']
    # → ☕ Java          ('.java',)    ['bitwise', 'slice8', 'table']
    # → 🐍 Python        ('.py',)      ['bitwise', 'table']
    # → 🦀 Rust          ('.rs',)      ['bitwise', 'slice8', 'table']
    # → 🔷 TypeScript    ('.ts',)      ['bitwise', 'slice8', 'table']
    # → 🔧 Verilog       ('.sv',)      ['bitwise']
    # → 🔌 VHDL          ('.vhd',)     ['bitwise']
```

Each entry is a frozen `LanguageInfo` dataclass with:

- `code`: dispatch key (`"c"`, `"csharp"`, ..., `"typescript"`, `"verilog"`)
- `extensions`: file extension tuple (`(".h", ".c")` for C; single-element for the rest)
- `variants`: subset of `{"bitwise", "table", "slice8"}` that the generator accepts
- `generator(name, ...)`: name-lookup callable (returns source string, or `(header, source)` tuple for C)
- `generator_from_entry(name, algo, ...)`: bypass the catalogue with a custom `AlgorithmInfo`
- `combiner(outputs, stem)`: merge several generator outputs into one file (powers multi-algorithm bundling); per-symbol tables keep the merge collision-free
- `emoji`: single-grapheme pictographic identifier for terminals / docs
- `display_name`: human-readable name (e.g. `"C / C++"`, `"TypeScript"`), distinct from `code`
- UI helpers: `generate_files(...)` returns ready-to-write `GeneratedFile`s; `format_name(stem, kind)` / `format_filename(stem)` case a stem to the identifier or filename crcglot will emit; `validate_symbol(stem)` pre-checks a name; module-level `default_stem(algorithm)` gives crcglot's default stem (the algorithm name, or `crc_bundle` for a bundle).  A UI reads its name field from these instead of hardcoding per-language rules

### `ALGORITHMS`: the reveng CRC catalogue

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
from crcglot import AlgorithmInfo, Crc, LANGUAGES, generic_crc

# Compute the canonical check value for a custom poly.
spec = Crc(width=16, poly=0x1234, init=0xFFFF, refin=True, refout=True, xorout=0x0000)
check = generic_crc(b"123456789", spec)

# Build an AlgorithmInfo (a named, checked Crc) and feed it to any generator.
algo = AlgorithmInfo(
    width=16, poly=0x1234, init=0xFFFF,
    refin=True, refout=True, xorout=0x0000, check=check,
    desc="My custom CRC-16", source="custom",
)
code = LANGUAGES["rust"].generator_from_entry("my_crc16", algo, table=True)
```

## Use with an MCP client (optional)

`crcglot[mcp]` exposes the CLI surface as a [Model Context Protocol](https://modelcontextprotocol.io) server so LLM clients (Claude Desktop, Cursor, mcp-cli, …) can call `crc_detect` / `crc_reverse` / `crc_verify` / `crc_compute` / `crc_generate` etc. as named tools.  The LLM never has to remember a polynomial, slice bytes off a packet to find the CRC, or write a reflection loop; it asks crcglot.  The three packet tools take the same input shape (a frame with the CRC at the tail, either binary hex/base64 or a `data <sep> hexcrc` text line): `crc_detect` names a *known* CRC, `crc_reverse` recovers an *unknown / custom* one, and `crc_verify` checks a frame against a named algorithm.

Why hand an LLM a tool instead of letting it write the CRC itself?  Because the division of labor is exactly right: the model does the judgment (which tool, which parameters, what the result means) and crcglot does the arithmetic — deterministic, catalogue-grounded, execution-verified.  An LLM can converge on correct *generation* of a known algorithm if it has a compiler and the check value to iterate against, but the problems people actually show up with have no value to converge on: "here are six frames from my device and the checksum matches nothing."  Recovering unknown parameters is a search, not a generation task, and a hand-written searcher that's subtly wrong fails silently.  That's the case where a verified tool isn't merely faster — it's the only thing that works.

Here's that case end to end, as a chat session.  Every value below is a real tool output — paste the same frames at a connected client and you get the same answers:

> **You:** I captured these frames from a serial link.  Last two bytes look like a CRC but nothing standard matches: `5057523a31322e3430569771`, `544d503a34382e31433d4d`, `52504d3a303031343530da2e`, `5354413a4f4bea3b`
>
> **Claude** *(calls `crc_detect`)*: No catalogue algorithm matches, and it's not a common non-CRC checksum either — this is a custom CRC.  *(calls `crc_reverse`)*  Four frames aren't enough to pin the polynomial: I need a few more captures, ideally including two or more of the **same length** with different content.
>
> **You:** Here are six more: `5057523a31322e333856b10d`, `544d503a34382e3343bde8`, `52504d3a303031343438eebc`, `5354413a52554e0492`, `5057523a31322e3431565723`, `4552523a4e4f4e458030`
>
> **Claude** *(calls `crc_reverse`)*: Recovered it: **CRC-16, poly 0xA097, init 0x1D0F, refin/refout true, xorout 0**, CRC field 2 bytes little-endian — verified against a held-out frame.  (Three algebraically equivalent init/xorout labellings also reproduce every frame; all predict identical CRCs, so any of them interoperates.)  Want a drop-in implementation?
>
> **You:** C, for an STM32.
>
> **Claude** *(calls `crc_generate`)*: Here's `vendor_crc.h` / `vendor_crc.c` — table-driven CRC-16 with your recovered parameters and an embedded `vendor_crc_self_test()` you can call at boot.

Four tool calls, no hand-rolled bit arithmetic anywhere, and the artifact carries its own proof.  Note the middle beat: when the data couldn't support an answer, the tool said so and named exactly what was missing — a deterministic "underdetermined" beats a confident guess.

```bash
pip install 'crcglot[mcp]'        # the extra ships the MCP SDK
# or:  uv tool install 'crcglot[mcp]'
```

Then wire it into your MCP client.  Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "crcglot": {
      "command": "uvx",
      "args": ["--from", "crcglot[mcp]", "crcglot-mcp"]
    }
  }
}
```

Tools: `crc_list` · `crc_info` · `crc_detect` · `crc_reverse` · `crc_verify` · `crc_encode` · `crc_compute` · `crc_compute_many` · `crc_generate` · `crc_credits`.  Resources: `crcglot://catalogue.json` · `crcglot://languages.json` · `crcglot://variants.json`.  Full reference and Claude Desktop walkthrough live in [docs/MCP.md](docs/MCP.md).

## Fast runtime CRC (optional C extension)

Beyond *generating* code, crcglot can *compute* CRCs at runtime, and it's fast.

> **Performance, stated honestly:** with the C extension, crcglot computes any of the more than 100 CRCs from Python at compiled-C-class throughput on bulk data (~1.7 GB/s on a 1 MiB buffer, on par with generated C and ahead of generated Rust), and for IEEE CRC-32 / JAMCRC it delegates to the stdlib's hardware path (~tens of GB/s), *faster* than the generated code.  The pure-Python fallback always works but is ~1000× slower.  Two caveats: the "compiled-class" numbers need the extension installed (it ships in the prebuilt wheel), and they hold for bulk/streaming data; many tiny one-shot calls pay Python↔C overhead per call (use the [batch API](#streaming-and-batch) for those).  All figures are platform-specific; see [BENCHMARKS.md](BENCHMARKS.md).

At runtime there's **no variant choice to make**, the same philosophy as `--small`/`--fast` on the generator, taken all the way: you just call `crcglot.generic_crc(data, crc)` (passing a `Crc`, or any `AlgorithmInfo`) and it picks the fastest path available on your machine.  There's no `table=`/`slice8=` knob here; the speed you get depends only on whether the C extension is installed.

Under the hood it dispatches three ways (you never select among them):

1. **IEEE CRC-32 / JAMCRC → stdlib `zlib.crc32`** (hardware CRC folding: PCLMULQDQ on x86, PMULL / `crc32` instructions on ARM): tens of GB/s.  No software CRC out-runs silicon, so crcglot borrows the stdlib's path for the algorithms it covers.
2. **Everything else → the optional C extension** (`crcglot._c`, slice-by-8 / table-driven): ~1-2 GB/s, ~2,000× over pure Python.
3. **No extension built → pure Python**: always works, just slow.

The extension ships in the prebuilt wheels; `pip install crcglot` (or `uv tool install crcglot`) gets it on common platforms, with no extra to enable.  To force a build from source instead of using a prebuilt wheel:

```bash
pip install --no-binary crcglot crcglot
```

It's a single abi3 wheel per platform (CPython 3.11+), and crcglot stays fully functional in pure Python if no wheel matches your platform.

```python
from crcglot import Crc, generic_crc

# One-shot.  crc32 here rides the zlib hardware path automatically.
ieee = Crc(width=32, poly=0x04C11DB7, init=0xFFFFFFFF,
           refin=True, refout=True, xorout=0xFFFFFFFF)
crc = generic_crc(b"123456789", ieee)
```

### Streaming and batch

> **⚠️ Don't call `generic_crc` in a hot loop.**  It's a *one-shot*: for any table/slice-by-8 algorithm (everything byte-aligned except IEEE crc32 / jamcrc, which ride zlib) it **rebuilds the lookup table on every call**, with no cache.  Looping it over many messages of the same algorithm rebuilds the table each iteration, which on small buffers is **4–11× slower than necessary** and only worsens the longer you loop.  **For many CRCs of the same algorithm, build the table once with a `CrcStream` and `update` per message** (and independent streams run fully in parallel across threads).  Use `generic_crc` for a *single* CRC; use streaming for repetition.

Two reasons to use the **streaming** API instead of `generic_crc`: **chunked data** (a message arriving in pieces, such as large files, sockets, or sensor logs) and **repetition** (many messages of the same algorithm, so you build the table once, not per call).  It's the runtime counterpart to the generated `init → update* → finalize` triple.  Bind the algorithm once by catalogue name, feed chunks, and read the finalized value on demand (hashlib idiom: `update` / `digest` / `reset` / `copy`):

```python
from crcglot import crc_stream

s = crc_stream("crc32")           # by catalogue name
for chunk in chunks:              # any chunking — the answer never changes
    s.update(chunk)
s.digest()        # 0xCBF43926 — an int; non-destructive, call it again
s.hexdigest()     # 'cbf43926'
```

`crc_stream` is **backend-smart**, taking the same three-tier dispatch as `generic_crc`: stdlib `zlib.crc32` for IEEE crc32 / jamcrc, the C extension when built, pure-Python otherwise.  So it always works, and is fast where it can be.  For a custom (non-catalogue) CRC, build it from a `Crc` value object (or its raw keyword parameters), or from an `AlgorithmInfo`:

```python
from crcglot import CrcStream, Crc, ALGORITHMS

CrcStream.from_crc(Crc(width=16, poly=0x8005, init=0xFFFF, refin=True, refout=True, xorout=0))
CrcStream(width=16, poly=0x8005, init=0xFFFF, refin=True, refout=True, xorout=0)  # raw kwargs
CrcStream.from_info(ALGORITHMS["crc16-modbus"])
```

For high-volume small-buffer workloads (framed protocols, packet streams, bulk validation) where you have a list of payloads up front, **`generic_crc_many`** CRCs them all in one call, building the lookup table **once** for the whole batch and paying the Python↔C transition once, instead of rebuilding per call the way a loop of `generic_crc` would:

```python
from crcglot import generic_crc_many, ALGORITHMS

a = ALGORITHMS["crc16-modbus"]
results = generic_crc_many(list_of_packets, a)   # one CRC per packet, in order
```

It uses the same dispatch as `generic_crc` (zlib for crc32 / jamcrc, the C extension's `c_crc_many` otherwise, pure-Python fallback), and is exposed over MCP as the `crc_compute_many` tool, so an agent can CRC a whole batch of captured frames in a single tool call.

See [BENCHMARKS.md](BENCHMARKS.md) for measured throughput of each runtime path against the generated-code gallery.

## Example output

See [EXAMPLES.md](EXAMPLES.md) for the actual generated source for `crc32` across every language × implementation combination (C / Rust / Python / VHDL / Verilog / Go / C# / Java / TypeScript crossed with bit-by-bit, table-driven, and slice-by-8 where supported).  Every block is reproducible with one CLI command.

## Benchmarks

See [BENCHMARKS.md](BENCHMARKS.md) for measured `crc32` throughput across every (language × variant) cell at 1 KiB and 1 MiB.  Within each language the trend is monotonic (`bit-by-bit < table < slice-by-8`) but the absolute speedup at each step depends heavily on how well the compiler optimizes the baseline: Rust's LLVM-vectorized bit-by-bit nearly ties its table-driven, while C# / Python see a 10×+ jump just from table-driven because their bitwise loops aren't vectorized.  VHDL and Verilog are excluded: they're simulator references for hardware datapaths, not software runtime.

## When to reach for something else

crcglot tries to be the whole toolbox for CRC *problems*, not the best tool for every CRC-adjacent job.  Two honest pointers:

- **Bulk runtime hashing of non-CRC-32 algorithms:** [anycrc](https://pypi.org/project/anycrc/) computes any ≤64-bit CRC via hardware carry-less multiplication at ~10×, crcglot's C-extension throughput on large in-memory buffers.  If your workload is "checksum gigabytes that are already in RAM with crc16," use it.  (For IEEE CRC-32 crcglot already rides the stdlib's hardware path, and for small framed messages its batch API is the faster of the two.  Behind real file I/O the difference mostly disappears — see [BENCHMARKS.md](BENCHMARKS.md).)  crcglot uses anycrc itself, as one of the two independent engines that generate its reference vectors.
- **Deep reverse-engineering of pathological captures:** [reveng](https://reveng.sourceforge.io/) (the C tool) has decades of accumulated handling for obscure reversal cases.  crcglot's `reverse()` / `crc_reverse` covers the common paths — catalogue identification plus algebraic recovery of custom parameters — but if it comes up empty on a gnarly capture, reveng is the reference instrument, and its catalogue is the source crcglot's own algorithm data derives from.

## Acknowledgments

crcglot builds on:

- **[The reveng CRC catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm)** by Greg Cook: the canonical source of CRC algorithm parameters since 1999, and the source of the more than 100 parameter sets, descriptions, and check values every catalogue entry in crcglot is derived from.
- **[zlib](https://zlib.net/)** by Mark Adler, Jean-loup Gailly et al.: the runtime fast path for CRC-32/ISO-HDLC and JAMCRC, which take the PCLMULQDQ folding path on x86 and the PMULL / `crc32` instructions on ARM.
- **[The Rocksoft Model CRC parameterization](http://ross.net/crc/download/crc_v3.txt)** by Ross N. Williams: the `(width, poly, init, refin, refout, xorout, check)` vocabulary every catalogue entry is expressed in.

`crcglot credits` prints this same content in the terminal, and `crcglot.ATTRIBUTION` / `crcglot.ACKNOWLEDGMENTS` expose it programmatically.

## License

MIT
