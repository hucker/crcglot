# Using crcglot output in safety-certified software

Read this first: **crcglot is not a qualified tool, its output is not pre-certified, and nothing it generates is a drop-in certified component.**  What it is instead: crcglot is developed with Claude Code (Anthropic's AI coding tool), and it was held to some of the same verification methods you would apply when certifying real software.  Reference vectors come from independent engines that had to agree, every catalogue entry is generated and executed in CI, and each generated file ships a re-runnable acceptance test.  That discipline produced the pieces below.  It does not transfer certification to your project: it makes your own verification cheaper, not unnecessary.  Nothing on this page substitutes for your process, your records, or your certification authority's judgment.  What crcglot does is put the pieces your process needs in front of you, in a shape designed to make the work you must do anyway as small as possible.  The claim is "verification-ready inputs," never "certified output."

crcglot is free software under the MIT License, which disclaims all warranties and liability (see [LICENSE](../LICENSE)).  Nothing it generates is certified for, or warranted suitable for, any safety-of-life application.  If you put its output into a system that can hurt someone, the verification that makes that safe, and the responsibility for having done it, are entirely yours.

## The pieces you get

**The requirement, written down and traceable.**  Every generated file's header carries the full Rocksoft/Williams parameter set (width, poly, init, refin, refout, xorout, check).  For catalogue algorithms those parameters trace to the published [reveng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm), a public reference that is independent of both crcglot and your project.  For most processes this is the low-level requirement and its trace, ready to cite.

**Independent test vectors, with their provenance on record.**  The embedded self-test on the table-driven targets checks four fixed inputs: the empty message, the canonical `"123456789"` check string, all 256 byte values, and a 1 KiB pseudo-random pattern.  (Verilog and VHDL are bitwise with no table, so the two large table-coverage vectors are dropped there; they check the empty message and the check string.)  The expected values were not computed by crcglot's engine (a tool grading itself proves nothing).  They were computed by two independent implementations ([anycrc](https://pypi.org/project/anycrc/) and [crccheck](https://pypi.org/project/crccheck/)) that were required to agree, anchored to the catalogue's published check value.  The derivation script ships in the repository (`scripts/gen_vectors.py`), so the provenance chain is inspectable and re-runnable, not asserted.

**An acceptance test that runs on your target.**  The self-test compiles into your build and executes under your compiler, your optimization flags, your endianness, and your integer widths.  The two large inputs are regenerated in loops, so nothing bulky lands in flash.  This is a requirements-based test you can wire into a unit test, a startup assertion, or a boot check, and its expected values carry the provenance above.

**Code shaped for review.**  The generated unit is small, static, and bounded: no allocation, no recursion, fixed-size tables, loops with constant bounds.  Documentation comments come in your doc tool's convention.  Output is deterministic: the same crcglot version and parameters produce the same bytes, so re-reviews after a regeneration are diff-sized, not file-sized.

**Configuration-management-friendly provenance.**  The generated file is plain source you vendor as a configuration item.  Recording the crcglot version and the generating command makes the artifact reproducible at any later date; regenerating and diffing is a one-command provenance check.

**Upstream verification, for context.**  crcglot's own CI generates, compiles, and executes every catalogue algorithm in every supported variant in every target language against the vectors above.  This tells you the generator was sound when your file was produced.  It is context for your tool assessment, not evidence inside your quality system; your evidence is what you run and record yourself.

## What remains yours

These items are yours in every regime, and no supplier can take them:

- Bringing the generated file under your configuration management as a controlled item.
- Reviewing the code against your coding standard and recording the review.
- Executing the requirements-based tests in your environment and keeping the records.
- Measuring structural coverage to the level your classification requires (statement, branch, MC/DC).
- Sizing the CRC as a safety decision: width and polynomial choice fix the detection strength for your hazard analysis, and crcglot only generates what you select.
- Assessing the tool itself within your framework (see the regime notes below).

## Regime notes

**Airborne software (DO-178C with DO-330).**  crcglot is a development tool whose output becomes part of your software.  DO-330 requires qualification of such a tool *unless its output is fully verified by your own DO-178C verification process*.  crcglot has no TQL and no tool qualification data package, and none is planned; the deliverables above exist to make the output-verification path the cheap one.  Whether that path satisfies your project is a determination for your certification liaison, not for this page.

**Medical device software (IEC 62304, FDA).**  The generated file becomes your source code and is verified within your process at your software safety class; it is not SOUP, and it arrives with its unit-level test and requirement attached.  For the tool-validation expectations that apply to development tools, the verification story above can seed your intended-use validation file, with your own run of the self-test on your target as the confirming record.

**Airborne hardware (DO-254).**  The Verilog and VHDL targets are positioned as simulator reference models, not synthesis-qualified design source.  The emission follows lint discipline anyway: braced conditional branches, explicit bit comparisons (`crc[15] == 1'b1`, `crc(15) = '1'`), sized literals, `numeric_std` rather than the legacy arithmetic packages, and a clean separation between the synthesizable core (`init` / `update` / `finalize`: fixed widths, constant-bound loops) and the simulation-only conveniences (the one-shot and self-test use dynamic arrays).  If you take the core into a DO-254 design, the verification logic of this page applies unchanged: the parameters and vectors above are your requirements and test seed, and everything in "what remains yours" remains yours, plus your lint baseline, synthesis constraints, and hardware verification flow.

## What we do not claim

- **No coding-standard conformance has been assessed.**  The generated C deliberately uses MISRA-leaning constructions (braced bodies, explicit `!= 0U` comparisons, suffixed unsigned constants, single-exit self-tests, no `++` inside expressions), but no checker run has been recorded and no conformance statement exists.
- **No structural coverage figures are published.**  The four vectors exercise the code broadly, including every byte value, but nobody has measured statement, branch, or MC/DC coverage from them; measure on your toolchain.
- **Custom polynomials carry a weaker check.**  A non-catalogue polynomial has no independent reference, so its embedded self-test compares against a value crcglot computed itself.  That still catches a toolchain mismatch; it cannot catch an error shared by the generator and its output.  For certified use of a custom polynomial, supply your own independently-derived vectors.
- **crcglot's CI is not your evidence.**  It ran outside your quality system, on hardware you do not control.
- **The HDL targets carry no synthesis or DO-254 claim.**  No lint-tool baseline or synthesis run has been recorded for the Verilog/VHDL output, and the one-shot and self-test functions use simulation-only constructs by design.
- **No fitness claim for any DAL or software safety class.**  That determination belongs to you and your authority.
