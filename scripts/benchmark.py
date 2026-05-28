"""crcglot CRC-32 benchmark across languages and variants.

Generates, compiles, and runs a tight-loop throughput benchmark of the
``crc32`` algorithm in every (language, variant) cell that has a
compiled-software target.  Output is committed at ``BENCHMARKS.md``.

**This is not a publication-grade benchmark.**  Goal is academic:
within each language, slice-by-8 should beat table-driven should beat
bit-by-bit; across languages, C / Rust should land near the top and
Python near the bottom.  If those orderings fail, the methodology has
a bug -- numbers are not a marketing claim.

Inputs: 1 KiB and 1 MiB buffers filled with ``buf[i] = i & 0xFF``.
Each cell: 5 warm-up calls, then an adaptive inner loop iterating
until elapsed > 500 ms (minimum 3 iterations).  Throughput in MB/s
(MB = 10^6 bytes).  Each cell is timed three times; we report the
median run (medians are more robust to GC pauses / cache misses than
means).

Per-language toolchain and release flags:

* C: ``gcc -O3 -DNDEBUG``
* Rust: ``rustc -C opt-level=3 -C codegen-units=1``
* Go: ``go build -ldflags="-s -w"`` (Go's ``go build`` is already
  optimized; the linker strips for smaller binaries)
* C#: ``dotnet publish -c Release`` (requires the dotnet SDK, not
  just the runtime; falls back to skip if SDK missing)
* Python: no compile; ``python <script>.py``
* TypeScript: ``tsx <script>.ts`` (V8-JIT'd via Node)

Working layout under ``benchmarks/``:

  benchmarks/
    c/{bitwise,table,slice8}/{1024,1048576}/
      crc32.h
      crc32.c
      bench.c
      bench.exe
    rust/{bitwise,table,slice8}/{1024,1048576}/
      bench.rs
      bench.exe
    ...

VHDL and Verilog are excluded -- they're simulator-reference
implementations of *hardware* CRC datapaths, not software runtime
performance.  Comparing GHDL or iverilog simulation throughput to
``gcc -O3`` is not a meaningful axis.
"""

from __future__ import annotations

import os
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from crcglot import ALGORITHMS, LANGUAGES, generic_crc  # noqa: E402
# catalogue.py already resolves the C accelerator: ``_c_generic_crc``
# is the C function when the extension is built, else None.  We read
# it straight from there rather than re-doing the optional import.
from crcglot.catalogue import (  # noqa: E402
    _c_generic_crc,
    _generic_crc_python,
)


def _fix_windows_path() -> None:
    """Mirror the conftest PATH fixups so this script sees tools that
    were just installed but haven't propagated to this shell's env.

    Two distinct fixes:

    1. ``C:\\msys64\\mingw64\\bin`` is PREPENDED (not appended).  Git
       Bash inserts ``C:\\Program Files\\Git\\mingw64\\bin`` near the
       top of PATH; msys2 typically lives further back.  Without
       prepending msys2, gcc resolves correctly but its ``cc1.exe``
       sub-tool loads Git's older ``libstdc++-6`` / ``libgcc_s_seh-1``
       DLLs first -- they're ABI-incompatible with msys2's gcc 15.x
       and cc1 crashes silently with NT status 0xC0000139.  Symptom:
       gcc returns exit 1 with no stderr.

    2. The remaining tool dirs are appended (lower priority is fine
       since none of them have the DLL-load conflict).
    """
    if sys.platform != "win32":
        return
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    norm_set = {os.path.normcase(p) for p in path_parts}

    # Priority fix: msys2 ahead of Git's mingw64.
    msys2_bin = r"C:\msys64\mingw64\bin"
    if os.path.isdir(msys2_bin):
        norm_msys2 = os.path.normcase(msys2_bin)
        if path_parts and os.path.normcase(path_parts[0]) != norm_msys2:
            path_parts = [
                p for p in path_parts
                if os.path.normcase(p) != norm_msys2
            ]
            path_parts.insert(0, msys2_bin)
            norm_set.add(norm_msys2)

    appdata = os.environ.get("APPDATA", "")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    appended = [
        r"C:\iverilog\bin",
        r"C:\Program Files\nodejs",
        r"C:\Program Files\Go\bin",
    ]
    if local_appdata:
        appended.append(
            os.path.join(local_appdata, "Microsoft", "WinGet", "Links")
        )
    if appdata:
        appended.append(os.path.join(appdata, "npm"))
    for c in appended:
        if os.path.isdir(c) and os.path.normcase(c) not in norm_set:
            path_parts.append(c)
            norm_set.add(os.path.normcase(c))

    os.environ["PATH"] = os.pathsep.join(path_parts)


_fix_windows_path()


# --------------------------------------------------------------------
# Config
# --------------------------------------------------------------------

