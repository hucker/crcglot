"""Command-line interface for crcglot.

Usage:
    crcglot c crc32 --slice8 file=mycrc       # writes mycrc.h + mycrc.c
    crcglot rust crc64-xz --slice8 > mycrc.rs
    crcglot vhdl crc32 > mycrc.vhd
    crcglot python crc16-modbus
    crcglot list                              # browse catalogue
    crcglot info crc32                        # show parameters
    crcglot detect packet.bin                 # identify CRC from a packet
    crcglot encode crc32 "123456789"          # build a packet (round-trip pair)
    crcglot credits                           # acknowledgments

    # Custom Rocksoft/Williams polynomial:
    crcglot c --custom width=16 poly=0x1234 init=0xFFFF \\
             refin=true refout=true xorout=0x0000 file=mycustom
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crcglot import (
    ALGORITHMS,
    ATTRIBUTION,
    LANGUAGES,
    NAMING_ORDER,
    AlgorithmInfo,
    detect,
    encode,
    encode_int,
    encode_text,
    generic_crc,
)
from crcglot.comments import styles_for_language


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


def _cmd_list(args: argparse.Namespace) -> int:
    """Print catalogue entries; optional glob filter."""
    import fnmatch
    pat = args.glob or "*"
    names = sorted(n for n in ALGORITHMS if fnmatch.fnmatch(n, pat))
    if not names:
        print(f"No algorithms match {pat!r}", file=sys.stderr)
        return 1
    if args.json:
        payload = {
            "algorithms": [],
            "count": len(names),
        }
        for n in names:
            algo = ALGORITHMS[n]
            hex_w = (algo.width + 3) // 4
            payload["algorithms"].append({
                "name": n,
                "width": algo.width,
                "poly": algo.poly,
                "poly_hex": f"0x{algo.poly:0{hex_w}X}",
                "init": algo.init,
                "init_hex": f"0x{algo.init:0{hex_w}X}",
                "refin": algo.refin,
                "refout": algo.refout,
                "xorout": algo.xorout,
                "xorout_hex": f"0x{algo.xorout:0{hex_w}X}",
                "check": algo.check,
                "check_hex": f"0x{algo.check:0{hex_w}X}",
                "desc": algo.desc,
                "source": algo.source,
            })
        print(json.dumps(payload, indent=2))
        return 0
    for n in names:
        algo = ALGORITHMS[n]
        print(f"  {n:<24}  width={algo.width:>2}  {algo.desc}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    """Print parameters for a single algorithm."""
    algo = ALGORITHMS.get(args.name)
    if algo is None:
        print(f"Unknown algorithm: {args.name!r}", file=sys.stderr)
        return 1
    w = algo.width
    hex_w = (w + 3) // 4
    print(f"{args.name}")
    print(f"  width:    {w}")
    print(f"  poly:     0x{algo.poly:0{hex_w}X}")
    print(f"  init:     0x{algo.init:0{hex_w}X}")
    print(f"  refin:    {algo.refin}")
    print(f"  refout:   {algo.refout}")
    print(f"  xorout:   0x{algo.xorout:0{hex_w}X}")
    print(f"  check:    0x{algo.check:0{hex_w}X}")
    if algo.desc:
        print(f"  desc:     {algo.desc}")
    print(f"  source:   {algo.source}")
    return 0


def _read_binary_packets(inputs: list[str]) -> list[bytes]:
    """Load binary packets from a list of file specs.

    Args:
        inputs: File paths.  ``"-"`` (or an empty list, treated as
            ``["-"]``) reads stdin in full as a single packet.

    Returns:
        One ``bytes`` per packet, in input order.

    Raises:
        FileNotFoundError: One of the named paths does not exist.
    """
    if not inputs:
        inputs = ["-"]
    out: list[bytes] = []
    for spec in inputs:
        if spec == "-":
            out.append(sys.stdin.buffer.read())
        else:
            out.append(Path(spec).read_bytes())
    return out


def _read_text_packets(arg: str) -> list[str]:
    """Parse the value of ``--text`` into one-or-more packets.

    Args:
        arg: The literal value passed to ``--text``.  ``"-"`` reads
            stdin and splits it into one packet per non-empty line;
            anything else is a single inline packet.

    Returns:
        The list of text packets.
    """
    if arg == "-":
        text = sys.stdin.read()
        return [ln for ln in text.splitlines() if ln.strip()]
    return [arg]


def _cmd_detect(args: argparse.Namespace) -> int:
    """Run the ``crcglot detect`` subcommand.

    Reads packets according to ``--text`` / ``--hex`` / positional file
    inputs, calls :func:`crcglot.detect`, and prints each surviving
    candidate.

    Args:
        args: Parsed argparse namespace for the ``detect`` subparser.

    Returns:
        ``0`` on at least one match, ``1`` on no match, ``2`` on
        invalid input (bad hex, missing file, etc.).
    """
    if args.text is not None:
        packets: list[str] | list[bytes] = _read_text_packets(args.text)
        mode = "text"
    elif args.hex is not None:
        try:
            packets = [bytes.fromhex(args.hex)]
        except ValueError as e:
            print(f"Error: invalid hex string: {e}", file=sys.stderr)
            return 2
        mode = "binary"
    else:
        try:
            packets = _read_binary_packets(args.inputs)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        mode = "binary"

    result = detect(
        packets,
        mode=mode,
        encoding=args.encoding,
        algorithms=args.algorithms,
        match=args.match,
    )
    if not result.matched:
        print("No match.", file=sys.stderr)
        return 1

    from crcglot import HexFormat, TextFormat  # imported here to avoid cycles
    for m in result.candidates:
        line = f"{m.algorithm}  width={m.info.width}  endianness={m.endianness}"
        if isinstance(m.padding, TextFormat):
            line += (
                f"  separator={m.padding.separator!r}"
                f"  leader={m.padding.hex_prefix!r}"
                f"  uppercase={m.padding.uppercase}"
            )
        elif isinstance(m.padding, HexFormat):
            line += (
                f"  byte_separator={m.padding.byte_separator!r}"
                f"  prefix={m.padding.prefix!r}"
                f"  per_byte={m.padding.prefix_per_byte}"
                f"  uppercase={m.padding.uppercase}"
            )
        print(line)
    return 0


def _cmd_encode(args: argparse.Namespace) -> int:
    """Run the ``crcglot encode`` subcommand.

    Calls :func:`crcglot.encode` (with ``--binary``) or
    :func:`crcglot.encode_text` and writes the result to stdout.

    Args:
        args: Parsed argparse namespace for the ``encode`` subparser.

    Returns:
        ``0`` on success, ``2`` on unknown algorithm or missing
        text-mode data.
    """
    if args.algorithm not in ALGORITHMS:
        print(
            f"Error: unknown algorithm {args.algorithm!r}. "
            f"Use 'crcglot list' to browse.",
            file=sys.stderr,
        )
        return 2
    endianness = "little" if args.little else "big"
    if args.binary:
        data = sys.stdin.buffer.read()
        packet = encode(data, args.algorithm, endianness=endianness)
        sys.stdout.buffer.write(packet)
        return 0
    if args.data is None:
        print(
            "Error: text-mode encode requires a data argument "
            "(or use --binary to read stdin as bytes).",
            file=sys.stderr,
        )
        return 2
    text = encode_text(
        args.data,
        args.algorithm,
        sep=args.sep,
        leader=args.leader,
        uppercase=args.upper,
        endianness=endianness,
        encoding=args.encoding,
        fmt=args.fmt,
    )
    print(text)
    return 0


def _cmd_compute(args: argparse.Namespace) -> int:
    """Run the ``crcglot compute`` subcommand.

    Computes the raw CRC integer of ``data`` (binary or text) under a
    catalogue algorithm and prints it as hex by default (or decimal via
    ``--dec``).  Pairs with
    ``crcglot encode`` (which packages the same value into a packet) and
    sits below ``crcglot detect`` (which asks the inverse question:
    "which algorithm produces this CRC?").

    Args:
        args: Parsed argparse namespace for the ``compute`` subparser.

    Returns:
        ``0`` on success, ``2`` on unknown algorithm or missing
        text-mode data.
    """
    if args.algorithm not in ALGORITHMS:
        print(
            f"Error: unknown algorithm {args.algorithm!r}. "
            f"Use 'crcglot list' to browse.",
            file=sys.stderr,
        )
        return 2
    if args.binary:
        data: bytes | str = sys.stdin.buffer.read()
    elif args.data is None:
        print(
            "Error: text-mode compute requires a data argument "
            "(or use --binary to read stdin as bytes).",
            file=sys.stderr,
        )
        return 2
    else:
        data = args.data
    crc = encode_int(data, args.algorithm, encoding=args.encoding)
    width = ALGORITHMS[args.algorithm].width
    hex_w = (width + 3) // 4
    if args.hex:
        print(f"0x{crc:0{hex_w}X}")
    else:
        print(crc)
    return 0


def _cmd_credits(args: argparse.Namespace) -> int:
    """Run the ``crcglot credits`` subcommand.

    Prints :data:`crcglot.ATTRIBUTION` verbatim, appending a trailing
    newline if the constant doesn't already end with one.

    Args:
        args: Parsed argparse namespace (unused; included for the
            uniform dispatch shape).

    Returns:
        Always ``0``.
    """
    del args  # uniform handler shape; nothing to read from the namespace.
    sys.stdout.write(ATTRIBUTION)
    if not ATTRIBUTION.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _cmd_codegen(args: argparse.Namespace, lang: str) -> int:
    """Generate source code for the given language."""
    # At most one variant selector.  Intent flags (--small/--fast) and
    # the expert overrides (--table/--slice8) all pick the same single
    # axis, so more than one is ambiguous.
    chosen = [
        flag for flag, on in (
            ("--small", args.small),
            ("--fast", args.fast),
            ("--table", args.table),
            ("--slice8", args.slice8),
        ) if on
    ]
    if len(chosen) > 1:
        print(
            f"Error: {', '.join(chosen)} are mutually exclusive -- pick "
            "one variant selector (or none for the default bit-by-bit).",
            file=sys.stderr,
        )
        return 2

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

    name_override = kv.get("name")
    if name_override == "":
        print("Error: name= requires a value", file=sys.stderr)
        return 2

    # Map the variant selector flags to one variant string for generate_files.
    # slice-by-8 on a language that doesn't emit it falls back to table (the one
    # width-independent fallback); width-illegal variants surface from the
    # generator.  Everything else ("auto") resolves to the fastest per width.
    note: str | None = None
    if args.small:
        variant_arg = "bitwise"
    elif args.table:
        variant_arg = "table"
    elif args.slice8 and "slice8" not in LANGUAGES[lang].variants:
        variant_arg = "table"
        note = (
            "Note: --slice8 is slower than --table in CPython (measured 0.79x); "
            "using --table instead." if lang == "python"
            else f"Note: --slice8 is not implemented for {lang}; using --table instead."
        )
    elif args.slice8:
        variant_arg = "slice8"
    else:
        variant_arg = "auto"
    if note:
        print(note, file=sys.stderr)

    gen_algorithm: list[str] | None = None
    gen_custom: AlgorithmInfo | None = None

    if args.custom:
        # ----- Custom Rocksoft/Williams parameters -----
        # --custom builds exactly one CRC from width=/poly=/...; a bare
        # algorithm name has no meaning here (and bundling is catalogue-only),
        # so reject stray names loudly instead of silently dropping them.
        if bare:
            print(
                f"Error: --custom takes raw parameters, not algorithm names "
                f"(got {', '.join(bare)}).  Drop --custom to bundle catalogue "
                f"algorithms, or drop the name(s) to generate the custom CRC.",
                file=sys.stderr,
            )
            return 2
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
        check = generic_crc(b"123456789", width, poly, init, refin, refout, xorout)
        desc = kv.get("desc") or (
            f"Custom CRC-{width} (poly=0x{poly:X}, init=0x{init:X}, "
            f"refin={refin}, refout={refout}, xorout=0x{xorout:X})"
        )
        gen_custom = AlgorithmInfo(
            width=width,
            poly=poly,
            init=init,
            refin=refin,
            refout=refout,
            xorout=xorout,
            check=check,
            desc=desc,
            source="custom",
        )
        advised_algos: list[str | AlgorithmInfo] = [gen_custom]
    else:
        # ----- Catalogue lookup (one or more algorithms) -----
        if not bare:
            print(
                f"Error: usage: crcglot {lang} <algorithm> [<algorithm>...] "
                f"[--table|--slice8] [file=STEM] [symbol=NAME]",
                file=sys.stderr,
            )
            return 2
        # Multiple algorithm names bundle into one output file; dedup
        # (order-preserving) so a repeat can't collide with itself.
        names = list(dict.fromkeys(b.lower() for b in bare))
        unknown = [n for n in names if n not in ALGORITHMS]
        if unknown:
            print(
                f"Error: unknown algorithm {unknown[0]!r}. "
                "Use 'crcglot list' to browse.",
                file=sys.stderr,
            )
            return 2
        advised_algos = list(names)
        # ``symbol=`` renames the single emitted function; it can't name
        # many.  With >1 algorithm each uses its catalogue-derived name.
        if symbol_override is not None and len(names) > 1:
            print(
                "Error: symbol= names a single function; omit it when "
                "generating multiple algorithms (each uses its catalogue name).",
                file=sys.stderr,
            )
            return 2
        gen_algorithm = names

    # crcglot owns naming + filenames: one call returns ready-to-write,
    # correctly-named files (Java's class == file, C's .h/.c pair).
    try:
        gfiles = LANGUAGES[lang].generate_files(
            gen_algorithm,
            custom=gen_custom,
            variant=variant_arg,
            comment_style=args.comment,
            naming=args.naming,
            name=name_override,
            symbol=symbol_override,
            file_stem=file_stem,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    # Informational advisories (faster stdlib path, Python-runtime) go to
    # stderr so a redirected stdout stays a clean source file.
    for adv in LANGUAGES[lang].advisories_for(advised_algos):
        prefix = "Warning:" if adv.severity == "warning" else "Note:"
        print(f"{prefix} {adv.message}", file=sys.stderr)

    # ----- Output -----
    if file_stem is not None:
        for f in gfiles:
            path = Path.cwd() / f.filename
            path.write_text(f.content, encoding="utf-8")
            print(f"Wrote {path}")
        return 0

    # Stdout: emit each file's content (C: header then source).
    for i, f in enumerate(gfiles):
        if i:
            sys.stdout.write("\n")
        sys.stdout.write(f.content)
    sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse tree for the crcglot CLI."""
    parser = argparse.ArgumentParser(
        prog="crcglot",
        description=(
            "Verified CRC source-code generator for C, C#, Go, Java, "
            "Python, Rust, TypeScript, Verilog, and VHDL.  Catalogue-driven, "
            "self-test embedded."
        ),
    )
    subs = parser.add_subparsers(dest="command", required=True)

    # crcglot list [glob] [--json]
    p_list = subs.add_parser("list", help="List catalogue algorithms")
    p_list.add_argument("glob", nargs="?", help="Optional glob filter (e.g. 'crc16-*')")
    p_list.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON with full parameters per algorithm",
    )

    # crcglot info <name>
    p_info = subs.add_parser("info", help="Show algorithm parameters")
    p_info.add_argument("name", help="Algorithm name (e.g. crc32)")

    # crcglot detect [packet.bin ...] [--text TEXT|--hex HEX]
    p_detect = subs.add_parser(
        "detect",
        help="Identify which catalogue CRC matches a packet",
    )
    p_detect.add_argument(
        "inputs", nargs="*",
        help="Binary packet files (or '-' for stdin); ignored with --text/--hex",
    )
    p_detect.add_argument(
        "--text", metavar="TEXT",
        help="Text packet (inline string, or '-' to read lines from stdin)",
    )
    p_detect.add_argument(
        "--hex", metavar="HEX",
        help="Binary packet supplied as a hex string",
    )
    p_detect.add_argument(
        "--match", choices=["first", "all", "set"], default="first",
        help=(
            "first (default): early-stop on first hit; "
            "all: forensic, every consistent candidate; "
            "set: strict singleton (succeed only on a unique algorithm)"
        ),
    )
    p_detect.add_argument(
        "--algorithms", metavar="GLOB",
        help="fnmatch glob to narrow the scan (e.g. 'crc16-*')",
    )
    p_detect.add_argument(
        "--encoding", default="utf-8",
        help="Encoding for text-mode data portion (default: utf-8)",
    )

    # crcglot encode <algorithm> [<data>] [--binary] [--little] [--sep STR] ...
    p_encode = subs.add_parser(
        "encode",
        help="Build a packet by appending the CRC (round-trip pair to detect)",
    )
    p_encode.add_argument("algorithm", help="Catalogue name (e.g. crc32)")
    p_encode.add_argument(
        "data", nargs="?",
        help="Text data (text mode); omit when --binary reads stdin as bytes",
    )
    p_encode.add_argument(
        "--binary", action="store_true",
        help="Binary mode: read stdin as bytes, write packet bytes to stdout",
    )
    p_encode.add_argument(
        "--little", action="store_true",
        help="Little-endian CRC byte order (default: big)",
    )
    p_encode.add_argument(
        "--sep", default=" ",
        help="Text separator between data and hex (default: single space)",
    )
    p_encode.add_argument(
        "--leader", default="",
        help="Text hex leader: '', '0x', or '0X' (default: '')",
    )
    p_encode.add_argument(
        "--upper", action="store_true",
        help="Uppercase hex digits",
    )
    p_encode.add_argument(
        "--fmt", default="{data}{sep}{leader}{crc}",
        help="str.format template (tokens: {data} {sep} {leader} {crc})",
    )
    p_encode.add_argument(
        "--encoding", default="utf-8",
        help="Encoding for text-mode data (default: utf-8)",
    )

    # crcglot compute <algorithm> [<data>] [--binary] [--hex|--dec] [--encoding]
    p_compute = subs.add_parser(
        "compute",
        help="Compute the raw CRC integer of data (no packet framing)",
    )
    p_compute.add_argument("algorithm", help="Catalogue name (e.g. crc32)")
    p_compute.add_argument(
        "data", nargs="?",
        help="Text data (text mode); omit when --binary reads stdin as bytes",
    )
    p_compute.add_argument(
        "--binary", action="store_true",
        help="Binary mode: read stdin as bytes instead of taking a text argument",
    )
    fmt_group = p_compute.add_mutually_exclusive_group()
    fmt_group.add_argument(
        "--hex", dest="hex", action="store_true", default=True,
        help="Print as 0x-prefixed hex (default)",
    )
    fmt_group.add_argument(
        "--dec", dest="hex", action="store_false",
        help="Print as decimal integer",
    )
    p_compute.add_argument(
        "--encoding", default="utf-8",
        help="Encoding for text-mode data (default: utf-8)",
    )

    # crcglot credits
    subs.add_parser(
        "credits",
        help="Show acknowledgments for the projects crcglot builds on",
    )

    # crcglot {c,csharp,go,java,python,rust,typescript,verilog,vhdl} <algo>
    # [--table|--slice8] [file=STEM] [symbol=NAME]
    # Or: crcglot c --custom width=... poly=... ...
    for lang in LANGUAGES:
        p = subs.add_parser(lang, help=f"Generate {lang.upper()} source code")
        # Intent front door (pick one): --small / --fast.  crcglot maps
        # them to the right implementation for the language and width.
        p.add_argument(
            "--small", action="store_true",
            help="Smallest code, no lookup table (bit-by-bit).",
        )
        p.add_argument(
            "--fast", action="store_true",
            help=(
                "Fastest implementation the target supports "
                "(slice-by-8 for width 32/64, else table-driven). "
                "This is the default when no variant flag is given."
            ),
        )
        # Expert overrides: name the exact implementation.  Most users
        # want --small / --fast instead.
        p.add_argument(
            "--table", action="store_true",
            help="Expert: 256-entry lookup table (the middle size point).",
        )
        p.add_argument(
            "--slice8", action="store_true",
            help=(
                "Expert: slice-by-8 (8 tables). Width 32/64, compiled "
                "targets only; python / unsupported fall back to --table."
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
        # Only the styles valid for THIS language -- derived from each style's
        # own `languages`, so argparse rejects e.g. `rust --comment=doxygen`
        # up front with the right options, no hardcoded matrix here.
        p.add_argument(
            "--comment", choices=styles_for_language(lang), default="plain",
            metavar="STYLE",
            help=(
                "Comment / documentation style for the generated code. "
                "'plain' (default) is professional human-readable comments "
                "in the language's native syntax; 'doxygen' emits "
                "/** @brief @param */ markup for C / C# / Java; Python has "
                "'google' (Args / Returns), 'numpy' (underlined Parameters / "
                "Returns) and 'rest' (Sphinx :param: field lists); 'rustdoc' "
                "emits /// Markdown (# Arguments / "
                "# Returns) for Rust; 'godoc' emits identifier-led // docs "
                "for Go; 'javadoc' emits /** @param @return */ for Java; "
                "'jsdoc' emits TSDoc /** @param x - ... @returns */ for "
                "TypeScript; 'docfx' emits /// <summary> <param> <returns> "
                "XML doc comments for C#."
            ),
        )
        # Only the naming conventions THIS language offers, ordered simplest
        # first.  argparse rejects e.g. `rust --naming=pascal` up front; the
        # default is each language's idiomatic convention (snake for
        # C / Rust / Python / Verilog / VHDL, pascal for Go / C#, camel for
        # Java / TypeScript).
        p.add_argument(
            "--naming",
            choices=[n for n in NAMING_ORDER if n in LANGUAGES[lang].naming],
            default=LANGUAGES[lang].default_naming,
            metavar="CONVENTION",
            help=(
                "Naming convention for the generated public function / method "
                "names: 'snake' (crc16_modbus_update), 'camel' "
                "(crc16ModbusUpdate), or 'pascal' (Crc16ModbusUpdate). "
                "Defaults to the language's idiomatic convention; only the "
                "conventions that language uses are offered."
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
    if args.command == "detect":
        return _cmd_detect(args)
    if args.command == "encode":
        return _cmd_encode(args)
    if args.command == "compute":
        return _cmd_compute(args)
    if args.command == "credits":
        return _cmd_credits(args)
    if args.command in LANGUAGES:
        return _cmd_codegen(args, args.command)
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
