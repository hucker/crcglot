# Decision log

A dated record of the design decisions behind crcglot, from the initial extraction on 2026-05-25 onward.

**How this was assembled.** Everything through v0.28.0 was reconstructed on 2026-08-12 from four sources already in the repository: the commit bodies (299 commits, ~3,600 lines of prose), the merged branch names, the revision history of `CLAUDE.md`, and `CHANGELOG.md` plus the reports in `docs/verification/`. The conversations that produced those decisions were not retained, so the reconstructed entries carry what the commits recorded: the decision, the reason given for it, and where to read the change. Alternatives weighed and dropped before a commit are not there, because git never saw them.

Entries from v0.30.0 onward are written **as the decision is made**, so they can carry what reconstruction cannot: the options that were rejected and why.

Each entry cites its commit. `git show <sha>` has the full reasoning.

## Origins: extraction and shape (v0.1.0 to v0.3.0, 2026-05-25)

**Split out of termapy.** CRC generation lived in `termapy.protocol.crcgen`. It moved to its own package so engineers who want the codegen do not pull in a TUI dependency tree, and so it can release on its own cadence. `6df70b8`

**Tests organized by target language, not by phase.** The old layout split structural tests and execution tests into two files, so seeing everything about one language meant reading both. One file per target plus `test_catalogue.py` for cross-cutting concerns. Small helpers were duplicated into each language file rather than factored into a shared `tests/_helpers.py`, keeping each file readable on its own. `3544fc4`

**Every target emits a callable `_self_test()`, including Python.** Python originally signalled correctness with a `check:` line in its docstring. That is documentation, not verification. It now emits a real function returning `bool`, so a caller can assert it from pytest or a startup check like every other target. `144a538`

**Execution tests against real toolchains, not just structural assertions.** Adding actual `go run` / `dotnet run` / `zig run` immediately surfaced three bugs no structural test could see: Zig 0.13 has no `<<%` operator, C# masks an int shift count to 5 bits so `b << 56` silently became `<< 24`, and Go requires imports directly after the package clause. `9a067d8`

**Streaming splittability is checked on every target, with distinct exit codes.** Each execution runner performs four checks in one compiled binary (one-shot, split-at-4, empty-chunk-first, empty-chunk-last) and exits 1 through 4 so a future regression in one specific pattern stays diagnosable. `cb25455`

## A typed public surface (v0.4.0 to v0.6.0, 2026-05-26)

**Loose dicts replaced by frozen dataclasses and typed registries.** `CRC_CATALOGUE` / `GENERATORS` became `ALGORITHMS` / `LANGUAGES` carrying `AlgorithmInfo` and `LanguageInfo`. Breaking, with a migration table in the commit message. Downstream tools iterate typed registries instead of hardcoding lists. `bae9148`

**CI runs the fast tier only.** The slow tier shells out to six compilers to check the generated code, which is a developer-machine concern. What ships to the user is the embedded `_self_test()` they call on their own toolchain, so compiler version, optimization flags, endianness, and integer widths cannot introduce a silent disagreement. `bae9148`

**EXAMPLES.md is generated, never hand-edited.** `scripts/regenerate_examples.py` walks `LANGUAGES`, so adding a target picks it up automatically. `bae9148`

**`_generic_crc` promoted to public `generic_crc`.** Needed for CRCs that are not in the catalogue, such as a vendor-defined polynomial, without routing through the CLI. `c8b40a0`

**Rust's self-test moved out of `#[cfg(test)]`.** The cfg block was discoverable by `cargo test` and invisible to everything else, so a caller wiring the self-test into a boot check (the path the README recommended) had nothing to call. It is now a plain runtime function. `fe67d04`

## Targets added, and one removed (v0.7.0, 2026-05-26)

**TypeScript uses `number` below width 64 and `bigint` at 64.** Both use native JS bitwise operators, no external library, no 2^53-1 ceiling. `>>> 0` coercion keeps non-reflected CRC-32 results out of the negative int32 range. `d54a881`

**Verilog and VHDL ship bit-by-bit only, deliberately.** On silicon, bit-by-bit is the streaming datapath, and the one-shot self-test already clocks data through `_update` incrementally. Synthesizable pipelined RTL was left as a future enhancement. `d54a881`, `8de5aae`

**Zig removed.** Zig 0.13 to 0.16 changed enough that the slice-by-8 CRC-64 output went flaky under parallel test execution, and revalidating the language was not in that release's scope. Migration was to pin `crcglot==0.6.0` or pick another compiled target. `d54a881`

**`LanguageInfo` gains `emoji` and `display_name`.** So terminal output, docs, and the examples gallery render a target without hardcoding labels, and the examples script becomes data-driven. `d54a881`

