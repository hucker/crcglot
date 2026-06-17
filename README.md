# crcglot

[![PyPI](https://img.shields.io/pypi/v/crcglot)](https://pypi.org/project/crcglot/) ![license](https://img.shields.io/badge/license-MIT-blue) ![tests](https://img.shields.io/badge/tests-6588%20passed-brightgreen) ![coverage](https://img.shields.io/badge/coverage-95%25-brightgreen) ![ruff](https://img.shields.io/badge/ruff-passing-brightgreen) ![ty](https://img.shields.io/badge/ty-passing-brightgreen)

**A multi-language CRC toolkit.**  Generate verified code for C / C++ ⚙️, Rust 🦀, Go 🚦, C# 💠, Java ☕, Python 🐍, TypeScript 🔷, Verilog 🔧, and VHDL 🔌.  Compute, detect, and reverse-engineer CRCs, from Python or over MCP.  Catalogue-driven, execution-verified, self-test embedded.  **Zero runtime dependencies: stdlib only** (an optional bundled C accelerator speeds up runtime computation; everything works without it).

LLMs will gladly write you CRC code.  It might even be right.  `crcglot` doesn't ask you to trust the generator; it proves the output by *running* it: every algorithm, in every variant, in every language, is generated, compiled, and executed against the **hardcoded** canonical [reveng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm) vector (`crc("123456789") == <check value>`).  More than 100 algorithms across nine languages, verified by execution rather than inspection, and every generated file embeds a self-test over four independent reference vectors so you can re-prove it on your own toolchain.

If you work with CRCs, this is most of the toolbox in one install: the package that generates the code also computes, detects, reverse-engineers, and identifies non-CRC trailers, so one tool covers the workflow end to end.  The deliberate exception is bulk runtime hashing throughput; [when to reach for something else](#when-to-reach-for-something-else) names the better tool for that.

crcglot is developed with AI assistance, on these terms: every release gates on the verification matrix below, the reference values come from engines that are not ours, and the suite is yours to run.  If a vector fails, file an issue.

## Quick start

A packet in one hand, a mystery CRC in the other: name it, then generate the verified implementation:

```bash
uv tool install crcglot

crcglot detect --hex "313233343536373839cbf43926"    # what CRC ends this packet?
# crc32  width=32  endianness=big

crcglot c crc32 file=mycrc                           # drop-in C, self-test included
```

That's `mycrc.h` + `mycrc.c`: a verified CRC-32 with a built-in `_self_test()` that re-proves it against four independent reference CRCs on *your* toolchain.

**The whole model is three choices:** which **algorithm** (`crc32`, `crc16-modbus`, … ; `crcglot list` shows the more than 100), which **language** (`c` / `python` / `rust` / `vhdl` / `verilog` / `go` / `csharp` / `java` / `typescript`), and whether you want it **`--fast`** (fastest the target supports, and the default) or **`--small`** (smallest code).  crcglot figures out the implementation details, so you never have to know what "slice-by-8" is.

```bash
crcglot rust crc32 file=mycrc            # fastest Rust crc32 (the default) to mycrc.rs
crcglot c crc8 --small                   # smallest C crc8, to stdout
```

### Installation

```bash
uv tool install crcglot      # the CLI on PATH
uv add crcglot               # as a library in your project
```

Python 3.11+, zero runtime dependencies: `crcglot` imports nothing beyond the standard library.  (The prebuilt wheels bundle an optional C accelerator for runtime CRC computation, covered in [docs/api.md](docs/api.md), so the package is not "pure Python" in the packaging sense, but it runs fully, on any platform, without it.)  Per-target toolchains (`gcc`, `rustc`, `tsx`, `iverilog`, etc.) only matter if you want to *run* the generated code; the generator produces source either way.

## What you get per language

| Function                                 | Purpose                                                          |
| ---------------------------------------- | ---------------------------------------------------------------- |
| `<fname>_init` / `_update` / `_finalize` | Streaming triple; feed data chunk by chunk                       |
| `<fname>`                                | One-shot wrapper that calls the streaming triple                 |
| `<fname>_self_test`                      | Verify against four independent reference CRCs on your toolchain |

Every target ships a runtime-callable `_self_test()`: C returns 0/1; Rust / Go / C# / Java / TypeScript / Python / Verilog / VHDL return `bool` / `boolean` / `bit`.  No `#[cfg(test)]` gating, so you can call it from your release build, a boot self-check, or a startup assertion.  The generated files are also documented (per-language doc-tool styles) and named in each target's idiomatic casing; see [docs/generated-code.md](docs/generated-code.md).

The generated **Python is pure Python**: portable and dependency-free, but interpreted, so it is the slow path.  When you want speed in Python, don't run the generated file -- reach for crcglot's own runtime: `crcglot.compute(data, "crc32")` dispatches to a bundled C extension (near-C speed, on the order of 2,000× the interpreted code), and IEEE CRC-32 rides the stdlib's hardware `zlib.crc32` (CPU CRC instructions, roughly another 30× on top).  Generate the `.py` to port a CRC into a zero-dependency codebase; call the package to compute one fast.  Details in [docs/api.md](docs/api.md).

## How it's verified

**The guarantee is behavioral, not structural.**  crcglot doesn't lint the generated code, it runs it.  Three axes, fully crossed: every one of the **more than 100 algorithms**, in **every variant** the target supports (bit-by-bit, table-driven, slice-by-8), in **every one of the nine languages**, is executed and its output checked against the hardcoded canonical vector.  Nothing ships on "the generator looks correct."

| Language | bit-by-bit | table | slice-by-8 | executed via |
| -------- | :--------: | :---: | :--------: | ------------ |
| C / C++ | ✓ | ✓ | ✓ | `gcc` |
| Rust | ✓ | ✓ | ✓ | `rustc` |
| Go | ✓ | ✓ | ✓ | `go` |
| C# | ✓ | ✓ | ✓ | `dotnet` |
| Java | ✓ | ✓ | ✓ | `javac` + `java` |
| TypeScript | ✓ | ✓ | ✓ | `tsx` (Node) |
| Python | ✓ | ✓ | — | CPython |
| Verilog | ✓ | — | — | `iverilog` |
| VHDL | ✓ | — | — | `ghdl` |

Every ✓ is the **whole catalogue** (all 100+ algorithms) generated, compiled, and executed through that real toolchain in CI, with outputs checked against the reference vectors.  The em-dash cells are variants the target deliberately does not offer, not gaps in coverage.

That cross is also why the test badge reads in the thousands.  The count is not coverage chasing: it is a small set of assertions parametrized over algorithms × languages × variants × reference inputs, because CRC correctness is a finite, enumerable space and covering an enumerable space exhaustively is cheap (the whole cross runs in about a minute).  The number measures the size of the matrix, not the size of the test code.

CI runs the Python-level suite on every push: every algorithm in the reveng catalogue is checked against **four independent reference vectors** (the empty input, the canonical `"123456789"` check string, all 256 byte values, and a 1 KiB pseudo-random pattern), computed by two independent engines that had to agree, so the null, the trivial, and the complex cases are all covered and a silent regression in crcglot's own engine can't hide.  The Python generator is run end-to-end (generated, exec'd, and exercised) against those same vectors.  The slow tier on top of that compiles and executes the generated source for **every** algorithm in C, Rust, Go, C#, Java, TypeScript, Verilog, and VHDL via `gcc` / `rustc` / `go` / `dotnet` / `javac`+`java` / `tsx` (Node) / `iverilog` / `ghdl` and re-checks the runtime result: the same algorithm coverage, exercised through each real toolchain.

Every generated file also ships its own `_self_test()`.  For a catalogue algorithm it now checks **four** fixed inputs (the empty string, `"123456789"`, all 256 byte values, and a 1 KiB pseudo-random pattern), so the byte-table and the high-bit handling get exercised, not just the one short check string.  The two large inputs are regenerated inside the self-test with a byte-at-a-time loop, so the embedded code carries no big array (it stays friendly to flash- and RAM-constrained targets).  Those four reference CRCs are not computed by crcglot: using the engine to grade itself would be circular.  They come from two independent implementations ([anycrc](https://pypi.org/project/anycrc/) and [crccheck](https://pypi.org/project/crccheck/)) that had to agree, anchored to reveng's published value at the check string; both are dev-only tools, so the shipped package keeps its zero-dependency footprint.  In short: crcglot agrees with every reference implementation it can be compared against, including the stdlib's `zlib.crc32`, which it uses directly at runtime for IEEE CRC-32.  A custom (non-catalogue) polynomial has no independent reference, so it falls back to a single check value crcglot computed itself, a weaker check that still catches a toolchain mismatch but, unlike a catalogue algorithm, can't catch an error shared by the generator and the generated code.

**For every target except Python, you should call `_self_test()` once in your build environment**, wired into a unit test, a startup assertion, or your boot self-check.  Our CI proves the generator emits correct code on our reference toolchain; only running `_self_test()` on yours proves your compiler version, optimization flags, target endianness, and integer widths haven't introduced a subtle disagreement.  Python is the exception: the interpreter that ran the CI suite is the one running your code, so the in-environment check would be redundant.

### What the embedded self-test buys you beyond correctness

- **A boot-time integrity check.**  A table-driven CRC carries ~1 KiB of constants in flash, and a corrupted table entry produces silently wrong CRCs forever.  The all-bytes and 1 KiB vectors drive over a thousand lookups through the table, so calling `_self_test()` at startup doubles as a flash-corruption tripwire, not just a build-time sanity check.
- **A self-evidencing artifact.**  An auditor holding the generated file needs no access to crcglot, its CI, or the internet: the file states its claim ("this is CRC-16/CCITT-FALSE") and carries executable acceptance criteria for it, derived from references crcglot didn't compute.  Years later, when nobody remembers how the file was generated, it still proves itself.
- **Tamper-evidence for well-meaning edits.**  Any later hand-edit to the algorithm either keeps the self-test passing or visibly deletes the assertions; both are auditable events in a diff.  Silent drift becomes loud drift.
- **A cleaner story for regulated builds.**  Certification frameworks ask whether your code generator is qualified; the standard alternative is independently verified *output*.  Vectors computed by two independent engines, anchored to a published catalogue, embedded as re-runnable assertions next to the implementation, are that evidence, attached to the artifact rather than the tool.  [docs/certification.md](docs/certification.md) lays the whole story out, including what crcglot deliberately does not claim.

## Use with an MCP client (optional)

`crcglot[mcp]` exposes the toolkit as a [Model Context Protocol](https://modelcontextprotocol.io) server, so an LLM client (Claude Desktop, Cursor, mcp-cli, …) does the judgment (which tool, which parameters, what the result means) while crcglot does the arithmetic: deterministic, catalogue-grounded, execution-verified.  That split matters most where there is nothing for a model to converge on: recovering *unknown* CRC parameters is a search, not a generation task, and a hand-written searcher that's subtly wrong fails silently.

Here's that case end to end, as a chat session.  Every value below is a real tool output: paste the same frames at a connected client and you get the same answers:

> **You:** I captured these frames from a serial link.  Last two bytes look like a CRC but nothing standard matches: `5057523a31322e3430569771`, `544d503a34382e31433d4d`, `52504d3a303031343530da2e`, `5354413a4f4bea3b`
>
> **Claude** *(calls `crc_detect`)*: No catalogue algorithm matches, and it's not a common non-CRC trailer (checksum or digest) either; this is a custom CRC.  *(calls `crc_reverse`)*  Four frames aren't enough to pin the polynomial: I need a few more captures, ideally including two or more of the **same length** with different content.
>
> **You:** Here are six more: `5057523a31322e333856b10d`, `544d503a34382e3343bde8`, `52504d3a303031343438eebc`, `5354413a52554e0492`, `5057523a31322e3431565723`, `4552523a4e4f4e458030`
>
> **Claude** *(calls `crc_reverse`)*: Recovered it: **CRC-16, poly 0xA097, init 0x1D0F, refin/refout true, xorout 0**, CRC field 2 bytes little-endian, verified against a held-out frame.  (Three algebraically equivalent init/xorout labellings also reproduce every frame; all predict identical CRCs, so any of them interoperates.)  Want a drop-in implementation?
>
> **You:** C, for an STM32.
>
> **Claude** *(calls `crc_generate`)*: Here's `vendor_crc.h` / `vendor_crc.c`: table-driven CRC-16 with your recovered parameters and an embedded `vendor_crc_self_test()` you can call at boot.

Four tool calls, no hand-rolled bit arithmetic anywhere, and the artifact carries its own proof.  Note the middle beat: when the data couldn't support an answer, the tool said so and named exactly what was missing.  A deterministic "underdetermined" beats a confident guess.

```bash
uv tool install 'crcglot[mcp]'    # the extra ships the MCP SDK
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

Tools: `crc_list` · `crc_info` · `crc_detect` · `crc_reverse` · `crc_identify_trailer` · `crc_verify` · `crc_encode` · `crc_compute` · `crc_compute_many` · `crc_generate` · `crc_credits`.  Resources: `crcglot://catalogue.json` · `crcglot://languages.json` · `crcglot://variants.json`.  Full reference and Claude Desktop walkthrough live in [docs/MCP.md](docs/MCP.md).

## CLI at a glance

| Subcommand                     | What it does                                                                                                                                                      |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `crcglot list [GLOB]`          | Browse the catalogue (more than 100 algorithms)                                                                                                                   |
| `crcglot info <name>`          | Full Rocksoft/Williams parameters for one algorithm                                                                                                               |
| `crcglot detect`               | Name the catalogue CRC ending a packet (file, hex, or text)                                                                                                       |
| `crcglot identify`             | Name a **non-CRC** trailer: checksum (sum/LRC/XOR/Fletcher/Adler) or digest (MD5/SHA/BLAKE2, full or truncated); notes a likely MAC when nothing matches |
| `crcglot reverse`              | Recover the parameters of an unknown / custom CRC; prints ready-to-paste `--custom` tokens                                                                       |
| `crcglot verify`               | Check a frame's trailing CRC against a named algorithm                                                                                                            |
| `crcglot encode`               | Build a packet by appending the CRC (round-trip partner to `detect`)                                                                                              |
| `crcglot compute`              | The raw CRC integer of some data                                                                                                                                  |
| `crcglot c \| rust \| go \| …` | Generate verified source for that language (`--fast` default, `--small`, `--custom`, bundling, `--comment`, `--naming`)                                           |
| `crcglot credits`              | Acknowledgments for the work crcglot builds on                                                                                                                    |
| `crcglot version`              | Installed crcglot version (the same string stamped into generated code)                                                                                          |

Every option, token, and example lives in [docs/cli.md](docs/cli.md).

## Catalogue

More than 100 algorithms covering everything from CRC-8 (ATM, AUTOSAR, Bluetooth, Maxim 1-Wire) through CRC-16 (Modbus, XMODEM, CCITT, IBM SDLC) through CRC-32 (Ethernet, bzip2, iSCSI, AUTOSAR) to CRC-64 (XZ, ECMA-182, NVMe, Redis), plus the non-byte-aligned families: CAN (CRC-15), CAN FD (CRC-17/21), FlexRay (CRC-11/24), LTE/BLE/OpenPGP (CRC-24), and the GSM/UMTS/CDMA2000 telecom set.  Browse with `crcglot list`.

## Programmatic API

Everything the CLI does is callable from Python, behind two typed registries.  `import crcglot` loads only the compute core (4 modules, ~30 ms); the rest loads on first use.

```python
from crcglot import LANGUAGES, ALGORITHMS

header, source = LANGUAGES["c"].generator("crc32")     # generate
modbus = ALGORITHMS["crc16-modbus"]                    # introspect
print(modbus.width, hex(modbus.check), modbus.desc)
# → 16 0x4b37 Modbus RTU serial protocol
```

`LanguageInfo` carries everything a UI or build script needs per target (extensions, variants, generators, naming/casing helpers); `AlgorithmInfo` is the full parameter set.  Custom polynomials plug into the same generators via `generator_from_entry`.  The full API (registries, custom polys, the runtime engine, streaming and batch) is in [docs/api.md](docs/api.md).

## Fast runtime CRC (optional C extension)

Beyond *generating* code, crcglot *computes* CRCs at runtime:

> **Performance:** with the bundled C extension, crcglot computes any of the more than 100 CRCs from Python at compiled-C-class throughput on bulk data (~1.7 GB/s on a 1 MiB buffer), and for IEEE CRC-32 / JAMCRC it delegates to the stdlib's hardware path (~tens of GB/s).  The pure-Python fallback always works but is ~1000× slower.  The compiled-class numbers hold for bulk/streaming data; many tiny one-shot calls pay Python↔C overhead per call; use the batch API for those.  All figures are platform-specific; see [BENCHMARKS.md](BENCHMARKS.md).

There's no variant knob at runtime: `generic_crc(data, crc)` picks the fastest path available (stdlib `zlib.crc32` for IEEE crc32/jamcrc, the C extension otherwise, pure Python as the universal fallback).  For chunked data or many messages of one algorithm, use the streaming (`crc_stream`) and batch (`generic_crc_many`) APIs; details and the hot-loop warning are in [docs/api.md](docs/api.md).

## Example output and benchmarks

- **[EXAMPLES.md](EXAMPLES.md):** the actual generated source for `crc32` across every language × variant combination; every block reproducible with one CLI command.
- **[BENCHMARKS.md](BENCHMARKS.md):** measured throughput for every (language × variant) cell, plus the runtime engine's paths.

## When to reach for something else

crcglot tries to be the whole toolbox for CRC *problems*, not the best tool for every CRC-adjacent job.  Two pointers:

- **Bulk runtime hashing of non-CRC-32 algorithms:** [anycrc](https://pypi.org/project/anycrc/) computes any ≤64-bit CRC via hardware carry-less multiplication at ~10× crcglot's C-extension throughput on large in-memory buffers.  If your workload is "checksum gigabytes that are already in RAM with crc16," use it.  (For IEEE CRC-32 crcglot already rides the stdlib's hardware path, and for small framed messages its batch API is the faster of the two.  Behind real file I/O the difference mostly disappears; see [BENCHMARKS.md](BENCHMARKS.md).)  crcglot uses anycrc itself, as one of the two independent engines that generate its reference vectors.
- **Deep reverse-engineering of pathological captures:** [reveng](https://reveng.sourceforge.io/) (the C tool) has decades of accumulated handling for obscure reversal cases.  crcglot's `reverse()` / `crc_reverse` covers the common paths (catalogue identification plus algebraic recovery of custom parameters), but if it comes up empty on a gnarly capture, reveng is the reference instrument, and its catalogue is the source crcglot's own algorithm data derives from.

## Acknowledgments

crcglot builds on:

- **[The reveng CRC catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm)** by Greg Cook: the canonical source of CRC algorithm parameters since 1999, and the source of the more than 100 parameter sets, descriptions, and check values every catalogue entry in crcglot is derived from.
- **[zlib](https://zlib.net/)** by Mark Adler, Jean-loup Gailly et al.: the runtime fast path for CRC-32/ISO-HDLC and JAMCRC, which take the PCLMULQDQ folding path on x86 and the PMULL / `crc32` instructions on ARM.
- **[The Rocksoft Model CRC parameterization](http://ross.net/crc/download/crc_v3.txt)** by Ross N. Williams: the `(width, poly, init, refin, refout, xorout, check)` vocabulary every catalogue entry is expressed in.

`crcglot credits` prints this same content in the terminal, and `crcglot.ATTRIBUTION` / `crcglot.ACKNOWLEDGMENTS` expose it programmatically.

## License

MIT
