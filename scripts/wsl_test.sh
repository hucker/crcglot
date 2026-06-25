#!/usr/bin/env bash
# Run crcglot's fast suite on real Linux via WSL.
#
# Why: Windows can't reproduce Linux-only behaviour -- most importantly a
# case-SENSITIVE filesystem (the class of bug that shipped Multi.cs). This
# clones the repo into the WSL distro's NATIVE ext4 home (case-sensitive),
# NOT /mnt/c (case-insensitive NTFS), and runs `pytest -m "not slow"`
# there. The 'slow' tier (compiling generated code) is intentionally left
# out -- the fast suite is what catches the OS-specific Python bugs.
#
# Run it from WSL, pointing at the Windows checkout's copy of this script:
#   wsl -d Ubuntu bash /mnt/c/Users/chuck/src/repo/crcglot/scripts/wsl_test.sh [branch]
#
# [branch] defaults to whatever the Windows repo currently has checked out.
# The native clone syncs from your LOCAL Windows repo (origin), not GitHub,
# so a local commit is enough -- no push needed. Uncommitted working-tree
# changes are NOT included; commit first.
#
# One-time prereqs in the distro (see the project_wsl_linux_test_env note):
#   wsl -d Ubuntu -u root -- apt-get install -y build-essential python3-dev
#   curl -LsSf https://astral.sh/uv/install.sh | sh
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"   # uv lands here

# Locate the Windows repo from this script's own path (<repo>/scripts/<this>).
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/crcglot"
BRANCH="${1:-$(git -C "$SRC" branch --show-current)}"

echo "source : $SRC"
echo "branch : $BRANCH"
echo "clone  : $DEST  (native ext4, case-sensitive)"

if [ ! -d "$DEST/.git" ]; then
    echo "=== cloning into native fs ==="
    git clone "$SRC" "$DEST"
fi

cd "$DEST"
echo "=== sync to origin/$BRANCH ==="
git fetch origin --prune
git checkout -B "$BRANCH" "origin/$BRANCH"
git reset --hard "origin/$BRANCH"
git log --oneline -1

echo "=== uv sync (builds crcglot._c) ==="
uv sync

echo "=== fast suite (-m 'not slow') ==="
exec uv run pytest -m "not slow" -o addopts="-n auto -q -rfE -p no:cacheprovider"
