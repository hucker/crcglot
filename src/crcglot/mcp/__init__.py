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
pure-stdlib and gives users a clear ``ModuleNotFoundError`` if they
type ``crcglot-mcp`` without the extra.
"""

from __future__ import annotations


def main() -> None:
    """Entry point for the ``crcglot-mcp`` script.

    Imports :mod:`crcglot.mcp.server` lazily so the ``mcp`` SDK is only
    required at the moment the server is actually started -- not at
    bare-package import time.
    """
    from crcglot.mcp.server import main as _server_main
    _server_main()


__all__ = ["main"]
