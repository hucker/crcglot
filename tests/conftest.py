"""Test-session environment setup.

Two phases, two mechanisms -- correctly this time:

* ``pytest_configure`` (a pytest hook, runs **before** test collection)
  does the PATH fixups.  Anything that controls test discovery -- in
  particular ``HAS_<tool> = shutil.which("<tool>") is not None`` flags
  that test modules evaluate at *import* time -- must see the corrected
  PATH, and pytest's collection phase imports those modules.  Earlier
  this lived in a ``@pytest.fixture(scope="session", autouse=True)``,
  which fires *after* collection and so was too late: 383 Go-toolchain
  tests silently skipped with ``HAS_GO`` frozen at ``False``.
* A session-scope autouse **fixture** does the Go ``build std``
  warm-up.  That step is purely about throughput (a cold ``GOCACHE``
  on Windows makes the per-test 30 s timeout flake under xdist) and
  doesn't gate any test's collection / skipif state, so a fixture is
  the right shape there.

See CLAUDE.md ("Skipped tests are not 'passed'") for the rule this
file is preventing future regressions of.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import warnings

import pytest

from crcglot.catalogue import AlgorithmInfo


# ---------------------------------------------------------------------------
# PATH-setup helpers (plain functions; called from ``pytest_configure``
# below so the corrected PATH is in place before any test module imports.)
# ---------------------------------------------------------------------------


def _append_to_path_if_present(candidate: str) -> None:
    """Append ``candidate`` to PATH if it exists and isn't already there.

    No-op when the directory doesn't exist (cross-platform / not-installed
    cases) or when it's already on PATH at any position.
    """
    if not os.path.isdir(candidate):
        return
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    norm = [os.path.normcase(p) for p in parts]
    if os.path.normcase(candidate) in norm:
        return
    os.environ["PATH"] = os.pathsep.join(parts + [candidate])


def _fix_msys2_path_on_windows() -> None:
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


def _add_windows_tool_dirs_to_path() -> None:
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
    - A JDK ``bin`` (``javac`` / ``java``): version-stamped install dirs
      (``...\\jdk-21.0.x\\bin``) can't be hardcoded, so :func:`_find_jdk_bin`
      checks ``%JAVA_HOME%`` and globs the common vendor roots.

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
    jdk_bin = _find_jdk_bin()
    if jdk_bin:
        candidates.append(jdk_bin)
    for c in candidates:
        _append_to_path_if_present(c)


def _find_jdk_bin() -> str | None:
    """Locate a JDK ``bin`` directory containing ``javac``.

    JDK install paths are version-stamped, so they can't be hardcoded like
    the other tool dirs.  Prefer ``%JAVA_HOME%``; otherwise glob the common
    Windows vendor roots (Microsoft OpenJDK, Adoptium/Temurin, Corretto,
    Zulu, Oracle).  Returns the first ``bin`` holding ``javac.exe``, or
    ``None``.  No-op on non-Windows.
    """
    if sys.platform != "win32":
        return None
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        cand = os.path.join(java_home, "bin")
        if os.path.isfile(os.path.join(cand, "javac.exe")):
            return cand
    patterns = [
        r"C:\Program Files\Microsoft\jdk-*\bin",
        r"C:\Program Files\Eclipse Adoptium\jdk-*\bin",
        r"C:\Program Files\Amazon Corretto\jdk*\bin",
        r"C:\Program Files\Zulu\zulu-*\bin",
        r"C:\Program Files\Java\jdk*\bin",
    ]
    for pat in patterns:
        for cand in sorted(glob.glob(pat), reverse=True):  # newest first
            if os.path.isfile(os.path.join(cand, "javac.exe")):
                return cand
    return None


# ---------------------------------------------------------------------------
# Pytest hooks
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add ``--exhaustive`` to opt into the per-algorithm execution tests.

    Those tests (marked ``exhaustive``) spawn one compiler/runtime process
    per algorithm and are superseded for routine runs by the batch-execution
    tests, which compile the whole catalogue in a single build.  They stay
    available as a single-algorithm isolation tool: ``pytest --exhaustive
    -k crc32``.  Deselected (not skipped) by default -- see
    ``pytest_collection_modifyitems`` -- so a normal run stays green, not
    amber, per CLAUDE.md.
    """
    parser.addoption(
        "--exhaustive",
        action="store_true",
        default=False,
        help="run the per-algorithm execution tests (marked 'exhaustive'); "
        "deselected by default in favour of the batch-execution tests.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Deselect ``exhaustive``-marked tests unless ``--exhaustive`` is given.

    Uses deselection (reported as ``deselected``), NOT skipping, so the
    default suite summary never shows a non-zero ``skipped`` count for
    these -- the green/amber distinction in CLAUDE.md.  Explicit ``-m
    exhaustive`` also forces them in (a deliberate marker selection
    shouldn't be silently overridden).
    """
    if config.getoption("--exhaustive"):
        return
    markexpr = config.getoption("-m", default="")
    if "exhaustive" in markexpr:
        return
    selected, deselected = [], []
    for item in items:
        if item.get_closest_marker("exhaustive") is not None:
            deselected.append(item)
        else:
            selected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected


def pytest_configure(config: pytest.Config) -> None:
    """Run PATH setup before test collection.

    ``pytest_configure`` fires before pytest collects test modules, so
    by the time ``tests/test_go_gen.py`` (or any other test module)
    evaluates a module-level ``HAS_<tool> = shutil.which("<tool>") is
    not None`` flag, this hook has already extended PATH to include the
    Windows install dirs.  Don't move this back to a session-autouse
    fixture -- those fire **after** collection and the ``HAS_<tool>``
    flags freeze in the wrong state.

    See CLAUDE.md ("Skipped tests are not 'passed'") for the
    don't-do-that note and the 383-test regression that motivated it.
    """
    del config  # unused; required by the hook signature
    _fix_msys2_path_on_windows()
    _add_windows_tool_dirs_to_path()


# ---------------------------------------------------------------------------
# Session-scope autouse fixtures (for things that don't gate discovery)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def warm_go_build_cache_on_windows() -> None:
    """Pre-populate Go's build cache so the slow-tier Go tests don't
    time out under pytest-xdist.

    Symptom: in ``test_go_gen.py::TestGeneratedGoExecutes`` and friends,
    under ``-n auto``, a handful of tests (2-6 per run, different
    algorithm names each time) fail with
    ``subprocess.TimeoutExpired: 'go run ...' timed out after 30
    seconds``.  Observed on the v0.8.0 release suite and on multiple
    feature-branch verification runs.

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

    This is correctly a fixture (not a hook): it doesn't gate any test's
    discovery / skipif state, only its throughput.  PATH must already
    have ``C:\\Program Files\\Go\\bin`` on it; ``pytest_configure``
    above takes care of that, so by the time this fixture fires
    ``go.exe`` is resolvable.

    No-op on non-Windows (the flake is Windows-specific) and on
    Windows shells where Go isn't installed.  Best-effort: any failure
    here is swallowed and the slow-tier Go tests are left to surface
    real Go misconfigurations on their own.
    """
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


# ---------------------------------------------------------------------------
# Shared verification-matrix fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def asymmetric_oracle_cases() -> list[tuple[str, AlgorithmInfo, int]]:
    """Two ``refin != refout`` custom parameter sets with two-oracle expected CRCs.

    The catalogue's single asymmetric entry (crc12-umts) covers only the
    refin=False/refout=True direction with xorout=0.  These two customs cover
    the opposite direction and the reflect+XOR finalize, for the generated-code
    execution tests (``TestAsymmetricCustomExecution`` in each language file
    and the generated-Python equivalent).

    Expected values for ``b"123456789"`` are computed live by anycrc and
    crccheck, which must agree.  Hard imports: a missing oracle errors every
    dependent test, never skips.  ``AlgorithmInfo.check`` is computed by
    crcglot's own engine and is deliberately not the reference here.

    Returns:
        ``[(label, AlgorithmInfo, oracle_crc_of_check_string), ...]`` in the
        fixed order refin-only, refout-only-xor (tests parametrize by index).
    """
    import anycrc
    from crccheck.crc import Crc as CrccheckCrc

    from crcglot import custom_algorithm

    data = b"123456789"
    specs = [
        (
            "refin-only",
            custom_algorithm(
                width=16, poly=0x8005, init=0xFFFF, refin=True, refout=False,
                desc="asymmetric probe: input reflection only",
            ),
        ),
        (
            "refout-only-xor",
            custom_algorithm(
                width=32, poly=0x04C11DB7, init=0xFFFFFFFF, refin=False,
                refout=True, xorout=0xFFFFFFFF,
                desc="asymmetric probe: output reflection + final XOR",
            ),
        ),
    ]
    cases: list[tuple[str, AlgorithmInfo, int]] = []
    for label, algo in specs:
        v_anycrc = anycrc.CRC(
            algo.width, algo.poly, algo.init, algo.refin, algo.refout, algo.xorout
        ).calc(data)
        v_crccheck = CrccheckCrc(
            width=algo.width, poly=algo.poly, initvalue=algo.init,
            reflect_input=algo.refin, reflect_output=algo.refout,
            xor_output=algo.xorout,
        ).calc(data)
        assert v_anycrc == v_crccheck, (
            f"{label}: anycrc=0x{v_anycrc:X} != crccheck=0x{v_crccheck:X} "
            f"-- oracle regression, not a crcglot bug"
        )
        cases.append((label, algo, v_anycrc))
    return cases
