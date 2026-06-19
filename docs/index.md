# crcglot documentation

The [README](../README.md) is the overview; the reference lives here, one section per file:

- **[CLI reference](cli.md)**: every subcommand (`list`, `info`, `detect`, `identify`, `encode`, `compute`, the nine generators, `--custom`) with all options and examples.
- **[Programmatic API](api.md)**: the `LANGUAGES` / `ALGORITHMS` registries, custom polynomials, the runtime engine, streaming (`CrcStream`) and batch (`generic_crc_many`).
- **[Generated code style](generated-code.md)**: documentation-comment styles per language, naming conventions, and why the generated docs are deterministic.
- **[MCP server](MCP.md)**: exposing crcglot's tools to LLM clients (Claude Desktop, Cursor, …): setup, every tool, resources, prompts.
- **[Certification story](certification.md)**: crcglot is not certified code and not a drop-in certified component; it held itself to some of the same verification methods you would use on real certified software and hands you that evidence.  What you get, what stays yours, what is not claimed.
- **[Architecture](ARCHITECTURE.md)**: how the package is put together.

Also in the repository root:

- **[llms.txt](../llms.txt)**: a concise, linked map of crcglot for an LLM or agent to load first, instead of crawling the source.
- **[EXAMPLES.md](../EXAMPLES.md)**: generated source for `crc32` in every language × variant combination (auto-generated gallery).
- **[BENCHMARKS.md](../BENCHMARKS.md)**: measured throughput per language × variant, plus the runtime engine's paths.
- **[CHANGELOG.md](../CHANGELOG.md)**: release history.
