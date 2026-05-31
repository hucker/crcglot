"""Session-scope fixtures that prepare the test environment.

Three autouse, session-scoped fixtures run before any test:

* :func:`fix_msys2_path_on_windows` -- put msys2's ``mingw64\\bin``
  ahead of Git Bash's on Windows so pytest's ``gcc`` subprocess loads
  the right DLLs.
* :func:`add_windows_tool_dirs_to_path` -- append the standard install
  locations of Go, Node, iverilog, etc. that some Windows installers
  don't add to PATH on their own.
* :func:`warm_go_build_cache_on_windows` -- pre-populate Go's build
  cache so the slow-tier Go tests don't time out under
  ``pytest-xdist``.  Depends on :func:`add_windows_tool_dirs_to_path`
  so ``go.exe`` is reachable when we shell out.

Each fixture is a no-op on platforms or shells where the underlying
issue doesn't apply (Linux/macOS, or Windows shells where the tool is
already on PATH), and idempotent under xdist (every worker re-imports
this module, but the fixture bodies all detect "already done" and
skip).
"""

from __future__ import annotations

import os
import subprocess
import sys
import warnings

import pytest


# ---------------------------------------------------------------------------
# Internal helpers (plain functions; not fixtures because nothing requests
# them by parameter -- they're only called from the fixtures below).
# ---------------------------------------------------------------------------


def _append_to_path_if_present(candidate: str) -> None:
    """Append ``candidate`` to PATH if it exists and isn't already there.

    Used by the Windows tool-dir fixture below.  No-op when the directory
    doesn't exist (cross-platform / not-installed cases) or when it's
    already on PATH at any position.
    """
    if not os.path.isdir(candidate):
        return
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    norm = [os.path.normcase(p) for p in parts]
    if os.path.normcase(candidate) in norm:
        return
    os.environ["PATH"] = os.pathsep.join(parts + [candidate])


# ---------------------------------------------------------------------------
# Session-scope autouse fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def fix_msys2_path_on_windows() -> None:
    """Ensure msys2's ``mingw64\\bin`` precedes Git's ``mingw64\\bin`` in PATH.

    Symptom: under Git Bash on Windows, the CRC codegen-exec tests
    silently fail -- gcc returns rc=1 with empty stderr.  The fast
    suite, lint, and ty all stay green; only the subprocess-spawning
    codegen-exec tests fall over.

    Root cause: Git Bash prepends ``C:\\Program Files\\Git\\mingw64\\bin``
    to PATH.  When pytest spawns gcc via Python subprocess, gcc finds
    its sub-tool (cc1.exe) but Windows DLL resolution loads Git's
    older libstdc++-6 / libgcc_s_seh-1 DLLs first -- which are
    incompatible with msys2's gcc 15.x.  cc1.exe fails to load with
    NT status 0xC0000139 (STATUS_ENTRYPOINT_NOT_FOUND); gcc reports
    rc=1 with no diagnostic.

    Fix: prepend ``C:\\msys64\\mingw64\\bin`` to PATH for the test
    session, AND warn so the user knows it happened (silent fixes
    hide reality -- the user might wonder why their other tooling
    sees one gcc and pytest sees another).  No-op on Linux/macOS
    (msys2 path doesn't exist) and no-op on Windows shells where
    msys2 is already first (PowerShell, cmd with normal config).
    """
    if sys.platform != "win32":
        return
    msys2_bin = r"C:\msys64\mingw64\bin"
    if not os.path.isdir(msys2_bin):
        return
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    norm = [os.path.normcase(p) for p in parts]
    norm_msys2 = os.path.normcase(msys2_bin)
    # Already first?  No-op.
    if norm and norm[0] == norm_msys2:
        return
    # Detect the specific bad condition: Git's mingw64\bin appears
    # in PATH ahead of msys2's.  Worth a loud warning in that case
    # because (a) the user's interactive gcc and pytest's gcc would
    # resolve to different installs, and (b) a permanent fix is a
    # one-line .bashrc edit.
    git_norm = os.path.normcase(r"C:\Program Files\Git\mingw64\bin")
    git_idx = norm.index(git_norm) if git_norm in norm else -1
    msys2_idx = norm.index(norm_msys2) if norm_msys2 in norm else -1
    if git_idx >= 0 and (msys2_idx < 0 or git_idx < msys2_idx):
        warnings.warn(
            f"crcglot tests: prepending {msys2_bin!r} to PATH for the test "
            f"session.  Git's mingw64\\bin appears in PATH at position "
            f"{git_idx} -- ahead of msys2's at position "
            f"{'absent' if msys2_idx < 0 else msys2_idx}.  Without this fix, "
            f"pytest's gcc subprocess loads Git's libstdc++-6 DLL and "
            f"cc1.exe crashes with NT status 0xC0000139.  Make permanent "
            f"by adding 'export PATH=\"/c/msys64/mingw64/bin:$PATH\"' to "
            f"your .bashrc / .zshrc.",
            RuntimeWarning,
            stacklevel=2,
        )
    # Prepend (drop any existing later occurrence so PATH doesn't grow
    # every test session if conftest reloads).
    parts = [p for p, n in zip(parts, norm) if n != norm_msys2]
    os.environ["PATH"] = os.pathsep.join([msys2_bin] + parts)


