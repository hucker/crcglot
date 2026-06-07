# crcglot

![tests](https://img.shields.io/badge/tests-3594%20passed-brightgreen)
![coverage](https://img.shields.io/badge/coverage-94%25-brightgreen)
![ruff](https://img.shields.io/badge/ruff-passing-brightgreen)
![ty](https://img.shields.io/badge/ty-passing-brightgreen)

**Verified CRC source code for C / C++ ⚙️, Rust 🦀, Go 🚦, C# 💠, Java ☕, Python 🐍, TypeScript 🔷, Verilog 🔧, and VHDL 🔌.**  Catalogue-driven, execution-verified, self-test embedded, multi-language by design.  **Pure-stdlib package — zero runtime dependencies.**

LLMs will gladly write you CRC code.  It might even be right.  `crcglot` doesn't ask you to trust the generator — it proves the output by *running* it: every algorithm, in every variant, in every language, is generated, compiled, and executed against the **hardcoded** canonical [reveng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm) vector (`crc("123456789") == <check value>`).  More than 70 algorithms across nine languages, verified by execution rather than inspection — and every generated file ships that same self-test so you can re-prove it on your own toolchain.

## Quick start

```bash
uv tool install crcglot         # or: pip install crcglot
crcglot c crc32 file=mycrc
```

That's it.  You now have `mycrc.h` and `mycrc.c` — drop-in CRC-32 with a built-in `_self_test()` you can call to verify it matches the canonical [reveng](https://reveng.sourceforge.io/crc-catalogue/all.htm) check value.

**The whole model is three choices:** which **algorithm** (`crc32`, `crc16-modbus`, … — `crcglot list` shows the more than 70), which **language** (`c` / `python` / `rust` / `vhdl` / `verilog` / `go` / `csharp` / `java` / `typescript`), and whether you want it **`--small`** (smallest code, the default) or **`--fast`** (fastest the target supports).  crcglot figures out the implementation details — you never have to know what "slice-by-8" is.

```bash
crcglot rust crc32 --fast file=mycrc     # fastest Rust crc32 to file mycrc.rs
crcglot c crc8 --small                   # smallest C crc8, to stdout
```

### Installation

| Tool                  | Command                   | Use when                                                                                            |
| --------------------- | ------------------------- | --------------------------------------------------------------------------------------------------- |
| **uv (recommended)**  | `uv tool install crcglot` | You just want the `crcglot` CLI on PATH.  Isolated install, no global pollution.                    |
| **uv (as a library)** | `uv add crcglot`          | You're calling the generators from Python (e.g. a build script that emits CRC code into your repo). |
| **pip**               | `pip install crcglot`     | You don't have `uv`.  Identical package, slower install.                                            |
| **pipx**              | `pipx install crcglot`    | Same isolation story as `uv tool`, if pipx is what you have.                                        |

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

Every target ships a runtime-callable `_self_test()`: C returns 0/1; Rust / Go / C# / Java / TypeScript / Python / Verilog / VHDL return `bool` / `boolean` / `bit`.  No `#[cfg(test)]` gating — call it from your release build, a boot self-check, or a startup assertion.

## How it's verified

**The guarantee is behavioral, not structural** — crcglot doesn't lint the generated code, it runs it.  Three axes, fully crossed: every one of the **more than 70 algorithms**, in **every variant** the target supports (bit-by-bit, table-driven, slice-by-8), in **every one of the nine languages**, is executed and its output checked against the hardcoded canonical vector.  Nothing ships on "the generator looks correct."

CI runs the Python-level suite on every push: every algorithm in the reveng catalogue is checked against its **hardcoded** canonical check value — not the catalogue's own `check` field, so a silent regression in the engine can't hide — and the Python generator is run end-to-end (generated, exec'd, and called on `b"123456789"`) against the same hardcoded vectors.  The slow tier on top of that compiles and executes the generated source for **every** algorithm in C, Rust, Go, C#, Java, TypeScript, Verilog, and VHDL via `gcc` / `rustc` / `go` / `dotnet` / `javac`+`java` / `tsx` (Node) / `iverilog` / `ghdl` and re-checks the runtime result — same algorithm coverage, exercised through each real toolchain.

Every generated file also ships its own `_self_test()` carrying that same canonical vector.  **For every target except Python, you should call `_self_test()` once in your build environment** — wire it into a unit test, a startup assertion, or your boot self-check.  Our CI proves the generator emits correct code on our reference toolchain; only running `_self_test()` on yours proves your compiler version, optimization flags, target endianness, and integer widths haven't introduced a subtle disagreement.  Python is the exception: the interpreter that ran the CI suite is the one running your code, so the in-environment check would be redundant.

## Documentation comments

The generated code isn't just correct — it's **documented**.  Every file gets a header (algorithm parameters, a copy-paste streaming example, the self-test contract) and a doc comment above each of the five functions, so a reader learns the `init → update* → finalize` streaming contract from the source, not from the tests.  Pick the convention with `--comment=<style>`; `plain` (clean human-readable comments in each language's native syntax) is the default, and every language also has its idiomatic doc-tool style:

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

crcglot offers each language only the styles its doc-tool actually understands — `crcglot rust --comment=doxygen` is rejected, because Doxygen doesn't read Rust.  The matrix is derived from the styles themselves; nothing hardcodes it.

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

The generators take the chosen name directly — `LANGUAGES["python"].generator("crc32", comment_style="numpy")` — and the same `{name, label, description}` records are served over MCP in the `crcglot://languages.json` resource (each language's `comment_styles`).

### Why generate the docs instead of asking an LLM?

You still can — point an LLM at the output and let it write whatever prose you like; nothing here stops you.  But the generated code is **fully known**: the parameters, the API contract, and the streaming semantics are deterministic facts, so the *documentation* can be deterministic too.  That buys three things an LLM pass can't:

- **Reproducible.**  The same request produces the same comment, byte for byte.  Everyone who generates `crc32` gets the *identical* documentation — no drift, no "it phrased it differently this time," no diff churn between two runs.
- **Correct by construction — or wrong in exactly one place.**  The comment is rendered from the same source of truth as the code, so it can't hallucinate a parameter or misdescribe the API.  And if a description *is* wrong, it's wrong *uniformly* — caught once, fixed once in the generator, and the fix reaches every output everywhere.  Per-invocation LLM wrongness is the opposite: subtly different each time, and far harder to audit.
- **Free, offline, auditable.**  No API call, no token cost, no network — it runs in CI and on an air-gapped build.  A reviewer (the class-III-medical-device kind) audits the comment generator *once* and can then trust every file it emits.

Layer an LLM on top when you want richer prose.  The point is that the *baseline* everyone ships by default is deterministic, uniform, and reviewable.

## Naming conventions

The generated public functions read like hand-written code in each target: Go and C# get `PascalCase` (`Crc16ModbusUpdate`), Java and TypeScript get `camelCase` (`crc16ModbusUpdate`), and C, Rust, Python, Verilog, and VHDL get `snake_case` (`crc16_modbus_update`).  Those are the **defaults** — a linter (`govet`, StyleCop, ESLint, …) won't flag the output.  Override with `--naming=<convention>`; each language offers only the conventions its ecosystem actually uses (C is a free-for-all, Python and Rust are snake-only):

| Language       | default      | `--naming` choices         |
| -------------- | ------------ | -------------------------- |
| C / C++ ⚙️     | `snake`      | `snake`, `camel`, `pascal` |
| C# 💠          | `pascal`     | `pascal`, `camel`          |
| Go 🚦          | `pascal`     | `pascal`, `camel`          |
| Java ☕         | `camel`      | `camel`, `pascal`          |
| TypeScript 🔷  | `camel`      | `camel`, `snake`, `pascal` |
| Rust 🦀        | `snake`      | `snake`                    |
| Python 🐍      | `snake`      | `snake`                    |
| Verilog 🔧 / VHDL 🔌 | `snake` | `snake`                   |

```bash
crcglot go crc32 --naming camel          # crc32Update instead of Crc32Update
crcglot c crc32 --naming pascal          # Crc32Update; the CRC32_H guard stays SCREAMING_SNAKE
```

Only the public function/method names are re-cased — header guards, table symbols, package names, and class names keep their own fixed idiom.  An explicit `symbol=NAME` is emitted verbatim, bypassing `--naming`.  As with `--comment`, `crcglot rust crc32 --naming=pascal` is rejected (Rust is snake-only); the matrix is derived from `LANGUAGES[code].naming`, nothing hardcodes it.  The same `{name, label, description}` records are served over MCP in `crcglot://languages.json` (each language's `naming` + `default_naming`).

## CLI reference

```text
crcglot <command> [options...]
```

### `crcglot list [GLOB] [--json]`

Browse the catalogue.  Optional `GLOB` filters by shell-style pattern (e.g. `crc16-*`).  Exit code 1 if nothing matches.

```bash
crcglot list                # more than 70 algorithms
crcglot list 'crc32-*'      # just the CRC-32 family
crcglot list --json         # machine-readable list with full parameters
```

### `crcglot info <name>`

Print parameters (width, poly, init, refin, refout, xorout, check, desc) for one algorithm.  Exit 1 on unknown name.

```bash
crcglot info crc64-xz
```

### `crcglot detect [INPUTS...]`

Brute-force identify which catalogue CRC matches a packet whose tail is the CRC.  Useful for reverse-engineering unfamiliar protocols, debugging captured frames, or confirming a sample really uses the CRC you think it does.

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

`--match` selects the strategy: `first` (default — early-stop on the first hit, priority order is `crc32`, `crc32-jamcrc`, `crc32-iscsi`, then the rest of the catalogue), `all` (exhaustive forensic view), `set` (strict singleton: succeed only if exactly one algorithm survives across all packets).  Exit 0 on match, 1 otherwise.  For text packets the inferred separator + hex leader + case are reported so you can reproduce the same format via `crcglot encode`.

### `crcglot encode <algorithm> [<data>]`

Build a packet by appending the CRC.  Round-trip partner to `detect` — feed `detect`'s `(algorithm, endianness, padding)` shape back to `encode` to rebuild a packet in the same format.

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

### `crcglot credits`

Print acknowledgments for the upstream work crcglot stands on (also exported as `crcglot.ATTRIBUTION` / `crcglot.ACKNOWLEDGMENTS`).  See [Acknowledgments](#acknowledgments).

### `crcglot {c | csharp | go | java | python | rust | typescript | verilog | vhdl} <algorithm> [<algorithm>...] [options...] [tokens...]`

Generate source code for the chosen target language.  Pick your intent — crcglot picks the implementation:

| Option / token    | Effect                                                                                                                    |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `--small`         | Smallest code, zero RAM table (bit-by-bit).  **The default** — works for any width.                                       |
| `--fast`          | Fastest the target supports: slice-by-8 for width 32/64 on compiled targets, table-driven otherwise.                      |
| `--custom`        | Use raw Rocksoft/Williams params instead of a catalogue lookup (see below).                                               |
| `--comment=STYLE` | Documentation style for the generated comments (default `plain`).  See [Documentation comments](#documentation-comments). |
| `--naming=CONVENTION` | Casing of the public function/method names (`snake` / `camel` / `pascal`).  Defaults to each language's idiomatic convention.  See [Naming conventions](#naming-conventions). |
| `file=STEM`       | Write to disk (extension picked per language; see below).  Omit for stdout.                                               |
| `symbol=NAME`     | Override the emitted function name (emitted verbatim, bypassing `--naming`).  Default: derived from algorithm, or from `file=STEM` if given. |

File extensions per language: C emits `STEM.h` + `STEM.c`; Python `.py`; Rust `.rs`; VHDL `.vhd`; Verilog `.sv` (SystemVerilog 2012); Go `.go`; C# `.cs`; Java `.java`; TypeScript `.ts`.  (For Java, every algorithm shares one container class named after `STEM`, so the stem must be a valid Java identifier.)

**Bundle several algorithms into one file** by naming more than one — `crcglot c crc32 crc16-modbus crc8 file=mycrcs` writes a single `mycrcs.h` / `mycrcs.c` containing all three (one `.go` / `.rs` / `.cs` / … for the other languages).  Each algorithm keeps its own catalogue-derived function names (`crc32`, `crc16_modbus`, …) and the tables are namespaced per symbol, so they never collide.  `symbol=` is rejected with more than one algorithm (it names a single function); duplicates are de-duplicated; an unknown name aborts the whole bundle.

**Expert overrides** (you usually don't need these — `--fast` chooses for you): `--table` forces the 256-entry single-table form, and `--slice8` forces the 8-table form.  They exist for the rare case where you want the *middle* of the size/speed curve explicitly — e.g. a RAM-constrained target where the 1 KiB table is fine but slice-by-8's 8 KiB isn't.  `--slice8` is CRC-32/64 + compiled targets only.

Rules:

- The variant selectors `--small` / `--fast` / `--table` / `--slice8` are mutually exclusive — pick at most one (exit 2 otherwise).  No selector = `--small`.
- `--slice8 python` silently falls back to `--table` (CPython's per-int overhead eats the slice-by-8 speedup; stderr warns).  `--fast` never needs this fallback — it only picks slice-by-8 where it actually applies.
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

More than 70 algorithms covering everything from CRC-8 (ATM, AUTOSAR, Bluetooth, Maxim 1-Wire) through CRC-16 (Modbus, XMODEM, CCITT, IBM SDLC) through CRC-32 (Ethernet, bzip2, iSCSI, AUTOSAR) to CRC-64 (XZ, ECMA-182, NVMe, Redis).  Browse with `crcglot list`.

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
    # → ☕ Java          ('.java',)    ['bitwise', 'slice8', 'table']
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
- `combiner(outputs, stem)` — merge several generator outputs into one file (powers multi-algorithm bundling); per-symbol tables keep the merge collision-free
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

## Use with an MCP client (optional)

`crcglot[mcp]` exposes the CLI surface as a [Model Context Protocol](https://modelcontextprotocol.io) server so LLM clients (Claude Desktop, Cursor, mcp-cli, …) can call `crc_detect` / `crc_compute` / `crc_generate` etc. as named tools.  The LLM never has to remember a polynomial, slice bytes off a packet to find the CRC, or write a reflection loop — it asks crcglot.

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

Tools: `crc_list` · `crc_info` · `crc_detect` · `crc_encode` · `crc_compute` · `crc_generate` · `crc_credits`.  Resources: `crcglot://catalogue.json` · `crcglot://languages.json` · `crcglot://variants.json`.  Full reference and Claude Desktop walkthrough live in [docs/MCP.md](docs/MCP.md).

## Fast runtime CRC (optional C extension)

Beyond *generating* code, crcglot can *compute* CRCs at runtime — and it's fast.

> **Performance, stated honestly:** with the C extension, crcglot computes any of the more than 70 CRCs from Python at compiled-C-class throughput on bulk data (~1.7 GB/s on a 1 MiB buffer — on par with generated C and ahead of generated Rust), and for IEEE CRC-32 / JAMCRC it delegates to the stdlib's hardware path (~tens of GB/s), *faster* than the generated code.  The pure-Python fallback always works but is ~1000× slower.  Two caveats: the "compiled-class" numbers need the extension installed (the wheel / `crcglot[fast]`), and they hold for bulk/streaming data — many tiny one-shot calls pay Python↔C overhead per call (use the [batch API](#streaming-and-batch) for those).  All figures are platform-specific; see [BENCHMARKS.md](BENCHMARKS.md).

At runtime there's **no variant choice to make** — the same philosophy as `--small`/`--fast` on the generator, taken all the way: you just call `crcglot.generic_crc(data, width, poly, init, refin, refout, xorout)` and it picks the fastest path available on your machine.  There's no `table=`/`slice8=` knob here; the speed you get depends only on whether the C extension is installed.

Under the hood it dispatches three ways (you never select among them):

1. **IEEE CRC-32 / JAMCRC → stdlib `zlib.crc32`** (hardware CRC folding — PCLMULQDQ on x86, PMULL / `crc32` instructions on ARM): tens of GB/s.  No software CRC out-runs silicon, so crcglot borrows the stdlib's path for the algorithms it covers.
2. **Everything else → the optional C extension** (`crcglot._c`, slice-by-8 / table-driven): ~1-2 GB/s, ~2,000× over pure Python.
3. **No extension built → pure Python**: always works, just slow.

The extension ships in the prebuilt wheels (`pip install crcglot` gets it on common platforms).  To force it / pull the build deps explicitly:

```bash
uv tool install "crcglot[fast]"     # or: pip install "crcglot[fast]"
```

It's a single abi3 wheel per platform (CPython 3.11+), and crcglot stays fully functional in pure Python if no wheel matches your platform.

```python
from crcglot import generic_crc

# One-shot.  crc32 here rides the zlib hardware path automatically.
crc = generic_crc(b"123456789", 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)
```

### Streaming and batch

> **⚠️ Don't call `generic_crc` in a hot loop.**  It's a *one-shot*: for any table/slice-by-8 algorithm (everything byte-aligned except IEEE crc32 / jamcrc, which ride zlib) it **rebuilds the lookup table on every call** — there is no cache.  Looping it over many messages of the same algorithm rebuilds the table each iteration, which on small buffers is **4–11× slower than necessary** and only worsens the longer you loop.  **For many CRCs of the same algorithm, build the table once with a `CrcStream` and `update` per message** (and independent streams run fully in parallel across threads).  Use `generic_crc` for a *single* CRC; use streaming for repetition.

Two reasons to use the **streaming** API instead of `generic_crc`: **chunked data** (a message arriving in pieces — large files, sockets, sensor logs) and **repetition** (many messages of the same algorithm — build the table once, not per call).  It's the runtime counterpart to the generated `init → update* → finalize` triple.  Bind the algorithm once by catalogue name, feed chunks, and read the finalized value on demand (hashlib idiom: `update` / `digest` / `reset` / `copy`):

```python
from crcglot import crc_stream

s = crc_stream("crc32")           # by catalogue name
for chunk in chunks:              # any chunking — the answer never changes
    s.update(chunk)
s.digest()        # 0xCBF43926 — an int; non-destructive, call it again
s.hexdigest()     # 'cbf43926'
```

`crc_stream` is **backend-smart**, taking the same three-tier dispatch as `generic_crc`: stdlib `zlib.crc32` for IEEE crc32 / jamcrc, the C extension when built, pure-Python otherwise — so it always works, and is fast where it can be.  For a custom (non-catalogue) CRC, construct from raw parameters (signature matches the C extension's) or from an `AlgorithmInfo`:

```python
from crcglot import CrcStream, ALGORITHMS

CrcStream(width=16, poly=0x8005, init=0xFFFF, refin=True, refout=True, xorout=0)
CrcStream.from_info(ALGORITHMS["crc16-modbus"])
```

For high-volume small-buffer workloads, the C extension CRCs many buffers in a single Python↔C transition (the win for framed protocols / packet streams):

```python
from crcglot import _c   # present iff the extension is installed

results = _c.c_crc_many(list_of_packets, 32, 0x04C11DB7, 0xFFFFFFFF,
                        True, True, 0xFFFFFFFF)
```

See [BENCHMARKS.md](BENCHMARKS.md) for measured throughput of each runtime path against the generated-code gallery.

## Example output

See [EXAMPLES.md](EXAMPLES.md) for the actual generated source for `crc32` across every language × implementation combination (C / Rust / Python / VHDL / Verilog / Go / C# / Java / TypeScript crossed with bit-by-bit, table-driven, and slice-by-8 where supported).  Every block is reproducible with one CLI command.

## Benchmarks

See [BENCHMARKS.md](BENCHMARKS.md) for measured `crc32` throughput across every (language × variant) cell at 1 KiB and 1 MiB.  Within each language the trend is monotonic (`bit-by-bit < table < slice-by-8`) but the absolute speedup at each step depends heavily on how well the compiler optimizes the baseline — Rust's LLVM-vectorized bit-by-bit nearly ties its table-driven, while C# / Python see a 10×+ jump just from table-driven because their bitwise loops aren't vectorized.  VHDL and Verilog are excluded: they're simulator references for hardware datapaths, not software runtime.

## Acknowledgments

crcglot stands on:

- **[The reveng CRC catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm)** by Greg Cook — the canonical source of CRC algorithm parameters since 1999, and the source of the more than 70 parameter sets, descriptions, and check values every catalogue entry in crcglot is derived from.
- **[zlib](https://zlib.net/)** by Mark Adler, Jean-loup Gailly et al. — the runtime fast path for CRC-32/ISO-HDLC and JAMCRC, which take the PCLMULQDQ folding path on x86 and the PMULL / `crc32` instructions on ARM.
- **[The Rocksoft Model CRC parameterization](http://ross.net/crc/download/crc_v3.txt)** by Ross N. Williams — the `(width, poly, init, refin, refout, xorout, check)` vocabulary every catalogue entry is expressed in.

`crcglot credits` prints this same content in the terminal, and `crcglot.ATTRIBUTION` / `crcglot.ACKNOWLEDGMENTS` expose it programmatically.

## License

MIT
