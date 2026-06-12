"""Oracle-mutation paranoia test: prove the golden-vector assertions have teeth.

Why this is in the suite
------------------------

Every other test here checks that the engine and generators are *correct*.
None of them checks that the *assertions actually compare anything* -- a test
that accidentally compares a value to itself, or a refactor that turns a
comparison into a tautology, stays green forever and is invisible in a
passing run.  "Tests that pass because they aren't really tests" is a real
failure mode, and a green suite cannot reveal it about itself.

This file closes that gap with one deliberate wrench: corrupt every golden in
``crcglot._vectors.VECTORS`` (XOR 1, so every value stays in-domain and is
guaranteed different) and re-run the vectors suite in a subprocess.  If the
assertions are wired up, every VECTORS-consuming case must fail; if any of
them survives, an assertion has lost its teeth.

This is *oracle* mutation, not code mutation: one mutation and one extra
pytest run total (not one run per mutant the way mutmut-style tools work), so
the cost is a single slow-tier subprocess, about the price of one toolchain
test.

Two implementation hazards this design dodges, both of which would quietly
turn the paranoia test itself into a test-to-pass:

1. ``tests/test_independent_vectors.py`` snapshots VECTORS into its
   parametrize lists at *import* time.  Mutating after collection would
   change nothing, so the mutation runs in a ``pytest_configure`` plugin in a
   fresh subprocess -- guaranteed to land before the test module imports.
2. "At least one failure" would itself be a weak assertion.  The expected
   failure count is *computed* from the real test module's parametrize lists
   (no hardcoded count to rot), and the pass count must match the cases that
   are deliberately oracle-free: ``TestChunkingInvariance`` compares the
   stream engine against the one-shot engine (engine vs engine, no goldens),
   and the fixture-integrity test compares key *sets*, not values.  Their
   survival under mutation is correct behavior, and asserting it documents
   which tests consult the oracle and which deliberately do not.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_VECTOR_TESTS = Path(__file__).resolve().parent / "test_independent_vectors.py"

_PLUGIN = '''\
"""Injected via -p: corrupt every golden before test modules import."""


def pytest_configure(config):
    import crcglot._vectors as vectors

    vectors.VECTORS = {
        name: {inp: value ^ 1 for inp, value in goldens.items()}
        for name, goldens in vectors.VECTORS.items()
    }
    vectors._BY_PARAMS = None  # drop the goldens_for cache built from the originals
'''


def _vector_suite_case_counts() -> tuple[int, int]:
    """(expected_failures, expected_passes) computed from the real module.

    Imported by file path with *unmutated* goldens, purely to read the sizes
    of its parametrize lists, so the expected counts track the test module
    instead of rotting as hardcoded numbers.
    """
    spec = importlib.util.spec_from_file_location("_tiv_counts", _VECTOR_TESTS)
    assert spec is not None and spec.loader is not None, "test module must load"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    consults_oracle = len(mod._CASES) + len(mod._STREAM_CASES)
    oracle_free = len(mod._CHUNK_CASES) + 1  # +1: fixture-integrity (key sets)
    return consults_oracle, oracle_free


@pytest.mark.slow
class TestOracleMutation:
    """One corrupted oracle, one subprocess: every golden-consuming assertion
    must fail, and exactly the oracle-free ones must survive."""

    def test_corrupting_every_golden_fails_every_consuming_case(self, tmp_path):
        # Arrange -- a plugin that XORs all goldens at pytest_configure time,
        # delivered on PYTHONPATH so -p can import it in the child run.
        (tmp_path / "mutate_goldens.py").write_text(_PLUGIN, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
        expected_failed, expected_passed = _vector_suite_case_counts()

        # Act -- run the vectors suite against the corrupted oracle.
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(_VECTOR_TESTS),
             "-p", "mutate_goldens", "-o", "addopts=", "-q", "--no-header",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, cwd=_REPO, env=env,
        )

        # Assert -- the run must fail, with exactly the computed split.
        assert proc.returncode != 0, (
            "the vectors suite PASSED against a corrupted oracle: "
            "its assertions are not comparing against the goldens"
        )
        summary = re.search(r"(\d+) failed, (\d+) passed", proc.stdout)
        assert summary is not None, (
            f"could not parse the child pytest summary from:\n{proc.stdout[-2000:]}"
        )
        actual_failed = int(summary.group(1))
        actual_passed = int(summary.group(2))
        assert actual_failed == expected_failed, (
            f"{expected_failed} cases consult the oracle but only "
            f"{actual_failed} failed under mutation: "
            f"{expected_failed - actual_failed} assertion(s) have lost their teeth"
        )
        assert actual_passed == expected_passed, (
            f"expected exactly the {expected_passed} oracle-free cases to "
            f"survive (chunk invariance + fixture integrity), got "
            f"{actual_passed}: an oracle-free test may have grown a golden "
            f"dependency, or a consuming test went missing"
        )
