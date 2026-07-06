# Programmatic API

Everything the CLI does is reachable from Python.  `import crcglot` loads only the compute core (the engine, the catalogue, and the streaming API: 4 modules in ~30 ms); detection, reverse-engineering, trailer identification, and the nine generators load on first use.  The public surface is identical either way, and the package root is the only import surface you need: `from crcglot import ...` covers all of it.

One toolkit, three surfaces.  Every capability has the same name and shape on the CLI, over MCP, and in Python; the same table opens [docs/cli.md](cli.md), [docs/MCP.md](MCP.md), and [docs/api.md](api.md).

| Capability | CLI | MCP tool | Python |
| ---------- | --- | -------- | ------ |
| Browse the catalogue | `list` | `crc_list` | `ALGORITHMS` |
| Algorithm parameters | `info` | `crc_info` | `ALGORITHMS[name]` |
| Self-test vectors | `vectors` | `crc_self_test_vectors` | `self_test_vectors()` |
| Detect a known CRC | `detect` | `crc_detect` | `detect()` |
| Identify a non-CRC trailer | `identify` | `crc_identify_trailer` | `identify_trailer()` |
| Reverse an unknown CRC | `reverse` | `crc_reverse` | `reverse_packets()` |
| Verify a frame | `verify` | `crc_verify` | `verify()` |
| Compute a CRC | `compute` | `crc_compute` | `compute()` |
| Batch compute | — | `crc_compute_many` | `generic_crc_many()` |
| Build a packet | `encode` | `crc_encode` | `encode()` |
| Stream chunked data | — | — | `crc_stream()` |
| Generate verified code | `c` / `rust` / … | `crc_generate` | `generate_c()` … / `LANGUAGES` |
| Custom polynomial | `--custom` tokens | `custom_params` | `custom_algorithm()` |
| Credits | `credits` | `crc_credits` | `ATTRIBUTION` |

Every `DetectMatch` carries `.form` -- the input representation the CRC was found in: `"binary"`, `"hex"`, `"text"`, or `"json"`.  It derives from `.padding` (the round-trip detail stays there) and reuses a named form's `category`, so the value set is `{binary, hex, text} ∪ FormatInfo.category` values.

`detect()` also recognises a CRC wrapped in a named **payload form**: a text/JSON envelope such as `{"t":1234,"v":42,"crc":"1352"}` (CRC-16/XMODEM over the JSON prefix), where the CRC is not a bare tail.  Such a match reports `form="json"` (the protocol name stays on `padding.info`).  Pass `form=` (an fnmatch glob) to narrow or disable the form pass; a matched form is a `FormatMatch` on the candidate's `padding`, carrying the `FormatInfo`.  The forms ship as a registry mirroring the catalogue: `FORMATS` (the dict), `FormatInfo` (the record), and `format_info(name)` (the lookup).

Two registries, both keyed by short code:

## `LANGUAGES`: supported target languages

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

