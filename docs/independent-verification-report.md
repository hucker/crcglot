# Independent verification report

Date: 2026-06-18. Target: crcglot 0.23.0.

The two findings below were addressed for crcglot 0.24.0; the Resolution section at the end records how each was closed.

This report records an external check of crcglot that deliberately does not rely on the bundled test suite. The goal was to answer six questions from scratch: do the features compute what they claim, does the generated code work in every language, does the C accelerator deliver compiled-class speed, can it reverse-engineer a CRC, can it stand in for the common CRC tools, and are there bugs or unsafe code. Every correctness claim below is graded against references that do not come from crcglot.

## Method

The check builds its trust outside the package first. The oracle is an independent CRC engine, written from scratch in a different style than crcglot's: a bit-at-a-time LFSR, where crcglot uses byte-aligned tables and a reflected-polynomial trick. It earns that role only after reproducing two external references: the standard library's `zlib.crc32`, and eleven published catalogue check constants supplied by hand (CRC-8/SMBUS 0xF4, CRC-16/CCITT-FALSE 0x29B1, CRC-16/MODBUS 0x4B37, CRC-16/XMODEM, CRC-16/KERMIT, CRC-32/ISO-HDLC, CRC-32C, CRC-32/BZIP2, CRC-64/XZ, CRC-5/USB, CRC-7/MMC). It matched all of them, across reflected and non-reflected algorithms and widths from 5 to 64 bits. Only then does it grade crcglot.

The generated code is checked by execution, not inspection. Each language's output goes through its real toolchain and has to pass two checks: the embedded `self_test()`, and a CRC over a novel input string (`Verify-CRCglot-Independently!`) compared against the oracle. The toolchains were gcc 15.2, rustc 1.95, go 1.26, dotnet 9, javac/java 21, tsx on Node 24, iverilog 12, and ghdl 5.1.

## Results by question

**Features compute what they claim.** All 113 catalogue check values match the oracle. The runtime dispatch path (`compute` / `generic_crc`, which routes to `zlib`, then the C extension, then the pure-Python fallback) was graded over 113 algorithms times four inputs each (empty, the canonical check string, all 256 byte values, and a 1 KiB random pattern) with zero mismatches. The C extension and the pure-Python engine agree bit-for-bit across the same matrix.

**The multi-language code works in eight of nine languages.** C, Rust, Go, Java, TypeScript, Python, Verilog, and VHDL each compiled, ran, passed their embedded self-test, and reproduced the oracle on the novel input. C# is the exception: its generated source does not compile. See Finding 1.

**The C extension delivers compiled-class speed.** Measured on a 1 MiB buffer: crc64-xz on the slice-by-8 path reached about 1.9 GB/s, crc16-modbus on the table path about 540 MB/s, and IEEE crc32 over the standard library's hardware path about 53 GB/s. The C engine also survived a 40,000-case fuzz against the oracle: random widths from 8 to 64, random polynomial, init, xorout, and reflection flags, and lengths chosen to straddle the slice-by-8 block boundaries. Zero mismatches. Out-of-range widths raise `ValueError` cleanly, and `bytes`, `bytearray`, and `memoryview` inputs all agree. A read of the extension source found correct reference counting, the input buffer pinned across the GIL-released compute, and no shared mutable state.

**It reverse-engineers CRCs for real.** Given only `(message, crc)` pairs built with the oracle, `reverse()` recovered crc16-modbus from the catalogue, and recovered a custom polynomial that is not in the catalogue (poly 0x1337, init 0xABCD) exactly, with the recovered parameters reproducing a separate held-out frame set. It is also candid about under-determination: a single frame returns "underdetermined" with no candidates, two same-length frames are flagged with an ambiguity-bits count, and random non-CRC data returns "underdetermined" rather than a fabricated match.

**It stands in for the common CRC tools.** `compute`, `encode`, `verify`, and `detect` all matched the oracle on built packets, `detect` named every algorithm it was given, and `identify_trailer` correctly classified MD5, SHA-256, and an 8-bit sum trailer. The package is upfront that bulk runtime hashing of non-crc32 algorithms is not its lane.

## Findings

### Finding 1 (significant): C# generated code does not compile

Every default C# invocation emits a static class and a static method with the same name, which the C# compiler rejects:

```text
crcglot csharp crc8  ->  public static class Crc8 { ... public static byte Crc8(byte[] data) ... }
error CS0542: 'Crc8': member names cannot be the same as their enclosing type
```

