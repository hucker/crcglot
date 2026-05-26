"""Command-line interface for crcglot.

Usage:
    crcglot c crc32 --slice8 file=mycrc       # writes mycrc.h + mycrc.c
    crcglot rust crc64-xz --slice8 > mycrc.rs
    crcglot vhdl crc32 > mycrc.vhd
    crcglot python crc16-modbus
    crcglot list                              # browse catalogue
    crcglot info crc32                        # show parameters

    # Custom Rocksoft/Williams polynomial:
    crcglot c --custom width=16 poly=0x1234 init=0xFFFF \\
             refin=true refout=true xorout=0x0000 file=mycustom
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from crcglot import (
    CRC_CATALOGUE,
    GENERATORS,
    GENERATORS_FROM_ENTRY,
    _generic_crc,
)


_CRC_FILE_EXTENSIONS = {
    "c": (".h", ".c"),
    "csharp": (".cs",),
    "go": (".go",),
    "python": (".py",),
    "rust": (".rs",),
    "vhdl": (".vhd",),
    "zig": (".zig",),
}


_LANGS = ("c", "csharp", "go", "python", "rust", "vhdl", "zig")


_CUSTOM_KV_KEYS = {
    "width", "poly", "init", "refin", "refout", "xorout", "name", "desc",
}


def _parse_int(value: str) -> int:
    """Parse a hex (``0x...``) or decimal integer."""
    s = value.strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s)


def _parse_bool(value: str) -> bool:
    """Parse a permissive boolean: true / false / 1 / 0 / yes / no."""
    v = value.strip().lower()
    if v in ("true", "1", "yes", "on"):
        return True
    if v in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"expected true/false (got {value!r})")


def _symbol_from_stem(file_stem: str) -> str:
    """Derive a valid C/Rust/Python identifier from a file path / stem."""
    base = Path(file_stem).name
    return base.replace("-", "_").replace(".", "_")


def _parse_kv_tokens(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """Split a list of CLI tokens into ``key=value`` pairs vs bare tokens."""
    kv: dict[str, str] = {}
    bare: list[str] = []
    for tok in tokens:
        if "=" in tok and tok.split("=", 1)[0] in (_CUSTOM_KV_KEYS | {"file", "symbol"}):
            k, v = tok.split("=", 1)
            kv[k] = v
        else:
            bare.append(tok)
    return kv, bare


def _write_files(
    result: str | tuple[str, str],
    lang: str,
    file_stem: str,
    cwd: Path,
) -> list[Path]:
    """Write generator output to disk.  C returns (header, source); others one string."""
    extensions = _CRC_FILE_EXTENSIONS.get(lang, (".txt",))
    written: list[Path] = []
    if isinstance(result, tuple):
        for content, ext in zip(result, extensions):
            path = cwd / f"{file_stem}{ext}"
            path.write_text(str(content), encoding="utf-8")
            written.append(path)
    else:
        path = cwd / f"{file_stem}{extensions[0]}"
        path.write_text(str(result), encoding="utf-8")
        written.append(path)
    return written


def _cmd_list(args: argparse.Namespace) -> int:
    """Print catalogue entries; optional glob filter."""
    import fnmatch
    pat = args.glob or "*"
    names = sorted(n for n in CRC_CATALOGUE if fnmatch.fnmatch(n, pat))
    if not names:
        print(f"No algorithms match {pat!r}", file=sys.stderr)
        return 1
    for n in names:
        entry = CRC_CATALOGUE[n]
        desc = entry.get("desc", "")
        print(f"  {n:<24}  width={entry['width']:>2}  {desc}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    """Print parameters for a single algorithm."""
    entry = CRC_CATALOGUE.get(args.name)
    if entry is None:
        print(f"Unknown algorithm: {args.name!r}", file=sys.stderr)
        return 1
    w = entry["width"]
    hex_w = (w + 3) // 4
    print(f"{args.name}")
    print(f"  width:    {w}")
    print(f"  poly:     0x{entry['poly']:0{hex_w}X}")
    print(f"  init:     0x{entry['init']:0{hex_w}X}")
    print(f"  refin:    {entry['refin']}")
    print(f"  refout:   {entry['refout']}")
    print(f"  xorout:   0x{entry['xorout']:0{hex_w}X}")
    print(f"  check:    0x{entry['check']:0{hex_w}X}")
    desc = entry.get("desc")
    if desc:
        print(f"  desc:     {desc}")
    return 0


def _cmd_codegen(args: argparse.Namespace, lang: str) -> int:
    """Generate source code for the given language."""
    use_table = args.table
    use_slice8 = args.slice8

    if use_slice8 and use_table:
        print(
            "Error: --slice8 and --table are mutually exclusive "
            "(slice-by-8 already uses tables, just 8 of them).",
            file=sys.stderr,
        )
        return 2
    if use_slice8 and lang in ("csharp", "go", "zig"):
        print(
            f"Note: --slice8 is not implemented for {lang}; "
            f"using --table instead.",
            file=sys.stderr,
        )
        use_slice8 = False
        use_table = True
    if use_slice8 and lang == "python":
        print(
            "Note: --slice8 is slower than --table in CPython "
            "(measured 0.79x); using --table instead.",
            file=sys.stderr,
        )
        use_slice8 = False
        use_table = True

    # Build kv dict from positional tokens (for --custom path: width=N
    # poly=X ..., plus file=STEM / symbol=NAME in any path).
    kv, bare = _parse_kv_tokens(args.tokens)

    file_stem = kv.get("file")
    if file_stem == "":
        print("Error: file= requires a value", file=sys.stderr)
        return 2
    symbol_override = kv.get("symbol")
    if symbol_override == "":
        print("Error: symbol= requires a value", file=sys.stderr)
        return 2

    if args.custom:
        # ----- Custom Rocksoft/Williams parameters -----
        if "width" not in kv or "poly" not in kv:
            print(
                "Error: --custom requires width=N and poly=X (plus optional "
                "init=, refin=, refout=, xorout=)",
                file=sys.stderr,
            )
            return 2
        try:
            width = _parse_int(kv["width"])
            poly = _parse_int(kv["poly"])
            init = _parse_int(kv.get("init", "0"))
            xorout = _parse_int(kv.get("xorout", "0"))
            refin = _parse_bool(kv.get("refin", "false"))
            refout = _parse_bool(kv.get("refout", "false"))
        except ValueError as e:
            print(f"Error: custom CRC param: {e}", file=sys.stderr)
            return 2
        if width not in (8, 16, 32, 64):
            print(
                f"Error: custom CRC width must be 8, 16, 32, or 64 (got {width})",
                file=sys.stderr,
            )
            return 2
        check = _generic_crc(b"123456789", width, poly, init, refin, refout, xorout)
        custom_name = kv.get("name") or "crc_custom"
        desc = kv.get("desc") or (
            f"Custom CRC-{width} (poly=0x{poly:X}, init=0x{init:X}, "
            f"refin={refin}, refout={refout}, xorout=0x{xorout:X})"
        )
        entry = {
            "width": width, "poly": poly, "init": init,
            "refin": refin, "refout": refout, "xorout": xorout,
            "check": check, "desc": desc,
        }
        symbol = (
            symbol_override
            or (_symbol_from_stem(file_stem) if file_stem else None)
            or _symbol_from_stem(custom_name)
        )
        gen_kwargs = {"table": use_table, "symbol": symbol}
        if use_slice8:
            gen_kwargs["slice8"] = True
        try:
            result = GENERATORS_FROM_ENTRY[lang](custom_name, entry, **gen_kwargs)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
    else:
        # ----- Catalogue lookup -----
        if not bare:
            print(
                f"Error: usage: crcglot {lang} <algorithm> [--table|--slice8] "
                f"[file=STEM] [symbol=NAME]",
                file=sys.stderr,
            )
            return 2
        name = bare[0].lower()
        if name not in CRC_CATALOGUE:
            print(
                f"Error: unknown algorithm {name!r}. Use 'crcglot list' to browse.",
                file=sys.stderr,
            )
            return 2
        symbol = (
            symbol_override
            or (_symbol_from_stem(file_stem) if file_stem else None)
        )
        gen_kwargs = {"table": use_table, "symbol": symbol}
        if use_slice8:
            gen_kwargs["slice8"] = True
        try:
            result = GENERATORS[lang](name, **gen_kwargs)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2

    # ----- Output -----
    if file_stem is not None:
        written = _write_files(result, lang, file_stem, Path.cwd())
        for p in written:
            print(f"Wrote {p}")
        return 0

    # Stdout: C returns (header, source) -> emit both separated by a banner.
    if isinstance(result, tuple):
        header, source = result
        sys.stdout.write(header)
        sys.stdout.write("\n")
        sys.stdout.write(source)
    else:
        sys.stdout.write(result)
    sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse tree for the crcglot CLI."""
    parser = argparse.ArgumentParser(
        prog="crcglot",
        description=(
            "Verified CRC source-code generator for C, Rust, VHDL, "
            "and Python.  Catalogue-driven, self-test embedded."
        ),
    )
    subs = parser.add_subparsers(dest="command", required=True)

    # crcglot list [glob]
    p_list = subs.add_parser("list", help="List catalogue algorithms")
    p_list.add_argument("glob", nargs="?", help="Optional glob filter (e.g. 'crc16-*')")

    # crcglot info <name>
    p_info = subs.add_parser("info", help="Show algorithm parameters")
    p_info.add_argument("name", help="Algorithm name (e.g. crc32)")

    # crcglot {c,go,python,rust,vhdl} <algo> [--table|--slice8] [file=STEM] [symbol=NAME]
    # Or: crcglot c --custom width=... poly=... ...
    for lang in _LANGS:
        p = subs.add_parser(lang, help=f"Generate {lang.upper()} source code")
        p.add_argument(
            "--table", action="store_true",
            help="Use 256-entry lookup table (4-8x faster)",
        )
        p.add_argument(
            "--slice8", action="store_true",
            help=(
                "Use slice-by-8 (5-10x faster than --table). "
                "C / Rust only; widths 32 or 64 only. "
                "Accepted for python but falls back to --table (CPython regression)."
            ),
        )
        p.add_argument(
            "--custom", action="store_true",
            help=(
                "Use raw Rocksoft/Williams parameters instead of a "
                "catalogue lookup. Required follow-up tokens: "
                "width=N poly=X. Optional: init=, refin=, refout=, "
                "xorout=, name=, desc="
            ),
        )
        p.add_argument(
            "tokens", nargs="*",
            help=(
                "Algorithm name (catalogue path) OR width=N poly=X ... "
                "(with --custom).  Plus optional file=STEM and symbol=NAME."
            ),
        )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point — returns process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "list":
        return _cmd_list(args)
    if args.command == "info":
        return _cmd_info(args)
    if args.command in _LANGS:
        return _cmd_codegen(args, args.command)
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
