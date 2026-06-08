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
    "java":       ["bitwise", "table", "slice8"],
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
    "javac":   shutil.which("javac"),
    "java":    shutil.which("java"),
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
    return {"variant": variant}


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


def _emit_java(cell_dir: Path, variant: str, size: int) -> None:
    # Java puts every algorithm in one container class (default CrcGlot);
    # splice a timing main() into it before the class's closing brace.
    code = LANGUAGES["java"].generator(_ALGORITHM, **_gen_kwargs(variant))
    bench_main = f"""
    public static void main(String[] args) {{
        final int SIZE = {size};
        byte[] buf = new byte[SIZE];
        for (int i = 0; i < SIZE; i++) buf[i] = (byte) (i & 0xFF);
        int crc = 0;
        for (int w = 0; w < 5; w++) crc ^= crc32(buf);
        long t0 = System.nanoTime();
        long iters = 0;
        while (true) {{
            crc ^= crc32(buf);
            iters++;
            if (iters >= 3 && (System.nanoTime() - t0) / 1000000L > {_INNER_LOOP_TARGET_MS}) break;
        }}
        long ns = System.nanoTime() - t0;
        double mbps = ((double) iters * SIZE) / ((double) ns / 1e9) / 1e6;
        System.out.printf("java,{variant},%d,%.3f,%d,%d,0x%08X%n", SIZE, mbps, iters, ns, crc);
    }}
"""
    src = code.rstrip()
    assert src.endswith("}"), "expected generated Java to end with the class brace"
    src = src[:-1].rstrip() + "\n" + bench_main + "}\n"
    (cell_dir / "CrcGlot.java").write_text(src)


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
    "java":       _emit_java,
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


def _compile_java(cell_dir: Path) -> Path | None:
    r = subprocess.run(
        [_tool("javac"), "-d", str(cell_dir), "CrcGlot.java"],
        capture_output=True, cwd=cell_dir,
    )
    if r.returncode != 0:
        print(f"  ! javac failed:\n{r.stderr.decode(errors='replace')}", file=sys.stderr)
        return None
    cls = cell_dir / "CrcGlot.class"
    # No single binary -- the run is `java -cp <dir> CrcGlot` (see
    # _exec_args).  Return the .class as a non-None success marker.
    return cls if cls.exists() else None