@pytest.fixture(scope="session", autouse=True)
def add_windows_tool_dirs_to_path() -> None:
    """Add tool dirs to PATH for Windows installers that don't update it.

    Each entry is a directory that some standard Windows installer of a
    test-time tool drops binaries into without amending PATH, or where
    a winget-installed shim lives that an already-open shell hasn't yet
    refreshed PATH to see:

    - ``C:\\iverilog\\bin``: Icarus Verilog winget / official installer.
    - ``C:\\Program Files\\nodejs``: Node.js LTS winget / MSI.
    - ``C:\\Program Files\\Go\\bin``: Go via winget / official MSI.
    - ``%LOCALAPPDATA%\\Microsoft\\WinGet\\Links``: winget's shim
      directory (archive-distributed tools without their own
      installer land here -- safe to include even if no current
      target depends on it).
    - ``%APPDATA%\\npm``: where ``npm install -g <pkg>`` drops shims
      (tsx, etc.).

    All entries are checked for existence first, so this no-ops on
    Linux/macOS and on Windows shells without the tools installed.
    Without this fixup, the slow-tier tests for any of these tools
    would skip after a fresh install -- pytest's subprocess inherits
    the parent shell's pre-install PATH and can't see the binaries
    that the install just added.
    """
    if sys.platform != "win32":
        return
    appdata = os.environ.get("APPDATA", "")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        r"C:\iverilog\bin",
        r"C:\Program Files\nodejs",
        r"C:\Program Files\Go\bin",
    ]
    if local_appdata:
        candidates.append(
            os.path.join(local_appdata, "Microsoft", "WinGet", "Links")
        )
    if appdata:
        candidates.append(os.path.join(appdata, "npm"))
    for c in candidates:
        _append_to_path_if_present(c)


@pytest.fixture(scope="session", autouse=True)
def warm_go_build_cache_on_windows(
    add_windows_tool_dirs_to_path: None,
) -> None:
    """Pre-populate Go's build cache so the slow-tier Go tests don't
    time out under pytest-xdist.

    Symptom: in ``test_go_gen.py::TestGeneratedGoExecutes`` and friends,
    under ``-n auto``, a handful of tests (2-6 per run, different
    algorithm names each time) fail with
    ``subprocess.TimeoutExpired: 'go run ...' timed out after 30
    seconds``.  Observed on the v0.8.0 release suite, on the
    feat/crc-codec verification run, and on chore/lang-subpackage.

    Root cause: Go's ``GOCACHE`` (``C:\\Users\\<user>\\AppData\\Local\\
    go-build`` by default) is empty on a fresh box and gets partially
    invalidated after a Go-version upgrade.  Each xdist worker's first
    ``go run`` triggers a compile of the Go standard library; the
    workers race on a cold cache and the slowest worker's compile time
    becomes the per-test wall, which exceeds 30 s for several tests.

    Fix: run ``go build std`` once at session start, *before* any test.
    Idempotent -- on an already-warm cache ``go build std`` notices
    the cache hits and exits in milliseconds, so re-running the suite
    has no measurable extra cost.

    The ``add_windows_tool_dirs_to_path`` parameter is the explicit
    "depends on" link to the tool-dir fixture above so Go is on PATH
    by the time we shell out.

    No-op on non-Windows (the flake is Windows-specific) and on
    Windows shells where Go isn't installed.  Best-effort: any failure
    here is swallowed and the slow-tier Go tests are left to surface
    real Go misconfigurations on their own.
    """
    del add_windows_tool_dirs_to_path  # consumed only for ordering
    if sys.platform != "win32":
        return
    go = r"C:\Program Files\Go\bin\go.exe"
    if not os.path.isfile(go):
        return
    # Generous timeout: a truly cold cache takes ~2 minutes; the cap
    # exists only to bound a hang from a broken Go install.  ``check=
    # False`` because we don't want a Go misconfig to abort the entire
    # test session -- the per-test failures are still informative.
    try:
        subprocess.run(
            [go, "build", "std"],
            check=False,
            timeout=300,
            capture_output=True,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass
