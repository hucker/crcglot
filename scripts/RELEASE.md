# Releasing crcglot

This is the release runbook.  It is written to be followed by a human
**or** by an AI assistant (e.g. Claude Code) driving the two release
scripts.  The only step that needs judgement -- rewriting the changelog
into a user-facing summary -- is called out explicitly.

crcglot ships a **C extension** (`crcglot._c`), so a release is not a
single `uv build`.  The cross-platform wheels are built by the
cibuildwheel matrix on CI; this dev box only prepares the source and
pushes the tag that triggers them.

## Mental model

Two scripts, one human-in-the-loop gap between them:

```text
release_prep.py 0.8.0        # local: bump, regen, test, CHANGELOG stub, commit
        |
        v
 (you / AI rewrite CHANGELOG.md, amend the commit)
        |
        v
release_publish.py --yes     # local: merge, tag, push  ->  CI builds wheels + publishes
```

- **prep** does everything that must happen *before* a tag exists and is
  fully reversible (just delete the branch).
- **publish** does the irreversible things (merge to main, tag, push) and
  hands off to CI for the actual PyPI upload.

## One-time setup: PyPI trusted publisher

Publishing uses **OIDC trusted publishing** (no API token in repo
secrets).  PyPI ties a trusted publisher to a specific **workflow
filename**.  Publishing now lives in **`wheels.yml`** (it used to be in
`publish.yml`, which has been removed).

Before the first release that uses this flow, confirm the trusted
publisher on <https://pypi.org/manage/project/crcglot/settings/publishing/>
lists:

| Field             | Value           |
| ----------------- | --------------- |
| Owner             | `hucker`        |
| Repository        | `crcglot`       |
| Workflow filename | `wheels.yml`    |
| Environment       | *(leave blank)* |

If it still says `publish.yml`, add a new publisher for `wheels.yml` (or
edit the existing one).  Symptom of getting this wrong: the CI publish
step fails with *"not a trusted publisher"* after the matrix builds
green.

## Preconditions

- On `main`, clean tree, in sync with `origin/main`.
- `ruff` and `ty` both clean (the prep script hard-gates this).
- `git`, `uv`, `uvx`, and `gh` (authenticated) on PATH.
- The full test suite passes locally (prep runs it; it needs
  `gcc`, `rustc`, `go`, `dotnet`, `tsx`, `iverilog`, `ghdl` for the slow
  subprocess tests).

## Step 1 — prep

```bash
python scripts/release_prep.py 0.8.0      # bare version, no leading 'v'
```

This cuts `rel/v0.8.0`, bumps `pyproject.toml`, refreshes `uv.lock`,
regenerates `EXAMPLES.md`, runs the full suite, refreshes the README
test-count + coverage badges, and inserts a CHANGELOG stub.  It stops on
the release branch with an uncommitted-to-final commit already made.

If it fails partway, get back to a clean state and retry:

```bash
git checkout main && git branch -D rel/v0.8.0
```

## Step 2 — rewrite the CHANGELOG (the human / AI step)

`release_prep.py` seeds `CHANGELOG.md` with a `## v0.8.0 — <date>`
section containing a `<!-- TODO ... -->` marker and the raw `git log`
subjects since the last tag.

Rewrite that section into a user-facing summary:

- Group by what the *user* sees: new targets, new flags, fixes,
  performance.  Match the prose style of the existing v0.7.0 entry
  (short headed subsections, code fences for new commands).
- Delete the `<!-- TODO ... -->` comment and the raw bullet list.
- `release_publish.py` refuses to run while any `TODO` remains, so this
  is enforced, not optional.

Then fold the edit into the release commit:

```bash
git add CHANGELOG.md README.md   # README too if you tweaked badges/wording
git commit --amend --no-edit
```

Review the whole diff before publishing:

```bash
git log -p main..HEAD
```

## Step 3 — publish

```bash
python scripts/release_publish.py --yes
```

This merges `rel/v0.8.0` into `main` with `--no-ff`, tags `v0.8.0`,
pushes `main` + tag + release branch, and creates the GitHub release with
notes pulled from the CHANGELOG section.

**It does not build or upload anything to PyPI.**  Pushing the `v0.8.0`
tag triggers `.github/workflows/wheels.yml`, which builds the full wheel
matrix + sdist and publishes them via trusted publishing.

## Step 4 — watch CI finish (both workflows must be green)

A release fires **two** workflows: the tag push triggers `wheels.yml`, and the
merge-to-main push triggers `tests.yml`.  Both must go green before the release
is done.  `wheels.yml` now runs a fast-suite **gate** whose result the `publish`
job needs, so a failing test blocks the PyPI upload rather than racing a live
release past it (the trap that shipped 0.24.0 with a red `Tests` run).  Watch
both, and do not call the release done until both are green:

```bash
gh run watch                                  # the Wheels run: gate + matrix + publish
gh run list --workflow tests.yml --limit 1    # confirm the main-push Tests run is green too
```

The Wheels run builds wheels on Linux (x86_64 + aarch64, manylinux + musllinux),
Windows (x64 + arm64), and macOS (arm64), runs the in-wheel parity tests
on each, builds the sdist, then the `publish` job uploads everything to
PyPI.  Confirm:

```bash
gh release view v0.8.0 --web
# wheels appear here once the matrix completes:
open https://pypi.org/project/crcglot/0.8.0/
```

A smoke install on a clean machine is the final proof:

```bash
pip install crcglot==0.8.0
python -c "from crcglot._c import c_generic_crc; print(c_generic_crc(b'123456789', 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF))"
```

## If something goes wrong

- **Prep failed:** `git checkout main && git branch -D rel/v<version>`,
  fix, re-run prep.  Nothing was pushed; nothing to undo.
- **Publish failed before the tag push:** you are on `main` (merge done)
  or still on the rel branch.  `git log --oneline -5` to see where you
  are; the tag/push steps are idempotent-ish but inspect before re-running.
- **Publish pushed the tag but CI's publish step failed:** the tag and GH
  release exist; do **not** re-tag.  Fix the CI/PyPI issue (most commonly
  the trusted-publisher filename above), then re-run the failed `wheels.yml`
  run from the Actions tab -- the matrix rebuilds and re-uploads.  PyPI
  rejects a duplicate file, so a partial upload is the only thing to watch
  for; bump to a new patch version if files for `0.8.0` already landed.