Every generated file header carries a `Reproduce with crcglot` provenance block (the producing crcglot version, then the resolved request values; details in [generated-code.md](generated-code.md#provenance)).  On the API side the record is the public `ProvInfo` dataclass in `crcglot.comments` (built via `build_prov(...)`, carried on `AlgoMeta.provenance`), and its `version` is `crcglot.__version__`.

## `ALGORITHMS`: the reveng CRC catalogue

```python
from crcglot import ALGORITHMS

modbus = ALGORITHMS["crc16-modbus"]
print(modbus.width, hex(modbus.check), modbus.desc)
# → 16 0x4b37 Modbus RTU serial protocol

# Filter to CRC-32 only.
crc32_family = [a for a in ALGORITHMS.values() if a.width == 32]
```

Each entry is a frozen `AlgorithmInfo` dataclass carrying the full Rocksoft / Williams parameter set (`width`, `poly`, `init`, `refin`, `refout`, `xorout`) plus `check`, `desc`, and `source`.  The algorithm name is the `ALGORITHMS` dict key, not a field on the record.

## `self_test_vectors`: the four self-test goldens

`check` is one of four fixed vectors the generated `self_test` drives; the other three (`empty`, `all_bytes`, `binary_1k`) were computed the same way (two independent engines agreeing, `check` anchored to reveng) but only lived inside the generated code. `self_test_vectors(name)` exposes all four as a typed record, and `SELF_TEST_INPUTS` gives the inputs, so you can validate any implementation:

```python
from crcglot import self_test_vectors, SELF_TEST_INPUTS

v = self_test_vectors("crc32")
# → SelfTestVectors(empty=0x0, check=0xCBF43926, all_bytes=0x29058C73, binary_1k=0x6D4552DB)

for name, data in SELF_TEST_INPUTS.items():
    assert my_crc32(data) == getattr(v, name), name   # each field is the CRC of that input
```

It accepts a catalogue name or an `AlgorithmInfo`, and returns `None` for a custom polynomial that is not in the catalogue (no independently-generated goldens). `v.check` always equals `ALGORITHMS[name].check`.  The same vectors are reachable from the CLI (`crcglot vectors <name>`) and over MCP (`crc_self_test_vectors`), so an assistant can fetch them to audit an implementation.

## Custom polynomials

One call builds a ready-to-use entry; the check value is computed for you, and the result plugs into every generator and compute function:

```python
from crcglot import LANGUAGES, custom_algorithm

algo = custom_algorithm(width=16, poly=0x1234, init=0xFFFF,
                        refin=True, refout=True, desc="My custom CRC-16")
code = LANGUAGES["rust"].generator_from_entry("my_crc16", algo, table=True)
```

This is the Python twin of the CLI's `--custom width=... poly=...` tokens and the MCP tools' `custom_params` argument; all three build their entries through the same helper.  (The pieces stay public if you need them: `Crc` is the bare parameter set, `generic_crc(b"123456789", spec)` computes a check value, and `AlgorithmInfo` can be constructed field by field.)

## Runtime CRC computation

Beyond *generating* code, crcglot can *compute* CRCs at runtime.  The everyday form is by name, sharing the verb with `crcglot compute` and the MCP `crc_compute` tool:

```python
from crcglot import compute

compute(b"123456789", "crc16-modbus")   # 0x4B37; any catalogue name or Crc/AlgorithmInfo
```

There is no variant knob: `compute` (and `generic_crc`, its typed twin taking a `Crc` or `AlgorithmInfo`) picks the fastest path available on your machine, and the speed you get depends only on whether the C extension is installed.  Under the hood it dispatches three ways, and you never select among them:

1. **IEEE CRC-32 / JAMCRC → stdlib `zlib.crc32`** (hardware CRC folding: PCLMULQDQ on x86, PMULL / `crc32` instructions on ARM): tens of GB/s.  No software CRC out-runs silicon, so crcglot borrows the stdlib's path for the algorithms it covers.
2. **Everything else → the optional C extension** (`crcglot._c`, slice-by-8 / table-driven): ~1-2 GB/s on bulk data, compiled-C-class.  The gain over pure Python ranges from a few hundred times to a few thousand, widening with the width (a 64-bit CRC's pure-Python loop is far slower than a 16-bit one), so it is not a single headline number.
3. **No extension built → pure Python**: always works, just slow.

The extension ships in the prebuilt wheels; `uv add crcglot` gets it on common platforms, with no extra to enable.  To force a build from source instead of using a prebuilt wheel:

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

## Streaming and batch

> **Don't call `generic_crc` in a hot loop.**  It is a one-shot: for any table/slice-by-8 algorithm (everything byte-aligned except IEEE crc32 / jamcrc, which ride zlib) it rebuilds the lookup table on every call, so looping it over many messages of one algorithm is 4-11× slower than necessary on small buffers.  For repetition, build the table once: a `CrcStream` with one `update` per message (independent streams run in parallel across threads), or `generic_crc_many` for a batch.

Two reasons to use the **streaming** API instead of `generic_crc`: **chunked data** (a message arriving in pieces, such as large files, sockets, or sensor logs) and **repetition** (many messages of the same algorithm, so you build the table once, not per call).  It's the runtime counterpart to the generated `init → update* → finalize` triple.  Bind the algorithm once by catalogue name, feed chunks, and read the finalized value on demand (hashlib idiom: `update` / `digest` / `reset` / `copy`):

```python
from crcglot import crc_stream

s = crc_stream("crc32")           # by catalogue name
for chunk in chunks:              # any chunking; the answer never changes
    s.update(chunk)
s.digest()        # 0xCBF43926 (an int; non-destructive, call it again)
s.hexdigest()     # 'cbf43926'
```

`crc_stream` takes the same three-tier dispatch as `generic_crc`: stdlib `zlib.crc32` for IEEE crc32 / jamcrc, the C extension when built, pure-Python otherwise.  For a custom (non-catalogue) CRC, build it from a `Crc` value object (or its raw keyword parameters), or from an `AlgorithmInfo`:

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

See [BENCHMARKS.md](../BENCHMARKS.md) for measured throughput of each runtime path against the generated-code gallery.  Across the generated languages the per-variant trend is monotonic (`bit-by-bit < table < slice-by-8`) but the step size depends heavily on how well each compiler optimizes the baseline: Rust's LLVM-vectorized bit-by-bit nearly ties its table-driven, while C# / Python see a 10×+ jump just from table-driven because their bitwise loops aren't vectorized.  VHDL and Verilog are excluded: they're simulator references for hardware datapaths, not software runtime.
