# crcglot MCP server

`crcglot[mcp]` ships an optional [Model Context Protocol][mcp] server so
LLM clients can call into crcglot's catalogue, detector, encoder, and
code generators by name.  The server runs as a stdio JSON-RPC process —
the standard MCP transport — and is composable with any MCP-aware client
(Claude Desktop, Cursor, Cline, mcp-cli, ...).

[mcp]: https://modelcontextprotocol.io

The MCP layer is purely **transport adaptation**.  Every tool wraps an
already-public Python function whose correctness is asserted by
crcglot's existing ~3,000-test suite.  Generated source is identical
byte-for-byte to what `crcglot <subcommand>` would emit; the detector
returns the same `(algorithm, byte order)` pairs the CLI does.

## Install

```bash
pip install 'crcglot[mcp]'
# or
uv tool install 'crcglot[mcp]'
```

The `mcp` SDK only ships with the extra; the base `pip install crcglot`
stays pure-stdlib.

## Connect

### Claude Desktop

Edit `claude_desktop_config.json` (Settings → Developer → Edit Config):

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

Restart Claude Desktop.  The seven `crc_*` tools and three
`crcglot://` resources show up under the server name `crcglot`.

### mcp-cli (smoke test)

```bash
uv tool run --from mcp mcp-cli \
  --command "uv run crcglot-mcp" \
  --tool crc_detect \
  --args '{"packet_hex": "31 32 33 34 35 36 37 38 39 cb f4 39 26"}'
# Expect: {"matched": true, "candidates":[{"algorithm":"crc32",...}]}
```

## Tools

Most tools map to a `crcglot` CLI subcommand (`crc_reverse` and `crc_verify`
are MCP-only).  Every tool is annotated **read-only / idempotent**
(`readOnlyHint`, `idempotentHint`, `destructiveHint=false`,
`openWorldHint=false`) — they only list / compute / generate, never mutate
state or touch the network — so clients can auto-approve them without prompting
per call.

The three **packet tools** — `crc_detect`, `crc_reverse`, `crc_verify` — all
take the same input shape (a whole frame with the CRC as the trailing field),
so an agent learns one convention: `crc_detect` names a *known* CRC,
`crc_reverse` recovers an *unknown* one, `crc_verify` checks a frame against a
named algorithm.  `crc_encode` is the inverse of `crc_verify` (it builds the
frame `crc_verify` checks).

### `crc_list(glob=None)`

Browse the catalogue.  Optional `glob` filters with shell-glob syntax
(`crc16-*`, `crc32*`).  Mirrors `crcglot list`.

### `crc_info(name)`

Full Rocksoft/Williams parameters for one algorithm.  Numeric fields
appear in both decimal (`poly`) and hex (`poly_hex`).  Mirrors
`crcglot info`.

### `crc_detect(packet_hex | packet_text | packet_b64, ...)`

Identify the catalogue CRC of a captured packet.  Accepts exactly one
packet form; optional `target_crc` / `target_crc_hex` for the
out-of-band-CRC use case; optional `algorithms` glob to narrow the
scan; `endian` selector and `match` mode pass through to
[`crcglot.detect`][det].

**Wire-format note**: the result's `crc_byte_order` describes the byte
order of the CRC field *within the packet* — not the byte order of the
surrounding protocol.  A big-endian protocol can serialize its CRC
little-endian (and vice versa); the two are independent.

[det]: ../src/crcglot/detect.py

### `crc_reverse(packets, crc_bytes=None, crc_byte_order="big", packet_format="hex", ...)`

Recover the parameters of an **unknown / custom** CRC from captured packets —
the recovery counterpart to `crc_detect`, which only identifies CRCs already in
the catalogue.  Use it when a device's CRC isn't any known algorithm.  Takes the
**same packet shape as `crc_detect`**: whole frames with the CRC as the trailing
field.

`packets` is a list of frames; `packet_format` selects the encoding — `hex`
(default, any common formatting tolerated), `base64`, or `text` for a
`data <sep> hexcrc` line (the trailing hex CRC is peeled structurally, the same
way `crc_detect` reads it — handy for log lines / NMEA-style frames).  Supply
several **varied** frames — varied in *content* (so the polynomial converges)
and in *length* (to separate `init` from `xorout`); ~6+ is typical, more is
better.  `crc_bytes` is the trailing field size for binary frames (null
auto-detects it — largest consistent cut wins; ignored for text, where the hex
field is already delimited); `crc_byte_order` is `big` / `little` / `both`.  Fix
any known parameter (`width` / `refin` / `refout` / `poly` / `init` / `xorout`)
to reduce how many frames are needed.

