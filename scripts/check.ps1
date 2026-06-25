#!/usr/bin/env pwsh
# Local pre-commit gate for crcglot (Windows / PowerShell).
#
# Runs the lint + type checks and the test suite. By default it runs the
# FULL suite, including the 'slow' tier that compiles and runs the
# generated code through gcc / rustc / go / dotnet / tsx / iverilog /
# ghdl. CI never runs the slow tier (see .github/workflows/tests.yml),
# so this script is the only place it gets exercised -- run it before
# you commit.
#
# Usage:
#   .\scripts\check.ps1          # ruff + ty + full suite (slow tier included)
#   .\scripts\check.ps1 -Fast    # ruff + ty + fast suite only (-m "not slow")
#
# The full suite needs gcc, rustc, go, dotnet, tsx (node), iverilog, and
# ghdl on PATH; the fast suite needs none of them.

param([switch]$Fast)

# Always operate from the repo root (the parent of this script's folder),
# so the script works no matter where it is invoked from.
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Invoke-Step "ruff check" { uvx ruff check src tests }
Invoke-Step "ty check"   { uvx ty check src tests }

# Override the project's verbose `-v --cov` addopts with a terse line so a
# green run is a short summary, not 5000+ lines. Coverage review stays a
# separate, deliberate step (see the precommit checklist in CLAUDE.md).
$terse = 'addopts=-n auto -q -rfE'
if ($Fast) {
    Invoke-Step "pytest (fast: -m 'not slow')" { uv run pytest -m "not slow" -o $terse }
} else {
    Invoke-Step "pytest (full suite)" { uv run pytest -o $terse }
}

Write-Host "All checks passed." -ForegroundColor Green