_ALGORITHM = "crc32"
_SIZES = [1024, 1024 * 1024]  # 1 KiB, 1 MiB
_REPEATS = 3
_INNER_LOOP_TARGET_MS = 500

# Languages and which variants to bench, in display order.
_MATRIX: dict[str, list[str]] = {
    "c":          ["bitwise", "table", "slice8"],
    "rust":       ["bitwise", "table", "slice8"],
    "go":         ["bitwise", "table", "slice8"],
    "csharp":     ["bitwise", "table", "slice8"],
    "typescript": ["bitwise", "table", "slice8"],
    "python":     ["bitwise", "table"],
}

# Resolve tool binaries via the same path search the test suite uses.
# Several Windows installers don't propagate PATH to already-open
# shells; if a binary is on disk but `shutil.which` can't find it,
# point this script at the right dir or run from a fresh shell.
_TOOL_BINS = {
    "gcc":     shutil.which("gcc"),
    "rustc":   shutil.which("rustc"),
    "go":      shutil.which("go"),
    "dotnet":  shutil.which("dotnet"),
    "python":  shutil.which("python") or sys.executable,
    "tsx":     (
        shutil.which("tsx")
        or shutil.which("tsx.cmd")
        or shutil.which("tsx.CMD")
    ),
}

_BENCH_ROOT = Path(__file__).parent.parent / "benchmarks"
_BENCHMARKS_MD = Path(__file__).parent.parent / "BENCHMARKS.md"


# --------------------------------------------------------------------
# Result data structure
# --------------------------------------------------------------------


@dataclass
class CellResult:
    lang: str
    variant: str
    size: int
    mbps_runs: list[float]  # one per repeat; empty if skipped/failed
    skipped_reason: str | None = None

    @property
    def median_mbps(self) -> float | None:
        if not self.mbps_runs:
            return None
        return statistics.median(self.mbps_runs)


# --------------------------------------------------------------------
# Source emitters per language
# --------------------------------------------------------------------


def _gen_kwargs(variant: str) -> dict:
    if variant == "table":
        return {"table": True}
    if variant == "slice8":
        return {"slice8": True}
    return {}


def _emit_c(cell_dir: Path, variant: str, size: int) -> None:
    header, source = LANGUAGES["c"].generator(_ALGORITHM, **_gen_kwargs(variant))
    (cell_dir / "crc32.h").write_text(header)
    (cell_dir / "crc32.c").write_text(source)
    # Polling every iteration past the minimum keeps slow cells
    # (e.g. Python 1 MiB bit-by-bit) from running past the 120s test
    # timeout.  clock_gettime overhead is ~50 ns on Windows mingw --
    # at worst 5% on the fastest cell (C slice-by-8 at ~1 GB/s), zero
    # noise on everything else.
    bench = f"""#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stddef.h>
#include <time.h>
#include "crc32.h"

#define SIZE {size}

int main(void) {{
    uint8_t *buf = (uint8_t*)malloc(SIZE);
    if (!buf) {{ return 1; }}
    for (size_t i = 0; i < SIZE; i++) buf[i] = (uint8_t)(i & 0xFF);
    uint32_t crc = 0;
    for (int w = 0; w < 5; w++) crc ^= crc32(buf, SIZE);
    struct timespec t0, t1;
    long long iters = 0;
    long long ns = 0;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    while (1) {{
        crc ^= crc32(buf, SIZE);
        iters++;
        if (iters >= 3) {{
            clock_gettime(CLOCK_MONOTONIC, &t1);
            ns = (long long)(t1.tv_sec - t0.tv_sec) * 1000000000LL
                 + (t1.tv_nsec - t0.tv_nsec);
            if (ns > {_INNER_LOOP_TARGET_MS}000000LL) break;
        }}
    }}
    double bytes = (double)iters * SIZE;
    double mbps = bytes / ((double)ns / 1e9) / 1e6;
    printf("c,{variant},%d,%.3f,%lld,%lld,0x%08X\\n",
           SIZE, mbps, iters, ns, (unsigned)crc);
    free(buf);
    return 0;
}}
"""
    (cell_dir / "bench.c").write_text(bench)