Returns `{status, candidates, catalogue_name, ambiguity_bits,
validated_frames, note}`.  `status` is `catalogue` (matched a known algorithm),
`unique`, `equivalent` (several `(init, xorout)` labellings are observationally
identical — **all** are returned, a complete and provably-exhaustive set of
size `2 ** ambiguity_bits`; the polynomial is always unique),
`underdetermined`, or `none`.  When the field size / byte order was
auto-detected, `note` records the split chosen.  Every returned model is
self-verified against the engine and validated against a held-out frame: a
recovered model is correct on unseen data, or honestly reports underdetermined —
never confidently wrong.  Clean-room (derived from CRC linearity over GF(2), not
from reveng).  Mirrors [`crcglot.reverse_packets`][rev].

[rev]: ../src/crcglot/reverse.py

### `crc_verify(algorithm | custom_params, packet_hex | packet_text | packet_b64, ...)`

Check whether a frame's trailing CRC is valid — the inverse of `crc_encode`
(which builds the frame) and the natural follow-up to `crc_detect` (which names
the algorithm).  Peels the trailing CRC field, recomputes the CRC over the
message, and compares.  Identify the CRC with `algorithm` (a catalogue name)
**or** `custom_params` (see below) — so you can validate further frames against
a CRC `crc_reverse` recovered.  Accepts the same frame shapes as `crc_detect`:
`packet_hex` / `packet_b64` (binary) or `packet_text` (`data <sep> hexcrc`).
Returns `{valid, expected, expected_hex, actual, actual_hex, width, algorithm}`
— comparing `expected` vs `actual` shows *how* a bad frame is wrong.  Mirrors
[`crcglot.verify`][vfy].

[vfy]: ../src/crcglot/encode.py

### `crc_encode(algorithm | custom_params, data_text | data_b64, ...)`

Append a freshly-computed CRC to data and return the packet.  Pairs
round-trip with `crc_detect` / `crc_verify`.  `crc_byte_order` controls the CRC
bytes only.  Takes `algorithm` or `custom_params` (see below).  Mirrors
`crcglot encode`.

### `crc_compute(algorithm | custom_params, data_text | data_b64, ...)`

Compute the raw CRC integer of data without packet framing.  Returns
`{crc, crc_hex, width}`.  Mirrors `crcglot compute`.

**Custom / recovered CRCs** — `crc_compute`, `crc_compute_many`, `crc_encode`,
and `crc_verify` each take **either** `algorithm` (a catalogue name) **or**
`custom_params`, a Rocksoft tuple `{width, poly, init, refin, refout, xorout}`
(`width` + `poly` required) — the same shape `crc_generate` accepts and the
shape `crc_reverse` returns.  This closes the loop: recover a vendor's custom
CRC with `crc_reverse`, then compute / verify / build packets with it directly
— no need to be in the catalogue.

> **Perf note for `crc32` / `crc32-jamcrc`** — these two algorithms run
> ~30× faster via your target language's stdlib (Python: `zlib.crc32`;
> C: zlib + PCLMULQDQ; Rust: `crc32fast`) because they use CPU CRC
> instructions.  crcglot internally delegates them to zlib already; the
> MCP round-trip is wasteful for hot paths.

### `crc_compute_many(algorithm | custom_params, data_texts | data_b64s, ...)`

Compute the CRC of **many** messages with one algorithm in a single
call — the batch form of `crc_compute`.  Each message is CRC'd
independently (not concatenated); results return in order as
`{algorithm, width, count, results: [{crc, crc_hex}, ...]}`.  Supply
exactly one of `data_texts` / `data_b64s` (a list).

