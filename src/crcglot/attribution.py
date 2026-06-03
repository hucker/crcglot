"""Acknowledgments for the intellectual work crcglot stands on.

Two flavors of the same content:

* ``ATTRIBUTION`` -- a human-readable multi-line string suitable for
  printing (via ``crcglot credits``) or splashing into a help screen.
* ``ACKNOWLEDGMENTS`` -- a structured tuple of dicts (``name``,
  ``author``, ``url``, ``role``) for callers that want to render the
  same content programmatically -- HTML, JSON, a TUI panel, whatever.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping


ATTRIBUTION: str = """\
crcglot stands on the shoulders of:

- The reveng CRC catalogue by Greg Cook
  <https://reveng.sourceforge.io/crc-catalogue/all.htm>
  -- source of 70 of crcglot's 72 catalogue entries (every algorithm
  whose ``info.source == "reveng"``); the remaining entries cite their
  own primary documentation in the per-entry ``source`` field.

- zlib by Mark Adler, Jean-loup Gailly et al.
  <https://zlib.net/>
  -- runtime delegation for CRC-32/ISO-HDLC and JAMCRC, which take
  the PCLMULQDQ folding path on x86 and the PMULL/CRC32 instructions
  on ARM.

- The Rocksoft Model CRC parameterization by Ross N. Williams
  <http://ross.net/crc/download/crc_v3.txt>
  -- the (width, poly, init, refin, refout, xorout, check) vocabulary
  every catalogue entry is expressed in.

Thank you.
"""


# Use MappingProxyType so the published per-entry dicts are read-only
# from outside the module -- callers can't mutate the official table.
ACKNOWLEDGMENTS: tuple[Mapping[str, str], ...] = (
    MappingProxyType({
        "name": "reveng CRC catalogue",
        "author": "Greg Cook",
        "url": "https://reveng.sourceforge.io/crc-catalogue/all.htm",
        "role": "source of 70 of crcglot's 72 catalogue entries",
    }),
    MappingProxyType({
        "name": "zlib",
        "author": "Mark Adler, Jean-loup Gailly et al.",
        "url": "https://zlib.net/",
        "role": "hardware-accelerated runtime CRC-32 (PCLMULQDQ / PMULL)",
    }),
    MappingProxyType({
        "name": "Rocksoft Model CRC parameterization",
        "author": "Ross N. Williams",
        "url": "http://ross.net/crc/download/crc_v3.txt",
        "role": "the (width, poly, init, refin, refout, xorout) vocabulary",
    }),
)