## Benchmarks (2026-05-27)

**Measured, single-threaded, and labelled as such.** Adaptive inner loop to 500 ms, median of three, and a prominent caveat that this is not a publication-grade benchmark. The goal was confirming the expected ordering, not producing a marketing number. `661855e`

**The monotonicity claim was wrong and got corrected.** The original text said any bit-by-bit beating table-driven indicated a methodology bug. Rust at `-O3` does exactly that, because LLVM unrolls and vectorizes the 8-iteration bit loop while a table lookup carries a serial dependency chain. Real result, not a bug. The checker was relaxed to flag only `slice8 < table`, which no legitimate optimization can invert. `4b66af8`

## The C extension (v0.8.0, 2026-05-27 to 05-28)

**Stable ABI pinned at 3.11, not 3.9.** The buffer protocol the wrapper uses only entered the Stable ABI at 3.11, and `requires-python` is `>=3.11` regardless. One `cp311-abi3` wheel per platform then covers every later CPython with no rebuild. Build backend moved from `uv_build` to setuptools, which supports native extensions. `592465a`, `d15fb6d`

**The parity test was comparing C against itself.** It checked `generic_crc` (which dispatches to C when the extension is present) against `_c.c_generic_crc`, a tautology once the wheel was installed. Splitting out a separately callable `_generic_crc_python` made the Python-versus-C claim hold on every run regardless of dispatch state. `219629b`

**The table cache was added, then removed.** Caching lookup tables per `(width, poly, refin)` forced a choice between a lock (correct, but serializing concurrent builds, so parallel search over distinct algorithms gets no speedup) and a data race. Removing it made the extension stateless and thread-safe by construction, correct on GIL and free-threaded builds alike, and dropped the shutdown leak and the 64-entry thrash cliff. Net deletion. Table reuse moved to where ownership is explicit: `CrcStream` builds once at construction, `c_crc_many` builds once per batch. `2bf76da`, then `4dee2b3`

**That removal made a documentation change necessary.** Without the cache, `generic_crc` in a loop rebuilds the table every call, 4 to 11x slower than needed on small buffers and worse the longer the loop runs. The docstring and README now say plainly that it is a one-shot and point hot loops at `CrcStream`. `923b9bf`

## External oracles (v0.11.0, 2026-06-03)

**The single canonical check value is not enough, demonstrated concretely.** `crc8-bacnet` was first landed with poly `0x81` from a web summary. Both `0x81` and the correct `0x03` produce `0x89` on `b"123456789"`, so the catalogue check passed. The cross-check against bacnet-stack's reference implementation failed on every other input and identified the error. Shipping `0x81` would have given every BACnet consumer wrong CRCs on every input except the canonical one. `af6a5a0`

**So verification pulls from four independent authorities.** `zlib.crc32` as the oracle for IEEE crc32, every catalogue entry's own check value, ported BACnet reference code, and 47 published vectors from RFC 7143, RFC 3720, and AUTOSAR R22-11. `af6a5a0`

**`AlgorithmInfo` gains `source`.** Reveng-derived entries say so; the BACnet pair cites its IETF draft. The provenance of each entry became visible through `info`, `list --json`, and MCP. `af6a5a0`

**Algorithm counts in prose became fuzzy, guarded by a test.** Exact counts had already drifted to four different values across README and the MCP description. Prose now says "more than 70" and a test trips at 80 telling the maintainer to bump the wording. `4663eee`, `6fbb7d1`

## Coexistence and the batch execution tier (2026-06-03 to 06-04)

**Lookup tables are namespaced per symbol.** Fixed names (`crc_table`, `CRC_TABLE`, `_TABLE`) meant two generated CRCs could not share a translation unit. All generators now emit `crcglot_table_<symbol>`, so distinct `symbol=` implies zero shared global identifiers. `023edbf`

**Execution tests batch the whole catalogue into one build per language.** The old tier spawned a compiler per case, roughly 300 processes per language, with TypeScript alone taking 4:06. The cost is process and compiler startup, not CRC math. One session-scoped fixture builds every algorithm and variant into a single source unit and caches the results; a parametrized test looks up the dict, so each case stays its own pytest node. Measured: TS 8.8s, Rust 8.8s, Go 12.8s, C# 22s, C 31s. `e6a669f`, `a3e09b8`

That single combined build is also the coexistence evidence. It only links because tables are per-symbol.

**Old per-algorithm classes are deselected, not skipped.** They live behind an `exhaustive` marker so a normal run stays green rather than amber, and `--exhaustive -k <algo>` still isolates one algorithm in its own translation unit. `e6a669f`

