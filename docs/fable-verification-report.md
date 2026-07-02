# Fable verification report

An independent adversarial verification of crcglot, run by Claude Fable 5 on 2026-07-02 against the 0.25.1 working tree. This is a from-scratch rerun of the method recorded in [independent-verification-report.md](independent-verification-report.md): it does not reuse that report's oracle, harnesses, or frames, and it deliberately does not rely on the bundled test suite. Beyond re-grading every core claim, this pass extended coverage to places the prior passes did not reach: sub-byte widths in the engine fuzz, streaming chunk-boundary invariants, mixed-reflection and 24-bit algorithms in the execution matrix, and targeted attacks on every input-validation boundary.

## Method

The oracle is a bit-at-a-time MSB-first LFSR written from scratch, sharing no code or structure with crcglot's byte-aligned table engine. It earned its role by reproducing `zlib.crc32` on ten varied buffers and twenty published reveng-catalogue check constants entered by hand (widths 3 to 64, reflected and non-reflected, including CRC-11/FLEXRAY and CRC-24/OPENPGP) before grading anything. Generated code is checked by execution through real toolchains: gcc 15.2, rustc 1.95, go 1.26.3, dotnet 9, javac/java 21, tsx on Node 24, iverilog 12, ghdl 5.1.1, CPython 3.14. The novel input for this pass is `Hole-hunt 2026-07-02 crcglot!`, chosen fresh so no prior pass's expected values could leak in.

## What holds

Every core claim re-graded clean:

- **Catalogue and engines.** All 113 catalogue check constants match the oracle. `compute`, `generic_crc`, the pure-Python reference loop, and the C extension agree with the oracle across the four self-test inputs for every algorithm (452 cases per engine; the fifteen sub-byte algorithms ride the pure-Python path by design). `generic_crc_many` matches per-buffer oracle values with mixed `bytes` / `bytearray` / `memoryview` batches.
- **The new `self_test_vectors` surface.** All 452 goldens (113 algorithms, four inputs) match the oracle, which makes this pass a third independent engine confirming the anycrc + crccheck pair. The CLI `vectors` command formats sub-byte hex correctly and its JSON `input_hex` fields reproduce the documented `binary_1k` formula; the MCP `crc_self_test_vectors` tool returns the same values with a did-you-mean on typos. `self_test_vectors` resolves an `AlgorithmInfo` by parameters, returns `None` for a custom, and raises the catalogue-aware error for unknown names.
- **Fuzz, now including sub-byte widths.** 40,000 random parameter sets over widths 1 to 64 (35,695 through the C engine), lengths straddling the slice-by-8 block boundaries: zero mismatches against the oracle. Prior passes fuzzed widths 8 to 64 only.
- **Streaming.** 2,000 cases with the message split at random chunk boundaries (empty chunks included) match the one-shot oracle value; `digest` is non-destructive, `reset` reproduces the empty-message CRC, `copy` is state-independent, and `hexdigest` pads to the width. The zlib, C, and pure-Python backends were all exercised.
- **Nine of nine languages execute, wider than before.** Eleven algorithms spanning widths 3, 5, 7, 8, 12, 16, 24, 32, and 64, including the mixed-reflection crc12-umts (refin false, refout true) and the 24-bit crc24-openpgp, were generated as default invocations and as multi-algorithm bundles in every variant each target supports. All 40 builds compiled without warnings, passed their embedded self-tests, and reproduced the oracle on the novel input. The `name=` override produced correctly cased filenames in all nine targets.
- **Packet tools.** `encode` matches oracle-built big-endian framing; `verify` accepts good frames and rejects payload and CRC corruption; multi-frame `detect` named all nine algorithms tried and returned no match on noise; `identify_trailer` classified MD5, SHA-256, and an 8-bit sum, and refused a random 16-byte trailer. `reverse` recovered crc16-modbus from `(message, crc)` pairs and recovered a non-catalogue polynomial (0x1337, init 0xABCD) exactly, with the recovered parameters reproducing a held-out oracle frame; the pairs form and the codeword form agree. Its input validation rejects an empty list, mixed shapes, a `bool` CRC, and a `str` CRC, each echoing the offending value.
- **Findings 1 to 3 of the prior report stay closed.** C# default invocations compile and run; the HDL self-tests check their two scoped vectors and pass under iverilog and ghdl; a `Crc` with width 65 or 0 is rejected with the documented message at every entry point that takes a `Crc`.
- **The `[mcp]` extra error.** With the `mcp` SDK import blocked, `import crcglot.mcp` still succeeds (the lazy contract), `crcglot.mcp.main()` exits with the actionable install message, and a break in crcglot's own server module correctly re-raises instead of being mis-translated into the missing-extra message.
- **Hygiene.** `uvx ruff check` and `uvx ty check` are clean over `src` and `tests`. No bare `except`, `eval` / `exec`, `os.system`, `shell=True`, or `subprocess` in shipped code, and no mutable default arguments. Twelve `assert` statements ship, all type-narrowing invariants, none validating input. The only extra referenced anywhere is `crcglot[mcp]`, which `pyproject.toml` defines.

