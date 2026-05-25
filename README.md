# crcglot

**Verified CRC source code for C, Rust, VHDL, and Python.**  Catalogue-driven, self-test embedded, multi-language by design.

LLMs will gladly write you CRC code.  It might even be right.  `crcglot` guarantees the generated code matches the canonical [reveng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm) test vector (`crc("123456789") == <check value>`) and ships a self-test you can run on your toolchain to prove it.

## Quick start

```bash
pip install crcglot

crcglot c crc32 --slice8 file=mycrc       # → mycrc.h + mycrc.c
crcglot rust crc64-xz --slice8 > mycrc.rs
crcglot vhdl crc32 > mycrc.vhd
crcglot python crc16-modbus > mycrc.py

crcglot list                              # browse the catalogue
crcglot info crc32                        # show parameters
```

## What you get per language

| Function | Purpose |
| --- | --- |
| `<fname>_init` / `_update` / `_finalize` | Streaming triple — feed data chunk by chunk |
| `<fname>` | One-shot wrapper that calls the streaming triple |
| `<fname>_self_test` | Verify against the reveng check value on your toolchain |

C / Rust / VHDL ship `_self_test()` returning 0/1 (or boolean for VHDL).  Python verifies via the docstring's `check:` line — the same interpreter generated it.

## Implementations

| Flag | Description | Width support |
| --- | --- | --- |
| (default) bit-by-bit | Smallest code, zero RAM table, slowest | All |
| `--table` | 256-entry lookup table, 4-8× faster | All |
| `--slice8` | 8 lookup tables, 5-10× faster than `--table` | CRC-32 / CRC-64 only |

Each generated file embeds the chosen implementation and the self-test.  In constrained embedded targets, standard toolchain flags (`-Wl,--gc-sections` for C, LTO for Rust) strip whatever you don't call.

## Custom polynomials

For algorithms not in the catalogue, pass Rocksoft/Williams parameters directly:

```bash
crcglot c --custom width=16 poly=0x1234 init=0xFFFF refin=true refout=true xorout=0x0000 file=mycustom
```

## Catalogue

64+ algorithms covering everything from CRC-8 (ATM, AUTOSAR, Bluetooth, Maxim 1-Wire) through CRC-16 (Modbus, XMODEM, CCITT, IBM SDLC) through CRC-32 (Ethernet, bzip2, iSCSI, AUTOSAR) to CRC-64 (XZ, ECMA-182, NVMe, Redis).  Browse with `crcglot list`.

## Acknowledgments

CRC catalogue data is derived from Greg Cook's [reveng project](https://reveng.sourceforge.io/) — the canonical source for CRC algorithm parameters since 1999.

## License

MIT
