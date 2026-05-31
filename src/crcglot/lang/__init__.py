"""Per-language code generators.

One module per target -- ``c``, ``csharp``, ``go``, ``python``, ``rust``,
``typescript``, ``verilog``, ``vhdl`` -- each exposing a ``generate_<lang>``
catalogue-lookup entry point and a ``generate_<lang>_from_entry`` raw-
parameters entry point.

These are wired into the public :data:`crcglot.LANGUAGES` registry by
``crcglot.targets``; downstream users should reach the generators through
that registry or via the convenience re-exports on :mod:`crcglot` rather
than importing the language modules directly.
"""
