"""Build-mode selector for the C accelerator ``crcglot._c``.

Everything else about the package is declared in pyproject.toml.  This file
exists because one C source has to be compiled three different ways and a
static TOML table cannot vary by interpreter:

  - GIL builds: the Stable ABI pinned at 3.11 (``Py_LIMITED_API``), giving
    one ``cp311-abi3`` wheel per platform that every CPython 3.11+ accepts.
  - Free-threaded 3.15 and later: the free-threaded Stable ABI (abi3t,
    PEP 803), which the same limited-API flag selects on such an
    interpreter.  The wheel is tagged ``cp315-abi3.abi3t`` and loads on GIL
    and free-threaded 3.15+ alike.
  - Free-threaded 3.13 and 3.14: no Stable ABI exists there (CPython issue
    111506), so the extension is built against the full C API and the wheel
    is version-specific (``cp314-cp314t``).  3.13t gets no wheel, since
    cibuildwheel 4 has no identifier for it; its sdist build is marked
    optional so a machine without a compiler still gets the pure-Python
    engine instead of a failed install.

DECISIONS.md ("Free-threaded CPython") records the spike that settled this.
"""

from __future__ import annotations

import sys
import sysconfig

from setuptools import Extension, setup

FREE_THREADED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))

if not FREE_THREADED:
    define_macros = [("Py_LIMITED_API", "0x030B0000")]
    py_limited_api = True
    wheel_abi_tag: str | None = "cp311"
    optional = False
elif sys.version_info >= (3, 15):
    define_macros = [("Py_LIMITED_API", "0x030F0000")]
    py_limited_api = True
    wheel_abi_tag = "cp315"
    optional = False
else:
    define_macros = []
    py_limited_api = False
    wheel_abi_tag = None
    optional = sys.version_info < (3, 14)

extension = Extension(
    "crcglot._c",
    sources=["src/crcglot/_c.c"],
    define_macros=define_macros,
    py_limited_api=py_limited_api,
    optional=optional,
)

# ``py_limited_api`` on the Extension compiles against the Stable ABI; this
# option tells bdist_wheel to *tag* the wheel accordingly (abi3, or
# abi3.abi3t on a free-threaded 3.15+ interpreter).  Both must agree.
options = {"bdist_wheel": {"py_limited_api": wheel_abi_tag}} if wheel_abi_tag else {}

setup(ext_modules=[extension], options=options)