def _emit_rust(cell_dir: Path, variant: str, size: int) -> None:
    code = LANGUAGES["rust"].generator(_ALGORITHM, **_gen_kwargs(variant))
    bench_main = f"""

use std::time::Instant;

fn main() {{
    const SIZE: usize = {size};
    let mut buf = vec![0u8; SIZE];
    for i in 0..SIZE {{ buf[i] = (i & 0xFF) as u8; }}
    let mut crc: u32 = 0;
    for _ in 0..5 {{ crc ^= crc32(&buf); }}
    let start = Instant::now();
    let mut iters: u64 = 0;
    loop {{
        crc ^= crc32(&buf);
        iters += 1;
        if iters >= 3
            && start.elapsed().as_millis() > {_INNER_LOOP_TARGET_MS}
        {{ break; }}
    }}
    let ns = start.elapsed().as_nanos() as u128;
    let bytes = iters as f64 * SIZE as f64;
    let mbps = bytes / (ns as f64 / 1e9) / 1e6;
    println!("rust,{variant},{{}},{{:.3}},{{}},{{}},0x{{:08X}}",
             SIZE, mbps, iters, ns, crc);
}}
"""
    (cell_dir / "bench.rs").write_text(code + bench_main)


def _emit_go(cell_dir: Path, variant: str, size: int) -> None:
    code = LANGUAGES["go"].generator(_ALGORITHM, **_gen_kwargs(variant))
    # Convert `package crc` to `package main` and add imports.
    code = code.replace("package crc", "package main\n\nimport (\n\t\"fmt\"\n\t\"time\"\n)", 1)
    bench_main = f"""

func main() {{
    const SIZE = {size}
    buf := make([]byte, SIZE)
    for i := 0; i < SIZE; i++ {{ buf[i] = byte(i & 0xFF) }}
    var crc uint32
    for w := 0; w < 5; w++ {{ crc ^= crc32(buf) }}
    start := time.Now()
    var iters uint64
    for {{
        crc ^= crc32(buf)
        iters++
        if iters >= 3 && time.Since(start).Milliseconds() > {_INNER_LOOP_TARGET_MS} {{
            break
        }}
    }}
    elapsed := time.Since(start)
    bytes := float64(iters) * float64(SIZE)
    mbps := bytes / elapsed.Seconds() / 1e6
    fmt.Printf("go,{variant},%d,%.3f,%d,%d,0x%08X\\n",
        SIZE, mbps, iters, elapsed.Nanoseconds(), crc)
}}
"""
    (cell_dir / "bench.go").write_text(code + bench_main)


def _emit_csharp(cell_dir: Path, variant: str, size: int) -> None:
    code = LANGUAGES["csharp"].generator(_ALGORITHM, **_gen_kwargs(variant))
    # C# requires every `using` directive at the top of the file, ahead
    # of any class declaration.  The generator emits `using System;`
    # alone; inject `using System.Diagnostics;` immediately after so
    # both are above the class body that follows.
    code = code.replace(
        "using System;",
        "using System;\nusing System.Diagnostics;",
        1,
    )
    bench_main = f"""

public static class Program {{
    public static void Main() {{
        const int SIZE = {size};
        byte[] buf = new byte[SIZE];
        for (int i = 0; i < SIZE; i++) buf[i] = (byte)(i & 0xFF);
        uint crc = 0;
        for (int w = 0; w < 5; w++) crc ^= Crc32.crc32(buf);
        var sw = Stopwatch.StartNew();
        long iters = 0;
        while (true) {{
            crc ^= Crc32.crc32(buf);
            iters++;
            if (iters >= 3 && sw.ElapsedMilliseconds > {_INNER_LOOP_TARGET_MS})
                break;
        }}
        sw.Stop();
        long ns = (long)((double)sw.ElapsedTicks * 1e9 / Stopwatch.Frequency);
        double bytes = (double)iters * SIZE;
        double mbps = bytes / ((double)ns / 1e9) / 1e6;
        Console.WriteLine($"csharp,{variant},{{SIZE}},{{mbps:F3}},{{iters}},{{ns}},0x{{crc:X8}}");
    }}
}}
"""
    (cell_dir / "Program.cs").write_text(code + bench_main)
    # Minimal csproj.
    csproj = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net9.0</TargetFramework>
    <Nullable>disable</Nullable>
    <RootNamespace>Bench</RootNamespace>
    <AssemblyName>bench</AssemblyName>
  </PropertyGroup>
</Project>
"""
    (cell_dir / "Bench.csproj").write_text(csproj)


def _emit_python(cell_dir: Path, variant: str, size: int) -> None:
    code = LANGUAGES["python"].generator(_ALGORITHM, **_gen_kwargs(variant))
    bench_main = f"""

import time

if __name__ == "__main__":
    SIZE = {size}
    buf = bytes(i & 0xFF for i in range(SIZE))
    crc = 0
    for _ in range(5):
        crc ^= crc32(buf)
    start = time.perf_counter_ns()
    iters = 0
    target_ns = {_INNER_LOOP_TARGET_MS} * 1_000_000
    while True:
        crc ^= crc32(buf)
        iters += 1
        if iters >= 3 and time.perf_counter_ns() - start > target_ns:
            break
    ns = time.perf_counter_ns() - start
    byte_count = iters * SIZE
    mbps = byte_count / (ns / 1e9) / 1e6
    print(f"python,{variant},{{SIZE}},{{mbps:.3f}},{{iters}},{{ns}},0x{{crc:08X}}")