## Findings

None of the findings touch the compute math. They cluster around boundaries: doors that skip a guard the front door enforces, one surface narrower than its siblings, and generated prose that overclaims for custom polynomials.

### Finding 1 (functional): the `CrcStream` keyword constructor bypasses the width guard

The prior report's Finding 3 was fixed by validating width in `Crc.__post_init__`, which guards every path that takes a `Crc`: `from_name`, `from_crc`, `from_info`. The keyword constructor documented as "the low-level path for custom CRCs" takes raw integers and never builds a `Crc`, so it kept both failure modes the fix eliminated:

```text
CrcStream(width=65, poly=0x1B, init=0).update(b"123456789"); .digest()
    -> 34947861251588560095      # a 65-bit value, silently outside the 1..64 contract
CrcStream(width=0, poly=0x1, init=0)
    -> ValueError: negative shift count   # the raw leak, one door over
```

Its docstring also still reads "width: CRC bit width (8, 16, 32, 64)", the stale width list the cruft audit warns about. Suggested fix: apply the same 1..64 check (same message) in `CrcStream.__init__`, and correct the docstring.

### Finding 2 (capability gap): CLI `--custom` rejects widths the API and MCP generate

`crcglot c --custom width=5 poly=0x05` exits 2 with `custom CRC width must be 8, 16, 32, or 64 (got 5)`. The same request succeeds through the Python API (`generate_files(None, custom=custom_algorithm(width=5, ...))`) and through the MCP `custom_params` path, both of which produced correct code whose embedded vectors match the oracle. The generators do support sub-byte and non-byte widths (the catalogue's width 3, 5, 7, 12, and 24 entries generate and execute in all nine languages), so the CLI restriction is a leftover from before that support landed, and its message misstates the library's real 1..64 range. This also breaks the documented rule that the CLI, MCP, and Python surfaces stay capability-identical.

### Finding 3 (accuracy): generated comments on custom polynomials overclaim

Every generated file carries the line `Generated by crcglot from reveng/<symbol> -- a verified reference implementation`, and its self-test doc opens `Self-test the implementation against independent reference CRCs`. For a catalogue algorithm both statements are true. For a genuine non-catalogue custom, neither is: the polynomial is not from reveng, and the self-test is a single check value crcglot computed itself. README.md scopes this exactly right ("A custom (non-catalogue) polynomial has no independent reference, so it falls back to a single check value crcglot computed itself, a weaker check"); the generated file contradicts the package's own documentation. A good design detail surfaced on the way: a custom whose parameters coincide with a catalogue entry picks up the four independent goldens via parameter resolution. Suggested fix: emit a scoped provenance line for customs (custom parameters, self-computed check value) and scope the self-test summary line.

### Finding 4 (boundary validation): negative and out-of-range poly / init / xorout pass silently

`custom_algorithm(width=8, poly=-1)` succeeds, computes `check=127`, and renders `poly=0x-1` in the description; the CLI equivalent exits 0 and generates code whose comment block carries the same `poly=0x-1` token. Out-of-range values (a poly, init, or xorout with bits above the width) are also accepted everywhere. The masking the engines apply is at least consistent: pure-Python, the C extension, and the oracle over masked parameters all agree, fuzz-confirmed, so there is no wrong-answer divergence. But the validating front door hands back a check value that corresponds to no real CRC instead of the boundary error the project's own error conventions call for. Suggested fix: range-check the three value fields (0 to 2^width - 1) where width is already checked, with messages that echo the value.

### Finding 5 (engine parity on input domain): exotic memoryviews diverge

Two cases break the "all paths produce identical output" contract on the accepted-input side rather than the value side:

- **Non-contiguous view** (`memoryview(data)[::2]`): the C path raises a raw untranslated `BufferError: memoryview: underlying buffer is not C-contiguous`; the pure-Python path accepts it and computes the CRC of the logical slice. So behavior depends on whether the accelerator is installed, and within one install a sub-byte stream accepts what a 16-bit stream rejects.
- **Non-byte itemsize view** (`memoryview(array('H', ...))`): the C path hashes the underlying bytes (defensible, and what `zlib.crc32` does); the pure-Python path iterates 16-bit items as if they were bytes and returns a silently wrong value. Measured: 0x5349 (C, equals the oracle over the raw buffer) versus 0x2d9b (pure Python) for the same call.

Suggested fix: normalize at the public boundary. Cast contiguous non-byte views with `.cast("B")` (no copy), and translate the non-contiguous case into one friendly error on both engines.

### Finding 6 (error quality): raw leaks at public boundaries

Four places leak low-level messages where the 0.25.x conventions promise translation:

- `reverse([raw_frame_bytes])`, the natural confusion with `reverse_packets`, raises `too many values to unpack (expected 2)`.
- `generate_files(algorithm_info)` (an `AlgorithmInfo` where a name is expected) raises `'AlgorithmInfo' object is not iterable` instead of pointing at `custom=`.
- `generate_files(custom={...})` (a dict where an `AlgorithmInfo` is expected) raises `AttributeError: 'dict' object has no attribute 'width'` from inside a generator module.
- The pure-Python engine reports wrong-typed data as `unsupported operand type(s) for >>: 'str' and 'int'` where the C path says `a bytes-like object is required, not 'str'` (the boundary normalization from Finding 5 also fixes this).

### Finding 7 (cruft): stale references and one unscoped claim

- Four comments cite `tests/test_crc_codegen_exec.py`, which no longer exists after the by-language test reorganization: `_helpers.py:307`, `lang/c.py:25`, `lang/c.py:280`, `lang/vhdl.py:29`.
- The `CrcStream` keyword-constructor docstring width list (Finding 1).
- README's per-language table row says `_self_test` verifies "against four independent reference CRCs", unscoped; the HDL targets check two by design and customs one, as the README's own "How it's verified" section states correctly further down.
- `reverse`'s guidance note can emit "all 1 frames have distinct lengths" for a single frame.

### Observation (not a defect): `reverse` wants more frames than its note suggests

The leave-one-out uniqueness check added in 0.24.0 is conservative. Four same-length frames plus three other lengths of a real custom CRC still came back "underdetermined"; five same-length frames plus seven other lengths recovered the model exactly with "parameters fully determined". It never fabricated a candidate, and the notes correctly diagnose what is missing, but "capture a few frames at one length" undersells the amount of data the validator wants. Recorded here to set expectations; the conservative default is the right trade.

## Bottom line

The compute engine, the C accelerator, the streaming API, the reverse-engineering search, all nine code generators, the packet tools, and the new `self_test_vectors` surface are correct under independent adversarial testing that went wider than the prior passes (sub-byte fuzz, chunk-boundary streaming, mixed-reflection and 24-bit execution). The prior report's three findings stay closed. The seven findings above are boundary and accuracy issues: one unguarded constructor, one surface narrower than its siblings, overclaiming comments on custom output, three validation gaps, and cruft. None affect a computed CRC value on the documented input domain.
