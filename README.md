# crcglot

[![PyPI](https://img.shields.io/pypi/v/crcglot)](https://pypi.org/project/crcglot/) ![license](https://img.shields.io/badge/license-MIT-blue) ![Py 3.11](https://img.shields.io/badge/Py%203.11-passing-brightgreen "8477 tests pass on CPython 3.11") ![Py 3.12](https://img.shields.io/badge/Py%203.12-passing-brightgreen "8477 tests pass on CPython 3.12") ![Py 3.13](https://img.shields.io/badge/Py%203.13-passing-brightgreen "8477 tests pass on CPython 3.13") ![Py 3.14](https://img.shields.io/badge/Py%203.14-passing-brightgreen "8477 tests pass on CPython 3.14") ![coverage](https://img.shields.io/badge/coverage-95%25-brightgreen) ![ruff](https://img.shields.io/badge/ruff-passing-brightgreen) ![ty](https://img.shields.io/badge/ty-passing-brightgreen)

**The CRC backend an AI assistant can delegate to.**  Deterministic, reveng-anchored CRC answers: compute, detect, reverse-engineer, and verify CRCs from a catalogue of 100+ algorithms, plus execution-verified code generation for C / C++ ⚙️, Rust 🦀, Go 🚦, C# 💠, Java ☕, Python 🐍, TypeScript 🔷, Verilog 🔧, and VHDL 🔌.  Call it over MCP, from the CLI, or in Python.  **Zero-dependency core: stdlib only** (an optional bundled C accelerator speeds up computation; the optional MCP server adds the MCP SDK).

Reach for it when a CRC crosses a boundary you don't control: a device to talk to, a capture to identify, an unknown checksum to reverse-engineer, or a verified implementation to drop into firmware.  CRC code is easy to write and hard to trust, and the verification is what this package is really selling: answers come from the published [reveng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm), everything it generates was compiled and executed against independent reference vectors before release, and every file embeds a self-test so you can re-run the check on your own toolchain.  For bulk runtime hashing throughput, [when to reach for something else](#when-to-reach-for-something-else) names the better tool.

## Quick start

A packet in one hand, a mystery CRC in the other: name it, then generate the verified implementation:

```bash
uv tool install crcglot
```

```console
$ crcglot detect --hex "313233343536373839cbf43926"
crc32  width=32  endianness=big  form=hex  separator=''  prefix=''  per_byte=False  uppercase=False

$ crcglot c crc32 file=mycrc
Note: Faster CRC-32 path on C / C++: zlib's `crc32()` (`<zlib.h>`).  The generated code is fine for small messages, but for large files or streaming throughput prefer that library; it uses CPU CRC instructions where the processor supports them.
Wrote <your dir>\mycrc.h
Wrote <your dir>\mycrc.c
```

That's `mycrc.h` + `mycrc.c`: a verified CRC-32 with a built-in `_self_test()` that re-checks it against four independent reference CRCs on *your* toolchain.

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
| `<fname>_self_test`                      | Verify against embedded reference CRCs on your toolchain (what each target checks: [docs/generated-code.md](docs/generated-code.md#the-embedded-self-test)) |

Every target ships a runtime-callable `_self_test()`: C returns 0/1; Rust / Go / C# / Java / TypeScript / Python / Verilog / VHDL return `bool` / `boolean` / `bit`.  No `#[cfg(test)]` gating, so you can call it from your release build, a boot self-check, or a startup assertion.  The generated files are also documented (per-language doc-tool styles) and named in each target's idiomatic casing; see [docs/generated-code.md](docs/generated-code.md).

The generated **Python is pure Python**: portable and dependency-free, but interpreted, so it is the slow path.  Generate the `.py` to port a CRC into a zero-dependency codebase; to *compute* fast in Python, call crcglot's own runtime instead ([below](#fast-runtime-crc-optional-c-extension)).

## How it's verified

**The guarantee is behavioral, not structural.**  crcglot doesn't lint the generated code, it runs it: every one of the more than 100 algorithms, in every variant a target supports, in every one of the nine languages, is generated, compiled through its real toolchain (`gcc`, `rustc`, `go`, `dotnet`, `javac`, `tsx`, `iverilog`, `ghdl`), and executed against reference vectors computed by two engines that are not ours.  Ten categories of evidence make up the verification matrix, from reference vectors through adversarial review, and all of it is yours to re-run.  [docs/verification/index.md](docs/verification/index.md) explains the review model and maps every category to the tests that carry it.

Every generated file also embeds a `_self_test()` over independent reference vectors.  **Call it once in your build environment** (a unit test, a startup assertion, a boot check): our CI verifies the generator's output on our reference toolchain, and only running the self-test on yours confirms your compiler, flags, endianness, and integer widths agree.  What it checks, where its expected values come from, and what it buys you beyond correctness (a boot-time integrity check, auditability, a cleaner story for regulated builds) are in [docs/generated-code.md](docs/generated-code.md#the-embedded-self-test).

## Use it with Claude (and any MCP client)

`crcglot[mcp]` exposes the toolkit as a [Model Context Protocol](https://modelcontextprotocol.io) server, so an assistant (Claude Desktop, Claude Code, Cursor, mcp-cli, …) does the judgment (which tool, which parameters, what the result means) while crcglot does the arithmetic.  The hardest case, recovering an unknown custom CRC from captured frames, is worked end to end as a real chat session in [docs/MCP.md](docs/MCP.md#end-to-end-examples).

```bash
uv tool install 'crcglot[mcp]'    # the extra ships the MCP SDK
```

Then wire it in.  Claude Code, one command:

```bash
claude mcp add crcglot -- uvx --from 'crcglot[mcp]' crcglot-mcp
```

Claude Desktop (and other clients), via `claude_desktop_config.json`:

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

Tools: `crc_list` · `crc_info` · `crc_self_test_vectors` · `crc_detect` · `crc_reverse` · `crc_identify_trailer` · `crc_verify` · `crc_encode` · `crc_compute` · `crc_compute_many` · `crc_generate` · `crc_credits`.  Resources: `crcglot://catalogue.json` · `crcglot://languages.json` · `crcglot://variants.json`.  Full reference and setup walkthrough live in [docs/MCP.md](docs/MCP.md).

## CLI at a glance

| Subcommand                     | What it does                                                                                                                                                      |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `crcglot list [GLOB]`          | Browse the catalogue (more than 100 algorithms)                                                                                                                   |
| `crcglot info <name>`          | Full Rocksoft/Williams parameters for one algorithm                                                                                                               |
| `crcglot detect`               | Name the catalogue CRC ending a packet (file, hex, text, or a crclink JSON frame)                                                                                |
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

Pointing an LLM or coding agent at crcglot?  Start with [llms.txt](llms.txt): a concise, linked map of what the package does and where to look, to load first instead of crawling the source.

## Fast runtime CRC (optional C extension)

Beyond *generating* code, crcglot *computes* CRCs at runtime.  With the bundled C extension, any of the more than 100 CRCs runs from Python at compiled-C-class throughput on bulk data (~1.7 GB/s on a 1 MiB buffer), and IEEE CRC-32 / JAMCRC ride the stdlib's hardware path (tens of GB/s); the pure-Python fallback always works, far more slowly.  `generic_crc(data, crc)` picks the fastest path available, with no variant knob.  Streaming (`crc_stream`) and batch (`generic_crc_many`) APIs, the hot-loop warning, and the dispatch details are in [docs/api.md](docs/api.md); measured figures are in [BENCHMARKS.md](BENCHMARKS.md).

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