This was confirmed as a plain library build with no driver involved, for `crc8`, `crc16-modbus`, `crc32`, and the `name=` and `file=` paths. The root cause is in `src/crcglot/lang/csharp.py` near line 538, where the class name and the one-shot method name are both the PascalCase form of the same base symbol.

The default test run does not catch this. The per-algorithm test that compiles the real default output (`TestGeneratedCSharpExecutes`) is marked `exhaustive` and is deselected by default; forcing it with `pytest --exhaustive` fails with a build error. The batch test that does run by default generates every algorithm under an underscore-suffixed symbol (`crc8_t`), which keeps the one-shot method snake-cased (`crc8_t`) and therefore distinct from the class (`Crc8T`), so it sidesteps the exact collision. A green suite ships C# that no user can compile.

Suggested fix: ensure the one-shot method name can never equal the class name (suffix the method, or detect the collision and adjust), and un-gate one C# default-output compile from the `exhaustive` tier so the regression cannot return silently.

### Finding 2 (minor): the HDL self-test under-delivers against its own claim

The Verilog and VHDL `self_test` functions check only the single `"123456789"` reference, while the embedded comment and the README describe four fixed inputs (empty, the check string, all 256 byte values, and a 1 KiB pattern) that drive over a thousand table lookups. The C, Rust, Go, Java, TypeScript, and Python self-tests do check all four. For the HDL targets the boot-time integrity and flash-corruption-tripwire claims do not hold, because the table is barely exercised. Either bring the HDL self-test up to four vectors, or scope the claim to the software targets.

### Minor notes

The README's "about 2,000x faster than interpreted" figure is a best case: crc64 on slice-by-8 measured about 3,200x over pure Python, while the crc16 table path is closer to 300x. The phrasing ("on the order of") is defensible, but the number is algorithm-dependent. Separately, fourteen `assert` statements remain in shipped code, almost all type-narrowing invariants in the MCP server rather than input validation, so the risk from `python -O` stripping them is low. The wider smell scan was clean: no bare `except`, no `eval` or `exec` or `os.system` or `shell=True`, no mutable default arguments, and the `except Exception` handlers surface the error rather than swallow it.

## Bottom line

The compute engine, the C accelerator, the reverse-engineering search, and eight of the nine code generators are correct and robust under independent adversarial testing. The one real defect is that the C# generator emits source that does not compile, and the default test run does not reveal it. Fixing the generator and adding a single un-gated compile check for the default C# output would close the gap.

## Resolution (0.24.0)

The two findings above drove fixes that landed for crcglot 0.24.0; this section records how each was closed. Everything above it is left as written at review time, so the as-reviewed record stands next to its resolution.

**Finding 1 (C# does not compile): fixed.** The C# generator no longer derives the one-shot method name from the class name, so the CS0542 collision cannot occur (`src/crcglot/lang/csharp.py`). A default-output C# compile was un-gated from the `exhaustive` tier so the regression cannot return silently behind a green run, which is exactly the gap the finding identified.

**Finding 2 (HDL self-test): scoped, with a vector added.** The Verilog and VHDL self-tests now check the empty message in addition to the check string. The two large table-coverage vectors stay software-only by design: the HDL targets are bitwise with no lookup table, so there is no table to corrupt and nothing for those vectors to exercise. The README and certification docs were scoped to say exactly this, so the claim and the code now agree.

**Further hardening since the review.** The review did not raise these, but the same independent, multi-direction method was turned on more of the package for 0.24.0:

- `reverse()` carried a uniqueness guarantee it did not always keep. It now cross-validates each recovered parameter set (leave-one-out, plus a width-minimality check) and downgrades to "underdetermined" rather than return an over-fit. The fix was checked three ways: an exhaustive small-case enumeration, an exact structural cross-check, and a property fuzz against the independent oracle.
- `detect()` now reports a uniform `form` (binary / hex / text / json) on every result.
- The MCP surface validates widths and accepts hex inputs instead of failing opaquely.
- The two independent engines behind the embedded self-test vectors (anycrc and crccheck) are now re-checked by the suite on every full run (`tests/test_vectors_provenance.py`), so their agreement is a live fact, not just a generation-time one.

Net: all nine generators compile, run, pass their embedded self-test, and reproduce the oracle. The one significant defect the review found is closed, along with the test gap that hid it.