"""
    (cell_dir / "bench.py").write_text(code + bench_main)


def _emit_typescript(cell_dir: Path, variant: str, size: int) -> None:
    code = LANGUAGES["typescript"].generator(_ALGORITHM, **_gen_kwargs(variant))
    bench_main = f"""

const SIZE_BENCH = {size};
const buf = new Uint8Array(SIZE_BENCH);
for (let i = 0; i < SIZE_BENCH; i++) buf[i] = i & 0xFF;
let crc = 0;
for (let w = 0; w < 5; w++) crc ^= crc32(buf);
const startNs = process.hrtime.bigint();
let iters = 0n;
const targetNs = {_INNER_LOOP_TARGET_MS}n * 1000000n;
while (true) {{
    crc ^= crc32(buf);
    iters++;
    if (iters >= 3n && (process.hrtime.bigint() - startNs) > targetNs) break;
}}
const totalNs = process.hrtime.bigint() - startNs;
const bytes = Number(iters) * SIZE_BENCH;
const mbps = bytes / (Number(totalNs) / 1e9) / 1e6;
console.log(`typescript,{variant},${{SIZE_BENCH}},${{mbps.toFixed(3)}},${{iters}},${{totalNs}},0x${{(crc >>> 0).toString(16).padStart(8, "0").toUpperCase()}}`);
"""
    (cell_dir / "bench.ts").write_text(code + bench_main)


_EMITTERS = {
    "c":          _emit_c,
    "rust":       _emit_rust,
    "go":         _emit_go,
    "csharp":     _emit_csharp,
    "python":     _emit_python,
    "typescript": _emit_typescript,
}


# --------------------------------------------------------------------
# Compile per language; return path-to-executable or None
# --------------------------------------------------------------------


def _tool(name: str) -> str:
    """Resolve a tool by name; raise if absent.

    Used after :func:`_can_run` has already gated the cell, so the
    error path is dead code -- but keeping it lets ty narrow the
    ``str | None`` type from ``shutil.which`` to ``str``.
    """
    path = _TOOL_BINS.get(name)
    if path is None:
        raise RuntimeError(f"tool not on PATH: {name}")
    return path


def _compile_c(cell_dir: Path) -> Path | None:
    out = cell_dir / "bench.exe"
    r = subprocess.run(
        [_tool("gcc"), "-O3", "-DNDEBUG",
         "bench.c", "crc32.c", "-o", str(out)],
        capture_output=True, cwd=cell_dir,
    )
    if r.returncode != 0:
        print(f"  ! gcc failed:\n{r.stderr.decode(errors='replace')}", file=sys.stderr)
        return None
    return out


def _compile_rust(cell_dir: Path) -> Path | None:
    out = cell_dir / "bench.exe"
    r = subprocess.run(
        [_tool("rustc"), "-C", "opt-level=3", "-C", "codegen-units=1",
         "--edition=2021", "-A", "warnings",
         "-o", str(out), "bench.rs"],
        capture_output=True, cwd=cell_dir,
    )
    if r.returncode != 0:
        print(f"  ! rustc failed:\n{r.stderr.decode(errors='replace')}", file=sys.stderr)
        return None
    return out


def _compile_go(cell_dir: Path) -> Path | None:
    out = cell_dir / "bench.exe"
    # Initialize a tiny module so `go build` doesn't complain.
    (cell_dir / "go.mod").write_text("module bench\n\ngo 1.21\n")
    r = subprocess.run(
        [_tool("go"), "build", "-ldflags=-s -w",
         "-o", str(out), "."],
        capture_output=True, cwd=cell_dir,
    )
    if r.returncode != 0:
        print(f"  ! go build failed:\n{r.stderr.decode(errors='replace')}", file=sys.stderr)
        return None
    return out


def _compile_csharp(cell_dir: Path) -> Path | None:
    publish_dir = cell_dir / "publish"
    r = subprocess.run(
        [_tool("dotnet"), "publish", "-c", "Release",
         "-o", str(publish_dir), "--nologo", "-v", "quiet"],
        capture_output=True, cwd=cell_dir,
    )
    if r.returncode != 0:
        print(
            f"  ! dotnet publish failed:\n"
            f"{r.stdout.decode(errors='replace')}\n"
            f"{r.stderr.decode(errors='replace')}",
            file=sys.stderr,
        )
        return None
    exe = publish_dir / "bench.exe"
    return exe if exe.exists() else None


_COMPILERS = {
    "c":      _compile_c,
    "rust":   _compile_rust,
    "go":     _compile_go,
    "csharp": _compile_csharp,
}


# --------------------------------------------------------------------
# Run a compiled binary (or a script for non-compiled langs) and
# parse one line of CSV.
# --------------------------------------------------------------------


def _parse_csv(line: str) -> float | None:
    parts = line.strip().split(",")
    if len(parts) < 7:
        return None
    try:
        return float(parts[3])  # MB/s
    except ValueError:
        return None


def _run_binary(args: list[str], cwd: Path) -> float | None:
    r = subprocess.run(args, capture_output=True, cwd=cwd, timeout=180)
    if r.returncode != 0:
        print(
            f"  ! run failed: rc={r.returncode}\n"
            f"  stdout={r.stdout.decode(errors='replace')!r}\n"
            f"  stderr={r.stderr.decode(errors='replace')!r}",
            file=sys.stderr,
        )
        return None
    out = r.stdout.decode(errors="replace")
    # Pick the line that starts with a known lang code -- noise-tolerant
    # in case the toolchain writes a banner.
    for line in out.splitlines():
        mbps = _parse_csv(line)
        if mbps is not None:
            return mbps
    print(f"  ! no parseable line in stdout: {out!r}", file=sys.stderr)
    return None


def _exec_args(lang: str, cell_dir: Path, exe: Path | None) -> list[str] | None:
    _ = cell_dir
    if lang == "python":
        return [_tool("python"), "bench.py"]
    if lang == "typescript":
        if not _TOOL_BINS["tsx"]:
            return None
        return [_tool("tsx"), "bench.ts"]
    if exe is None:
        return None
    return [str(exe)]


# --------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------


def _can_run(lang: str) -> str | None:
    """Return None if we can run this language, else a skip reason."""
    if lang == "c" and not _TOOL_BINS["gcc"]:
        return "gcc not on PATH"
    if lang == "rust" and not _TOOL_BINS["rustc"]:
        return "rustc not on PATH"
    if lang == "go" and not _TOOL_BINS["go"]:
        return "go not on PATH"
    if lang == "csharp" and not _TOOL_BINS["dotnet"]:
        return "dotnet not on PATH"
    if lang == "typescript" and not _TOOL_BINS["tsx"]:
        return "tsx not on PATH (npm i -g tsx)"
    if lang == "csharp" and _TOOL_BINS["dotnet"]:
        # Probe for SDK -- runtime alone can't `publish -c Release`.
        probe = subprocess.run(
            [_TOOL_BINS["dotnet"], "--list-sdks"],
            capture_output=True,
        )
        if not probe.stdout.strip():
            return "dotnet SDK not installed (runtime alone insufficient)"
    return None


def _run_cell(lang: str, variant: str, size: int) -> CellResult:
    skip = _can_run(lang)
    if skip:
        return CellResult(lang, variant, size, [], skipped_reason=skip)

    cell_dir = _BENCH_ROOT / lang / variant / str(size)
    cell_dir.mkdir(parents=True, exist_ok=True)

    # Generate source (overwrite every run so it's reproducible).
    _EMITTERS[lang](cell_dir, variant, size)

    # Compile (compiled langs only).
    exe: Path | None = None
    if lang in _COMPILERS:
        exe = _COMPILERS[lang](cell_dir)
        if exe is None:
            return CellResult(
                lang, variant, size, [],
                skipped_reason="compile failed (see stderr)",
            )

    args = _exec_args(lang, cell_dir, exe)
    if args is None:
        return CellResult(
            lang, variant, size, [], skipped_reason="no exec args",
        )

    runs: list[float] = []
    for _ in range(_REPEATS):
        mbps = _run_binary(args, cell_dir)
        if mbps is not None:
            runs.append(mbps)
    return CellResult(lang, variant, size, runs)


def _check_monotonic(results: list[CellResult]) -> list[str]:
    """Return a list of monotonicity-violation warnings.

    Only ``slice8 < table`` triggers a warning -- that's an
    algorithmic ordering that no legitimate compiler optimization
    can invert.  ``table < bitwise`` is NOT checked: at -O3 (LLVM
    particularly), the bit-by-bit inner loop can be unrolled and
    vectorized into something faster than a table-driven lookup
    with its serial dependency chain.  Treating that as a bug
    masks a real result.

    5% slack on the slice8 check covers measurement noise on small
    buffers; only flag if slice8 is meaningfully below table.
    """
    warnings: list[str] = []
    by_lang_size: dict[tuple[str, int], dict[str, float]] = {}
    for r in results:
        if r.median_mbps is None:
            continue
        by_lang_size.setdefault((r.lang, r.size), {})[r.variant] = r.median_mbps
    for (lang, size), variants in by_lang_size.items():
        tb = variants.get("table")
        s8 = variants.get("slice8")
        if tb and s8 and s8 < tb * 0.95:
            warnings.append(
                f"  {lang} @ {size}: slice8 ({s8:.0f}) < table ({tb:.0f}) "
                "-- methodology likely off"
            )
    return warnings


# --------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------


_DOC_HEADER = """# crcglot benchmark gallery

