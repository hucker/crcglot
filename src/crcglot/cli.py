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


def _symbol_from_stem(file_stem: str) -> str:
    """Derive a valid C/Rust/Python identifier from a file path / stem."""
    base = Path(file_stem).name
    return base.replace("-", "_").replace(".", "_")


_JAVA_KEYWORDS = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch",
    "char", "class", "const", "continue", "default", "do", "double", "else",
    "enum", "extends", "final", "finally", "float", "for", "goto", "if",
    "implements", "import", "instanceof", "int", "interface", "long",
    "native", "new", "package", "private", "protected", "public", "return",
    "short", "static", "strictfp", "super", "switch", "synchronized", "this",
    "throw", "throws", "transient", "try", "void", "volatile", "while",
    "true", "false", "null",
})


def _is_legal_java_identifier(s: str) -> bool:
    """True iff ``s`` is usable as a Java class name.

    Java ties the public class name to the file name, so the stem can't be
    mangled the way other languages' symbols can -- it must already be a
    legal identifier (and not a reserved word).
    """
    if not s or not (s[0].isalpha() or s[0] in "_$"):
        return False
    if not all(c.isalnum() or c in "_$" for c in s):
        return False
    return s not in _JAVA_KEYWORDS


def _java_container_name(file_stem: str | None) -> str:
    """Java container class name: the file stem's basename, or ``CrcGlot``."""
    return Path(file_stem).name if file_stem else "CrcGlot"


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
    extensions = LANGUAGES[lang].extensions
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


def _resolve_variant(
    *,
    small: bool,
    fast: bool,
    table: bool,
    slice8: bool,
    lang: str,
    width: int,
) -> tuple[str, str | None]:
    """Map the chosen CLI flags to a ``variant`` string for the generator.

    ``--small`` / ``--fast`` are the intent front door; ``--table`` /
    ``--slice8`` are expert overrides.  ``--fast`` -- and **no selector at
    all**, since the default is the fastest implementation, not the
    smallest -- picks the fastest the (language, width) actually supports:
    slice-by-8 for width 32/64 on languages that emit it, table-driven for
    byte-aligned widths, bit-by-bit for sub-byte widths.  ``--small`` is the
    explicit opt-in to bit-by-bit (smallest code, zero RAM table).

    Returns ``(variant, note)``; ``note`` is an optional stderr message
    for the explicit-``--slice8`` -> table fallback on languages that
    don't emit slice-by-8.  ``LANGUAGES[lang].variants`` /
    ``fastest_variant_for_width`` are the single source of truth.
    """
    variants = LANGUAGES[lang].variants
    # --fast, or no selector at all: the default is now fastest, not smallest.
    if fast or not (small or table or slice8):
        return (LANGUAGES[lang].fastest_variant_for_width(width), None)
    if table:
        return ("table", None)
    if slice8:
        if "slice8" in variants:
            return ("slice8", None)
        if lang == "python":
            note = (
                "Note: --slice8 is slower than --table in CPython "
                "(measured 0.79x); using --table instead."
            )
        else:
            note = (
                f"Note: --slice8 is not implemented for {lang}; "
                f"using --table instead."
            )
        return ("table", note)
    # --small: explicit bit-by-bit.
    return ("bitwise", None)


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
    # Variant resolution is deferred to _resolve_variant in the custom /
    # catalogue paths below, because --fast depends on the algorithm
    # width (slice-by-8 only applies to width 32/64).

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

    if lang == "java":
        # Java puts every algorithm's methods in one container class named
        # after file=STEM (Java requires class name == file name); methods
        # are named after their algorithm, so symbol= has no role.
        if symbol_override is not None:
            print(
                "Error: symbol= is not used for Java -- methods are named "
                "after their algorithm and the class after file=STEM; omit it.",
                file=sys.stderr,
            )
            return 2
        if file_stem is not None and not _is_legal_java_identifier(
            _java_container_name(file_stem)
        ):
            print(
                f"Error: file={file_stem!r} yields class name "
                f"{_java_container_name(file_stem)!r}, which is not a legal "
                f"Java identifier (the public class name must equal the file "
                f"name). Start with a letter; use only letters/digits/_.",
                file=sys.stderr,
            )
            return 2

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
        variant, note = _resolve_variant(
            small=args.small, fast=args.fast,
            table=args.table, slice8=args.slice8,
            lang=lang, width=width,
        )
        if note:
            print(note, file=sys.stderr)
        check = generic_crc(b"123456789", width, poly, init, refin, refout, xorout)
        custom_name = kv.get("name") or "crc_custom"
        desc = kv.get("desc") or (
            f"Custom CRC-{width} (poly=0x{poly:X}, init=0x{init:X}, "
            f"refin={refin}, refout={refout}, xorout=0x{xorout:X})"
        )
        algo = AlgorithmInfo(
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
        advised_algos: list[str | AlgorithmInfo] = [algo]
        # Java: methods are named from the algorithm (custom_name) and the
        # class from the stem, so don't derive the method symbol from the
        # stem; wrap the single result in the stem-named container class.
        if lang == "java":
            symbol = None
        else:
            symbol = (
                symbol_override
                or (_symbol_from_stem(file_stem) if file_stem else None)
                or _symbol_from_stem(custom_name)
            )
        try:
            result = LANGUAGES[lang].generator_from_entry(
                custom_name, algo, symbol=symbol, variant=variant,
                comment_style=args.comment, naming=args.naming,
            )
            if lang == "java":
                result = LANGUAGES[lang].combiner(
                    [result], _java_container_name(file_stem)
                )
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
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
        outputs = []
        notes_seen: set[str] = set()
        try:
            for nm in names:
                # --fast resolves per width, so resolve inside the loop;
                # print any fallback note at most once across the bundle.
                variant, note = _resolve_variant(
                    small=args.small, fast=args.fast,
                    table=args.table, slice8=args.slice8,
                    lang=lang, width=ALGORITHMS[nm].width,
                )
                if note and note not in notes_seen:
                    print(note, file=sys.stderr)
                    notes_seen.add(note)
                # Single algo keeps today's stem->symbol behaviour; for a
                # bundle each algo defaults to its own (unique) name.  Java
                # always uses the algorithm name (the class, not the methods,
                # carries the stem) and routes through its container combiner.
                if len(names) == 1 and lang != "java":
                    sym = (
                        symbol_override
                        or (_symbol_from_stem(file_stem) if file_stem else None)
                    )
                else:
                    sym = None
                outputs.append(
                    LANGUAGES[lang].generator(
                        nm, symbol=sym, variant=variant,
                        comment_style=args.comment, naming=args.naming,
                    )
                )
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        if lang == "java":
            result = LANGUAGES[lang].combiner(
                outputs, _java_container_name(file_stem)
            )
        elif len(names) == 1:
            result = outputs[0]
        else:
            result = LANGUAGES[lang].combiner(outputs, file_stem or "crcglot")

    # Informational advisories (faster stdlib path, Python-runtime) go to
    # stderr so a redirected stdout stays a clean source file.
    for adv in LANGUAGES[lang].advisories_for(advised_algos):
        prefix = "Warning:" if adv.severity == "warning" else "Note:"
        print(f"{prefix} {adv.message}", file=sys.stderr)

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