**Each batch is pinned with `xdist_group`.** Under `-n auto` a session-scoped fixture runs once per worker, so without the pin all 16 workers would rebuild the batch and spend most of the speedup. `e6a669f`

**Multi-algorithm bundling followed for free.** Per-symbol tables made `crcglot c crc32 crc16-modbus crc8 file=mycrcs` a CLI-parse and output-assembly change, via a new `combiner` callable on `LanguageInfo`. `9cb5ff8`

## MCP (v0.11.0 onward, 2026-06-03 to 06-08)

**Optional extra, lazy import, base install stays pure-stdlib.** `import crcglot.mcp` succeeds without the extra; only `main()` materializes the SDK. `d616d61`

**One wire-format field was renamed for the audience.** `DetectMatch.endianness` is exposed as `crc_byte_order` on the JSON boundary only, because LLMs read `endianness=little` as a claim about the protocol rather than about the CRC bytes within the packet. The internal field is unchanged. `d616d61`

**Tool descriptions matter as much as tools.** Noticed during MCP exploration and acted on repeatedly: `crc_list` gained a sentence steering toward discovery before the heavier tools, and `crc_generate` carries a performance steer toward the target's stdlib for IEEE crc32. `d616d61`, `3c8f3fc`

**All tools are annotated read-only and idempotent.** Every tool is a pure offline read that never mutates state or touches the network, so clients can auto-approve instead of prompting per call. A regression test keeps the whole surface annotated. `11b00ca`

**The server steers selection, not just usage.** The common failure mode is grabbing an arbitrary CRC with no rationale. The instructions carry the choose-versus-match fork (if the CRC crosses a boundary you do not control, match it) and size-to-payload guidance. Deliberately not a "default to crc32" rule, since that reflex is what the steering exists to prevent. Selection stays advisory, in the instructions and a `design-a-crc` prompt, rather than a rigid recommend-tool. `37f269d`

**Code generation defaults to the fastest valid variant, not the smallest.** It previously defaulted to bitwise, the smallest and slowest. `f49cdd3`

## Metadata parity (v0.13.0 to v0.14.0, 2026-06-05)

**Comment styles are a registry, and the compatibility matrix is derived.** Ten styles across nine languages, each a module plus one registry line. Generators emit structured `DocBlock`s and the style renders syntax, so a new style needs no generator change. `d88664b`

**Every metadata axis ships a record and a lookup.** `VariantInfo` / `variant_info` joined `StyleInfo` / `style_info`, and `LanguageInfo` gained `.styles` and `.variant_infos_for_width()` so a UI reaches one object instead of stitching namespaces together. Driven by friction a consumer hit across the 0.11, 0.12, and 0.13 upgrades. The conventions were written into `CLAUDE.md` in the same commit so the friction would not recur. `c2088d1`

## Completing the catalogue (v0.17.0, 2026-06-07)

**72 to 113 algorithms: sub-byte, non-byte-aligned, and CRC-24.** CAN, CAN FD, FlexRay, the CRC-24 family, the GSM/UMTS/CDMA2000/DECT/ATM telecom widths, and CRC-3 through CRC-7. `94011a1`

