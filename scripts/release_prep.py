"""Release prep: cut release branch, bump version, regen docs, test, commit.

Stops before merging to main.  You (or an AI assistant following
scripts/RELEASE.md) review the diffs -- especially the CHANGELOG stub --
rewrite the changelog into a real user-facing summary, then run
release_publish.py to finish.

Usage:
    python scripts/release_prep.py 0.8.0

What it does:
    1. Sanity-check git state (on main, clean, synced) + lint gate (ruff, ty)
    2. Cut rel/v<version> branch from main
    3. Bump version in pyproject.toml; refresh uv.lock
    4. Regenerate EXAMPLES.md (the generated gallery)
    5. Run the FULL pytest suite; refresh the README test-count + coverage badges
    6. Insert a CHANGELOG stub (raw git-log bullets + a TODO marker)
    7. Commit the release

Aborts loudly on any failure.  Safe-restart: if it fails halfway, get
back to main and delete the branch::

    git checkout main && git branch -D rel/v<version>

NOTE: crcglot ships a C extension, so the cross-platform wheels are
built on CI, not here.  release_prep does NOT build wheels -- it only
prepares the source.  Publishing happens in release_publish.py via the
tag-triggered wheels.yml matrix.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

# Allow running as `python scripts/release_prep.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_common import (  # noqa: E402
    REPO_ROOT,
    assert_clean_tree,
    assert_main_in_sync_with_origin,
    assert_on_main,
    assert_tag_does_not_exist,
    assert_tool_available,
    die,
    info,
    last_tag,
    ok,
    run,
    run_out,
    validate_version,
)


# ── lint gate ──────────────────────────────────────────────────────────────────


def assert_zero_lint() -> None:
    """Hard-fail the release if ruff or ty reports anything.

    crcglot's CLAUDE.md treats both clean as a release precondition, not
    a nice-to-have.  Both tools print ``All checks passed!`` when clean and
    exit nonzero otherwise, so we capture with ``check=False`` and look for
    the success sentinel.
    """
    ruff = run_out(["uvx", "ruff", "check", "src", "tests"], check=False)
    if "All checks passed!" not in ruff:
        die("ruff is not clean. Fix on a chore branch first:\n" + ruff)
    ty = run_out(["uvx", "ty", "check", "src", "tests"], check=False)
    if "All checks passed!" not in ty:
        die("ty is not clean. Fix on a chore branch first:\n" + ty)
    ok("lint clean (ruff + ty: All checks passed!)")


# ── version bumping ──────────────────────────────────────────────────────────────


def bump_pyproject(version: str) -> None:
    path = REPO_ROOT / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'^version = "[^"]+"',
        f'version = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        die('could not find a single `version = "..."` line in pyproject.toml')
    path.write_text(new_text, encoding="utf-8")
    ok(f"pyproject.toml -> {version}")


def refresh_uv_lock() -> None:
    run(["uv", "lock"])
    ok("uv.lock refreshed")


# ── generated docs ──────────────────────────────────────────────────────────────


def regenerate_examples() -> None:
    """Re-run the EXAMPLES.md generator.

    EXAMPLES.md is auto-generated from LANGUAGES; CLAUDE.md requires a
    fresh regeneration before every release so the published gallery
    matches the shipped generators.  Idempotent -- no diff if nothing
    changed.
    """
    run(["uv", "run", "python", "scripts/regenerate_examples.py"])
    ok("EXAMPLES.md regenerated")


# ── test run + badge refresh ─────────────────────────────────────────────────────


def run_full_suite_and_measure() -> tuple[int, int]:
    """Run the FULL pytest suite once; return (passed_count, coverage_percent).

    A single run serves three jobs: it is the release gate (a failure
    aborts), and its captured output yields both the passed-test count and
    the overall coverage percent for the README badges -- no redundant
    second run of the ~3-minute suite.
    """
    info("Running the full pytest suite (this compiles + runs generated code; ~3 min)...")
    proc = run(["uv", "run", "pytest"], check=False, capture=True)
    out = proc.stdout + (proc.stderr or "")
    if proc.returncode != 0:
        # Surface the captured output so the operator can see what failed.
        print(out)
        die("pytest failed. Fix before releasing.")

    m_pass = re.search(r"(\d+) passed", out)
    if not m_pass:
        print(out)
        die("could not parse passed-test count from pytest output")
    passed = int(m_pass.group(1))

    # Coverage terminal report ends with e.g. ``TOTAL   4210   84   98%``.
    m_cov = re.search(r"^TOTAL\s+\d+\s+\d+\s+(\d+)%", out, re.MULTILINE)
    if not m_cov:
        print(out)
        die("could not parse coverage percent from pytest --cov output")
    cov = int(m_cov.group(1))

    ok(f"full suite green ({passed} passed, {cov}% coverage)")
    return passed, cov


def coverage_badge_color(percent: int) -> str:
    """shields.io color for a coverage percentage.

    crcglot's target is >=90% overall, so green tracks that bar: 90+
    brightgreen, 75-89 yellow, below 75 red.
    """
    if percent >= 90:
        return "brightgreen"
    if percent >= 75:
        return "yellow"
    return "red"


def update_readme_badges(passed: int, cov_percent: int) -> None:
    """Refresh the test-count and coverage shields at the top of README.md.

    The ruff + ty badges are static ``passing`` shields gated by
    assert_zero_lint(), so they need no per-release rewrite.
    """
    path = REPO_ROOT / "README.md"
    text = path.read_text(encoding="utf-8")

    new_text, n_tests = re.subn(
        r"badge/tests-\d+%20passed-[a-z]+",
        f"badge/tests-{passed}%20passed-brightgreen",
        text,
        count=1,
    )
    if n_tests != 1:
        die("could not find the tests badge in README.md")

    color = coverage_badge_color(cov_percent)
    new_text, n_cov = re.subn(
        r"badge/coverage-\d+%25-[a-z]+",
        f"badge/coverage-{cov_percent}%25-{color}",
        new_text,
        count=1,
    )
    if n_cov != 1:
        die("could not find the coverage badge in README.md")

    path.write_text(new_text, encoding="utf-8")
    ok(f"README badges updated (tests={passed}, coverage={cov_percent}% ({color}))")


# ── changelog ────────────────────────────────────────────────────────────────────


def insert_changelog_stub(version: str) -> None:
    """Insert a CHANGELOG stub for the new version, seeded with git-log bullets.

    The stub carries a ``<!-- TODO ... -->`` marker; release_publish.py
    refuses to publish while any TODO remains, forcing the human/AI step
    of rewriting the raw commit list into a user-facing summary.
    """
    path = REPO_ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")

    try:
        prev = last_tag()
    except Exception:
        prev = ""

    log_range = f"{prev}..HEAD" if prev else "HEAD"
    commits = run_out(["git", "log", log_range, "--oneline", "--no-merges"])

    bullets = []
    for line in commits.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            bullets.append(f"- {parts[1]}")
    bullet_block = "\n".join(bullets) if bullets else "- (no commits found)"

    today = dt.date.today().isoformat()
    stub = (
        f"## v{version} — {today}\n"
        f"\n"
        f"<!-- TODO: rewrite the raw commit bullets below into a user-facing "
        f"summary, then delete this comment. Commits since {prev or 'the beginning'}: -->\n"
        f"{bullet_block}\n"
        f"\n"
    )

    # Insert just after the "# Changelog" header.  A lambda replacement
    # keeps backslashes in commit subjects from being read as regex
    # back-references.
    new_text = re.sub(
        r"(# Changelog\n+)",
        lambda m: m.group(0) + stub,
        text,
        count=1,
    )
    if new_text == text:
        die("could not find '# Changelog' header in CHANGELOG.md")
    path.write_text(new_text, encoding="utf-8")
    ok(f"CHANGELOG.md stub inserted for v{version} (edit before publishing)")


# ── git operations ───────────────────────────────────────────────────────────────


def cut_release_branch(version: str) -> None:
    branch = f"rel/v{version}"
    existing = run_out(["git", "branch", "--list", branch])
    if existing:
        die(
            f"branch {branch!r} already exists locally. "
            f"To start over: `git checkout main && git branch -D {branch}`"
        )
    run(["git", "checkout", "-b", branch])
    ok(f"on branch {branch}")


def commit_release(version: str) -> None:
    """Stage the release files and commit as ``Release v<version>``."""
    files = [
        "pyproject.toml",
        "uv.lock",
        "EXAMPLES.md",
        "README.md",
        "CHANGELOG.md",
    ]
    run(["git", "add", *files])
    status = run_out(["git", "status", "--porcelain", *files])
    if not status:
        die("no release-bump changes to commit. Did the version bump steps run?")
    run(["git", "commit", "-m", f"Release v{version}"])
    ok(f"release commit created (Release v{version})")


# ── main ─────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="New version, e.g. 0.8.0 (no leading 'v')")
    args = parser.parse_args()

    version = args.version
    validate_version(version)

    info(f"Preparing release v{version}")
    total = 7

    def step(n: int, label: str) -> None:
        info(f"[{n}/{total}] {label}")

    step(1, "Checking environment, git state, and lint...")
    assert_tool_available("git")
    assert_tool_available("uv")
    assert_tool_available("uvx")
    assert_on_main()
    assert_clean_tree()
    assert_main_in_sync_with_origin()
    assert_tag_does_not_exist(version)
    assert_zero_lint()
    ok("git state is clean and ready")

    step(2, "Cutting release branch...")
    cut_release_branch(version)

    step(3, "Bumping version + refreshing uv.lock...")
    bump_pyproject(version)
    refresh_uv_lock()

    step(4, "Regenerating EXAMPLES.md...")
    regenerate_examples()

    step(5, "Running full suite + refreshing README badges...")
    passed, cov = run_full_suite_and_measure()
    update_readme_badges(passed, cov)

    step(6, "Inserting CHANGELOG stub...")
    insert_changelog_stub(version)

    step(7, "Committing release...")
    commit_release(version)

    # ── Done ───────────────────────────────────────────────────────────────────
    print()
    ok(f"Release v{version} prepped on branch rel/v{version}")
    print()
    info("Next steps (see scripts/RELEASE.md):")
    print("  1. Review the diff:        git log -p main..HEAD")
    print("  2. Rewrite CHANGELOG.md    (the stub has a TODO marker + raw bullets)")
    print("  3. Amend the commit:       git add CHANGELOG.md && git commit --amend --no-edit")
    print("  4. Publish:                python scripts/release_publish.py --yes")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("aborted by user", code=130)