_COMPILERS = {
    "c":      _compile_c,
    "rust":   _compile_rust,
    "go":     _compile_go,
    "csharp": _compile_csharp,
    "java":   _compile_java,
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
    if lang == "java":
        return [_tool("java"), "-cp", str(cell_dir), "CrcGlot"]
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
    if lang == "java" and not (_TOOL_BINS["javac"] and _TOOL_BINS["java"]):
        return "javac/java not on PATH"
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

crcglot has **two** performance stories, and the table below shows both in one
place:

- **Generated source** (the per-language rows): complete, zero-dependency CRC
  for any of the 100+ catalogue algorithms in nine languages, verified by
  execution.  Portable source with nothing to link -- fast enough for every
  CRC need short of a heavily CPU-constrained hot path.
- **The package's own runtime** (the two **Python (runtime)** rows): crcglot
  ships a **compiled C extension** (`crcglot._c`), so calling `generic_crc` from
  Python runs at **C speed -- on the order of 1 GB/s for *every* algorithm**,
  roughly a thousandfold over the pure-Python engine.  That is the whole reason
  there is C in a pure-stdlib package: Python users get native CRC throughput
  without writing or compiling anything.  (For crc32 it goes one further and
  borrows the stdlib's hardware-accelerated `zlib.crc32`.)

`crc32` is the test algorithm throughout -- chosen only because it is universal
and identical in shape across every language, **not** as a speed target (it is
the one algorithm you would *not* hand-generate; see **Reading the results**).
Numbers are MB/s (MB = 10^6 bytes), median of 3 runs, each an adaptive inner
loop on a single buffer ({inner_target_ms} ms / 3 iterations minimum).  (VHDL
and Verilog are excluded -- those generators emit simulator references for
hardware datapaths, not a software runtime.)

"""


_DOC_FOOTER = """
## Reading the results

The trustworthy signal here is **coarse**: the across-language band and the
within-language *algorithmic* ordering.  The fine detail -- which variant
wins, by how much -- is sensitive to runtime, buffer size, and CPU, so read it
as a hint, not a verdict.

### Across languages -- the band

AOT-compiled targets (C, Rust, Go) cluster near the top; JIT runtimes (Java,
C#, TypeScript) in the middle; pure-interpreted Python at the bottom.  That
ordering is robust and reproduces across machines.  The absolute MB/s are not
-- they move with your CPU, compiler version, and the day.

### Within a language -- variant ordering

- **slice-by-8 should beat table-driven** at the same size: it processes 8
  bytes per iteration through 8 independent table lookups, shortening the
  serial dependency chain.  That is an algorithmic win in AOT code -- though a
  managed runtime can erase it (see *confident only for C / Rust* below).
  (Python has no slice-by-8 row: CPython's per-int overhead negates the win,
  so the generator doesn't offer it.)
- **table-driven vs bit-by-bit is NOT monotonic.**  Modern compilers (LLVM at
  `-O3`) unroll and vectorize the 8-iteration bit loop into register-only work
  with no memory traffic, while a table lookup is one serial dependent load
  per byte.  So a well-vectorized bit-by-bit loop can tie or beat
  table-driven, especially on large buffers.  Languages whose codegen leaves
  the bit loop un-vectorized (C#, TypeScript, Python, Go) show the classic
  bitwise->table jump; Rust at `-O3` barely does.
- The **Tables** column is the static RAM cost: 0 (bit-by-bit), 1 KiB (table,
  256 entries x width), 8 KiB (slice-by-8, 8 tables).  Compiled code is a few
  hundred bytes regardless, so this is a tight proxy for static footprint.

### Buffer size -- cache vs overhead

The two size columns pull in opposite directions.  A **1 KiB** buffer lives
entirely in L1 cache and is re-read at full speed; a **1 MiB** buffer fits in
neither L1 nor L2, so every pass streams it from L2/L3/RAM.  Two effects
compete as the buffer grows: fixed per-call overhead *amortizes* (favours
1 MiB), while the data stream *falls out of cache* (favours 1 KiB).  Tight,
latency-bound loops -- a serial CRC dependency chain -- are dominated by the
cache effect and often run **faster at 1 KiB** (e.g. Java table-driven drops
from 1 KiB to 1 MiB).  Overhead-bound runtimes (C#, Python) show the opposite.
Note the 1 KiB figure is a best case: real input is rarely already L1-hot.

### crc32 is the outlier, not the benchmark

crc32 is special: every mainstream runtime ships a hardware-accelerated crc32
in its standard library -- Python `zlib.crc32`, Java `java.util.zip.CRC32` /
`CRC32C`, .NET `System.IO.Hashing.Crc32`, Go `hash/crc32` -- folding with
CLMUL / SSE4.2 on any CPU since ~2010, tens of times faster than any portable
software CRC.  If you only need crc32 and can lean on the platform library, do.

crcglot's own Python runtime already does, and goes further -- which is what
the two **Python (runtime)** rows in the table above show:

- **crc32** -> the `generic_crc` dispatcher hands it to `zlib.crc32`, silicon
  speed for free.
- **the rest of the catalogue** -> the **compiled C extension** (`crcglot._c`,
  slice-by-8 in C) runs them at ~1 GB/s from Python -- roughly a thousandfold
  over the pure-Python engine.  This is why there is C in a pure-stdlib
  package: the Python build has a fast path for *every* algorithm, not just the
  one zlib happens to cover.

The **generated** source (the per-language rows) is the second product: a
complete, dependency-free CRC you can drop into firmware, an air-gapped build,
or a language with no CRC library at all.  For crc32 it is the one thing you
would not reach for -- use the stdlib -- but for the other algorithms with no
hardware shortcut, the generated table / slice-by-8 *is* the fast path, because
there is nothing faster to borrow.

### Variant selection is confident only for C / Rust

slice-by-8 > table > bit is an algorithmic ordering, but whether it survives
to the metal depends on the toolchain.  For AOT C / Rust at `-O3` -- no array
bounds checks, codegen you can inspect -- it holds and `--fast` is a sound
call.  For managed runtimes the JIT, GC, bounds-check elimination, and
slice-by-8's 8 KiB of tables can erase it: in some runs **C# slice-by-8 at
1 MiB is slower than plain table-driven**.  For those targets treat the rows
as a hint -- default to table-driven (a reliable middle that beats bit-by-bit
without slice-by-8's footprint) and measure your own runtime and message size
if it matters.

### Caveat

An academic comparison: single-threaded, in-process, one identical synthetic
buffer, no I/O.  It confirms the expected band and ordering; it is **not** a
publication-grade benchmark.  Real-world throughput depends on data source,
batch size, allocation, and your exact toolchain -- measure before deciding.

## Toolchain (this run)

| Tool | Release flags |
|------|----------------|
| `gcc` | `-O3 -DNDEBUG` |
| `rustc` | `-C opt-level=3 -C codegen-units=1` |
| `go build` | `-ldflags="-s -w"` (already optimized by default) |
| `dotnet publish` | `-c Release` (needs SDK, not just runtime) |
| `javac` / `java` | HotSpot, default tiered JIT |
| `tsx` | V8-JIT'd via Node.js |
| `python` | interpreter only |

## Reproduce

```bash
uv run python scripts/benchmark.py
```

Working files land under `benchmarks/<lang>/<variant>/<size>/` (gitignored);
the rendered output is this file.
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


def _render_table(
    results: list[CellResult], runtime_results: list[CellResult],
) -> str:
    """Render the gallery + crcglot's own Python runtime engines as one table.

    The compiled-language / generated-source rows come first, then crcglot's
    in-process runtime engines appended as ``Python (runtime)`` rows -- so the
    compiled C extension sits in the same table as the compiled languages and
    the fast Python path is impossible to miss.
    """
    lines = [
        "## Results",
        "",
        "| Language          | Variant / engine            |  Tables | 1 KiB (MB/s) | 1 MiB (MB/s) |",
        "|-------------------|-----------------------------|--------:|-------------:|-------------:|",
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
                f"| {lname:<17} | {v_label:<27} | "
                f"{tbl_s:>7} | "
                f"{r1k_s:>12} | {r1m_s:>12} |"
            )

    # Append crcglot's own runtime engines as Python rows.  These are NOT
    # generated source -- they are what `crcglot.generic_crc` uses when called
    # from Python: the compiled C extension (slice-by-8 for every algorithm)
    # and, for crc32 only, the stdlib's hardware `zlib.crc32`.
    def _rt(lang: str, size: int) -> CellResult | None:
        return next(
            (r for r in runtime_results if r.lang == lang and r.size == size),
            None,
        )

    slice8_tables = _fmt_bytes(_table_bytes("slice8", crc_width))
    rt_rows = [
        ("cpython-ext", "C extension (`crcglot._c`)", slice8_tables),
        ("dispatch", "`generic_crc` → `zlib.crc32`", "—"),
    ]
    for key, label, tbl in rt_rows:
        r1k = _rt(key, 1024)
        r1m = _rt(key, 1024 * 1024)
        if r1k is None or r1m is None:
            continue
        lines.append(
            f"| {'Python (runtime)':<17} | {label:<27} | "
            f"{tbl:>7} | {_fmt_mbps(r1k):>12} | {_fmt_mbps(r1m):>12} |"
        )
    lines.append("")

    # Distinguish the runtime rows from generated source, and quantify the
    # C-extension win over pure Python.
    py_eng = _rt("python-runtime", 1024 * 1024)
    cext = _rt("cpython-ext", 1024 * 1024)
    speedup = ""
    if py_eng and cext and py_eng.median_mbps and cext.median_mbps:
        speedup = (
            f"  The C extension is ~{cext.median_mbps / py_eng.median_mbps:,.0f}x "
            "faster than the pure-Python CRC."
        )
    lines.append(
        "The two **Python (runtime)** rows are crcglot's own engines, not "
        "generated source: calling `crcglot.generic_crc` from Python uses the "
        "**compiled C extension** (`crcglot._c`, slice-by-8 for *every* "
        "algorithm), and for crc32 alone delegates to the stdlib's "
        "hardware-accelerated `zlib.crc32`.  They put Python squarely in the "
        "compiled-language band -- which is the whole reason the package ships "
        "C." + speedup
    )
    lines.append("")
    lines.append(
        "The same compiled paths back the **streaming** API: "
        "`crcglot.crc_stream(name)` / `CrcStream` feed chunks into the C "
        "extension's `CrcStream` (or `zlib.crc32` incrementally for crc32), so "
        "chunked data -- large files, sockets, sensor logs -- runs at this same "
        "compiled speed, paying the Python/C transition once across the whole "
        "message rather than per call."
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
        + _render_table(results, runtime_results)
        + _DOC_FOOTER
    )
    _BENCHMARKS_MD.write_text(body, encoding="utf-8")
    print(f"\nWrote {_BENCHMARKS_MD}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