The widths forced real changes rather than data entry. The pure-Python reference gained a bit-serial branch for non-reflected widths below 8 (the C extension's domain is 8 to 64). The non-reflected bitwise loop feeds bytes MSB-first, because `byte << (width - 8)` underflows below width 8, which is a compile error in Rust, Go, and the HDLs, and undefined behavior in C. Java had masked left shifts only at widths 8 and 16 and relied on int wraparound at 32, wrong for the new widths 10 to 31. Detect and encode moved to `ceil(width/8)`-byte fields compared strictly, so a garbage pad bit is rejected rather than masked away.

**The cruft audit became a release step.** After the cache removal and the catalogue growth, a fresh-eyes sweep found a whole test class describing a table cache that no longer existed, a README reference to a `crcglot[fast]` extra that was never defined, and several stale counts. None were functional bugs, which is the point: tests assert behaviour, not the labels around them, so a green suite hides rotted prose. The recurring patterns went into `CLAUDE.md`. `3bd1dd1`

## Recovering unknown CRCs (v0.18.0, 2026-06-08)

**`reverse()` recovers parameters from codewords.** GCD over GF(2) of equal-length difference codewords recovers the generator (init and xorout cancel in the difference); a GF(2) linear solve recovers the complete `(init, xorout)` equivalence class. Most well-made CRCs carry the `(x+1)` factor, which admits several observationally identical labellings, so the whole finite class is returned with a canonical representative first rather than one arbitrary pick. `58be0e6`

**Clean-room provenance is explicit.** Derived from the public mathematics of CRC linearity over GF(2), written from first principles, not from CRC RevEng's source. Reveng is GPLv3+; crcglot is MIT. Init and xorout are recovered by probing `generic_crc` as a black box, a different method from reveng's analytic polynomial arithmetic. `58be0e6`

**The "never confidently wrong" guarantee was violated, then repaired.** Roughly 6% of random trials returned a confident model that was wrong on unseen data. Two causes: the `(init, xorout)` class was enumerated from observed lengths only, so members agreed there and diverged elsewhere; and thin data left the GCD a multiple of the true generator, so a wider model fit while a smaller-width divisor fit too. Fixed by computing the genuine class from synthetic frames at two consecutive lengths, adding a width-minimality check, and adding leave-one-out cross-validation that downgrades to underdetermined. Verified from three independent directions, including exhaustive width-8 brute force and a closed-form cross-check matching 1600/1600; the guarantee fuzz went 172 to 53 to 6 to 0 confidently-wrong as the mechanisms landed. `4cd0842`

**The packet tools share one input shape.** `crc_detect`, `crc_reverse`, and `crc_verify` all take a whole frame with the CRC as the trailing field, so a caller learns the convention once. `ae139ab`

## Provenance (v0.21.0 to v0.23.0, 2026-06-15 to 06-17)

**Generated code records how it was produced, with no flag.** A "Reproduce with crcglot" block in all nine languages, plus a linkable `const crcglot_provenance_t` record in C so firmware can report its CRC configuration over a diagnostic channel. Being a public symbol it never trips `-Wunused-const-variable` under `-Werror`, `--gc-sections` drops it when unused, and `-DCRCGLOT_NO_PROVENANCE` omits it entirely. `892dbe7`

**The version stamp was removed, then restored.** It was first dropped to keep output request-pure: reading installed package metadata made generated bytes depend on the install environment, and it stamped a stale version into the committed gallery. It came back two days later on the stronger argument: generators change between releases, so the version is what tells a reader which crcglot produced a file and whether regenerating would change it. Output stays reproducible within a version, and the block re-diffing on a version bump is the intended record. `6c53224`, then `b825fc4`

**`file=` stopped relabelling the algorithm.** The generator's `name` parameter was overloaded as both the algorithm label and the identifier base, so `crc16-xmodem file=mycrc` leaked the stem into the provenance record as `algorithm: mycrc`. Split into `name` and an optional `stem`. `fa9b216`

**A per-format verifier was the wrong shape.** A `verify_crclink()` prototype answered "are you this one thing?" when the useful question is open-ended "what is this?". Replaced by a `FORMATS` registry that describes each form by a regex splitting the frame into message and CRC, letting the existing matcher name the algorithm unchanged. `a36c3e7`, then `13915b6`

## What a green suite missed (v0.24.0, 2026-06-18)

**The default C# output did not compile, for any algorithm.** Each algorithm is wrapped in its own PascalCase class, and the one-shot method carried the same PascalCase name, which C# rejects (CS0542). Methods now carry the role only and the class carries the identity: `Crc32.Compute(data)`. `7d6f4c9`

Two things hid it. The per-algorithm test that compiles the real default output is marked `exhaustive` and deselected by default, and the batch test generated everything under an underscore-suffixed symbol, which kept the one-shot snake-cased and dodged the exact collision. The guard added against recurrence is deliberately toolchain-free and runs in the default tier, so a missing dotnet cannot skip it.

**The HDL self-test claimed four inputs and checked one.** The generated comment, README, and certification doc all said four. The all-bytes and 1 KiB vectors exist to exercise the byte lookup table, which bitwise HDL packages do not have, so only the empty input added real coverage. It was added, and every claim was scoped to what each target actually checks. A mutation check confirms the new vector is exercised rather than vacuous. `6db4bd3`

## Positioning and the verification model (v0.26.0 to v0.27.0, 2026-07-02 to 07-06)

**Verification reports get a permanent home and a fixed method.** `docs/verification/`, one file per pass named `YYYY-MM-DD-<reviewer>.md`, written as-reviewed and never edited after the fact, with resolutions appended below the original text. Harness scripts are deliberately not archived: each pass rebuilds its oracle from scratch and validates it against references outside the package before grading anything. That independence is the method; the report is the artifact. `2bc20b2`

**Findings are traced end to end.** Row 10 of the verification mapping links each finding to its as-reviewed entry, the commit that fixed it, and the regression test citing its number. `6a313cf`

**Correctness is claimed from converging evidence, not from review.** Ten categories: reference vectors, extended vectors, random vectors, cross-language equivalence, streaming, segmentation, byte-at-a-time, toolchain execution, parameter edge cases, and adversarial review. Nine run on every suite invocation; the tenth is episodic and performed by independent agents. The scoping notes keep the claim exactly as strong as it is. `8a758ef`, `000d7aa`, `c83e303`

**Proof-verbs were retired.** A finite test set over an infinite input space yields evidence, never proof. "Proves" and "re-proves" became "backs", "verifies", "re-checks". Added to `CLAUDE.md` as a banned crutch with a sweep grep. `8a758ef`

**Claims are scoped to the part they hold for.** "Zero-dependency core", not "zero dependencies", because the optional MCP server pulls the MCP SDK. "Some of the same methods used on certified software", never "certified". The general rule went into `CLAUDE.md`: every claim must be true as written for the reader who acts on it. `2ca1930`, `e14fe3c`, `bfd66d6`

**The README went on a diet twice.** The philosophy work left the same thesis restated in five places with mounting elaboration, the exact pattern that reads as machine-written; that came out first. Then the README dropped from 3,774 words to about 2,050 (reveng's homepage is around 320), with each removed section pointing at the docs page that owns the subject. `d084f39`, `35d314e`

**The test-count badge is floored, not exact.** An exact count goes stale between releases and can be wrong by convention, which is how an 8477 full-suite number once got stamped where the fast-tier count belonged. "6,000+" is true under any counting convention and only ratchets up. `4de42eb`, `7f1403e`

## The verb manifest (v0.27.0 to v0.28.0, 2026-07-06 to 07-07)

**Verbs became plain data.** `crcglot.VERBS` carries twelve `VerbSpec` records: summary, guidance prose, parameters with types and defaults and choices and help, mutual-exclusion groups, result fields, and the surface mapping. Registry-backed choices derive from `LANGUAGES` and friends at import time, so a frontend renders typed tools from one source instead of hand-rolling parameter metadata. `0aeb00b`

**Then the implementations moved to match.** The twelve MCP tool bodies lifted verbatim into an SDK-free `crcglot/_invoke.py`, each `@mcp.tool` becoming a one-line delegation, followed by a public `call_verb(name, **params)`. Core and MCP now share one implementation per verb by construction, and a frontend's loop is complete: render from `VerbSpec`, call `call_verb`, return the dict. An equivalence test asserts dict-equality between the two paths for every verb, with a completeness check so a new verb cannot escape it. `047f7bc`, `9f0550d`

## Property-based testing, scoped to the infinite axis (2026-08-13)

**Hypothesis enters as a dev-only dependency, for two properties.** The verification model here is evidence over review, and the method statement in `docs/verification/index.md` decides where a search-based tool belongs: enumerate the countable axis (algorithms x variants x languages) completely, sample the infinite one. Property-based testing is the right instrument only on the second. Both properties chosen sit there: the off-catalogue CRC parameter space in `reverse()`, and the chunkings of a message, which grow exponentially in its length.

**One candidate was rejected on that same rule, and the reasoning is the reusable part.** The `detect` -> `encode_match` hex-text round-trip looked like a natural property, but `HexFormat` is a closed product: separator x prefix x per-byte x case, roughly 96 combinations. That is a countable axis, so the method calls for enumerating it, and a parametrized cross-product beats a search on every axis that matters here: exhaustive rather than sampled, deterministic, and free of a dependency. It landed as a parametrize.

That decision paid immediately. The full cross-product failed on its first run, on all 16 combinations pairing a `0X` prefix with lowercase hex digits: the parser inferred `HexFormat.uppercase` from the prefix's case and let it override the digits' own, so `0X...cbf43926` round-tripped back as `0X...CBF43926`. The seven hand-picked cases it replaced had missed it because the only `0X` case among them also used uppercase digits. Fixed by letting the digits decide whenever they carry any case evidence, keeping the prefix only as the tiebreaker for a packet of digits 0-9 that carries none.

**CI runs a derandomized profile.** A release gate has to mean the same thing on every run, so `HYPOTHESIS_PROFILE=ci` fixes the examples in both workflows; the random `dev` profile is the local default, where discovery is welcome. Anything the dev profile finds gets pinned inline as an explicit example, so the deterministic gate carries it from then on. `deadline=None` in both profiles is not tuning: the suite runs `-n auto` across ~16 workers, where the default 200 ms per-example deadline measures scheduler noise.

**The acceptance test was shrinking, not passing.** A property test that cannot report a minimal counterexample is a slower version of the seeded sweep it replaces, so before keeping the dependency, both invariants were deliberately broken. Injecting a 3-byte-chunk bug into the pure-Python backend shrank a 400-byte message and 24 cut points down to `b'\x00\x00\x01'` fed as one 3-byte chunk, and correctly reported the algorithm as irrelevant. Corrupting a recovered `init` was caught in 2.7 seconds by the pinned `poly=1` corner rather than by search. Both restored afterward.

**What it replaced.** `TestRandomCustomCrcs::test_no_wrong_answers` (6 parametrized cases, 240 `reverse()` calls over a fixed seed) is gone: the property asserts the identical invariant with draws biased toward the boundary values a uniform sweep never reaches (`poly=1`, whose generator factors as `(x+1)**w` for maximal ambiguity; `init=0`; `xorout=0`). `TestSegmentation` was deliberately **kept**: it is exhaustive over two-way split positions, and a sampled search cannot replace an exhaustive guarantee.

## Frames off a live link (2026-08-14)

These came out of asking whether crcglot actually composes with termapy for the capture-to-identify workflow, rather than from the suite, which was green throughout.

**Frames are tried exactly as given first, and that ordering is semantics rather than speed.** A delimiter after the CRC moves the field the matcher looks for, so it has to be reconsidered; but the frames-as-given attempt *is* the competing hypothesis, that the delimiter sits inside the CRC's span, which is how `STX payload ETX BCC` framings work. Reconsidering trailing bytes only after the unmodified reading fails means the layout that needs no guessing always wins, and it makes the change incapable of altering a result that already resolved.

**The false-positive budget is spent on frame count, not on a shorter candidate list.** The first instinct was to keep the terminator vocabulary tiny. The arithmetic says otherwise: expected spurious hits across the catalogue run about 0.66 at one frame *before* any terminator is considered, because the catalogue holds 3- and 4-bit entries that fit random data one time in eight, and about 4 with terminators added. Three frames brings it to roughly 0.03. So the floor is three frames, and the vocabulary is insurance rather than the control. Requiring a candidate to end *every* frame does most of the remaining work and also removes any need for a length cap: the CRC differs frame to frame, so a shared suffix cannot reach back past it into the message.

**`trail` is strict across frames; `sep` and `lead` are not.** Rejected alternative: require every surface field to agree, treating uniform form as part of the recognition evidence. It is defensible for a real capture, where frames from one device are uniform, but it breaks pasted or hand-assembled input and would have turned matches that work today into misses. `trail` is different in kind because it decides the CRC's span, so inconsistency there means the layout is genuinely unknown. The permissive fields instead gained a `mixed` set naming what varied, so the record stops claiming a uniformity the input lacked, and `encode_match` refuses rather than rebuilding a shape part of the input never had. `uppercase` is excluded from that comparison: it is inferred from the digits present, so `0x1234` is indistinguishable from a lower-case producer and comparing it would flag nearly every real capture.

**Leading junk stays out.** A shared prefix is frequently real payload, an address or a function code, so stripping it fails plausibly rather than obviously, which is the worse failure. Trailing is safe to reconsider precisely because the CRC is defined to be last.

**The 1.x MCP wire golden was not regenerated when `crc_detect` gained `packets`.** `tests/goldens/mcp_wire.json` was captured from the last mcp 1.x build to hold the 2.0 port to the same wire. Extending `crc_detect` moved that wire on purpose, and the two options were to recapture the snapshot or to assert the delta. Recapturing would have replaced "this change is additive" with "whatever the wire is now is correct", which is the opposite of what a snapshot is for, so the delta is asserted instead: every 1.x property byte-identical, the required set untouched, exactly two names added.

The cost is worth writing down because it is not obvious. The two new parameters are covered by `test_schema_matches_manifest`, which checks the live schema agrees with `VERBS`, but that only shows server and manifest agree with each other. For every pre-existing parameter the golden supplies a second, independent opinion, since a manifest edit would move the schema and the golden would catch it. The new ones have no such check: edit them in `verbs.py` and both assertions happily agree with the new value. The exception list is also a maintenance tax that grows with each intentional wire change, and each entry is somewhere a real regression could hide.

**The exit, when the MCP surface is next touched:** capture a second golden at the current version. The 1.x file stays as a compatibility floor with its exception list frozen, and the new snapshot byte-pins the whole current wire including the added parameters. That recovers both properties without the exceptions accumulating. Deferred rather than done here because it is test-infrastructure work and this branch was already large. `e6cf3bc`

## Python 3.15 and the extension boundary (2026-10-02)

These came out of termapy's 3.15 readiness run rather than from crcglot's own suite, which was not exercised on 3.15 at the time.

**The abi3 wheel did what it promises on 3.15.0rc2.** On Windows x86-64 the `cp311-abi3` wheel's `_c` extension loaded, both C entry points were wired into the catalogue, and all 113 catalogue check values matched through `compute` (111 of them via the C engine, the two IEEE CRC-32 forms via zlib). termapy's full suite was green on the same interpreter. Nothing in crcglot needed to change. Consequence for the release matrix: do NOT add 3.15 to the wheel build. A cp315 wheel would compete with the abi3 one and defeat the single-binary point of the limited API. When 3.15.0 is final, add a 3.15 job to the fast-suite matrix and the classifier, nothing else.

**What the run exposed: the fallback is silent in a way that hides the one failure a new Python causes.** `catalogue.py` imports `_c` inside `try / except ImportError` and drops to the pure-Python loop when it fails. That is right for an sdist built where there is no compiler. But "not built" and "built, and will not load on this interpreter" raise the same `ImportError`, so a green suite cannot tell them apart, and a downstream project cannot either without naming `crcglot._c`, a private module. The second case is real: on the 3.15.0a5 build, rpds-py's cp315 wheel installed and then failed to load ("the specified procedure could not be found"). For crcglot the same event would mean slow-and-correct answers on every machine with that wheel, and nobody would know. The first misreading in termapy's run was exactly this: a nine-month-old uv tested a nine-month-old alpha, and the alpha's failures looked like dependency failures until the fallback paths were traced.

**Decision, deferred to the next touch of the extension: publish the accelerator state as a public fact, and make the fallback loud where the wheel is built.** The shape is a module-level flag or a function, `crcglot.accelerated` returning a bool or `backend()` returning `"c"` or `"python"`, and an assertion in the wheel CI (the in-wheel test run) that it reports the C engine, so the only place that knows the extension was built is the place that refuses silence. Two alternatives were rejected. Raising on a present-but-broken extension would turn a slow path into a hard failure for users who could still get correct answers; that policy belongs to an environment check, not to an import. Distinguishing the two cases inside crcglot with `importlib.util.find_spec` and emitting a warning is better than silence and may complement the flag, but a library warning is easy to miss and still gives downstream nothing to assert.

**Interim, on the termapy side.** `tests/test_crcglot_abi.py` in termapy calls `_c.c_generic_crc` and `_c.CrcStream` with one catalogue check vector each (crc16-modbus, chosen because the CRC-32 forms route to zlib and would never touch the extension) and fails rather than skips when the extension is missing. It is an environment check, one call per entry point, and names the private module once. When the flag ships it collapses to a single public assertion. The boundary it encodes is worth keeping: a downstream tests the facts it promises and the environment it runs in; it never enumerates or re-derives crcglot's facts.

**Done 2026-10-19, on 3.15.0 final.** The fast-suite matrices and the classifier gained 3.15, the Windows and macOS pins moved from 3.14 to 3.15, and the wheel build stays as decided above. The run was on the Windows reference box in a separate 3.15 venv (`UV_PROJECT_ENVIRONMENT`, so the project's 3.14 venv stayed put): 9530 passed, 0 skipped, with `_c` loaded in-process. The only obstacle was local. The untracked lockfile pinned `pywin32==311` (pulled in by `mcp` on Windows), and that release has no cp315 wheel; `uv lock --upgrade` moved it to 312, which does. CI never sees this because it resolves fresh, the same property that bit v0.32.0 the other way. The accelerator flag deferred above stays deferred.

## Lazy per-language binding and the stamped version (2026-10-17)

A termapy agent reported that reading `crcglot.VERBS` imports every code generator and asked for the fix in `verbs.py`. Measured in a fresh process, the premise was off by one layer: `LANGUAGES` alone loaded 28 extra modules (all eleven `crcglot.lang.*`, every `crcglot.comments` renderer, `_vectors`), because `targets.py` imported each generator at the top to bind it into `LanguageInfo`. `VERBS`, `call_verb`, the CLI and the MCP server all build on `LANGUAGES`, so every frontend that described the targets at startup paid for code it never ran. The agent's "LANGUAGES adds nothing" number had been taken after `VERBS` had loaded everything in the same process. A fix in `verbs.py` would have left the CLI and MCP paths as they were.

**Module count is the cost, not module size.** On the Windows reference box each module file open costs about 0.7 ms (cProfile: 41 opens were 29 of the 99 ms behind `import crcglot.cli`), so what matters is how many files a path touches. That argued for cutting edges in the import graph rather than slimming modules.

**`LanguageInfo` keeps a module path and resolves the three callables on first read.** The alternative was a proxy callable per field, which keeps the dataclass shape but breaks identity (`LANGUAGES["c"].generator is crcglot.generate_c`) and introspection. Properties backed by `functools.cached_property` return the real function, import one language's module, and leave the siblings cold; the cost is that `generator` / `generator_from_entry` / `combiner` are no longer dataclass fields and the constructor takes `module=` instead. Nothing outside `targets.py` constructs a `LanguageInfo`, so the break is confined. The six languages that bundle by concatenation export `combine_<code>` explicitly rather than falling back to `combine_concat` inside the property, so a misnamed export fails loudly. `_encode.py` got the same treatment for the detection layer: two `isinstance` checks and the text branch of `verify()` were the only runtime uses, so `compute()` on bytes now loads six modules instead of eight. `56b9b7b`

**The version is written once, in `_version.py`.** `crcglot.__version__` resolved through `importlib.metadata`, which scans every installed distribution: 60 to 170 ms, paid by every generated file (the provenance block records the producing version) and by `crcglot version`. It was the largest single cost in the package and had nothing to do with import structure. `pyproject.toml` now declares the version dynamic and points setuptools at the attribute, which it reads statically; `release_prep.py` bumps that file. One wrinkle is recorded in `[tool.uv] cache-keys`: uv rebuilds an editable install only when a listed file changes, and its default is `pyproject.toml` alone, so without `_version.py` listed a bump would leave the installed metadata stale and the test that pins `__version__` to the metadata would fail inside release prep. That test had been comparing the metadata to itself; it now guards real drift. `93446bf`

**Measured** in a fresh process on a host with `typing` and `dataclasses` already imported (termapy's case), median of 21 runs, crcglot modules in brackets: `LANGUAGES` 35.4 ms [33] to 12.7 ms [7]; `VERBS` 38.5 [34] to 33.2 [21]; `call_verb` bound 65 [43] to 58.7 [31]; `import crcglot.cli` 47 [39] to 43.5 [27]; `compute()` 14.3 [8] to 10.1 [6]; `__version__` 62 to 10; `generate_c()` 80 to 33. Bare interpreter: `LANGUAGES` 56 to 36, `VERBS` 96 to 58, `__version__` 170 to 33.

**Deferred, with their measured value.** `VERBS` still loads `crcglot.comments` (12 modules, about 9 ms) because `verbs.py` and the CLI read `COMMENT_STYLES` / `style_info` from a registry that imports every renderer class to get its name, label and languages; making the registry a static table with lazily loaded classes is a medium refactor across the ten style modules, and the one item on termapy's acceptance list this change does not meet. `_invoke.py` imports `detect` / `reverse` / `identify_trailer` at the top, so binding `call_verb` at startup costs about 25 ms more than `VERBS`; moving those into the verb functions is mechanical. The CLI's `build_parser()` takes 41 ms, of which argparse 3.14 spends about 16 ms probing colour support once per argument (670 calls); building only the requested subcommand's arguments would save about 25 ms per run out of roughly 280 ms wall.

## Decisions that were reversed

Four, each reversed on a stated argument rather than drifting back.

| Decision | Reversal | Why the reversal won |
| --- | --- | --- |
| Cache lookup tables in the C extension (`2bf76da`) | Remove the cache entirely (`4dee2b3`) | The cache forced a choice between a lock that serializes parallel builds and a data race. Stateless is thread-safe by construction, and reuse belongs where ownership is explicit. |
| Keep the tool version out of provenance for request-purity (`6c53224`) | Stamp the version (`b825fc4`) | Generators change between releases, so the version is what tells a reader whether regenerating would change the file. |
| Per-format `verify_crclink()` (`a36c3e7`) | A `FORMATS` registry feeding `detect` (`13915b6`) | "Are you this one thing?" is the wrong question. "What is this?" is the one users have. |
| Flag bit-by-bit beating table-driven as a methodology bug (`661855e`) | Only flag `slice8 < table` (`4b66af8`) | LLVM vectorizes the bit loop while table lookups carry a serial dependency chain. The measurement was right and the checker was wrong. |

## Recurring patterns

Five habits show up repeatedly across the history, each traceable to a specific incident.

**External oracles catch what self-consistency cannot.** The `crc8-bacnet` polynomial error passed the catalogue's own check value and failed against reference C code. Every verification category added afterward pulls its expected values from outside the package.

**A green suite is not evidence that the prose is current.** Tests assert behaviour, not labels, so removed features leave their vocabulary behind in test names, docstrings, and READMEs. Hence the cruft audit.

**Deselected beats skipped.** A skipped test reads as amber and gets ignored. The `exhaustive` marker deselects, so a default run is genuinely green and the isolation tier stays available on demand.

**A test that can be skipped will be, at the worst moment.** The C# CS0542 guard is toolchain-free and runs in the default tier precisely because the test that should have caught the bug was deselected and the one that ran had dodged the collision.

**Scope the claim to the part that holds.** Zero-dependency core. Some of the same methods. Two vectors on HDL, four on the table-driven targets. Every one of these started as a broader claim that was true enough to survive a charitable reading and got narrowed to what a skeptical user would still call fair.
