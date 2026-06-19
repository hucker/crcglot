# CLI reference

Every crcglot capability is a subcommand of the form `crcglot <command> [options...]`:

One toolkit, three surfaces.  Every capability has the same name and shape on the CLI, over MCP, and in Python; the same table opens [docs/cli.md](cli.md), [docs/MCP.md](MCP.md), and [docs/api.md](api.md).

| Capability | CLI | MCP tool | Python |
| ---------- | --- | -------- | ------ |
| Browse the catalogue | [`list`](#crcglot-list-glob---json) | `crc_list` | `ALGORITHMS` |
| Algorithm parameters | [`info`](#crcglot-info-name) | `crc_info` | `ALGORITHMS[name]` |
| Detect a known CRC | [`detect`](#crcglot-detect-inputs) | `crc_detect` | `detect()` |
| Identify a non-CRC trailer | [`identify`](#crcglot-identify-inputs) | `crc_identify_trailer` | `identify_trailer()` |
| Reverse an unknown CRC | [`reverse`](#crcglot-reverse-inputs) | `crc_reverse` | `reverse_packets()` |
| Verify a frame | [`verify`](#crcglot-verify-algorithm-inputs) | `crc_verify` | `verify()` |
| Compute a CRC | [`compute`](#crcglot-compute-algorithm-data) | `crc_compute` | `compute()` |
| Batch compute | — | `crc_compute_many` | `generic_crc_many()` |
| Build a packet | [`encode`](#crcglot-encode-algorithm-data) | `crc_encode` | `encode()` |
| Stream chunked data | — | — | `crc_stream()` |
| Generate verified code | [`c`](#crcglot-c--csharp--go--java--python--rust--typescript--verilog--vhdl-algorithm-algorithm-options-tokens) / `rust` / … | `crc_generate` | `generate_c()` … / `LANGUAGES` |
| Custom polynomial | `--custom` tokens | `custom_params` | `custom_algorithm()` |
| Credits | [`credits`](#crcglot-credits) | `crc_credits` | `ATTRIBUTION` |

The generation subcommands are named after their target language (`crcglot c crc32`, `crcglot rust crc16-modbus`, …); everything else is a verb.  Exit codes are uniform: `0` on success/match, `1` on no-match/unknown name, `2` on invalid invocation.

## `crcglot list [GLOB] [--json]`

Browse the catalogue.  Optional `GLOB` filters by shell-style pattern (e.g. `crc16-*`).  Exit code 1 if nothing matches.

```bash
crcglot list                # more than 100 algorithms
crcglot list 'crc32-*'      # just the CRC-32 family
crcglot list --json         # machine-readable list with full parameters
```

## `crcglot info <name>`

Print parameters (width, poly, init, refin, refout, xorout, check, desc) for one algorithm.  Exit 1 on unknown name.

```bash
crcglot info crc64-xz
```

## `crcglot detect [INPUTS...]`

Brute-force identify which catalogue CRC matches a packet whose tail is the CRC.  Useful for reverse-engineering unfamiliar protocols, debugging captured frames, or confirming a sample really uses the CRC you expect.

If you have a hex string you can find the CRC type using `detect` with `--hex` (this frame is the bytes `123456789` followed by an unknown 2-byte trailer):

```text
> crcglot detect --hex "31323334353637383931c3"
crc16-xmodem  width=16  endianness=big
```

```text
> crcglot detect packet.bin   


From there, `crcglot c crc16-xmodem file=mycrc` generates the matching implementation.

All the input shapes and scan controls:

```bash
crcglot detect packet.bin                            # binary file (or '-' for stdin)
crcglot detect a.bin b.bin c.bin                     # multi-packet (intersected)
crcglot detect --text "123456789 cbf43926"           # text mode, inline
crcglot detect --text -                              # text mode, one packet per line on stdin
crcglot detect --hex "313233343536373839cbf43926"    # hex-encoded bytes
crcglot detect --algorithms 'crc16-*' packet.bin     # narrow the scan to a family
crcglot detect --match all packet.bin                # forensic: every candidate
crcglot detect --match set a.bin b.bin               # strict: succeed only on a single algorithm
crcglot detect --text '{"t":1234,"v":42,"crc":"1352"}'  # a crclink JSON frame
```

`--match` selects the strategy: `first` (default; early-stop on the first hit, priority order is `crc32`, `crc32-jamcrc`, `crc32-iscsi`, then the rest of the catalogue), `all` (exhaustive forensic view), `set` (strict singleton: succeed only if exactly one algorithm survives across all packets).  Exit 0 on match, 1 otherwise.  For text packets the inferred separator + hex leader + case are reported so you can reproduce the same format via `crcglot encode`.

A CRC is not always a bare tail.  A crclink JSON frame carries it inside the object as `{"t":1234,"v":42,"crc":"1352"}`, where the `"crc"` value is the CRC-16/XMODEM of the text up to that key.  `detect` recognises such named **payload forms** automatically and reports the algorithm plus the form:

```text
> crcglot detect --text '{"t":1234,"v":42,"crc":"1352"}'
crc16-xmodem  width=16  endianness=big  form=crclink  category=json  crc='1352'
```

`--form GLOB` (an fnmatch over form names) narrows or disables the form pass; the known forms live in `crcglot.FORMATS`.

When no CRC matches, `detect` (and `reverse`) also report a `trailer_hint` if the trailing field looks like a common **non-CRC** trailer; see `crcglot identify` below.

## `crcglot reverse [INPUTS...]`

Recover the parameters of an **unknown / custom** CRC from whole captured frames: the recovery counterpart to `detect`, for when the CRC is not any known algorithm.  Takes the same frame shapes as `detect`; supply several varied frames, including at least two of the **same length** with different content (their difference pins the polynomial) plus other lengths (separating `init` from `xorout`).  The catalogue is tried first; when nothing matches, it automatically escalates to algebraic recovery (suppress with `--std-only`).

Recovered candidates print as ready-to-paste `--custom` tokens, so the loop closes straight into generation:

```text
$ crcglot reverse --hex 5057523a31322e3430569771 --hex 544d503a34382e31433d4d                   --hex 52504d3a303031343530da2e --hex 5354413a4f4bea3b                   --hex 5057523a31322e333856b10d --hex 544d503a34382e3343bde8                   --hex 52504d3a303031343438eebc --hex 5354413a52554e0492                   --hex 5057523a31322e3431565723 --hex 4552523a4e4f4e458030
status=equivalent  candidates=4  validated_frames=8
--custom width=16 poly=0xA097 init=0x1D0F refin=true refout=true xorout=0x0
...
note: 4 (init, xorout) labellings reproduce all codewords identically ...  [CRC field: 2 byte(s), little-endian]

$ crcglot c --custom width=16 poly=0xA097 init=0x1D0F refin=true refout=true xorout=0x0 file=vendor_crc
```

| Option | Default | Effect |
| ------ | ------- | ------ |
| `--hex FRAME` | | One hex-encoded frame; repeat the flag for several. |
| `--text TEXT` | | Text frames (`data <sep> hex`; `-` reads one per line on stdin). |
| `--crc-bytes N` | auto | Trailing CRC field size in bytes. |
| `--byte-order` | `both` | CRC field byte order: `big`, `little`, or `both`. |
| `--std-only` | off | Catalogue match only; no algebraic recovery. |

Exit 0 when a catalogue algorithm matched or parameters were recovered (statuses `catalogue` / `unique` / `equivalent`; an `equivalent` result lists every (init, xorout) labelling, all of which produce identical CRCs).  Exit 1 when underdetermined or nothing fits; the guidance on stderr says what additional captures would help.

## `crcglot identify [INPUTS...]`

Identify a **non-CRC** trailing field in a packet.  Two kinds: simple checksums (8-bit sum / LRC / one's-complement / XOR, 16-bit sum, Internet checksum, Fletcher-16, Fletcher-32, Adler-32) and cryptographic digests (MD5, SHA-1, the SHA-2 and SHA-3 families, BLAKE2, double SHA-256; full length, or the common 4/8-byte leading truncations like base58check's `sha256d[:4]`).  Identification only: crcglot doesn't generate code for these (checksums are one-liners; digests live in every stdlib).  The point is information for whoever decides next, human or LLM: "the trailer is an Adler-32" or "found a 32-byte field that's no unkeyed digest, so likely a MAC" redirects the whole investigation in one step.

```bash
crcglot identify packet.bin                          # binary file (or '-' for stdin)
crcglot identify --hex "74656c656d65...4b8806d2"     # hex-encoded packet
crcglot identify --text "data 1f2a"                  # text packet
crcglot identify --trailers 'sha*' a.bin             # narrow the candidates
crcglot identify --endian little a.bin b.bin         # checksum byte order (default: try both)
```

```text
$ crcglot identify --hex "74656c656d657472792d6672616d652d3030314b8806d2"
adler32  kind=checksum  width=32  endianness=big  frames_agreed=1  (Adler-32)

$ crcglot identify --hex "66772d757064...<sha256 trailer>"
sha256  kind=digest  width=256  endianness=big  frames_agreed=1  (SHA-256)

$ crcglot identify --text "sensor-frame-001 abab...ab"      # 32 bytes, matches nothing
No trailer match.
Note: found a 32-byte trailing field matching no un-keyed digest; could be a MAC
(HMAC / CMAC -- keyed, unverifiable without the key) or an uncommon / truncated hash
```

Keyed MACs are undetectable by design; that's the third example's answer, and it's still useful: it ends the CRC hunt and names what the field probably is.  Confidence scales with `frames_agreed`: one frame is a hint, several corroborating frames are a finding.  Exit 0 on a match, 1 otherwise.

## `crcglot encode <algorithm> [<data>]`

Build a packet by appending the CRC.  Round-trip partner to `detect`: feed `detect`'s `(algorithm, endianness, padding)` shape back to `encode` to rebuild a packet in the same format.

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

## `crcglot verify <algorithm> [INPUTS...]`

Check a frame's trailing CRC against a named algorithm: the per-frame yes/no, with the diagnosis when it's a no.  Use `detect` when the algorithm is unknown.

```text
$ crcglot verify crc16-xmodem --hex "31323334353637383931c3"
VALID  crc16-xmodem  expected=0x31C3  actual=0x31C3

$ crcglot verify crc16-xmodem --hex "32323334353637383931c3"   # corrupted first byte
INVALID  crc16-xmodem  expected=0x1C87  actual=0x31C3
```

Takes the same input shapes as `detect` (`--hex`, `--text`, or binary files; several files verify each in turn).  `--little` reads the CRC field little-endian.  Exit 0 when every frame is valid, 1 otherwise.

## `crcglot compute <algorithm> [<data>]`

Compute the raw CRC integer of some data: no packet framing, just the value.  The quick check when you have data in one hand and an expected CRC in the other.

```bash
crcglot compute crc16-modbus "123456789"        # → 0x4B37
crcglot compute crc32 "123456789" --dec         # decimal instead of hex
crcglot compute crc64-xz --binary < data.bin    # bytes from stdin
```

## `crcglot credits`

Print acknowledgments for the upstream work crcglot builds on (also exported as `crcglot.ATTRIBUTION` / `crcglot.ACKNOWLEDGMENTS`).

## `crcglot version`

Print the installed crcglot version (also exported as `crcglot.__version__`).  It is the same string stamped into the `Reproduce with crcglot` block at the top of every generated file, so you can match a file in hand to the release that produced it.

```bash
crcglot version        # e.g. 0.21.0
```

## `crcglot {c | csharp | go | java | python | rust | typescript | verilog | vhdl} <algorithm> [<algorithm>...] [options...] [tokens...]`

Generate source code for the chosen target language.  Pick your intent; crcglot picks the implementation:

| Option / token        | Effect                                                                                                                                                                                                            |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--small`             | Smallest code, zero RAM table (bit-by-bit).  Works for any width.                                                                                                                                                 |
| `--fast`              | Fastest the target supports: slice-by-8 for width 32/64 on compiled targets, table-driven otherwise.  **The default** when no variant flag is given.                                                              |
| `--custom`            | Use raw Rocksoft/Williams params instead of a catalogue lookup (see below).                                                                                                                                       |
| `--comment=STYLE`     | Documentation style for the generated comments (default `plain`).  See [Generated code style](generated-code.md).                                                                                                 |
| `--naming=CONVENTION` | Casing of the public function/method names (`snake` / `camel` / `pascal`).  Defaults to each language's idiomatic convention.  See [Generated code style](generated-code.md).                                     |
| `file=STEM`           | Write to disk (extension picked per language; see below).  Omit for stdout.                                                                                                                                       |
| `name=NAME`           | Rename the CRC: replaces the algorithm name as the base for the functions / class / filename, **cased per language** (`name=my-widget` → `my_widget.rs`, `MyWidget.java`, `MyWidget.cs`).  Single algorithm only. |
| `symbol=NAME`         | Escape hatch: emit this exact identifier **verbatim**, bypassing `--naming`.  Single algorithm; not for Java.  Prefer `name=` for the usual "call it X" case.                                                     |

File extensions per language: C emits `STEM.h` + `STEM.c`; Python `.py`; Rust `.rs`; VHDL `.vhd`; Verilog `.sv` (SystemVerilog 2012); Go `.go`; C# `.cs`; Java `.java`; TypeScript `.ts`.  For Java and C# the file is named after the public class (PascalCase of `name=` / the algorithm, or of `STEM`), so the stem must yield a legal class identifier; `STEM` is otherwise sanitized to a valid identifier (`file=my-crc` → `my_crc.rs`).

**Bundle several algorithms into one file** by naming more than one: `crcglot c crc32 crc16-modbus crc8 file=mycrcs` writes a single `mycrcs.h` / `mycrcs.c` containing all three (one `.go` / `.rs` / `.cs` / … for the other languages).  Each algorithm keeps its own catalogue-derived function names (`crc32`, `crc16_modbus`, …) and the tables are namespaced per symbol, so they never collide.  `name=` and `symbol=` rename a single CRC, so both are rejected with more than one algorithm; duplicates are de-duplicated; an unknown name aborts the whole bundle.

**Expert overrides** (you usually don't need these, since `--fast` chooses for you): `--table` forces the 256-entry single-table form, and `--slice8` forces the 8-table form.  They exist for the rare case where you want the *middle* of the size/speed curve explicitly, e.g. a RAM-constrained target where the 1 KiB table is fine but slice-by-8's 8 KiB isn't.  `--slice8` is CRC-32/64 + compiled targets only.

Rules:

- The variant selectors `--small` / `--fast` / `--table` / `--slice8` are mutually exclusive: pick at most one (exit 2 otherwise).  No selector = `--fast` (the fastest the target supports); pass `--small` for the smallest code.
- `--slice8 python` silently falls back to `--table` (CPython's per-int overhead eats the slice-by-8 speedup; stderr warns).  `--fast` never needs this fallback; it only picks slice-by-8 where it actually applies.
- Without `file=`, output goes to stdout.  For C, header is emitted first, then source.
- Every target embeds `<symbol>_self_test()` (C returns 0 on success; the rest return `bool` / `boolean` / `bit`).  In constrained embedded targets, standard toolchain flags (`-Wl,--gc-sections` for C, LTO for Rust) strip whatever you don't call.
- Every file header carries a `Reproduce with crcglot` block of the resolved parameters (algorithm, target, variant, comment style, symbol, naming), so generated code says how it was produced.  There is no flag for it; it costs nothing once the compiler discards comments.  C additionally emits a linkable `const crcglot_provenance_t <symbol>_provenance` for runtime introspection of the CRC configuration.  Being a public symbol it never warns under `-Werror`; `-Wl,--gc-sections` drops it when unused, and `-DCRCGLOT_NO_PROVENANCE` omits it for a toolchain without section GC.  The block records only request-derived values (no tool version), so the same request always produces the same bytes.

## `--custom` (raw Rocksoft/Williams parameters)

For algorithms not in the catalogue:

```bash
crcglot c --custom width=16 poly=0x1234 init=0xFFFF \
         refin=true refout=true xorout=0x0000 file=mycustom
```

| Param       | Required | Notes                                                                                                       |
| ----------- | -------- | ----------------------------------------------------------------------------------------------------------- |
| `width=N`   | yes      | 8, 16, 32, or 64 only                                                                                       |
| `poly=X`    | yes      | Hex (`0x...`) or decimal                                                                                    |
| `init=X`    | no       | Default 0.  Hex or decimal.                                                                                 |
| `refin=B`   | no       | Default `false`.  Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`.                                     |
| `refout=B`  | no       | Default `false`.  Same boolean syntax.                                                                      |
| `xorout=X`  | no       | Default 0.                                                                                                  |
| `name=NAME` | no       | Default `crc_custom`.  Names the functions / class / filename (cased per language) and labels the comments. |
| `desc=TEXT` | no       | Free-form description in comments.                                                                          |

The check value for the custom parameters is computed automatically (`generic_crc(b"123456789", ...)`) and embedded into the generated `_self_test()`.