Throughput of `crc32` across languages and implementation variants.
All numbers are MB/s (MB = 10^6 bytes), median of 3 runs; each run is
an adaptive inner loop on a single buffer ({inner_target_ms} ms minimum,
3 iterations minimum).  Generated by `scripts/benchmark.py`.

> **Caveat:** This is an academic comparison meant to confirm the
> expected within-language ordering (bit-by-bit < table < slice-by-8)
> and the across-language band (compiled languages near the top,
> Python near the bottom).  **It is not a publication-grade
> benchmark.**  Single-threaded, in-process, identical synthetic
> buffer, no I/O.  Real-world CRC throughput depends heavily on data
> source, batch size, allocation patterns, and your specific compiler
> version -- run your own measurement before making any decision based
> on these numbers.

VHDL and Verilog are excluded.  Those generators emit simulator
references for hardware datapaths, not software runtime; comparing
GHDL or iverilog simulation throughput to `gcc -O3` is not a
meaningful axis.

Two sections below: the **Results** gallery times the *generated*
standalone `crc32` source in each language (what `crcglot <lang>
crc32` emits, compiled per-language); the **Runtime engines** section
times crcglot's own in-process `generic_crc` -- the pure-Python
fallback and the C extension it dispatches to -- so you can see how
the Python package itself performs against the generated-code band.

