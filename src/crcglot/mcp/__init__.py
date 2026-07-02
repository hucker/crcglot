"""crcglot MCP server -- optional subpackage.

Exposes the package's CLI surface as a Model Context Protocol server
so LLM clients (Claude Desktop, Cursor, mcp-cli, ...) can call
``crc_detect`` / ``crc_encode`` / ``crc_generate`` etc. as named
tools.  See ``docs/MCP.md`` for the protocol and tool reference.

Install with::

    pip install 'crcglot[mcp]'

The ``mcp`` SDK is only required to actually run the server.  The
import path ``crcglot.mcp`` itself is lazy: ``import crcglot.mcp``
without the ``mcp`` extra installed succeeds (this module exposes
nothing yet); only ``crcglot.mcp.main`` materialises the SDK-dependent
:mod:`crcglot.mcp.server` module.  That keeps the base install
pure-stdlib and lets :func:`main` turn the missing extra into an
actionable message instead of a bare ``ModuleNotFoundError``.
"""

from __future__ import annotations


def main() -> None:
    """Entry point for the ``crcglot-mcp`` script.

    Imports :mod:`crcglot.mcp.server` lazily so the ``mcp`` SDK is only
    required at the moment the server is actually started -- not at
    bare-package import time.  When the ``[mcp]`` extra is not installed,
    the import fails; catch that one case and exit with the install
    instructions rather than a bare ``ModuleNotFoundError``.
    """
    try:
        from crcglot.mcp.server import main as _server_main
    except ModuleNotFoundError as e:
        # Only translate the missing-extra case; a genuine import bug in
        # our own code (or a broken mcp install) must still surface.
        if (e.name or "").split(".")[0] != "mcp":
            raise
        raise SystemExit(
            "crcglot-mcp needs the MCP SDK, which ships with the 'mcp' extra.\n"
            "  install:  pip install 'crcglot[mcp]'   (or  uv add 'crcglot[mcp]')\n"
            "  or run without installing:  uvx --from 'crcglot[mcp]' crcglot-mcp"
        ) from e
    _server_main()


__all__ = ["main"]
