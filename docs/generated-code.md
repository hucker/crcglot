# Generated code style: comments and naming

The code crcglot delivers is the same code its CI generated, compiled, ran, and checked against independent reference vectors, and every delivered file embeds a self-test you should run once on your target to confirm your compiler settings, endianness, and integer widths reproduce that verified behavior ([The embedded self-test](#the-embedded-self-test) below covers what it checks and why).  This page covers how the output *reads*: documented in your doc-tool's convention, named in your language's casing.  Both axes are queryable, so UIs and scripts never hardcode them.

## Documentation comments

Every file gets a header (algorithm parameters, a copy-paste streaming example, the self-test contract) and a doc comment above each of the five functions, so a reader learns the `init → update* → finalize` streaming contract from the source, not from the tests.  Pick the convention with `--comment=<style>`; `plain` (clean human-readable comments in each language's native syntax) is the default, and every language also has its idiomatic doc-tool style:

| Language           | `--comment` styles                                    |
| ------------------ | ----------------------------------------------------- |
| C / C++ ⚙️          | `plain`, `doxygen`                                    |
| C# 💠               | `plain`, `doxygen`, `docfx` (XML `/// <summary>`)     |
| Java ☕             | `plain`, `doxygen`, `javadoc`                         |
| Python 🐍           | `plain`, `google`, `numpy`, `rest` (Sphinx `:param:`) |
| Rust 🦀             | `plain`, `rustdoc` (`///` + `# Arguments`)            |
| Go 🚦               | `plain`, `godoc`                                      |
| TypeScript 🔷       | `plain`, `jsdoc` (TSDoc)                              |
| Verilog 🔧 / VHDL 🔌 | `plain`                                               |

```bash
crcglot c crc32 --comment=doxygen        # /** @brief @param @return */
crcglot python crc32 --comment=numpy     # numpydoc underlined Parameters / Returns
crcglot rust crc32 --comment=rustdoc     # /// with # Arguments markdown
```

crcglot offers each language only the styles its doc-tool actually understands: `crcglot rust --comment=doxygen` is rejected, because Doxygen doesn't read Rust.  The matrix is derived from the styles themselves; nothing hardcodes it.

**Building a UI?**  The matrix is queryable, so a front end can populate a language → style dropdown with no hardcoding.  Each record carries a machine `name` (the dropdown value, handed back to the generator), a human `label`, and a `description`:

```python
from crcglot.comments import comment_styles_for_language
for s in comment_styles_for_language("python"):
    print(s.name, "|", s.label, "|", s.description)
# plain  | Plain            | Human-readable comments in the language's native syntax
# google | Google           | Google-style docstrings (Args / Returns / Note)
# numpy  | NumPy            | NumPy (numpydoc) docstrings, underlined Parameters / Returns
# rest   | reStructuredText | Sphinx field-list docstrings (:param: / :returns:)
```

The generators take the chosen name directly (`LANGUAGES["python"].generator("crc32", comment_style="numpy")`), and the same `{name, label, description}` records are served over MCP in the `crcglot://languages.json` resource (each language's `comment_styles`).

### Why generate the docs instead of asking an LLM?

You still can: point an LLM at the output and let it write whatever prose you like; nothing here stops you.  But the generated code is **fully known**: the parameters, the API contract, and the streaming semantics are deterministic facts, so the *documentation* can be deterministic too.  That buys three things an LLM pass can't:

- **Reproducible.**  The same request on the same crcglot version produces the same comment, byte for byte.  Everyone who generates `crc32` gets the *identical* documentation: no drift, no "it phrased it differently this time," no diff churn between two runs.
- **Correct by construction, or wrong in exactly one place.**  The comment is rendered from the same source of truth as the code, so it can't hallucinate a parameter or misdescribe the API.  And if a description *is* wrong, it's wrong *uniformly*: caught once, fixed once in the generator, and the fix reaches every output everywhere.  Per-invocation LLM wrongness is the opposite: subtly different each time, and far harder to audit.
- **Free, offline, auditable.**  No API call, no token cost, no network; it runs in CI and on an air-gapped build.  A reviewer (the class-III-medical-device kind) audits the comment generator *once* and can then trust every file it emits.

Layer an LLM on top when you want richer prose.  The point is that the *baseline* everyone ships by default is deterministic, uniform, and reviewable.

## Provenance

Every file header carries a `Reproduce with crcglot` block: the producing crcglot `version`, then the resolved parameters (algorithm, target, variant, comment style, symbol, and naming).  It is always on (no flag), and it costs nothing once the compiler discards comments.  The version comes first because generators do change between releases (a fixed reflection bug, a new variant), so it is the one field that tells a reader which crcglot emitted a file and whether regenerating with a newer one would change it.  Everything else is fully reproducible: the same request on the same crcglot version produces the same bytes.

C goes one step further and emits the same record as **linkable data**: a public `const crcglot_provenance_t <symbol>_provenance` that a program can read at runtime, e.g. firmware reporting its CRC configuration and crcglot version over a diagnostic channel.  Because it is a public symbol it never trips `-Wunused-const-variable` under `-Werror`.  A linker with `--gc-sections` drops it when nothing references it, so it is free unless you use it; on a toolchain without section GC, define `CRCGLOT_NO_PROVENANCE` to omit it. The values are constrained tokens (catalogue name, enum, identifier), so the record never needs escaping.

## Naming conventions

The generated public functions read like hand-written code in each target: Go and C# get `PascalCase` (`Crc16ModbusUpdate`), Java and TypeScript get `camelCase` (`crc16ModbusUpdate`), and C, Rust, Python, Verilog, and VHDL get `snake_case` (`crc16_modbus_update`).  Those are the **defaults**, so a linter (`govet`, StyleCop, ESLint, …) won't flag the output.  Override with `--naming=<convention>`; each language offers only the conventions its ecosystem actually uses (C is a free-for-all, Python and Rust are snake-only):

| Language           | default  | `--naming` choices         |
| ------------------ | -------- | -------------------------- |
| C / C++ ⚙️          | `snake`  | `snake`, `camel`, `pascal` |
| C# 💠               | `pascal` | `pascal`, `camel`          |
| Go 🚦               | `pascal` | `pascal`, `camel`          |
| Java ☕             | `camel`  | `camel`, `pascal`          |
| TypeScript 🔷       | `camel`  | `camel`, `pascal`          |
| Rust 🦀             | `snake`  | `snake`                    |
| Python 🐍           | `snake`  | `snake`                    |
| Verilog 🔧 / VHDL 🔌 | `snake`  | `snake`                    |

```bash
crcglot go crc32 --naming camel          # crc32Update instead of Crc32Update
crcglot c crc32 --naming pascal          # Crc32Update; the CRC32_H guard stays SCREAMING_SNAKE
```

Only the public function/method names are re-cased; header guards, table symbols, package names, and class names keep their own fixed idiom.  An explicit `name=NAME` renames the CRC and is cased per language (so it follows `--naming`); `symbol=NAME` is the escape hatch, emitted verbatim and bypassing `--naming`.  As with `--comment`, `crcglot rust crc32 --naming=pascal` is rejected (Rust is snake-only); the matrix is derived from `LANGUAGES[code].naming`, nothing hardcodes it.  The same `{name, label, description}` records are served over MCP in `crcglot://languages.json` (each language's `naming` + `default_naming`).

## The embedded self-test

Every generated file ships its own `_self_test()`.  For a catalogue algorithm the table-driven targets check **four** fixed inputs (the empty string, `"123456789"`, all 256 byte values, and a 1 KiB pseudo-random pattern), so the byte-table and the high-bit handling get exercised, not just the one short check string.  Verilog and VHDL are bitwise with no lookup table, so the two large vectors (which only exist to drive the table) are dropped there; they check the empty input and the check string, the empty case still covering the init-then-finalize path.  The two large inputs are regenerated inside the self-test with a byte-at-a-time loop, so the embedded code carries no big array (it stays friendly to flash- and RAM-constrained targets).  Those four reference CRCs are not computed by crcglot: using the engine to grade itself would be circular.  They come from two independent implementations ([anycrc](https://pypi.org/project/anycrc/) and [crccheck](https://pypi.org/project/crccheck/)) that had to agree, anchored to reveng's published value at the check string; both are dev-only tools, so the shipped package keeps its zero-dependency footprint.  In short: crcglot agrees with every reference implementation it can be compared against, including the stdlib's `zlib.crc32`, which it uses directly at runtime for IEEE CRC-32.  A custom (non-catalogue) polynomial has no independent reference, so it falls back to a single check value crcglot computed itself, a weaker check that still catches a toolchain mismatch but, unlike a catalogue algorithm, can't catch an error shared by the generator and the generated code.

**For every target except Python, you should call `_self_test()` once in your build environment**, wired into a unit test, a startup assertion, or your boot self-check.  crcglot's CI verifies the generator's output on its reference toolchain; only running `_self_test()` on yours confirms your compiler version, optimization flags, target endianness, and integer widths haven't introduced a subtle disagreement.  Python is the exception: the interpreter that ran the CI suite is the one running your code, so the in-environment check would be redundant.

### What it buys you beyond correctness

- **A boot-time integrity check.**  A table-driven CRC carries ~1 KiB of constants in flash, and a corrupted table entry produces silently wrong CRCs forever.  The all-bytes and 1 KiB vectors drive over a thousand lookups through the table, so calling `_self_test()` at startup doubles as a flash-corruption tripwire, not just a build-time sanity check.
- **A self-evidencing artifact.**  An auditor holding the generated file needs no access to crcglot, its CI, or the internet: the file states its claim ("this is CRC-16/CCITT-FALSE") and carries executable acceptance criteria for it, derived from references crcglot didn't compute.  Years later, when nobody remembers how the file was generated, it still checks itself.
- **Tamper-evidence for well-meaning edits.**  Any later hand-edit to the algorithm either keeps the self-test passing or visibly deletes the assertions; both are auditable events in a diff.  Silent drift becomes loud drift.
- **A cleaner story for regulated builds.**  First, the disclaimer that matters: crcglot is not certified software, and its output is not a drop-in certified component.  What it is: a generator that held itself to some of the same verification methods you would run when certifying real software, then hands you that evidence.  Certification frameworks ask whether your code generator is qualified; the standard alternative is independently verified *output*.  Vectors computed by two independent engines, anchored to a published catalogue, embedded as re-runnable assertions next to the implementation, are that evidence, attached to the artifact rather than the tool.  [certification.md](certification.md) lays the whole story out, including what crcglot deliberately does not claim.
