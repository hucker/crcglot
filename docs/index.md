# crcglot documentation

The [README](../README.md) is the overview; the reference lives here, one
section per file:

- **[CLI reference](cli.md)** — every subcommand (`list`, `info`, `detect`,
  `identify`, `encode`, `compute`, the nine generators, `--custom`) with all
  options and examples.
- **[Programmatic API](api.md)** — the `LANGUAGES` / `ALGORITHMS` registries,
  custom polynomials, the runtime engine, streaming (`CrcStream`) and batch
  (`generic_crc_many`).
- **[Generated code style](generated-code.md)** — documentation-comment
  styles per language, naming conventions, and why the generated docs are
  deterministic.
- **[MCP server](MCP.md)** — exposing crcglot's tools to LLM clients (Claude
  Desktop, Cursor, …): setup, every tool, resources, prompts.
- **[Architecture](ARCHITECTURE.md)** — how the package is put together.

Also in the repository root:

- **[EXAMPLES.md](../EXAMPLES.md)** — generated source for `crc32` in every
  language × variant combination (auto-generated gallery).
- **[BENCHMARKS.md](../BENCHMARKS.md)** — measured throughput per language ×
  variant, plus the runtime engine's paths.
- **[CHANGELOG.md](../CHANGELOG.md)** — release history.
