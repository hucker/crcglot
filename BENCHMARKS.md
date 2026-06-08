# crcglot benchmark gallery

crcglot has two performance stories, and the table below shows both in one
place:

- **Generated source** (the per-language rows): complete, zero-dependency CRC
  for any of the 100+ catalogue algorithms in nine languages, verified by
  execution.  Portable source with nothing to link, fast enough for every
  CRC need short of a heavily CPU-constrained hot path.
- **The package's own runtime** (the two Python (runtime) rows): crcglot
  ships a compiled C extension (`crcglot._c`), so calling `generic_crc` from
  Python runs at C speed, on the order of 1 GB/s for *every* algorithm,
  roughly a thousandfold over the pure-Python engine.  That is the whole reason
  there is C in a pure-stdlib package: Python users get native CRC throughput
  without writing or compiling anything.  (For crc32 it goes one further and
  borrows the stdlib's hardware-accelerated `zlib.crc32`.)

`crc32` is the test algorithm throughout, chosen only because it is universal
and identical in shape across every language, not as a speed target (it is
the one algorithm you would *not* hand-generate; see **Reading the results**).
Numbers are MB/s (MB = 10^6 bytes), median of 3 runs, each an adaptive inner
loop on a single buffer (500 ms / 3 iterations minimum).  (VHDL
and Verilog are excluded because those generators emit simulator references for
hardware datapaths, not a software runtime.)

## Results

| Language         | Variant / engine             | Tables | 1 KiB (MB/s) | 1 MiB (MB/s) |
| ---------------- | ---------------------------- | -----: | -----------: | -----------: |
| C / C++          | bit-by-bit                   |      — |         99.1 |        110.7 |
| C / C++          | table-driven                 |  1 KiB |        311.3 |        365.8 |
| C / C++          | slice-by-8                   |  8 KiB |      1,415.4 |      1,425.4 |
| Rust             | bit-by-bit                   |      — |        336.7 |        388.2 |
| Rust             | table-driven                 |  1 KiB |        304.5 |        385.9 |
| Rust             | slice-by-8                   |  8 KiB |      1,246.4 |      1,422.1 |
| Go               | bit-by-bit                   |      — |         44.4 |         22.9 |
| Go               | table-driven                 |  1 KiB |        393.0 |        396.6 |
| Go               | slice-by-8                   |  8 KiB |      1,270.7 |      1,409.5 |
| C#               | bit-by-bit                   |      — |         24.2 |         26.1 |
| C#               | table-driven                 |  1 KiB |        231.5 |        298.3 |
| C#               | slice-by-8                   |  8 KiB |        219.2 |        594.8 |
| Java             | bit-by-bit                   |      — |         68.5 |         61.2 |
| Java             | table-driven                 |  1 KiB |        340.5 |        349.7 |
| Java             | slice-by-8                   |  8 KiB |        882.2 |        663.4 |
| TypeScript       | bit-by-bit                   |      — |         80.6 |         20.7 |
| TypeScript       | table-driven                 |  1 KiB |        172.9 |         68.0 |
| TypeScript       | slice-by-8                   |  8 KiB |        372.8 |        226.5 |
| Python           | bit-by-bit                   |      — |          0.5 |          0.6 |
| Python           | table-driven                 |  1 KiB |          3.3 |          3.8 |
| Python (runtime) | C extension (`crcglot._c`)   |  8 KiB |        790.0 |      1,424.6 |
| Python (runtime) | `generic_crc` → `zlib.crc32` |      — |      1,290.6 |     40,023.4 |

The two **Python (runtime)** rows are crcglot's own engines, not generated source: calling `crcglot.generic_crc` from Python uses the compiled C extension (`crcglot._c`, slice-by-8 for *every* algorithm), and for crc32 alone delegates to the stdlib's hardware-accelerated `zlib.crc32`.  They put Python squarely in the compiled-language band, which is the whole reason the package ships C.  The C extension is ~2,201x faster than the pure-Python CRC.

The same compiled paths back the streaming API: `crcglot.crc_stream(name)` / `CrcStream` feed chunks into the C extension's `CrcStream` (or `zlib.crc32` incrementally for crc32), so chunked data (large files, sockets, sensor logs) runs at this same compiled speed, paying the Python/C transition once across the whole message rather than per call.

## Reading the results

The trustworthy signal here is coarse: the across-language band and the
within-language *algorithmic* ordering.  The fine detail (which variant
wins, and by how much) is sensitive to runtime, buffer size, and CPU, so read it
as a hint, not a verdict.

### Across languages: the band

AOT-compiled targets (C, Rust, Go) cluster near the top; JIT runtimes (Java,
C#, TypeScript) in the middle; pure-interpreted Python at the bottom.  That
ordering is robust and reproduces across machines.  The absolute MB/s are not;
they move with your CPU, compiler version, and the day.

### Within a language: variant ordering

- **slice-by-8 should beat table-driven** at the same size: it processes 8
  bytes per iteration through 8 independent table lookups, shortening the
  serial dependency chain.  That is an algorithmic win in AOT code, though a
  managed runtime can erase it (see *confident only for C / Rust* below).
  (Python has no slice-by-8 row: CPython's per-int overhead negates the win,
  so the generator doesn't offer it.)
- **table-driven vs bit-by-bit is NOT monotonic.**  Modern compilers (LLVM at
  `-O3`) unroll and vectorize the 8-iteration bit loop into register-only work
  with no memory traffic, while a table lookup is one serial dependent load
  per byte.  So a well-vectorized bit-by-bit loop can tie or beat
  table-driven, especially on large buffers.  Languages whose codegen leaves
  the bit loop un-vectorized (C#, TypeScript, Python, Go) show the classic
  bitwise->table jump; Rust at `-O3` barely does.
- The **Tables** column is the static RAM cost: 0 (bit-by-bit), 1 KiB (table,
  256 entries x width), 8 KiB (slice-by-8, 8 tables).  Compiled code is a few
  hundred bytes regardless, so this is a tight proxy for static footprint.

### Buffer size: cache vs overhead

The two size columns pull in opposite directions.  A **1 KiB** buffer lives
entirely in L1 cache and is re-read at full speed; a **1 MiB** buffer fits in
neither L1 nor L2, so every pass streams it from L2/L3/RAM.  Two effects
compete as the buffer grows: fixed per-call overhead *amortizes* (favours
1 MiB), while the data stream *falls out of cache* (favours 1 KiB).  Tight,
latency-bound loops (a serial CRC dependency chain) are dominated by the
cache effect and often run faster at 1 KiB (e.g. Java table-driven drops
from 1 KiB to 1 MiB).  Overhead-bound runtimes (C#, Python) show the opposite.
Note the 1 KiB figure is a best case: real input is rarely already L1-hot.

### crc32 is the outlier, not the benchmark

crc32 is special: every mainstream runtime ships a hardware-accelerated crc32
in its standard library (Python `zlib.crc32`, Java `java.util.zip.CRC32` /
`CRC32C`, .NET `System.IO.Hashing.Crc32`, Go `hash/crc32`), folding with
CLMUL / SSE4.2 on any CPU since ~2010, tens of times faster than any portable
software CRC.  If you only need crc32 and can lean on the platform library, do.

crcglot's own Python runtime already does, and goes further, which is what
the two **Python (runtime)** rows in the table above show:

- **crc32** -> the `generic_crc` dispatcher hands it to `zlib.crc32`, silicon
  speed for free.
- **the rest of the catalogue** -> the compiled C extension (`crcglot._c`,
  slice-by-8 in C) runs them at ~1 GB/s from Python, roughly a thousandfold
  over the pure-Python engine.  This is why there is C in a pure-stdlib
  package: the Python build has a fast path for *every* algorithm, not just the
  one zlib happens to cover.

The **generated** source (the per-language rows) is the second product: a
complete, dependency-free CRC you can drop into firmware, an air-gapped build,
or a language with no CRC library at all.  For crc32 it is the one thing you
would not reach for (use the stdlib), but for the other algorithms with no
hardware shortcut, the generated table / slice-by-8 *is* the fast path, because
there is nothing faster to borrow.

### Variant selection is confident only for C / Rust

slice-by-8 > table > bit is an algorithmic ordering, but whether it survives
to the metal depends on the toolchain.  For AOT C / Rust at `-O3`, with no array
bounds checks and codegen you can inspect, it holds and `--fast` is a sound
call.  For managed runtimes the JIT, GC, bounds-check elimination, and
slice-by-8's 8 KiB of tables can erase it: in some runs C# slice-by-8 at
1 MiB is slower than plain table-driven.  For those targets treat the rows
as a hint.  Default to table-driven (a reliable middle that beats bit-by-bit
without slice-by-8's footprint) and measure your own runtime and message size
if it matters.

### Caveat

An academic comparison: single-threaded, in-process, one identical synthetic
buffer, no I/O.  It confirms the expected band and ordering; it is not a
publication-grade benchmark.  Real-world throughput depends on data source,
batch size, allocation, and your exact toolchain, so measure before deciding.

## Toolchain (this run)

| Tool             | Release flags                                     |
| ---------------- | ------------------------------------------------- |
| `gcc`            | `-O3 -DNDEBUG`                                    |
| `rustc`          | `-C opt-level=3 -C codegen-units=1`               |
| `go build`       | `-ldflags="-s -w"` (already optimized by default) |
| `dotnet publish` | `-c Release` (needs SDK, not just runtime)        |
| `javac` / `java` | HotSpot, default tiered JIT                       |
| `tsx`            | V8-JIT'd via Node.js                              |
| `python`         | interpreter only                                  |

## Reproduce

```bash
uv run python scripts/benchmark.py
```

Working files land under `benchmarks/<lang>/<variant>/<size>/` (gitignored);
the rendered output is this file.