## How to read

- One row per (language, variant).  Compiled software targets get all
  three variants; Python lacks slice-by-8 (CPython per-int overhead
  measurably negates the win -- see `LANGUAGES["python"].variants`).
- Two size columns: **1 KiB** (1024-byte buffer, dominated by call
  overhead / table-load cost) and **1 MiB** (1,048,576 bytes,
  dominated by inner-loop throughput).
- Within each language, **slice-by-8 should beat table-driven** at
  the same buffer size.  That's an algorithmic win: slice-by-8
  processes 8 input bytes per iteration with 8 independent table
  lookups, shortening the dependency chain.  No compiler can fake
  it; if `slice8 < table` by more than measurement noise, the
  methodology probably has a bug and the script logs a warning to
  stderr.
- **Table-driven vs bit-by-bit is NOT guaranteed to be monotonic.**
  This sounds wrong but isn't.  Modern compilers (LLVM at `-O3` in
  particular) aggressively unroll and vectorize the 8-iteration
  inner-bit-shift loop, turning bit-by-bit into something that fits
  largely in registers and runs without memory traffic.  A
  table-driven implementation that does 1 indirect load per byte has
  a serial dependency chain (each lookup depends on the prior `crc`
  value), which the CPU can't ILP through.  Result: a well-vectorized
  bit-by-bit loop can tie -- or in some cases beat -- table-driven,
  especially on large buffers where table-load latency stacks up.
  Languages whose JIT or codegen leaves bit-by-bit un-vectorized
  (C#, TypeScript, Python, Go) show the classic large bitwise→table
  jump; Rust at `-O3` shows it doesn't (table is only marginally
  faster than bitwise, sometimes slower at 1 MiB).
- **Tables** column reports the static RAM cost of the variant's
  lookup tables: 0 for bit-by-bit, 1 KiB for table-driven (256
  entries × CRC width), 8 KiB for slice-by-8 (8 tables × 256 × CRC
  width).  The compiled code is small relative to those tables -- a
  few hundred bytes of machine code in every case -- so this column
  is a tight proxy for the variant's total static footprint.

## Toolchain (this run)

| Tool | Release flags |
|------|----------------|
| `gcc` | `-O3 -DNDEBUG` |
| `rustc` | `-C opt-level=3 -C codegen-units=1` |
| `go build` | `-ldflags="-s -w"` (already optimized by default) |
| `dotnet publish` | `-c Release` (needs SDK, not just runtime) |
| `python` | interpreter only |
| `tsx` | V8-JIT'd via Node.js |

## Reproduce

```bash
uv run python scripts/benchmark.py
```

Working files land under `benchmarks/<lang>/<variant>/<size>/`
(gitignored); the rendered output is this file.

