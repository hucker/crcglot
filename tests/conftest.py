"""Test-session environment setup.

Two phases, two mechanisms -- correctly this time:

* ``pytest_configure`` (a pytest hook, runs **before** test collection)
  fixes the msys2 / Git-Bash gcc ordering.  Anything that controls test
  discovery -- in particular ``HAS_<tool> = shutil.which("<tool>") is
  not None`` flags that test modules evaluate at *import* time -- must
  see the corrected PATH, and pytest's collection phase imports those
  modules.  Earlier this lived in a ``@pytest.fixture(scope="session",
  autouse=True)``, which fires *after* collection and so was too late:
  383 Go-toolchain tests silently skipped with ``HAS_GO`` frozen at
  ``False``.

  This hook does **not** hunt for toolchain install dirs.  It used to
  append a hardcoded list of them (Go, Node, iverilog, a globbed JDK,
  the winget shim dir, ...), which was a maintenance tax -- every new
  target meant another entry -- and it masked a stale shell rather
  than surfacing it.  The toolchains belong on PATH; a fresh install
  needs a new shell before pytest sees it, and until then the affected
  tests SKIP, which this project already treats as amber and
  investigates (see "Skipped tests are not 'passed'").
* A session-scope autouse **fixture** does the Go ``build std``
  warm-up.  That step is purely about throughput (a cold ``GOCACHE``
  on Windows makes the per-test 30 s timeout flake under xdist) and
  doesn't gate any test's collection / skipif state, so a fixture is
  the right shape there.

See CLAUDE.md ("Skipped tests are not 'passed'") for the rule this
file is preventing future regressions of.
"""

from __future__ import annotations

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
    _register_hypothesis_profiles()


# ---------------------------------------------------------------------------
# Hypothesis profiles
# ---------------------------------------------------------------------------


def _register_hypothesis_profiles() -> None:
    """Register the ``ci`` / ``dev`` Hypothesis profiles and load one.

    Property-based tests search the *infinite* axis (message bytes,
    off-catalogue parameter tuples); the countable axis stays
    exhaustively enumerated by parametrize.  See
    ``docs/verification/index.md``.

    Two settings are non-negotiable here:

    * ``deadline=None`` -- the suite runs ``-n auto`` across ~16 xdist
      workers, where Hypothesis's default 200 ms per-example deadline
      flakes on scheduler noise rather than on real slowness.
    * ``derandomize=True`` in the ``ci`` profile -- a release gate must
      mean the same thing on every run.  Discovery happens in the
      ``dev`` profile locally; anything it finds gets pinned as an
      ``@example`` so the ``ci`` profile carries it forever after.

    Selected with ``HYPOTHESIS_PROFILE`` (default ``dev``); CI sets
    ``ci`` in ``.github/workflows/{tests,exec}.yml``.

    No-ops when Hypothesis is absent.  cibuildwheel tests each built
    wheel in a MINIMAL venv (``test-requires = "pytest"``) running only
    ``tests/test_c_extension.py``, which needs no property testing --
    and an unguarded import here raised ``ModuleNotFoundError`` inside
    ``pytest_configure``, which pytest reports as INTERNALERROR, failing
    all five wheel jobs and skipping the PyPI publish.  A genuinely
    missing Hypothesis still fails loudly where it matters: the property
    test modules import it at the top and error at collection.
    """
    try:
        from hypothesis import HealthCheck, settings
    except ImportError:
        return

    # Passed explicitly rather than via a **dict: an untyped dict widens the
    # values and the checker then matches them against the wrong parameter.
    settings.register_profile(
        "ci",
        derandomize=True,
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    settings.register_profile(
        "dev",
        max_examples=250,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


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