Prefer this over looping `crc_compute`: it builds the lookup table once
for the whole batch (via the C extension's `c_crc_many`) and pays the
Python↔C transition once, so it is far faster for many small messages of
the same algorithm — packet streams, framed protocols, bulk validation.
One MCP call instead of N round-trips.

### `crc_generate(language, algorithm, variant="bitwise", ...)`

Emit verified source code for a (language, variant) cell.
`language` ∈ {`c`, `csharp`, `go`, `python`, `rust`, `typescript`,
`verilog`, `vhdl`}.  Variants validated against
`LanguageInfo.variants_for_width(width)` — invalid combinations return
a structured error listing the valid options.  `custom_params` enables
off-catalogue Rocksoft tuples.  Mirrors `crcglot <lang>`.

`algorithm` accepts **one name, a list of names, or a space-separated
string** (`"crc32 crc16-modbus crc8"`) — multiple names **bundle into one
file** (one `.h` + one `.c` for C), each keeping its catalogue-derived
function names; per-symbol tables keep the bundle collision-free.  The
chosen `variant` must be legal for every algorithm's width (`slice8` is
width 32/64 only), `symbol` is rejected with more than one algorithm, and
the response's `algorithms` field lists what was generated.  Mirrors
`crcglot <lang> crc32 crc16-modbus … file=STEM`.

### `crc_credits()`

Attribution for reveng, zlib, and the Rocksoft/Williams parameterization.
Mirrors `crcglot credits`.

## Resources

Read-only JSON snapshots the LLM can ingest once and reason from.

| URI | Contents |
|-----|----------|
| `crcglot://catalogue.json` | Every algorithm with full parameters (decimal + hex) |
| `crcglot://languages.json` | Per-target metadata (extensions, supported variants, emoji) |
| `crcglot://variants.json` | `variants_for_width(width)` cross-product for widths 8/16/32/64 |

The third preempts invalid `crc_generate` calls — the LLM can check
`variants_by_width["32"]["python"]` before asking for `slice8` on
Python.

## Prompts

### `design-a-crc(use_case="")`

A user-invokable template for the open-ended *"I need a CRC"* /
*"add a checksum to my protocol"* ask — the case where someone would
otherwise grab an arbitrary algorithm.  It expands to a guided message that
walks the **match-vs-choose** decision: if the CRC must interoperate with
something you don't control, *match* it (`crc_detect` / `crc_reverse`); if the
protocol is yours, *choose* by sizing the CRC to the payload and overhead budget
(`crc32` for large / unconstrained transfers, `crc16` for small framed or serial
protocols à la XMODEM / Modbus / CAN, `crc8` for tiny payloads) — then
`crc_generate` / `crc_encode` / `crc_verify`.  The optional `use_case`
string is folded in.  The same guidance lives ambiently in the server
instructions, so the model steers correctly even without the explicit prompt.

## End-to-end example

```text
USER:  Here's a packet from my Modbus device:
       01 03 00 00 00 0A C5 CD
       Give me C code I can drop into my STM32 firmware.

LLM →  crc_detect(packet_hex="01 03 00 00 00 0A C5 CD")
   ←  { matched: true,
        candidates: [{ algorithm: "crc16-modbus", crc_byte_order: "little" }] }

LLM →  crc_generate(language="c", algorithm="crc16-modbus", variant="table")
   ←  { files: [{extension:".h", content:"..."},
                {extension:".c", content:"..."}] }

LLM:   It's CRC-16/Modbus.  The CRC field is little-endian on the wire
       (matches Modbus RTU).  Here's the C table-driven implementation
       with an embedded self-test.  Drop crc16_modbus.h + .c into your
       STM32 project.
```

## Troubleshooting

- **`crcglot-mcp: command not found`** — the `[mcp]` extra wasn't
  installed.  Use `pip install 'crcglot[mcp]'` (note the quotes — the
  bracket is shell-meaningful).
- **Claude Desktop doesn't list `crcglot` tools** — check the desktop
  logs (`~/Library/Logs/Claude/mcp.log` on macOS,
  `%LOCALAPPDATA%\Claude\logs\mcp.log` on Windows).  Most failures are
  `uvx` not on PATH for the Claude Desktop process; install crcglot
  into the user environment instead and use
  `"command": "crcglot-mcp"` with no `args`.
- **`variant=slice8 is not valid for language=python at width=...`** —
  expected; Python doesn't ship a slice-by-8 generator (per-int
  overhead eats the win in CPython).  Use `variant="table"` or read
  `crcglot://variants.json` first.

## What's deliberately *not* here (v1)

- **Prompts** — modern LLMs chain `crc_detect → crc_info → crc_generate`
  unprompted; adding a workflow prompt is discovery cost for zero lift.
- **Multi-packet session state** — every MCP call is stateless.
- **Reverse lookup by check value alone** — interesting, but not the
  workflow people actually ask for.
- **Custom polynomial design** — crcglot serves the existing catalogue,
  not selection.
