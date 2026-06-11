# Architecture

## The feature set *is* the design

You can read crcglot's internal structure straight off its feature list. Wherever
the API offers a set of interchangeable options along one axis, that axis is
a *first-class entity in the code* (a registry of records with a lookup), and
extending it is the cheap path. The breadth you see is breadth that was *easy*:

| User-visible axis (many options)        | First-class entity behind it                | Add one by…                 |
| --------------------------------------- | ------------------------------------------- | --------------------------- |
| **Algorithms** (113)                    | `ALGORITHMS: dict[str, AlgorithmInfo]`      | adding a catalogue row      |
| **Languages** (9)                       | `LANGUAGES: dict[str, LanguageInfo]`        | registering a generator     |
| **Variants** (bitwise / table / slice8) | `VARIANT_ORDER` + `VariantInfo`             | a generator branch + record |
| **Comment styles** (10)                 | `comment_styles_for_language` + `StyleInfo` | a style record              |
| **Naming** (snake / camel / pascal)     | `NAMING_ORDER` + `NamingInfo`               | a naming record             |

We support nine languages because it is easy: a language is a registered
entity, not a fork in the control flow. We support several naming conventions
for the same reason. None of these are special-cased; each is just another row
along an axis the architecture already treats as first-class.

## Why it looks this way

Every metadata axis ships the same shape: a frozen `*Info(name, label,
description, …)` record, a `*_info(name)` lookup, and a per-language
accessor (see the "Public API ergonomics" section of [CLAUDE.md](../CLAUDE.md)).
That convention is the mechanism that keeps each axis first-class. It buys two
things:

- **Cheap breadth.** Adding the Nth option touches one seam, not N call sites.
  A new language is picked up by `EXAMPLES.md`, the MCP `languages.json` resource,
  and the CLI automatically, because they all walk the same registry.
- **Cost signals.** If a requested feature *doesn't* fall along an existing
  axis, that's the architecture telling you it's the expensive path: it needs a
  new seam, not a new row. The recent `reverse` / `verify` packet tools are an
  example of the inverse working in our favour. "A frame with the CRC at the
  tail" was *already* a first-class shape (`detect` consumed it), so making
  `reverse` and `verify` accept the same shape was a row-add, not a redesign.

## The discipline

The architecture doesn't *force* the easy paths to be the useful ones. That's a
choice. The aim is to make the axes that users actually need to vary (which
algorithm, which language, which output style) be exactly the axes that are
cheap to extend, and to resist widening an axis nobody asked to vary just
because it's easy. When the easy path and the needed path are the same line, the
feature set grows itself; when they diverge, the table above stops predicting the
code, and that's the smell to act on.
