# Independent verification report

Two passes are recorded here. The first (2026-06-18, target crcglot 0.23.0) is the original external check; its two findings were addressed for 0.24.0, and the Resolution section records how each closed. The second (2026-07-02, target crcglot 0.25.0) re-ran the same independent method against the shipped package and is appended at the end under "0.25.0 re-verification".

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

## 0.25.0 re-verification (2026-07-02)

This pass re-ran the independent method above against crcglot 0.25.0. The oracle is unchanged: a from-scratch bit-at-a-time LFSR that earns its role by reproducing `zlib.crc32` and fifteen published catalogue check constants by hand across widths 3 to 64, then grades crcglot. Between 0.24.0 and 0.25.0 the nine code generators are byte-for-byte identical (verified with `git diff`), so the release's new surface is its error-message system (a `CrcglotError` hierarchy, catalogue-aware "unknown algorithm" help, and low-level-leak translation) and `reverse_packets` accepting `(message, crc)` pairs directly. Those got the adversarial attention; the core correctness claims were re-graded from scratch anyway.

Toolchains: gcc 15.2, rustc 1.95, go 1.26.3, dotnet 9 (SDK 9.0.314), javac/java 21, tsx 4.22 on Node 24, iverilog 12, ghdl 5.1.1, CPython 3.14.

### What still holds

Every core claim from the original pass re-graded clean at 0.25.0:

- **Catalogue values.** All 113 catalogue check constants match the oracle. The count has not drifted since 0.23.0.
- **Runtime dispatch and engine parity.** `generic_crc` and `compute` matched the oracle across 113 algorithms times four inputs (empty, the check string, all 256 byte values, a 1 KiB random pattern) with zero mismatches. The C extension and the pure-Python engine agree bit-for-bit over the byte-aligned subset (the fifteen sub-byte-width algorithms ride the pure-Python path by design), and both agree with the oracle.
- **Fuzz.** The engine survived 40,000 random cases against the oracle: widths 8 to 64, random polynomial, init, xorout, and reflection flags, lengths chosen to straddle the slice-by-8 block boundaries. Zero mismatches.
- **Nine of nine languages execute.** C, C#, Go, Java, Python, Rust, TypeScript, Verilog, and VHDL each compiled through their real toolchain, passed the embedded `self_test`, and reproduced the oracle on the novel input `Verify-CRCglot-Independently!` (crc32 `0x5FFB0A10`). C was built both ways: the single-unit stdout dump (after stripping its own redundant `#include`) and the two-file `file=` split. The HDL targets were driven through iverilog and ghdl.
- **Reverse-engineering, including the new pairs path.** `reverse()` recovered crc16-modbus from oracle-built codewords, and recovered a non-catalogue polynomial (0x1337, init 0xABCD) whose recovered parameters reproduced a held-out oracle frame set. It stays candid on thin data: a genuinely custom single frame returns "underdetermined" with no fabricated candidate. The new `reverse_packets((message, crc) pairs)` form recovers the same model as `reverse()` on the same data, the binary-frame form still works, and its input validation rejects empty input, mixed shapes, non-`(bytes, int)` pairs, and a `bool` masquerading as the CRC, each with a `ValueError` that echoes the offending value.
- **Tool standins.** `compute`, `encode`, and `verify` round-trip against the oracle; `verify` accepts the well-formed packet and rejects a corrupted one; multi-frame `detect` names the right algorithm as its top match for every algorithm tried (single-frame detection is deliberately ambiguous for narrow CRCs and is not a defect); the per-candidate `form` field reports `binary` / `hex`; and `identify_trailer` classifies MD5, SHA-256, and an 8-bit sum trailer.

### Findings 1 and 2 remain closed at 0.25.0

The C# default output still compiles: generating the exact cases the original finding named (`crc8`, `crc16-modbus`, `crc32`) and building them together as a .NET 9 class library succeeds with zero errors, and the one-shot method is now `Compute` rather than the class name, so CS0542 cannot recur. The HDL self-tests still check two vectors (the empty message and the `"123456789"` check string) and pass under iverilog and ghdl, matching the scoped claim in the Resolution section.

### The new error-message system meets its own bar

crcglot 0.25.0 sets an explicit standard for raised errors (echo the bad value, suggest or name the valid options, translate low-level leaks, point at the right place per surface). Graded against it, the system holds up:

- `suggest_algorithms` resolves in three tiers: an exact prefix (`crc16-mod` to `crc16-modbus`), the variant family of a bare `crc<width>` (`crc16` lists its 31 variants, recognized ones first), and a fuzzy "did you mean" for typos (`crc16-modbsu` to `crc16-modbus`). Nonsense returns an empty list rather than a fabricated match, and odd inputs (blank, `crc`, `crc999`, `CRC-32`) do not crash it.
- The `UnknownAlgorithmError` message echoes the value, frames a bare width as an ambiguous family with a count, and carries a surface-specific pointer: `crcglot.ALGORITHMS` for Python, `crcglot list` for the CLI, `crc_list` for MCP. It derives from both `CrcglotError` and `ValueError`, so an old `except ValueError` still catches it and it is never a bare `KeyError`.
- The public entry points route through it: `compute`, `encode`, `crc_stream`, `CrcStream.from_name`, and the codegen path all raise the helpful error on a bad name.
- On the CLI, every bad-input path exits 2 with an `Error:` prefix, and the raw `bytes.fromhex` and `int()` messages are translated: a bad `--hex` reports `invalid hex string 'zz-not-hex': expected an even count of hex digits`, and a bad custom `poly=` reports `expected a decimal or 0x-hex integer; got 'notanumber'`. The hardened type errors also name the offending type (`hex mode requires all str packets; got bytes at index 0`).

### Finding 3 (minor): the compute path does not validate the CRC width

The one new gap this pass found is in the same area 0.25.0 was hardening. The public compute entry points that take a `Crc` value object directly, `generic_crc`, `generic_crc_many`, `encode_int`, and `CrcStream.from_info`, do not validate its width, so an out-of-range width is not rejected at the boundary:

```text
generic_crc(b"x", Crc(65, 0x1, 0, False, False, 0))  ->  120      # silent wrong answer, no raise
generic_crc(b"x", Crc(0,  0x1, 0, False, False, 0))  ->  ValueError: negative shift count
```

A width above 64 returns a wrong value silently, which is the worst failure mode for a library whose whole job is matching an externally-fixed CRC. A width below 1 leaks a raw `negative shift count`, exactly the kind of low-level message the 0.25.0 conventions say to translate. The valid range is 1 to 64, and the sub-byte widths in it work correctly.

The fix already exists one layer up and is not applied here: the front-door constructor `custom_algorithm(width=65)` rejects it cleanly with `width must be in 1..64, got 65`, and the C extension's own `c_generic_crc` raises `width must be in [8, 64]`. Only the direct `Crc`-to-engine path is unguarded. Reaching it takes hand-constructing a `Crc` with a bad width, since the catalogue and `custom_algorithm` never produce one, which is why this is minor rather than significant. Suggested fix: validate the width once where these entry points converge (or in a `Crc.__post_init__`), reusing the `custom_algorithm` message so the boundary behaves the same whichever door the caller uses.

### Minor notes (0.25.0)

The shipped-code assert count is down from fourteen to ten, all still type-narrowing invariants (`is not None`, `isinstance`) in the MCP server, its wire helper, and one in `targets.py` that a prior xor-check already guarantees; none validate input, so the `python -O` stripping risk stays low. The wider smell scan was clean: no bare `except`, no `eval` / `exec` / `os.system` / `shell=True` / `subprocess` in shipped code, no mutable default arguments, and the `except Exception` handlers re-raise as a `ValueError` (or return, for the source-tree metadata fallback) rather than swallow. `uvx ruff check` and `uvx ty check` both report clean over `src` and `tests`.

### 0.25.0 bottom line

The compute engine, the C accelerator, the reverse-engineering search (now including the out-of-band pairs entry), the nine code generators, and the new error-message system are correct and robust under the same independent adversarial testing. Findings 1 and 2 stay closed. The one new issue is a minor boundary gap: the direct `Crc`-to-engine compute path does not range-check the width, so an out-of-range width returns a wrong value or leaks a low-level message instead of raising the clean error the front-door constructor already produces.

### Resolution (0.25.1)

**Finding 3 (width validation): fixed.** `Crc` now validates its own width at construction (`__post_init__` in `src/crcglot/catalogue.py`), raising `width must be in 1..64, got N`, the same message `custom_algorithm` already used. Because every compute entry point the finding named (`generic_crc`, `generic_crc_many`, `encode_int`, `CrcStream.from_info`) receives a `Crc`, none of them can be handed an out-of-range width any more: a width above 64 no longer returns a wrong value silently, and a width below 1 no longer leaks `negative shift count`. A regression test (`TestCrcWidthValidation` in `tests/test_catalogue.py`) pins the rejection at each entry point and asserts the two doors keep the identical message so they cannot drift. Re-running the width portion of the independent harness confirms the guard now raises cleanly while every supported width, sub-byte ones included, still computes.