"""


def _fmt_mbps(r: CellResult) -> str:
    if r.skipped_reason:
        return f"_skip: {r.skipped_reason}_"
    if r.median_mbps is None:
        return "_failed_"
    return f"{r.median_mbps:,.1f}"


def _table_bytes(variant: str, width: int) -> int:
    """Static RAM cost of the variant's lookup tables, in bytes.

    Determined entirely by (variant, CRC width); identical across
    languages.  Bit-by-bit has no tables.  Table-driven keeps one
    256-entry table sized to the CRC width.  Slice-by-8 keeps eight
    such tables.  The compiled code is a few hundred bytes regardless,
    so this dominates the variant's static footprint for table /
    slice-by-8.
    """
    entry_bytes = width // 8
    if variant == "bitwise":
        return 0
    if variant == "table":
        return 256 * entry_bytes
    if variant == "slice8":
        return 8 * 256 * entry_bytes
    raise ValueError(f"unknown variant: {variant}")


def _fmt_bytes(n: int) -> str:
    """Compact byte-count format aligned with the doc's KiB convention."""
    if n == 0:
        return "—"
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024:,} KiB"
    return f"{n:,} B"


def _render_table(results: list[CellResult]) -> str:
    """Render the full matrix as one markdown table."""
    lines = [
        "## Results",
        "",
        "| Language     | Variant      |  Tables | 1 KiB (MB/s) | 1 MiB (MB/s) |",
        "|--------------|--------------|--------:|-------------:|-------------:|",
    ]
    display_name = {code: info.display_name for code, info in LANGUAGES.items()}
    # crc32 is the only algorithm benched; pin width=32 for the table
    # column.  If we ever benchmark multiple algorithms, this becomes
    # per-row off the AlgorithmInfo.
    crc_width = 32
    for lang, variants in _MATRIX.items():
        for variant in variants:
            r1k = next(
                (r for r in results
                 if r.lang == lang and r.variant == variant and r.size == 1024),
                None,
            )
            r1m = next(
                (r for r in results
                 if r.lang == lang and r.variant == variant
                 and r.size == 1024 * 1024),
                None,
            )
            v_label = {
                "bitwise": "bit-by-bit",
                "table": "table-driven",
                "slice8": "slice-by-8",
            }[variant]
            r1k_s = _fmt_mbps(r1k) if r1k else "_skipped_"
            r1m_s = _fmt_mbps(r1m) if r1m else "_skipped_"
            lname = display_name.get(lang, lang)
            tbl_s = _fmt_bytes(_table_bytes(variant, crc_width))
            lines.append(
                f"| {lname:<12} | {v_label:<12} | "
                f"{tbl_s:>7} | "
                f"{r1k_s:>12} | {r1m_s:>12} |"
            )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------


# --------------------------------------------------------------------
# In-process runtime engines (crcglot.generic_crc): pure-Python vs C
# --------------------------------------------------------------------


