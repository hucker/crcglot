# crcglot

![tests](https://img.shields.io/badge/tests-1994%20passed-brightgreen)
![coverage](https://img.shields.io/badge/coverage-99%25-brightgreen)
![ruff](https://img.shields.io/badge/ruff-passing-brightgreen)
![ty](https://img.shields.io/badge/ty-passing-brightgreen)

**Verified CRC source code for C, Rust, VHDL, Python, Go, C#, and Zig.**  Catalogue-driven, self-test embedded, multi-language by design.

LLMs will gladly write you CRC code.  It might even be right.  `crcglot` guarantees the generated code matches the canonical [reveng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm) test vector (`crc("123456789") == <check value>`) and ships a self-test you can run on your toolchain to prove it.

## Quick start

```bash
pip install crcglot

crcglot c crc32 --slice8 file=mycrc       # → mycrc.h + mycrc.c
crcglot rust crc64-xz --slice8 > mycrc.rs
crcglot vhdl crc32 > mycrc.vhd
crcglot python crc16-modbus > mycrc.py
crcglot go crc32 --table > crc32.go
crcglot csharp crc16-modbus > Crc16Modbus.cs
crcglot zig crc64-xz --table > crc64.zig

crcglot list                              # browse the catalogue
crcglot info crc32                        # show parameters
```

## What you get per language

| Function                                 | Purpose                                                 |
| ---------------------------------------- | ------------------------------------------------------- |
| `<fname>_init` / `_update` / `_finalize` | Streaming triple — feed data chunk by chunk             |
| `<fname>`                                | One-shot wrapper that calls the streaming triple        |
| `<fname>_self_test`                      | Verify against the reveng check value on your toolchain |

Every target ships `_self_test()`: C returns 0/1, Rust emits a `#[cfg(test)]` block discovered by `cargo test`, VHDL / Python / Go / C# / Zig return `boolean` / `bool`.

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

### `crcglot {c | csharp | go | python | rust | vhdl | zig} <algorithm> [options...] [tokens...]`

Generate source code for the chosen target language.

| Option / token       | Effect                                                                                              |
| -------------------- | --------------------------------------------------------------------------------------------------- |
| (default) bit-by-bit | Smallest code, zero RAM table, slowest.  All widths.                                                |
| `--table`            | 256-entry lookup table, 4-8× faster.  All widths.                                                   |
| `--slice8`           | 8 lookup tables, 5-10× faster than `--table`.  CRC-32 / CRC-64 only.  C / Rust only.                |
| `--custom`           | Use raw Rocksoft/Williams params instead of a catalogue lookup (see below).                         |
| `file=STEM`          | Write to disk (extension picked per language; see below).  Omit for stdout.                         |
| `symbol=NAME`        | Override the emitted function name.  Default: derived from algorithm, or from `file=STEM` if given. |

File extensions per language: C emits `STEM.h` + `STEM.c`; Python `.py`; Rust `.rs`; VHDL `.vhd`; Go `.go`; C# `.cs`; Zig `.zig`.

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

The check value for the custom parameters is computed automatically (`_generic_crc(b"123456789", ...)`) and embedded into the generated `_self_test()`.

## Catalogue

64+ algorithms covering everything from CRC-8 (ATM, AUTOSAR, Bluetooth, Maxim 1-Wire) through CRC-16 (Modbus, XMODEM, CCITT, IBM SDLC) through CRC-32 (Ethernet, bzip2, iSCSI, AUTOSAR) to CRC-64 (XZ, ECMA-182, NVMe, Redis).  Browse with `crcglot list`.

## Example output

See [EXAMPLES.md](EXAMPLES.md) for the actual generated source for `crc32` across every language × implementation combination (C / Rust / Python / VHDL / Go / C# / Zig crossed with bit-by-bit, table-driven, and slice-by-8 where supported).  Every block is reproducible with one CLI command.

## Acknowledgments

CRC catalogue data is derived from Greg Cook's [reveng project](https://reveng.sourceforge.io/) — the canonical source for CRC algorithm parameters since 1999.

## License

MIT
