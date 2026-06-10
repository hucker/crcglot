# Changelog

## Unreleased

An integrator-ergonomics pass over the public surface, prompted by friction a
downstream app hit.  Several breaking changes; crcglot has a single consumer
today, so they ship without a deprecation cycle.

### Breaking: `generic_crc` takes a `Crc` value object

`generic_crc(data, width, poly, init, refin, refout, xorout)` is now
`generic_crc(data, crc)`, where `crc` is the new frozen `Crc(width, poly, init,
refin, refout, xorout)`.  `AlgorithmInfo` subclasses `Crc`, so a catalogue entry
passes straight in:

```python
from crcglot import Crc, generic_crc, ALGORITHMS
generic_crc(b"123456789", ALGORITHMS["crc16-modbus"])          # catalogue
generic_crc(b"123456789", Crc(width=16, poly=0x8005, init=0xFFFF,
                              refin=True, refout=True, xorout=0))  # custom
```

`generic_crc_many` changes the same way.  The compute functions that took
`str | AlgorithmInfo` (`encode_int`, `encode`, `verify`) now take `str | Crc`
(an `AlgorithmInfo` still works), and `CrcStream.from_crc(crc)` builds a stream
from any `Crc`.  Constructing `Crc` by keyword removes the six-positional-int
footgun, where the two adjacent bools were easy to transpose.

### Breaking: one naming knob

`generate_files` drops `file_stem`.  `name=` is now the single naming knob: it
sets the filename *and* the in-code identifier/class (cased per target), and it
works for a single CRC and for a bundle.  `symbol=` stays as the verbatim escape
hatch (it overrides only the identifier); `name=` + `symbol=` expresses the old
file-vs-identifier divergence.  On the CLI, `name=` and `file=` both name the
output (and `file=` still writes to disk), so giving both with different values
is now rejected.

### Breaking: unified packet-format field names

`TextFormat.hex_prefix` → `prefix`, and `HexFormat.byte_separator` → `separator`.
Both shapes now expose `separator` / `prefix`, so a consumer reads them the same
way instead of branching on the format type.

### Added

- `detect()` / `detect_iter()` (and `crcglot detect --width`, plus the MCP
  `crc_detect` tool) take `width=<bits>` — a first-class filter that replaces
  the `algorithms="crc16-*"` glob workaround.
- `LanguageInfo.variants_for_widths(widths)` — the variant set valid across a
  multi-width bundle (the intersection), so a UI no longer computes it itself.

### Fixed

- `detect(mode="auto")` decided hex-vs-text by probing the *filtered* algorithm
  set, so a `width=` / `algorithms=` filter could silently re-read a hex frame
  as text.  The decision now uses the full catalogue, so a filter narrows the
  scan without changing how the bytes are read.
- Docstring rot: the package described "72 algorithms" (the catalogue holds more
  than 100) and left Java off the target list.

## v0.18.0 — 2026-06-09

CRC reverse-engineering plus a coherent packet/MCP surface.  Additive over
0.17.0 **except** the code-generation default, which now favours speed over size
(see the first entry).

### NEW: `generate_files()` — crcglot owns output naming

Generation now hands back ready-to-write, correctly-named files instead of bare
source strings, so the CLI, the MCP `crc_generate` tool, and any UI stop
re-deriving per-language filename rules.  `crcglot.generate_files(language,
algorithm, ...)` (and `LanguageInfo.generate_files`) return a tuple of
`GeneratedFile(filename, content, role)`.  crcglot owns the naming: the
filename(s) per target (C's `.h`/`.c` pair; PascalCase for C#/Java), and the
in-code class renamed to match (Java's public class *must* equal the file).

- `name=` renames a CRC and is **cased per target** (`name="my-widget"` →
  `my_widget.rs`, `MyWidget.java`, `MyWidget.cs`) — the "call it X" knob,
  exposed on the CLI (`name=`), the package, and MCP (`crc_generate` `name`).
- `symbol=` remains the verbatim escape hatch; `file_stem` names the file
  independently of the in-code identifier.
- `LanguageInfo.validate_symbol(stem)` sanitizes/validates a desired name for a
  UI (rejecting a stem that can't be a legal Java/C# class).
- `LanguageInfo.format_name(stem, kind)` cases a stem the way crcglot will:
  `kind="filename"` gives the exact basename `generate_files()` writes,
  `kind="identifier"` gives the function/method base in the language's idiomatic
  case (snake/camel/pascal). `LanguageInfo.format_filename(stem)` is the
  filename shortcut. A UI can show the name crcglot will emit instead of
  reimplementing per-language casing.
- `default_stem(algorithm)` returns the stem crcglot defaults to — the
  algorithm's own name for a single CRC, `"crc_bundle"` for a bundle — so a UI
  seeds its name field from crcglot instead of re-deriving
  `name.replace("-", "_")`. A no-override **bundle** now writes
  `crc_bundle.<ext>` instead of `crcglot.<ext>`.
- `crc_generate` results now carry a `filename` per file.

Migration / behaviour note: a single-algorithm **Java** generation now defaults
its class and file to the PascalCase algorithm name (e.g. `Crc16Xmodem.java`)
instead of the generic `CrcGlot`; `file=` stems are now sanitized to a valid
identifier for the filename (e.g. `file=my-crc` → `my_crc.rs`), matching the
in-code symbol.  The low-level `generate_<lang>` functions are unchanged.

### CHANGED (default behaviour): the fastest variant is now the default

Code generation used to default to `bitwise` (smallest code, slowest).  It now
defaults to the **fastest variant the target + width support**: slice-by-8 for
width 32/64 on compiled languages, table-driven for byte-aligned widths,
bit-by-bit only where that's the only option (sub-byte widths, Verilog / VHDL).
Generating *on someone's behalf* shouldn't silently pick the slowest
implementation.

- A new `"auto"` variant (now the default everywhere) resolves to
  `LanguageInfo.fastest_variant_for_width(width)`; explicit
  `bitwise` / `table` / `slice8` are unchanged.
- **CLI:** `crcglot rust crc32` (no flag) now emits slice-by-8, not bit-by-bit.
  `--small` is the explicit opt-in to the smallest code; `--fast` still works
  and now equals the no-flag default.
- **Library:** `generate_*` / `generate_*_from_entry` and
  `LANGUAGES[code].generator(name)` default to `variant="auto"`.
- **MCP:** `crc_generate` defaults to `variant="auto"`; the server now also
  steers the model to ask smallest-vs-fastest when the user hasn't said.

Migration: pass `--small` (CLI) or `variant="bitwise"` (library / MCP) to keep
the old output.

### FIXED: TypeScript no longer offers `snake_case` naming

TypeScript advertised all three naming conventions, but `snake_case` function
names are non-idiomatic in TypeScript and a linter flags them.  TypeScript now
offers `camel` and `pascal` only (default `camel`), matching Java and the
per-language contract that each target exposes only the conventions its
ecosystem uses.  `crcglot ts crc32 --naming snake` and
`generate_typescript(..., naming="snake")` now raise instead of emitting
`snake_case`; pass `camel` (the default) or `pascal`, or an explicit `symbol=`
for a verbatim name.

### NEW: generation advisories (`Advisory` + `LanguageInfo.advisories_for`)

A first-class "a faster path exists here" note, shared by every generation
surface instead of reinvented downstream.  `LANGUAGES[lang].advisories_for(algos)`
returns frozen `Advisory(severity, kind, message)` records for two cases: a
**Python target** (warning: prefer the `crcglot` package, whose runtime beats
emitted pure-Python) and an **IEEE CRC-32 on a compiled target** (info: the
language's stdlib / canonical-package CRC-32, named in the new
`LanguageInfo.stdlib_crc32` field, is faster for large data).

- Eligibility is `has_faster_alternative(algo)`, keyed on the parameter tuple via
  the same table that routes `generic_crc` to `zlib.crc32`.  So it covers
  `crc32`, `crc32-jamcrc`, and **custom CRC-32-equivalent parameters** that a
  name check would miss.
- The **CLI** prints advisories to stderr (stdout stays a clean source file);
  the **MCP** `crc_generate` response gains an `advisories` array.  New exports:
  `Advisory`, `has_faster_alternative`.

### NEW: `crcglot.reverse(frames, …)` + `ReverseResult`

`detect` identifies CRCs that are already in the catalogue; `reverse` recovers a
CRC that **isn't** (a vendor's private polynomial) from a handful of
`(message, crc)` codewords.  Two tiers: it first tries the catalogue (default
`std_algo_only=True`, identical to `detect`), and with `std_algo_only=False`
falls through to an algebraic solve.

- **Clean-room, MIT.** Derived from the linearity of CRCs over GF(2), the same
  public mathematics CRC RevEng implements, written from first principles, *not*
  from reveng's source (reveng is GPLv3+).  Reuses only crcglot's own engine.
- **Polynomial** is recovered uniquely (GCD of equal-length difference
  codewords); **init/xorout** by a GF(2) linear solve; **width/refin/refout**
  are searched or fixed via keyword (a known parameter reduces the codewords
  needed).
- **Honest about ambiguity.** A generator carrying the `(x+1)` factor (most
  well-made CRCs do; it's what detects all odd-bit errors) admits several
  `(init, xorout)` labellings that are *observationally identical*.  `reverse`
  returns the **complete** equivalence class (a finite, provably-exhaustive
  coset of size `2 ** ambiguity_bits`) with a canonical representative first;
  `status` is `unique` or `equivalent`.
- **Self-verifying + held-out validation.** Every returned model is re-checked
  against `generic_crc`, and by default the model is also validated against a
  held-out frame it didn't train on (`validated_frames`).  The guarantee, under
  a counterexample-hunting test suite (all 113 catalogue algorithms + a random
  custom-CRC sweep, each validated on unseen messages): a recovered model is
  **correct on unseen data, or honestly reports none/underdetermined, never
  confidently wrong.**

Scope: the CRC is the trailing field of each codeword; byte-aligned messages;
width ≤ 64.

### NEW: `crcglot.reverse_packets(packets, …)`, the packet-shaped entry

`reverse` takes the `(message, crc)` split directly; `reverse_packets` takes
whole captured frames (the **same shapes `detect` accepts**) and peels the CRC
off for you.  Binary frames (`bytes`) split at the trailing `crc_bytes`
(auto-detected when omitted: the largest consistent cut, since CRC register
feedback can make a smaller cut look valid too).  Text frames
(`"data <sep> hexcrc"`) reuse `detect`'s trailing-hex parser, so a CRC appended
as hex to a log line / NMEA-style frame recovers with no manual splitting.
`crc_byte_order` is `big` / `little` / `both`.

### NEW: `crcglot.verify(packet, algorithm, …)` + `VerifyResult`

The inverse of `encode`: check a frame's trailing CRC against a **known**
algorithm.  Accepts the same binary or `"data <sep> hexcrc"` text frames; returns
`{valid, expected, actual, width}`; `expected` vs `actual` shows *how* a bad
frame is wrong.

### NEW: `crc_reverse` + `crc_verify` MCP tools (8 → 10)

The MCP server gains two tools so its three packet tools (`crc_detect`,
`crc_reverse`, `crc_verify`) now share **one input shape** (a frame with the
CRC at the tail, as hex / base64 / `data <sep> hexcrc` text): `crc_detect` names
a known CRC, `crc_reverse` recovers an unknown one (wrapping `reverse_packets`,
returning the full equivalence class with the determinacy / held-out-validation
fields), and `crc_verify` checks a frame against a named algorithm (wrapping
`verify`, the inverse of `crc_encode`).  All read-only annotated.  See
[docs/MCP.md](docs/MCP.md).

### CHANGED: compute / encode / verify accept a custom polynomial

`crc_compute`, `crc_compute_many`, `crc_encode`, and `crc_verify` now take
**either** `algorithm` (a catalogue name) **or** `custom_params`, a Rocksoft
tuple `{width, poly, init, refin, refout, xorout}`, the same shape `crc_generate`
already accepts and the shape `crc_reverse` returns.  This closes the
recover → use loop: an agent recovers a vendor's custom CRC with `crc_reverse`,
then computes / verifies / builds packets with it directly, instead of only
being able to generate code for it.  Underpinning this, the library's `encode` /
`encode_int` / `encode_text` / `verify` now accept an `AlgorithmInfo` in place
of a catalogue name (additive; names still work).

### NEW: algorithm-selection steering + `design-a-crc` prompt

The server now steers algorithm **selection**, not just usage.  The instructions
carry a *choose-vs-match* rule: if the CRC crosses a boundary you don't control,
match it (`crc_detect` / `crc_reverse`); only for a greenfield protocol do you
choose, and then by **sizing the CRC to the payload and overhead budget** (crc32
for large / unconstrained transfers, crc16 for small framed or serial protocols
like XMODEM / Modbus / CAN, crc8 for tiny payloads), not by reflex.  A new
`design-a-crc` MCP prompt (the server's first) makes that an explicit,
user-invokable template for the open-ended "I need a CRC" request, targeting the
common failure mode of grabbing an arbitrary CRC with no rationale.

## v0.17.0 — 2026-06-07

Reveng catalogue completion: the sub-byte, non-byte-aligned, and CRC-24
families.  Additive over 0.16.0 — no public API removed or renamed.

### NEW: 41 algorithms — the full reveng catalogue (72 → 113)

crcglot previously shipped only byte-aligned widths (8/16/32/64).  This release
adds every remaining reveng model, completing the catalogue:

- **CAN** (CRC-15), **CAN FD** (CRC-17, CRC-21), **FlexRay** (CRC-11, CRC-24)
- **CRC-24**: BLE, OpenPGP, LTE-A/B, FlexRay-A/B, Interlaken, OS-9
- **Telecom**: GSM / UMTS / CDMA2000 / DECT / ATM at widths 10–14
- **Sub-byte**: CRC-3/4/5/6/7 (USB, MMC, RFID EPC, ROHC, DARC, G-704, …)

Every entry is validated against its canonical reveng `check` value and
compiled + run through all nine target toolchains in the execution suite.

### CHANGED: code generators handle sub-byte widths (bit-by-bit)

The generators now emit correct code for widths below 8.  Sub-byte CRCs are
**bit-by-bit only** — a 256-entry table to checksum the tiny payloads these run
on (USB tokens, MMC commands, RFID) is pure overhead, so `variants_for_width`
advertises only `bitwise` below width 8 (joining the existing slice-by-8
exclusion).  The bitwise non-reflected loop feeds each byte MSB-first rather
than the byte-aligned `byte << (width-8)` fold, which underflows for width < 8
(a compile error in Rust/Go/HDL, undefined behaviour in C).

### CHANGED: `detect`, `encode`, the engine, and streaming handle sub-byte CRCs

Identifying and building packets for a non-byte-aligned CRC now works: the CRC
field occupies `ceil(width / 8)` bytes (right-justified, zero-padded), compared
**strictly** so a garbage pad bit is rejected rather than masked away.  Previous
`floor`-division logic (`width // 8`) truncated sub-byte fields and could
overflow `detect`'s `target_crc` byte-reversal.  The runtime engine
(`generic_crc` / `generic_crc_many`) and `CrcStream` route widths below 8 to the
pure-Python reference (the C extension's domain is `[8, 64]`); results are
bit-identical.

## v0.16.0 — 2026-06-07

Batch CRC + MCP ergonomics.  Fully additive over 0.15.1.

### NEW: `generic_crc_many` — batch CRC for many messages

`crcglot.generic_crc_many(buffers, width, poly, init, refin, refout, xorout)`
computes the CRC of many buffers for one algorithm in a single call,
returning one result per buffer in order.  Same dispatch and bit-identical
results as `generic_crc` (zlib for crc32 / jamcrc, the C extension's
`c_crc_many` otherwise, pure-Python fallback), but the C engine builds the
lookup table **once** for the whole batch and pays the Python↔C transition
once -- far faster than a loop of `generic_crc` for many small messages of
the same algorithm (packet streams, framed protocols, bulk validation).

### NEW: `crc_compute_many` MCP tool

The batch form of `crc_compute`: an agent CRCs a whole list of messages
(`data_texts` / `data_b64s`) in **one** tool call -- results in order --
instead of N round-trips.  Backed by `generic_crc_many`.

### CHANGED: MCP tools annotated read-only / idempotent

All MCP tools now advertise `ToolAnnotations(readOnlyHint=True,
idempotentHint=True, destructiveHint=False, openWorldHint=False)`.  Every
tool is a pure, deterministic, offline read (lists / computes / generates;
never mutates state or touches the network), so clients can **auto-approve**
the calls instead of prompting per invocation.

## v0.15.1 — 2026-06-07

A C-extension correctness and hardening patch.  No public API or generated
output changes; safe drop-in over 0.15.0.

### FIXED: C / Python parity for out-of-width `xorout`

`crcglot.generic_crc` (and the streaming pure-Python backend) now mask the
finalized result to `width`, matching the C engine, so the two are
bit-identical for *all* inputs -- not just the clean catalogue values.
Previously a caller passing an `xorout` with bits above the width got the
width-masked value from the C engine but the unmasked value from pure
Python.  Catalogue algorithms were never affected.  Regression tests pin
C == Python on dirty `poly` / `init` / `xorout`.

### CHANGED: the C extension is now stateless (no shared table cache)

The `crcglot._c` engine previously kept a process-global, append-only cache
of up to 64 lookup tables.  It has been removed: each table/slice-by-8 CRC
builds a fresh table the caller owns and frees.  This makes the extension
**thread-safe by construction** -- no lock, no shared state, correct on
free-threaded builds (PEP 703), and concurrent builds run fully in
parallel -- and drops the append-only shutdown leak and the 64-entry
cache-thrash cliff.

Table *reuse* now lives where ownership is explicit: `CrcStream` builds its
table once and reuses it across `update`s; `c_crc_many` builds once per
batch.  Only the bare one-shot `generic_crc` / `c_generic_crc` rebuilds per
call.  **Performance note:** because there is no cache, calling
`generic_crc` in a hot loop over the same algorithm rebuilds the table
every iteration (4-11x slower on small buffers).  For repeated CRCs of one
algorithm, use `crc_stream` / `CrcStream`.  Documented on `generic_crc` and
in the README.

### Other

Internal C cleanup: `PyMem_*` allocators, a `CrcEngine` enum replacing magic
codes, de-duplicated engine selection, honest doc comments, and removal of a
stale "follow-up commits" note and a dead `crcglot[fast]` extra reference.

## v0.15.0 — 2026-06-06

Two headline features — idiomatic per-language **naming** for generated code
and a **streaming** runtime engine — plus a comment-accuracy fix.  The naming
change is the one upgrade note: generated **Go / C# / Java / TypeScript**
function names change shape (see BREAKING).  Everything else is additive.

### BREAKING: idiomatic default naming for Go / C# / Java / TypeScript

Generated public function/method names now follow each language's convention
instead of `snake_case` everywhere: **Go** and **C#** default to `PascalCase`
(`Crc16ModbusUpdate`), **Java** and **TypeScript** to `camelCase`
(`crc16ModbusUpdate`).  C, Rust, Python, Verilog, and VHDL are unchanged.
Consumers who committed generated Go/C#/Java/TS code and call the old
`snake_case` names must regenerate (or pin the new names).  Pass
`--naming=snake` where a language still offers it, or an explicit `symbol=`
(emitted verbatim) to keep a specific name.

### NEW: `--naming` axis (`snake` / `camel` / `pascal`)

A new generator option chooses the casing of the public names, validated per
language exactly like `--comment` (`crcglot rust --naming=pascal` is rejected —
Rust is snake-only).  Ships the metadata twin of the comment/variant axes:
`NamingInfo` / `naming_info(name)` / `NAMING_ORDER`, plus `LanguageInfo.naming`,
`.default_naming`, and `.naming_infos`.  Surfaced on the CLI (`--naming`) and
over MCP (`crc_generate` `naming` param; `crcglot://languages.json` advertises
each language's `naming` + `default_naming`).  Only the public function names
re-case — header guards, table symbols, package names, and class names keep
their fixed idiom.

### NEW: streaming runtime engine — `crc_stream` / `CrcStream`

The runtime counterpart to the generated `init → update* → finalize` triple.
Bind a catalogue algorithm once and feed it in chunks:

```python
from crcglot import crc_stream
s = crc_stream("crc32")
for chunk in chunks:
    s.update(chunk)
s.digest()      # 0xCBF43926 — non-destructive int
s.hexdigest()   # 'cbf43926'
```

hashlib-style (`update` / `digest` / `reset` / `copy` / `hexdigest`), with
`CrcStream.from_name` / `.from_info` / a raw keyword constructor for custom
CRCs.  Backend dispatch mirrors `generic_crc`: stdlib `zlib.crc32` (streamed
incrementally) for IEEE crc32 / jamcrc, the C extension when built, pure-Python
otherwise — so chunked data runs at compiled speed and always works.  Results
are byte-identical to `generic_crc` no matter how the input is split.

### FIXED: `finalize` doc comments now match the parameters

The generated `finalize` doc summary was a fixed string claiming "output
reflection and the final XOR" for every algorithm; it is now derived from
`(refin, refout, xorout)`, so e.g. an `xorout=0` algorithm correctly reads as a
no-op rather than describing transforms it doesn't perform.  The EXAMPLES
gallery also gained a "Comment styles" section rendering every doc style.

## v0.14.0 — 2026-06-05

Ergonomic, **fully additive** introspection helpers for apps building UIs on
top of crcglot.  Nothing in 0.13.0 changed — every existing import keeps
working byte-for-byte; this release only *adds* symbols.  The theme, from a
downstream integration: fewer hardcoded label/description maps in client apps,
and fewer reach-ins to submodules from the `LanguageInfo` surface.

### NEW: `crcglot.__version__`

A module-level version string (resolved from installed package metadata).  An
app can now assert a minimum crcglot version at import time instead of
discovering a missing symbol as a runtime `AttributeError` mid-render.

### NEW: `variant_info(name)` / `VariantInfo`

`crcglot.variant_info("slice8")` returns a frozen
`VariantInfo(name, label, description, widths)` — the variant twin of the
existing `crcglot.comments.style_info` / `StyleInfo`.  `widths` is `None` for
"any width" and `frozenset({32, 64})` for slice-by-8.  A UI renders the
canonical `label` / `description` instead of maintaining its own
`{name: (label, description)}` table, removing a class of "what does this
variant mean" drift between an app and the CLI.

### NEW: `LanguageInfo.styles`

A property returning the comment styles valid for a language as rich
`StyleInfo` records, mirroring the `.variants` shape:
`LANGUAGES["java"].styles` instead of reaching into
`crcglot.comments.styles_for_language("java")`.  Everything a UI needs about a
target now lives on the one `LanguageInfo` object.

### NEW: `LanguageInfo.variant_infos_for_width(width)`

The rich companion to `variants_for_width`: returns `VariantInfo` records
instead of bare name strings, width-filtered the same way (slice-by-8 dropped
below width 32 / 64).  Same "one object per target, records not bare strings"
principle.

### Also

The benchmark gallery (`BENCHMARKS.md`) was restructured to lead with the data
and fold crcglot's runtime engines (the compiled C extension and the
`crc32` -> `zlib` dispatcher) into the single results table, so the fast Python
paths sit beside the compiled languages.  Documentation only; no code change.

## v0.13.0 — 2026-06-05

Generated code is now **documented**, not just correct.  Every emitted file
gets a header (algorithm parameters, a copy-paste streaming example, the
self-test contract) and a doc comment above each of the five functions — so a
reader learns the `init → update* → finalize` streaming contract from the
source rather than the tests.  Backward compatible: the new `plain` style is
the default, and these comments are the only change to the generated output.

### NEW: pluggable documentation comment styles

Pick the convention with `--comment=<style>`.  `plain` (clean human-readable
comments in each language's native syntax) is the default; every language also
has its idiomatic doc-tool style, and each is verified by compiling and running
the documented code on its real toolchain:

| Language       | `--comment` styles                 |
| -------------- | ---------------------------------- |
| C / C++        | `plain`, `doxygen`                 |
| C#             | `plain`, `doxygen`, `docfx`        |
| Java           | `plain`, `doxygen`, `javadoc`      |
| Python         | `plain`, `google`, `numpy`, `rest` |
| Rust           | `plain`, `rustdoc`                 |
| Go             | `plain`, `godoc`                   |
| TypeScript     | `plain`, `jsdoc`                   |
| Verilog / VHDL | `plain`                            |

```bash
crcglot c crc32 --comment=doxygen        # /** @brief @param @return */
crcglot python crc32 --comment=numpy     # numpydoc underlined Parameters / Returns
crcglot rust crc32 --comment=rustdoc     # /// with # Arguments markdown
```

crcglot offers each language only the styles its tool understands —
`crcglot rust --comment=doxygen` is rejected, because Doxygen doesn't read
Rust.  And why generate the docs at all instead of asking an LLM?  Because the
code is fully known, the documentation can be **deterministic**: the same
request yields byte-identical comments every time, rendered from the same
source of truth as the code (so it can't misdescribe the API) — and if a
description is ever wrong, it is wrong *uniformly*, fixed once in the generator
and propagated everywhere.  Layer an LLM on top for richer prose; the baseline
everyone ships is reproducible, uniform, and reviewable.

### NEW: UI-discoverable style matrix

The (language, style) compatibility matrix is derived from self-describing
styles, never hardcoded, so a front end can build a dropdown from it:

```python
from crcglot.comments import comment_styles_for_language
comment_styles_for_language("python")
# (StyleInfo(name='plain',  label='Plain',  description='…'),
#  StyleInfo(name='google', label='Google', description='…'), …)
```

Each record carries a machine `name` (the dropdown's value, handed back to the
generator), a human `label`, and a `description`.  The same
`{name, label, description}` records are served over MCP in the
`crcglot://languages.json` resource, and `crc_generate` takes a `comment_style`
argument.

### Also: Java in the benchmark gallery

`BENCHMARKS.md` now includes Java throughput figures (bit-by-bit / table /
slice-by-8), alongside an explicit note that the HDL targets (Verilog / VHDL)
are simulator-verified only and therefore carry no benchmark numbers.

### Internal

The comment subsystem ships as a `crcglot.comments` package, one small module
per style; adding a style is one module plus one registry line, with **no
generator changes** — the generators emit structured doc blocks and the style
renders the syntax.  3293 tests, 93% coverage.

## v0.12.0 — 2026-06-04

A ninth target language (Java), the ability to bundle several algorithms
into one generated file, and a generator change so multiple generated
outputs coexist in one translation unit.  Everything here is backward
compatible — single-algorithm output is byte-for-byte unchanged.

### NEW: Java target

`crcglot java crc32` generates Java, at full parity with the other
targets: every catalogue algorithm, the `--small` / `--table` / `--fast`
variants, and an embedded `_self_test()`.  Java has no unsigned integer
types, so the generator uses `int` (width ≤ 32) / `long` (width 64) with
logical (`>>>`) shifts and `& 0xFF` byte masking — and every (algorithm ×
variant) cell is verified by compiling and running it through `javac` /
`java`.

Java puts every algorithm into one container class named from `file=`
(default `CrcGlot`):

```bash
crcglot java crc32 file=Crc32        # -> Crc32.java, public final class Crc32
```

### NEW: bundle multiple algorithms into one file

Name more than one algorithm and crcglot emits them all into a single
file, each keeping its own catalogue-derived function names:

```bash
crcglot c crc32 crc16-modbus crc8 file=mycrcs    # -> mycrcs.h + mycrcs.c
crcglot rust crc32 crc64-xz file=crcs            # -> crcs.rs
```

Works for every language and over MCP — `crc_generate` now accepts a list
*or* a space-separated string of algorithm names.  `symbol=` is rejected
with more than one algorithm, duplicates are de-duplicated, and an unknown
name aborts the whole bundle (nothing is written).

### Generator change: per-symbol table names

Generated lookup tables are now named per symbol
(`crcglot_table_<algorithm>` / `crcglot_slice_<algorithm>`) instead of a
fixed `CRC_TABLE`, so several generated outputs — different algorithms, or
one algorithm in several variants — coexist in one translation unit
without colliding.  This is what lets the multi-algorithm bundles above
link cleanly.  CRC values are unchanged; only the internal table
identifiers differ.

### Also

- `--custom` now rejects a stray catalogue name (e.g.
  `crcglot c --custom width=16 poly=0x1234 crc32`) instead of silently
  dropping it.
- The execution test tier was overhauled to compile each language's whole
  catalogue in a single toolchain invocation (an internal speedup; no
  user-visible change).

## v0.11.0 — 2026-06-03

Three additions: an optional MCP server for LLM integration, two new
BACnet catalogue entries, and substantially expanded engine
verification.  The release also tightens the catalogue's
self-documentation by giving every entry an explicit `source` field.

### NEW: `crcglot[mcp]` — optional MCP server

Install with `pip install 'crcglot[mcp]'` and add to your Claude
Desktop / Cursor / mcp-cli config to expose crcglot as a Model Context
Protocol server.  Seven tools (`crc_list`, `crc_info`, `crc_detect`,
`crc_encode`, `crc_compute`, `crc_generate`, `crc_credits`) and three
JSON resources (`crcglot://catalogue.json` / `languages.json` /
`variants.json`) — every tool maps 1:1 to a `crcglot` CLI subcommand,
so the MCP layer is pure transport adaptation with zero new CRC logic.
Full walkthrough at [docs/MCP.md](docs/MCP.md).

The base `pip install crcglot` stays pure-stdlib; the `mcp` SDK only
arrives with the `[mcp]` extra.

Design highlights:

- `crc_detect` exposes the full Python API (`target_crc`, `endian`,
  `algorithms` glob, `match` mode) even though the CLI doesn't —
  out-of-band CRC values and strict-singleton checks are real LLM use
  cases.
- Wire-format relabel: `DetectMatch.endianness` becomes
  `crc_byte_order` on the JSON boundary only.  LLMs misread
  `endianness=little` as "the protocol is little-endian"; the rename
  makes the semantics ("the CRC field's byte order within the packet")
  unambiguous.
- `crc_generate` collapses the 8 per-language CLI subcommands into one
  tool with a `language` enum; pre-validates `variant` against
  `LanguageInfo.variants_for_width(width)` so the LLM gets a
  structured error rather than the deeper width-32/64 message.
- Performance steer in `crc_generate`'s description tells the LLM to
  prefer the target's stdlib (Python `zlib.crc32`, etc.) for IEEE
  crc32 and crc32-jamcrc — ~30× faster than generated code via CPU
  CRC instructions.

### NEW: `crc32-bacnet`, `crc8-bacnet`

BACnet MS/TP frame CRCs from ANSI/ASHRAE 135 Annex G (the spec is
paywalled, but the algorithms are quoted verbatim in IETF
draft-lynn-6lo-rfc8163-bis-01 Appendix C and reproduced in
bacnet-stack's reference C implementation):

- `crc32-bacnet` — large-frame CRC-32K (Koopman polynomial
  `0x741B8CD7` with `xorout=0xFFFFFFFF`).  Note: same polynomial as
  `crc32-mef` but distinct algorithm because the BACnet xorout
  inverts the final value.
- `crc8-bacnet` — header CRC over MS/TP frame headers (polynomial
  `0x03`, reflected, `init=xorout=0xFF`).

Catalogue size: 70 → 72.

### BREAKING: `AlgorithmInfo` gains a `source: str` field

Every catalogue entry now self-documents where its Rocksoft/Williams
parameters were sourced from.  The 70 reveng-derived entries carry
`source="reveng"`; the two BACnet entries carry
`source="ietf:draft-lynn-6lo-rfc8163-bis-01"`.

```python
>>> ALGORITHMS["crc16-modbus"].source
'reveng'
>>> ALGORITHMS["crc32-bacnet"].source
'ietf:draft-lynn-6lo-rfc8163-bis-01'
```

The new field is also surfaced by `crcglot info`, `crcglot list
--json`, the MCP `crc_info` tool, and the `crcglot://catalogue.json`
resource.

Breaking because constructing an `AlgorithmInfo` directly now requires
the field.  Custom callers (e.g. `generator_from_entry` for an
off-catalogue polynomial) need to pass `source="custom"` or similar.

### NEW: `tests/test_external_vectors.py` — multi-vector engine verification

169 cross-checks against four independent authorities catch engine /
parameter bugs that single-input verification misses:

1. `zlib.crc32` as the canonical oracle for IEEE crc32 + crc32-jamcrc
   over 13 input shapes (empty, single byte, patterns, 1 MB random).
2. Every catalogue entry's `check` field at `b"123456789"` (72
   algorithms).
3. Hardcoded BACnet vectors ported from bacnet-stack's reference C
   implementation (which the upstream file declares "copied directly
   from the BACnet standard").
4. 47 published vectors from primary specs:
   - RFC 7143 Appendix A.4 / RFC 3720 Appendix B.4 (5 vectors for
     `crc32-iscsi` / Castagnoli).
   - AUTOSAR_SWS_CRCLibrary R22-11 Tables 7.2 / 7.4 / 7.6 / 7.8 / 7.10
     / 7.12 / 7.14 (7 vectors each for `crc8-sae-j1850`, `crc8-autosar`,
     `crc16-arc`, `crc16-ibm-3740`, `crc32`, `crc32-autosar`,
     `crc64-xz`).

This expansion caught a real bug during development.  `crc8-bacnet`
was initially landed with `poly=0x81` per a paraphrased web summary of
the BACnet polynomial.  Both `0x81` and the correct `0x03` produce
`0x89` on `b"123456789"` by coincidence, so the canonical-check test
passed.  The bacnet-stack reference cross-check disagreed on every
other input, identifying the polynomial error before the commit
landed.  Had `0x81` shipped, every BACnet consumer would have seen
wrong CRCs except on the canonical input.

### NEW: CLI polish

- `crcglot list --json` — machine-readable JSON of the catalogue
  (matches the MCP `crc_list` tool's output shape).  Use case: pipe to
  other tooling without parsing the human-formatted output.
- `crcglot compute` defaults flip: hex output is now the default; pass
  `--dec` for decimal.  Hex is what almost every consumer of a CRC
  value wants (struct fields, comparison against doc-quoted values);
  the previous decimal default forced an extra conversion step.
- New CLI subcommand: `crcglot compute <algorithm> [<data>]` —
  computes the raw CRC integer without packet framing (wraps the
  existing public `encode_int()` function).  Added so the new MCP
  `crc_compute` tool has a CLI mirror.

Suite: 3258 passed, 0 skipped on the full suite (the slow tier
compiles + runs the generated source for every algorithm × language);
92% coverage.

## v0.10.0 — 2026-06-01

Catalogue ergonomics: one new algorithm, and the catalogue itself moves
from a private raw-dict + builder pair to a single typed dataclass
literal -- one source of truth, type checker validates entries at the
construction site.

### NEW: `crc16-usb`

USB Token / Data packet CRC.  Identical to `crc16-modbus` except for
the final XOR (`xorout=0xFFFF` vs modbus's `0x0000`), so the canonical
check value `0xB4C8 == 0x4B37 ^ 0xFFFF`.

```python
from crcglot import ALGORITHMS, generic_crc

algo = ALGORITHMS["crc16-usb"]
generic_crc(b"123456789", algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout)  # 0xB4C8
```

Catalogue size: 69 → 70.  All language generators and the runtime
engine pick it up automatically.

### BREAKING: `AlgorithmInfo.name` removed; catalogue is now direct

Before, `crcglot/catalogue.py` held a private
`_REVENG_CATALOGUE: dict[str, dict]` of raw kwargs and a
`_build_algorithms()` builder that walked it to construct each
`AlgorithmInfo`.  Now `ALGORITHMS: dict[str, AlgorithmInfo]` is itself
the literal -- each entry constructs its dataclass directly with
kwargs.  No more parallel data structures.

The `name` field was dropped from `AlgorithmInfo` because it always
shadowed the dict key (a parametrized test asserted exactly that
redundancy); callers can't reach the dataclass without already knowing
the key.  The 9 places that constructed `AlgorithmInfo(name=..., ...)`
(CLI custom-CRC path + per-language synthetic-algo tests) drop the
kwarg.

If you were reading `algo.name` anywhere, replace with the key you
used to look the entry up.

### Doc: `BENCHMARKS.md` -- unified Python row + clearer narrative

The Results table now shows all four Python paths inline:
`bit-by-bit`, `table-driven`, `c-extension` (the optional accelerator
that lifts pure Python from ~1 MB/s to ~1,700 MB/s for any of the 70
algorithms), and `zlib crc32 only` (the stdlib's CPU-instruction
fast path -- PCLMULQDQ on x86, PMULL / `crc32` on ARMv8 -- which
hits ~48 GB/s on 1 MiB buffers and applies to `crc32` and
`crc32-jamcrc`).  The old "Runtime engines" section consolidated into
a closing prose summary.

Suite: 2930 passed, 0 skipped (true green); 92% coverage.

## v0.9.1 — 2026-05-31

Bug fix: `detect()` with `target_crc=` now matches when the caller's
integer is the byte-reversed-at-width form of the algorithm's CRC.

Before, a caller whose tool printed the CRC bytes and read them
little-endian (e.g. `0x2639F4CB` instead of `0xCBF43926`) got no match
even though the data + algorithm + CRC bytes were correct.  The
comparison was a single `computed == target_crc` and the docstring
claimed the `endian` selector was moot under `target_crc`.

Now the path tries both readings per algorithm — the integer as-is
(big-endian) and byte-reversed at the algorithm's width (little-endian)
— and the matching `DetectMatch` reports the corresponding endianness:

```python
from crcglot import detect

# Natural BE reading of the bytes (unchanged behavior).
detect(b"123456789", target_crc=0xCBF43926).endianness   # 'big'

# LE reading of the same bytes (now matches; previously didn't).
detect(b"123456789", target_crc=0x2639F4CB).endianness   # 'little'

# endian= narrows the reading set, same as on the binary / text paths.
detect(b"123456789", target_crc=0x2639F4CB, endian="big").matched     # False
detect(b"123456789", target_crc=0xCBF43926, endian="little").matched  # False
```

Width-8 algorithms still dedup (BE and LE are byte-wise identical
under a single-byte CRC).  No public-API surface change; just the
comparison semantics.

## v0.9.0 — 2026-05-31

`detect()` learned to take a CRC out-of-band (`target_crc=`), to be
narrowed by byte order (`endian="big" | "little" | "both"`), and to
auto-decode hex-formatted byte strings in any common layout.  The
generator API tightens up: a single `variant=` kwarg replaces the
old `table=` / `slice8=` booleans on every target, and
`LanguageInfo.variants_for_width(width)` surfaces the slice-by-8
width constraint so callers don't have to encode magic numbers.
The conftest moves a long-standing source of silent test skips out of
the regression budget.

### NEW: `endian` selector on `detect()` / `detect_iter()`

```python
from crcglot import detect

# Default: try both byte orders (current behavior).
detect(packet)                       # "big" + "little" considered

# Narrow the scan: useful when the wire format is known, or when you
# want to rule out coincidental matches under the wrong endianness.
detect(packet, endian="big")
detect(packet, endian="little")
```

Halves the candidate set under a narrow selector.  Width-1 algorithms
respect the caller's label even though the byte content is invariant.
No effect on the `target_crc` path (no byte parsing happens).

### NEW: `target_crc=` — CRC supplied out of band

For protocols where the CRC arrives in a separate header field, separate
file, or as a user-typed expected value:

```python
# Whole packet is data; the integer is the externally-known CRC.
result = detect(b"123456789", target_crc=0xCBF43926)
result.algorithm    # 'crc32'
```

For each catalogue algorithm whose width can hold `target_crc`, the
computed CRC of `packet` is compared to `target_crc` directly — no
slicing of the tail.  Multi-packet input requires every packet's CRC
under the same algorithm to equal `target_crc`.

### NEW: auto-decode of hex-formatted packets

`detect()` on a `str` now transparently decodes hex-encoded byte strings
in any common surface format before scanning:

```python
# All of these resolve to the same 13-byte packet and match crc32:
detect("313233343536373839cbf43926")                                       # no separators
detect("31 32 33 34 35 36 37 38 39 cb f4 39 26")                           # wireshark-style
detect("0x31 0x32 0x33 0x34 0x35 0x36 0x37 0x38 0x39 0xcb 0xf4 0x39 0x26")  # 0x per byte
detect("31:32:33:34:35:36:37:38:39:CB:F4:39:26")                           # xxd / MAC style
detect("0x31,0x32,0x33,...,0xcb,0xf4,0x39,0x26")                           # C-array style
```

The surface format is captured in a new `HexFormat` dataclass on the
match's `padding` slot, so `encode_match(data, match)` reproduces the
exact same string byte-for-byte.  Plain `"data <whitespace> hex"` text
packets fall through the original parser unchanged.

Three explicit overrides on `mode=`:

- `"binary"` — bytes-like only
- `"text"` — skips the hex-pre-decode step, treats the whole str as
  `"data <sep> hex"`
- `"hex"` — requires the input to be hex-encoded bytes (no text-mode
  fallback), useful when you don't want a fortuitous text-mode
  re-interpretation

### BREAKING: `variant=` replaces `table=` / `slice8=` on every generator

The two boolean kwargs collapse into one Literal-typed string:

```python
# Before (v0.8.x)
generate_c("crc32")                       # bitwise
generate_c("crc32", table=True)
generate_c("crc32", slice8=True)

# Now (v0.9.0)
generate_c("crc32")                       # bitwise (default)
generate_c("crc32", variant="table")
generate_c("crc32", variant="slice8")
```

Applied to all eight generators (`generate_c`, `generate_csharp`,
`generate_go`, `generate_python`, `generate_rust`, `generate_typescript`,
`generate_verilog`, `generate_vhdl`) and their `_from_entry`
counterparts.  CLI flags (`--table` / `--slice8`) are unchanged.

Python keeps `("bitwise", "table")` (per-int overhead eats the slice8
win); Verilog and VHDL accept only `"bitwise"` and now raise
`ValueError` for `"table"` / `"slice8"` instead of silently ignoring
the kwarg.  Width-32 / width-64 enforcement on `"slice8"` still raises
`ValueError` at generation time; the message references the new kwarg
name.

### NEW: `LanguageInfo.variants_for_width(width)`

Returns the canonical-ordered tuple of variants that work at a given
algorithm width — pushes the "slice8 only at width 32 / 64" magic
number out of consumer code:

```python
LANGUAGES["c"].variants_for_width(32)     # ('bitwise', 'table', 'slice8')
LANGUAGES["c"].variants_for_width(16)     # ('bitwise', 'table')        slice8 dropped
LANGUAGES["python"].variants_for_width(32) # ('bitwise', 'table')
LANGUAGES["vhdl"].variants_for_width(32)  # ('bitwise',)
```

`VARIANT_ORDER = ("bitwise", "table", "slice8")` is also exported for
callers that need the canonical ordering directly.

Consumer wrapper boilerplate becomes a one-liner:

```python
list(LANGUAGES[code].variants_for_width(width))
LANGUAGES[code].generator(name, symbol=symbol, variant=variant)
```

### Fix: silently-skipped Go tests (conftest)

Moved PATH setup from a session-autouse fixture to `pytest_configure`.
Session-autouse fixtures run *after* test collection — by the time
`HAS_GO = shutil.which("go") is not None` evaluated at test-module
import time, the Windows-specific PATH fixups hadn't fired yet, so 383
Go-toolchain tests were silently skipped behind a `pytest.mark.skipif`
that froze in the false state.  `pytest_configure` is a real pytest
hook that runs *before* collection; the `HAS_<tool>` flags now see the
corrected PATH.

CLAUDE.md gains an explicit "Skipped tests are not 'passed'" section
codifying the rule that surfaced from this incident: a test run with
non-zero skips is **amber, not green**, and silent skips need
investigation, not celebration.

### Fix: outer whitespace on text packets

`detect("123456789 cbf43926\n")` now matches; leading indentation /
trailing newlines / CRLF line endings from copy-paste or `stdin` are
stripped before the regex runs.

### Removed: `crc16m` / `crc16x` catalogue aliases

Dropped two long-standing aliases that pointed at `crc16-modbus` /
`crc16-xmodem`.  Catalogue size: 71 → 69.  If you were depending on
the alias names, switch to the canonical `crc16-modbus` / `crc16-xmodem`.

### Internal: language generators moved into `crcglot.lang`

`crcglot.lang.c`, `crcglot.lang.csharp`, etc. — they re-export through
`crcglot` so the public import paths (`from crcglot import generate_c`)
are unchanged.  Just internal tidying ahead of further generator work.

### Stats

2891 passed, 0 skipped (true green); 92% coverage on the full suite.

## v0.8.0 — 2026-05-28

A fast **runtime CRC** engine ships as an optional C extension
(`crcglot._c`), `--small` / `--fast` become the front door for picking an
implementation, and releasing now publishes a cross-platform wheel matrix.

### NEW: fast runtime CRC via `crcglot._c` ⚙️

Until now crcglot only *generated* source code.  It now also computes
CRCs directly — fast — for all 71 catalogue algorithms:

```python
from crcglot import generic_crc

# CRC-32/ISO-HDLC over any bytes-like object
crc = generic_crc(b"123456789", 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF)
```

`generic_crc` picks the fastest available path automatically — you don't
choose:

1. **zlib hardware** for IEEE CRC-32 and JAMCRC (PCLMULQDQ on x86, the
   `crc32`/PMULL instructions on ARM),
2. the **C extension** for every other algorithm — bit-by-bit, 256-entry
   table, or slice-by-8 selected by width, with a per-`(width, poly,
   refin)` table cache, releasing the GIL on large buffers,
3. a **pure-Python** fallback when the extension isn't built — identical
   results.

The extension also exposes incremental and batch APIs:

- `CrcStream(...)` — `update()` / `digest()` / `reset()` / `copy()` for
  streaming,
- `c_crc_many(...)` — checksum many buffers in one call.

It ships as a single **abi3 wheel per platform** (one wheel covers
CPython 3.11+, no per-version rebuilds), built and parity-tested across
Linux (x86-64 + aarch64, glibc + musl), Windows (x64 + arm64), and macOS
(arm64).

### NEW: `--small` / `--fast` intent flags 🎛️

Express *what you want*, not which table layout:

```bash
crcglot c crc32 --fast        # fastest the target + width support
crcglot c crc32 --small       # smallest code (bit-by-bit; the default)
```

`--fast` resolves to slice-by-8 for width 32/64 where the language
supports it, else a 256-entry table, else bit-by-bit.  `--table` and
`--slice8` remain as explicit expert overrides, and default output is
unchanged.

### Release + publishing pipeline

- Two-stage release scripts — `scripts/release_prep.py` and
  `scripts/release_publish.py` — with a runbook (`scripts/RELEASE.md`).
- Tagging a release triggers `wheels.yml` to build and publish every
  platform wheel + the sdist to PyPI via OIDC trusted publishing; the old
  single-platform `publish.yml` is gone.

### Benchmarks

`BENCHMARKS.md` gains a cross-language crc32 matrix (1 KiB / 1 MiB,
release builds) plus pure-Python vs C-extension vs `generic_crc`
runtime-engine comparisons.

## v0.7.0 — 2026-05-26

Two new targets (TypeScript and Verilog), display metadata on
`LanguageInfo`, README install section showing `uv tool install`, and
**Zig has been removed** -- see below.

### NEW target: TypeScript 🔷

```bash
crcglot typescript crc32 file=mycrc
```

Emits a single `.ts` module with `init` / `update` / `finalize`
streaming triple, a one-shot wrapper, and a runtime-callable
`_self_test()` returning `boolean`.  Three variants: bitwise, table,
slice-by-8.  State type is `number` for widths 8 / 16 / 32 and
`bigint` for width 64 -- both with native JS bitwise operators (no
external library, no runtime ceiling at 2^53-1).

The emitted module is runtime-agnostic -- pure TypeScript with no
imports, runs under Node, Bun, Deno, browser ES modules, or any
bundler.  Internally, uint32 coercion (`>>> 0`) is applied at the
right points so non-reflected CRC-32 results don't slide into the
negative int32 range and surprise the caller.

Verified end-to-end via `tsx` (Node) across all 71 catalogue
algorithms × bitwise / table / slice8 variants on this dev box.

### NEW target: Verilog 🔧 (SystemVerilog 2012)

```bash
crcglot verilog crc32 file=mycrc
```

Emits a single `.sv` file containing `package <fname>_pkg` with
`automatic` functions for the streaming triple, one-shot wrapper,
and `_self_test()` returning `bit`.  Bit-by-bit only -- like VHDL,
this is a simulator-friendly reference implementation; synthesizable
pipelined RTL is a future enhancement and a different shape
(`always_ff` blocks, not pure functions).

Verified end-to-end via Icarus Verilog (`iverilog -g2012` + `vvp`)
across all 71 catalogue algorithms.

### NEW: display metadata on `LanguageInfo`

Two new fields on the frozen `LanguageInfo` dataclass:

- `emoji: str` -- single-grapheme pictographic identifier (e.g.
  `"🦀"` for Rust, `"🔷"` for TypeScript)
- `display_name: str` -- human-readable name (e.g. `"C / C++"`,
  `"TypeScript"`).  Distinct from `code`, which is the dispatch key.

Useful for terminal output, generated documentation, and the
auto-EXAMPLES script (which now derives section headings from
`display_name` instead of a hardcoded dict, so a new language only
needs to land in `LANGUAGES` to show up everywhere).

### BREAKING: Zig target removed

`crcglot zig <algo>` no longer exists, `generate_zig` and
`generate_zig_from_entry` are no longer importable, and the `"zig"`
entry has been dropped from `LANGUAGES`.

Migration: pick another compiled target (`c`, `rust`, `go`, `csharp`,
`typescript`) for the same algorithm and recompile.  If you were
distributing crcglot-generated Zig source in a build pipeline, pin
to `crcglot==0.6.0` until you migrate.

Why removed: Zig 0.13 → 0.16 changed enough that the existing
generator's CRC-64 slice-by-8 output became flaky under parallel
test execution, and re-validating it wasn't the right use of this
release's scope.  Zig may return as a separate generator branch
later -- the design lives in git history at v0.6.0.

### NEW: README install section

`uv tool install crcglot` is now the recommended install path
alongside `pip install`.  Section covers `uv tool` (isolated CLI),
`uv add` (library use), `pip`, and `pipx`.

### Registry shape and test infra

- `LANGUAGES` now has 8 entries (TypeScript + Verilog added; Zig
  removed; net +1 vs v0.6.0).
- `scripts/regenerate_examples.py` is data-driven on `display_name`
  / `extensions` -- no per-language hardcoding.  A new language
  landing in `LANGUAGES` shows up in the regenerated gallery
  automatically.
- `tests/conftest.py` learns a Windows PATH-fixup pass for install
  dirs that don't propagate to already-open shells:
  `C:\iverilog\bin`, `C:\Program Files\nodejs`,
  `C:\Program Files\Go\bin`, `%LOCALAPPDATA%\Microsoft\WinGet\Links`,
  `%APPDATA%\npm`.  Lets the slow tier pick up freshly-installed
  toolchains without restarting VS Code.

## v0.6.0 — 2026-05-26

Rust generator: `_self_test()` is now a runtime-callable `pub fn`,
not a `#[cfg(test)]` test block.  Brings Rust in line with every
other target (C / Go / C# / Zig / Python / VHDL) and makes the v0.5.0
README claim about calling `_self_test()` in your build environment
actually true for Rust output.

### Generated Rust changed shape

Old emission (v0.5.0 and earlier):

```rust
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn check_value_matches_reveng() {
        assert_eq!(crc32(b"123456789"), 0xCBF43926);
    }
}
```

New emission:

```rust
pub fn crc32_self_test() -> bool {
    crc32(b"123456789") == 0xCBF43926_u32
}
```

The old block was only compiled under `cargo test` / `rustc --test`,
so a release-build caller couldn't wire `_self_test()` into a boot
self-check or startup assertion -- contradicting what the README
recommends.  The new shape compiles in every build configuration and
returns a `bool` you can branch on.

### Migration for v0.5.0 callers

If you were relying on `cargo test` discovering the embedded test,
wrap the new function in your own `#[test]`:

```rust
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn check_value_matches_reveng() {
        assert!(crc32_self_test());
    }
}
```

If you wanted `_self_test()` callable from release code -- you
couldn't on v0.5.0; now you can.

### Test harness rewire

`tests/test_rust_gen.py::TestGeneratedRustExecutes` switched from
`rustc --test` to compiling with an injected `main()` that calls
`_self_test()` and exits 0 iff it returned `true`.  Same algorithm
coverage, exercised through the same path a downstream consumer
would actually use.  README "What you get per language" caption and
`rust.py` module docstring updated; `EXAMPLES.md` regenerated.

## v0.5.0 — 2026-05-26

Public helper `generic_crc` for field-defined CRCs, plus a verification
section in the README.  No generator code changed; emitted source is
byte-identical to v0.4.0.

### `generic_crc` is now public API

The Rocksoft/Williams CRC engine that powers `--custom` and populates
the catalogue's `check` values has been promoted from `_generic_crc`
to `generic_crc` and is now exported from the package root.  Use it
to compute the canonical check value for a CRC that isn't in the
reveng catalogue -- e.g. a vendor-defined poly -- without going
through the CLI:

```python
from crcglot import AlgorithmInfo, LANGUAGES, generic_crc

check = generic_crc(b"123456789", 16, 0x1234, 0xFFFF, True, True, 0x0000)
algo = AlgorithmInfo(
    name="my_crc16", width=16, poly=0x1234, init=0xFFFF,
    refin=True, refout=True, xorout=0x0000, check=check,
    desc="Vendor-defined CRC-16",
)
code = LANGUAGES["rust"].generator_from_entry("my_crc16", algo, table=True)
```

Non-breaking: the underscore-prefixed name was documented as private
in the v0.4.0 module docstring, so no legitimate public consumer was
importing it.  Internal callers (`cli.py`, `tests/test_catalogue.py`)
updated in lockstep.

### README: "How it's verified" section

New section between "What you get per language" and "CLI reference"
spells out the two-tier verification strategy:

- The Python suite (run by CI on every push) checks every algorithm
  against its **hardcoded** reveng vector -- not the catalogue's own
  `check` field, so a silent engine regression can't hide -- and
  also runs the Python generator end-to-end (generated, exec'd,
  called) against the same hardcoded vectors.
- The slow tier on top of that compiles and executes the generated
  source for every algorithm in C, Rust, Go, C#, Zig, and VHDL via
  `gcc` / `rustc` / `go` / `dotnet` / `zig` / `ghdl`.

The section also recommends calling `_self_test()` once in your build
environment for every target except Python -- compiler version,
optimization flags, target endianness, and integer widths can each
subtly disagree with the reference toolchain.

### Verification on this release

Run on a partially equipped box (gcc / rustc / dotnet / ghdl
present; go and zig absent): 1676 passed, 483 skipped (the skips are
the go/zig slow-tier execution tests), 0 failed, 97% coverage.  Fast
tier in isolation: 928 passed, 97%.  Since v0.5.0 changes no
generator code, the go/zig emitted source is bit-identical to v0.4.0
(which was verified end-to-end with all six toolchains).

## v0.4.0 — 2026-05-26

Typed introspection API + slice-by-8 for every compiled target.
**Breaking change** — replaces the loose-dict public surface
(`CRC_CATALOGUE`, `GENERATORS`, `GENERATORS_FROM_ENTRY`) with frozen
dataclasses (`AlgorithmInfo`, `LanguageInfo`) and typed registries
(`ALGORITHMS`, `LANGUAGES`).

### Slice-by-8 expansion

Go, C#, and Zig now emit slice-by-8 implementations on demand
(`crcglot <lang> <algo> --slice8`).  Previously only C and Rust did;
the three new targets in v0.3.0 shipped with bit-by-bit and
`--table` only.  All five compiled languages now offer the same
three implementation shapes: bit-by-bit, table-driven, slice-by-8.

Verification: each new slice-by-8 generator is checked by an
execution-equivalence test that compiles both the bit-by-bit and the
slice-by-8 forms under disjoint symbol names, runs them on inputs of
varying lengths (0, 1, 7, 8, 9, 15, 16, 100 bytes), and asserts every
result matches.  Since the bit-by-bit forms are reveng-verified,
equivalence proves slice-by-8 is correct.

Python remains bit-by-bit + table only (CPython per-int overhead
measurably negates the speedup; measured 0.79x).  VHDL remains
bit-by-bit only (simulator reference, not synthesizable throughput).

### CI verification model clarified

`.github/workflows/tests.yml` now runs `pytest -m "not slow"` only.
The slow tests shell out to six different compilers to verify the
*generated* code; that's a developer-machine concern, not a CI one.
The verification crcglot actually ships is the embedded
`_self_test()` function the end user calls on their toolchain.

### New introspection API

```python
from crcglot import LANGUAGES, ALGORITHMS, LanguageInfo, AlgorithmInfo

# Iterate target languages and their metadata.
for code, info in LANGUAGES.items():
    print(code, info.extensions, sorted(info.variants))
    # info.generator(name, ...) and info.generator_from_entry(name, algo, ...)

# Iterate algorithms.
for name, algo in ALGORITHMS.items():
    print(name, algo.width, algo.check, algo.desc)
```

`LanguageInfo` carries the file extensions (`(".h", ".c")` for C; a
single-element tuple for every other language), the supported variants
(subset of `{"bitwise", "table", "slice8"}`), and references to the
two generator callables.  `AlgorithmInfo` carries the full Rocksoft /
Williams parameters plus the canonical reveng `check` value and a
human-readable `desc`.

### Removed (breaking)

- `CRC_CATALOGUE: dict[str, dict]` is no longer exported.  The raw
  data still lives in `crcglot.catalogue._REVENG_CATALOGUE` but is
  now private; consumers must move to `ALGORITHMS`.
- `GENERATORS: dict[str, Callable]` is no longer exported.  Move to
  `LANGUAGES[code].generator`.
- `GENERATORS_FROM_ENTRY: dict[str, Callable]` is no longer exported.
  Move to `LANGUAGES[code].generator_from_entry`, and pass an
  `AlgorithmInfo` instance (not a dict) as the second argument.

### Migration notes

Mechanical search-and-replace for downstream callers:

| Old                           | New                                       |
| ----------------------------- | ----------------------------------------- |
| `CRC_CATALOGUE`               | `ALGORITHMS`                              |
| `entry["width"]`              | `algo.width` (etc.)                       |
| `GENERATORS[lang]`            | `LANGUAGES[lang].generator`               |
| `GENERATORS_FROM_ENTRY[lang]` | `LANGUAGES[lang].generator_from_entry`    |
| `entry = {"width": ..., ...}` | `AlgorithmInfo(name=..., width=..., ...)` |

### Internal

- All seven generator modules (`c.py`, `csharp.py`, `go.py`,
  `python.py`, `rust.py`, `vhdl.py`, `zig.py`) now consume
  `ALGORITHMS` and `AlgorithmInfo` directly.
- `cli.py`: dropped private `_CRC_FILE_EXTENSIONS` / `_LANGS`
  constants; everything derives from `LANGUAGES`.
- New module `src/crcglot/targets.py` holds `LanguageInfo` and
  `LANGUAGES`; `AlgorithmInfo` and `ALGORITHMS` live in
  `src/crcglot/catalogue.py` next to the raw data.

## v0.3.0 — 2026-05-25

Three new language targets and a Python self-test.  No breaking
changes to existing targets.

### New targets

- **Go** (`crcglot go <algo>`) -- emits a `package crc` file with the
  streaming triple, one-shot wrapper, and `_self_test() bool`.
  Supports bit-by-bit and `--table`.
- **C#** (`crcglot csharp <algo>`) -- emits a single `.cs` file
  declaring a `public static class` (named in PascalCase from the
  algorithm) with the streaming triple, one-shot wrapper, and
  `_self_test() bool`.  Supports bit-by-bit and `--table`.
- **Zig** (`crcglot zig <algo>`) -- emits a `.zig` file with `pub fn`
  exports for the streaming triple, one-shot wrapper, and
  `_self_test() bool`.  Supports bit-by-bit and `--table`.

### Python self-test

The Python generator now emits `<fname>_self_test() -> bool`, matching
the convention of the other targets.  Previously Python only signalled
correctness via the docstring `check:` line.

### Verification coverage

Every algorithm in the catalogue × every shipped variant × every
target compiles and runs on the real toolchain, verifying four
patterns per algorithm: one-shot vs reveng check value, split-at-4
streaming, empty-chunk-first streaming, empty-chunk-last streaming.
Slow execution tests on the new targets are gated on `go`,
`dotnet` (with SDK -- runtime alone is not enough), and `zig` being
on PATH; structural tests always run.

Full-suite tests: 1994 collected, fast-suite coverage ≥ 99%.

### Tooling

- `CLAUDE.md`: codified branch-naming convention
  (`feat/<dashed-slug>` etc.).
- `cspell.json`: terms from the new generators.

## v0.2.0 — 2026-05-25

Developer-experience release.  No public API changes; existing
generated code is byte-identical to v0.1.0.

### Testing infrastructure

- **Tests reorganized by target language**: `test_python_gen.py`,
  `test_c_gen.py`, `test_rust_gen.py`, `test_vhdl_gen.py`, plus
  `test_catalogue.py` (cross-cutting) and `test_cli.py`.  Replaces
  the previous phase-based layout where each language's tests were
  spread across two files.
- **New `test_cli.py`** (88 tests): `crcglot.cli` coverage 0% -> 99%,
  exercising every subcommand, flag, error path, and exit code.
- **New per-variant structural tests** for C and Rust generators
  raise fast-suite coverage on `c.py` and `rust.py` from 91% / 74%
  to 100%.
- **Overall fast-suite coverage 39% -> 99%**, full-suite 1082 ->
  1187 tests.

### Tooling and docs

- `CLAUDE.md`: codified quality gates (ruff + ty + IDE Problems
  pane all zero), test commands, coverage targets, precommit ritual.
- `cspell.json`: project-root spell-check dictionary for the
  reveng / Rocksoft / toolchain terminology used throughout.
- README: four status badges (tests, coverage, ruff, ty); new
  "CLI reference" section documents every subcommand, flag, and
  `--custom` parameter in one place.
- New `EXAMPLES.md`: the actual generated code for `crc32` across
  all 9 language × implementation combinations (C / Rust / Python /
  VHDL crossed with bit-by-bit / table-driven / slice-by-8 where
  supported).  Readers can compare output shapes side by side
  without installing.
- `uvx ruff check src tests` and `uvx ty check src tests` both
  pass clean.

## v0.1.0 — 2026-05-25

Initial release.

### What's in

- **Catalogue:** 64+ named CRC algorithms from the reveng catalogue
  (CRC-8 through CRC-64), each with verified Rocksoft/Williams
  parameters and a canonical `crc("123456789")` check value.
- **Generators:** C, Rust, VHDL, Python source code per algorithm.
  Three implementation shapes per language: bit-by-bit, table-driven,
  slice-by-8 (CRC-32 / CRC-64 only).
- **Custom polynomials:** generate from raw Rocksoft/Williams
  parameters via the `from_entry` API or the `--custom` CLI flag.
- **Embedded self-tests:** every C / Rust / VHDL file ships with a
  `<fname>_self_test` function asserting the catalogue check value.
- **CLI:** `crcglot c crc32 --slice8 file=mycrc` (and equivalents
  for python / rust / vhdl).
- **Streaming API:** `init / update / finalize` for chunked data,
  plus a one-shot wrapper.

### Verification

Tests live in `tests/`.  Run with `uv run pytest`.  Verification
strategy:

- Python output is exec'd against the reveng check value for every
  algorithm.
- C / Rust / VHDL output is compiled by gcc / rustc / ghdl,
  executed, and asserted against the same check value.  Each
  toolchain is auto-skipped if not on PATH (so the suite still
  partially runs without a full polyglot install).