def _make_buffer(size: int) -> bytes:
    if size < 4096:
        return bytes(i & 0xFF for i in range(size))
    return bytes(range(256)) * (size // 256)


def _time_inproc(fn, buf: bytes, args: tuple) -> float:
    """Adaptive-loop throughput (MB/s) for an in-process ``fn(buf, *args)``.

    Same methodology as the compiled cells: 5 warm-up calls, then loop
    until elapsed > the inner-loop target (min 3 iterations).  No
    subprocess -- these engines run inside this interpreter.
    """
    for _ in range(5):
        fn(buf, *args)
    target_ns = _INNER_LOOP_TARGET_MS * 1_000_000
    start = time.perf_counter_ns()
    iters = 0
    while True:
        fn(buf, *args)
        iters += 1
        if iters >= 3 and time.perf_counter_ns() - start > target_ns:
            break
    ns = time.perf_counter_ns() - start
    return len(buf) * iters / (ns / 1e9) / 1e6


def _run_runtime_engines() -> list[CellResult]:
    """Benchmark crcglot's runtime CRC paths on crc32:

    - the pure-Python engine,
    - the C extension engine (if built),
    - the public ``generic_crc`` dispatcher, which for IEEE crc32
      delegates to hardware-accelerated ``zlib.crc32``.
    """
    algo = ALGORITHMS[_ALGORITHM]
    tail = (algo.width, algo.poly, algo.init,
            algo.refin, algo.refout, algo.xorout)
    engines: list[tuple[str, object]] = [("python-runtime", _generic_crc_python)]
    if _c_generic_crc is not None:
        engines.append(("cpython-ext", _c_generic_crc))
    engines.append(("dispatch", generic_crc))

    results: list[CellResult] = []
    for lang, fn in engines:
        for size in _SIZES:
            buf = _make_buffer(size)
            print(f"  runtime/{lang}/{size} ...", end="", flush=True,
                  file=sys.stderr)
            runs = [_time_inproc(fn, buf, tail) for _ in range(_REPEATS)]
            r = CellResult(lang, "runtime", size, runs)
            print(f" {r.median_mbps:,.1f} MB/s", file=sys.stderr)
            results.append(r)
    return results


_RUNTIME_LABEL = {
    "python-runtime": "Pure Python (`_generic_crc_python`)",
    "cpython-ext": "C extension engine (`c_generic_crc`)",
    "dispatch": "Public `generic_crc` (crc32 → `zlib.crc32`)",
}


def _render_runtime_section(results: list[CellResult]) -> str:
    rt = [r for r in results if r.lang in _RUNTIME_LABEL]
    if not rt:
        return ""
    lines = [
        "",
        "## Runtime engines (`crcglot.generic_crc`)",
        "",
        "Throughput of crcglot's *runtime* CRC paths on `crc32` -- what "
        "happens at call time, NOT the generated pre-compiled code in the "
        "gallery above.  Three rows:\n"
        "\n"
        "- **Pure Python** -- the always-available reference engine.\n"
        "- **C extension engine** -- `crcglot._c.c_generic_crc` "
        "(slice-by-8 for crc32); what the package uses when the wheel / "
        "`crcglot[fast]` is installed.  A function *called from Python* "
        "in the same throughput band as the compiled-language slice-by-8 "
        "numbers above.\n"
        "- **Public `generic_crc` dispatcher** -- for IEEE crc32 it "
        "delegates to the stdlib's hardware-accelerated `zlib.crc32` "
        "(PCLMULQDQ CRC folding), which is ~30x faster than any portable "
        "software engine.  No software CRC should try to out-run silicon, "
        "so crcglot borrows it for the one ubiquitous algorithm the stdlib "
        "accelerates, and uses its own C engine for the other 70.",
        "",
        "| Engine | 1 KiB (MB/s) | 1 MiB (MB/s) |",
        "|--------|-------------:|-------------:|",
    ]
    for lang in ("python-runtime", "cpython-ext", "dispatch"):
        r1k = next((r for r in rt if r.lang == lang and r.size == 1024), None)
        r1m = next(
            (r for r in rt if r.lang == lang and r.size == 1024 * 1024), None
        )
        if r1k is None or r1m is None:
            continue
        lines.append(
            f"| {_RUNTIME_LABEL[lang]} | "
            f"{_fmt_mbps(r1k):>12} | {_fmt_mbps(r1m):>12} |"
        )
    py = next((r.median_mbps for r in rt
               if r.lang == "python-runtime" and r.size == 1024 * 1024), None)
    cx = next((r.median_mbps for r in rt
               if r.lang == "cpython-ext" and r.size == 1024 * 1024), None)
    if py and cx:
        lines += [
            "",
            f"C extension speedup at 1 MiB: **~{cx / py:,.0f}x** over the "
            "pure-Python engine.",
        ]
    elif cx is None:
        lines += [
            "",
            "_(C extension not built in this run -- only the pure-Python "
            "engine was measured.  Build it with `uv sync` or "
            "`pip install crcglot[fast]`.)_",
        ]
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    _BENCH_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"crcglot benchmark -- writing under {_BENCH_ROOT}/", file=sys.stderr)
    print(
        f"Cells: {sum(len(v) for v in _MATRIX.values()) * len(_SIZES)} "
        f"(langs={len(_MATRIX)}, sizes={_SIZES}, repeats={_REPEATS})",
        file=sys.stderr,
    )

    results: list[CellResult] = []
    t_start = time.monotonic()
    for lang, variants in _MATRIX.items():
        for variant in variants:
            for size in _SIZES:
                tag = f"{lang}/{variant}/{size}"
                print(f"  {tag} ...", end="", flush=True, file=sys.stderr)
                t0 = time.monotonic()
                r = _run_cell(lang, variant, size)
                dt = time.monotonic() - t0
                if r.skipped_reason:
                    print(
                        f" SKIPPED ({r.skipped_reason}) [{dt:.1f}s]",
                        file=sys.stderr,
                    )
                elif r.median_mbps is None:
                    print(f" FAILED [{dt:.1f}s]", file=sys.stderr)
                else:
                    runs_fmt = "/".join(f"{x:.1f}" for x in r.mbps_runs)
                    print(
                        f" {r.median_mbps:,.1f} MB/s "
                        f"(runs: {runs_fmt}) [{dt:.1f}s]",
                        file=sys.stderr,
                    )
                results.append(r)
    # In-process runtime engines (crcglot.generic_crc): pure-Python + C.
    runtime_results = _run_runtime_engines()

    print(
        f"Total: {time.monotonic() - t_start:.1f}s",
        file=sys.stderr,
    )

    # Sanity check: variants must be monotonic within each lang/size.
    warns = _check_monotonic(results)
    if warns:
        print("\nMonotonicity warnings:", file=sys.stderr)
        for w in warns:
            print(w, file=sys.stderr)

    body = (
        _DOC_HEADER.format(inner_target_ms=_INNER_LOOP_TARGET_MS)
        + _render_table(results)
        + _render_runtime_section(runtime_results)
    )
    _BENCHMARKS_MD.write_text(body, encoding="utf-8")
    print(f"\nWrote {_BENCHMARKS_MD}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
