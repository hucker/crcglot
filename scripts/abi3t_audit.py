"""abi3audit with exactly one tolerated finding: the PEP 793 entry point.

cibuildwheel runs ``abi3audit --strict`` on every abi3-tagged wheel.  abi3audit
0.0.26 predates PEP 793, so on ELF wheels (where it inspects exported symbols
too) it reports the module's ``PyModExport_<name>`` export as a symbol outside
the stable ABI and fails the build; the Windows and macOS wheels of the same
source pass because only imports are inspected there.  This wrapper runs the
same audit with ``--report`` and passes only when every reported violation is
that one export.  Any other symbol, and any ABI version mismatch, fails the
build exactly as the plain command would.  Retire it once abi3audit learns
PEP 793 (pyproject.toml's ``[tool.cibuildwheel.overrides]`` is the only caller).

Stdlib only: it runs inside cibuildwheel's audit venv, which has abi3audit on
PATH and nothing else.
"""

from __future__ import annotations

import json
import subprocess
import sys

TOLERATED_PREFIX = "PyModExport_"


def findings(report: object) -> list[tuple[str, list[str], dict[str, object]]]:
    """Walk the report for every scanned extension's (name, symbols, mismatches)."""
    out: list[tuple[str, list[str], dict[str, object]]] = []

    def walk(node: object, name: str) -> None:
        if isinstance(node, dict):
            name = str(node.get("name", name))
            if "non_abi3_symbols" in node:
                out.append(
                    (
                        name,
                        list(node.get("non_abi3_symbols") or []),
                        dict(node.get("future_abi3_objects") or {}),
                    )
                )
            for value in node.values():
                walk(value, name)
        elif isinstance(node, list):
            for item in node:
                walk(item, name)

    walk(report, "?")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: abi3t_audit.py <wheel>", file=sys.stderr)
        return 2
    wheel = argv[1]
    proc = subprocess.run(
        ["abi3audit", "--strict", "--report", wheel],
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stderr.write(proc.stderr)
    if proc.returncode == 0:
        return 0
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(proc.stdout)
        print("abi3t_audit: abi3audit failed and printed no report", file=sys.stderr)
        return proc.returncode or 1
    remaining = []
    for name, symbols, mismatches in findings(report):
        tolerated = [s for s in symbols if s.startswith(TOLERATED_PREFIX)]
        other = [s for s in symbols if not s.startswith(TOLERATED_PREFIX)]
        for s in tolerated:
            print(f"abi3t_audit: tolerating {s} in {name} (PEP 793 entry point)")
        if other or mismatches:
            remaining.append((name, other, mismatches))
    if remaining:
        for name, other, mismatches in remaining:
            print(f"abi3t_audit: {name}: non-abi3 symbols {other}, version mismatches {mismatches}", file=sys.stderr)
        return 1
    print("abi3t_audit: audit passed apart from the tolerated PEP 793 export")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
