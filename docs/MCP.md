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

Each tool maps 1:1 to a `crcglot` CLI subcommand.

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

### `crc_encode(algorithm, data_text | data_b64, ...)`

Append a freshly-computed CRC to data and return the packet.  Pairs
round-trip with `crc_detect`.  `crc_byte_order` controls the CRC bytes
only.  Mirrors `crcglot encode`.

### `crc_compute(algorithm, data_text | data_b64, ...)`

Compute the raw CRC integer of data without packet framing.  Returns
`{crc, crc_hex, width}`.  Mirrors `crcglot compute`.

> **Perf note for `crc32` / `crc32-jamcrc`** — these two algorithms run
> ~30× faster via your target language's stdlib (Python: `zlib.crc32`;
> C: zlib + PCLMULQDQ; Rust: `crc32fast`) because they use CPU CRC
> instructions.  crcglot internally delegates them to zlib already; the
> MCP round-trip is wasteful for hot paths.

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

The third pre-empts invalid `crc_generate` calls — the LLM can check
`variants_by_width["32"]["python"]` before asking for `slice8` on
Python.

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
