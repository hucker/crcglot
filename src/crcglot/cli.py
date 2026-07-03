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
    crcglot version                           # installed crcglot version

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
    SELF_TEST_INPUTS,
    AlgorithmInfo,
    custom_algorithm,
    detect,
    encode,
    encode_int,
    encode_text,
    self_test_vectors,
)
from crcglot.catalogue import unknown_algorithm_error
from crcglot.comments import styles_for_language


_CUSTOM_KV_KEYS = {
    "width", "poly", "init", "refin", "refout", "xorout", "name", "desc",
}


def _parse_int(value: str) -> int:
    """Parse a hex (``0x...``) or decimal integer, echoing the bad input on failure."""
    s = value.strip()
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    except ValueError:
        raise ValueError(
            f"expected a decimal or 0x-hex integer; got {value!r}"
        ) from None


def _hex_to_bytes(value: str) -> bytes:
    """Decode a hex string to bytes, echoing the bad input on failure.

    Replaces the raw ``bytes.fromhex`` message ("non-hexadecimal number found in
    fromhex() arg ...") with one that shows what the user actually typed.
    """
    try:
        return bytes.fromhex(value)
    except ValueError:
        raise ValueError(
            f"invalid hex string {value!r}: expected an even count of hex "
            f"digits (0-9, a-f)"
        ) from None


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
        print(
            f"Error: {unknown_algorithm_error(args.name, surface='cli')}",
            file=sys.stderr,
        )
        return 2
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


_VECTORS_PROVENANCE = (
    "two independent engines (anycrc + crccheck) agreed; check anchored to reveng"
)


def _cmd_vectors(args: argparse.Namespace) -> int:
    """Print the four self-test vectors (independent goldens) for one algorithm."""
    algo = ALGORITHMS.get(args.name)
    if algo is None:
        print(
            f"Error: {unknown_algorithm_error(args.name, surface='cli')}",
            file=sys.stderr,
        )
        return 2
    vectors = self_test_vectors(args.name)
    assert vectors is not None  # a catalogue name always resolves to goldens
    hex_w = (algo.width + 3) // 4
    if args.json:
        payload = {
            "algorithm": args.name,
            "width": algo.width,
            "provenance": _VECTORS_PROVENANCE,
            "vectors": [
                {
                    "input": name,
                    "input_hex": data.hex(),
                    "input_len": len(data),
                    "expected": getattr(vectors, name),
                    "expected_hex": f"0x{getattr(vectors, name):0{hex_w}X}",
                }
                for name, data in SELF_TEST_INPUTS.items()
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0
    print(f"{args.name} self-test vectors ({_VECTORS_PROVENANCE})")
    for name, data in SELF_TEST_INPUTS.items():
        expected = getattr(vectors, name)
        print(f"  {name:<11} ({len(data):>4} bytes)   0x{expected:0{hex_w}X}")
    print("  (--json emits each input's bytes as hex, for a runnable check)")
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


def _format_trailer_lines(hint: object) -> list[str]:
    """Render a ``TrailerResult``'s candidates as ``key=value`` lines."""
    lines = []
    for m in hint.candidates:  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        trunc = (
            f"  truncated_to={m.truncated_to}B" if m.truncated_to else ""
        )
        lines.append(
            f"{m.name}  kind={m.info.kind}  width={m.info.width}"
            f"  endianness={m.endianness}{trunc}"
            f"  frames_agreed={hint.frames_agreed}"  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
            f"  ({m.info.label})"
        )
    return lines


def _cmd_identify(args: argparse.Namespace) -> int:
    """Run the ``crcglot identify`` subcommand.

    Identifies a non-CRC trailing field in a packet: a checksum (8-bit sum /
    LRC / XOR, Adler-32, Fletcher, Internet checksum) or a cryptographic
    digest (MD5, SHA-1, SHA-2/3, BLAKE2; full or truncated).  Identification
    only -- crcglot does not generate code for these; this is a heads-up.

    Args:
        args: Parsed argparse namespace for the ``identify`` subparser.

    Returns:
        ``0`` on at least one match, ``1`` on no match, ``2`` on invalid input.
    """
    if args.text is not None:
        packets: list[str] | list[bytes] = _read_text_packets(args.text)
        mode = "text"
    elif args.hex is not None:
        try:
            packets = [_hex_to_bytes(args.hex)]
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        mode = "binary"
    else:
        try:
            packets = _read_binary_packets(args.inputs)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        mode = "binary"

    from crcglot import identify_trailer  # local import to avoid cycles

    result = identify_trailer(
        packets, mode=mode, encoding=args.encoding,
        endian=args.endian, trailers=args.trailers,
    )
    if not result.matched:
        print("No trailer match.", file=sys.stderr)
        if result.note:
            print(f"Note: {result.note}", file=sys.stderr)
        return 1
    for line in _format_trailer_lines(result):
        print(line)
    return 0


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
        # Pass the hex string through as a hex packet (mode="hex") rather than
        # pre-decoding to bytes, so the result reports form="hex" (and accepts
        # 0x / separators).  detect() raises ValueError on malformed hex.
        packets = [args.hex]
        mode = "hex"
    else:
        try:
            packets = _read_binary_packets(args.inputs)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        mode = "binary"

    try:
        result = detect(
            packets,
            mode=mode,
            encoding=args.encoding,
            algorithms=args.algorithms,
            width=args.width,
            match=args.match,
            form=args.form,
        )
    except ValueError as e:
        print(f"Error: invalid input: {e}", file=sys.stderr)
        return 2
    if not result.matched:
        print("No match.", file=sys.stderr)
        if result.trailer_hint is not None:
            print("Possible non-CRC trailer (heads-up):", file=sys.stderr)
            for line in _format_trailer_lines(result.trailer_hint):
                print(f"  {line}", file=sys.stderr)
        return 1

    from crcglot import FormatMatch, HexFormat, TextFormat  # here to avoid cycles
    for m in result.candidates:
        # ``form`` (the representation: binary / hex / text / json) leads every
        # line; the per-shape detail follows.
        line = (
            f"{m.algorithm}  width={m.info.width}  "
            f"endianness={m.endianness}  form={m.form}"
        )
        if isinstance(m.padding, TextFormat):
            line += (
                f"  separator={m.padding.separator!r}"
                f"  leader={m.padding.prefix!r}"
                f"  uppercase={m.padding.uppercase}"
            )
        elif isinstance(m.padding, HexFormat):
            line += (
                f"  separator={m.padding.separator!r}"
                f"  prefix={m.padding.prefix!r}"
                f"  per_byte={m.padding.prefix_per_byte}"
                f"  uppercase={m.padding.uppercase}"
            )
        elif isinstance(m.padding, FormatMatch):
            # The form name (crclink) stays off the line -- ``form=json`` is the
            # representation; just surface the embedded CRC.
            line += f"  crc={m.padding.crc_text!r}"
        print(line)
    return 0


def _custom_tokens(c) -> str:
    """Render an AlgorithmInfo's parameters as ``--custom``-ready tokens."""
    def b(v: bool) -> str:
        return "true" if v else "false"
    return (
        f"width={c.width} poly=0x{c.poly:X} init=0x{c.init:X} "
        f"refin={b(c.refin)} refout={b(c.refout)} xorout=0x{c.xorout:X}"
    )


def _cmd_reverse(args: argparse.Namespace) -> int:
    """Run the ``crcglot reverse`` subcommand.

    Recovers the parameters of an unknown / custom CRC from whole captured
    frames (the CLI face of :func:`crcglot.reverse_packets`).  Tries the
    catalogue first; unless ``--std-only`` is given, automatically escalates
    to algebraic recovery of a custom polynomial.  Recovered candidates are
    printed as ready-to-paste ``--custom`` tokens, so the loop closes into
    ``crcglot c --custom ... file=mycrc``.

    Args:
        args: Parsed argparse namespace for the ``reverse`` subparser.

    Returns:
        ``0`` when a catalogue algorithm matched or parameters were
        recovered, ``1`` when underdetermined / no recovery, ``2`` on
        invalid input.
    """
    if args.text is not None:
        frames: list[str] | list[bytes] = _read_text_packets(args.text)
    elif args.hex:
        try:
            frames = [_hex_to_bytes(h) for h in args.hex]
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
    else:
        try:
            frames = _read_binary_packets(args.inputs)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2

    from crcglot import reverse_packets

    result = reverse_packets(
        frames, crc_bytes=args.crc_bytes, crc_byte_order=args.byte_order,
        encoding=args.encoding, std_algo_only=True,
    )
    if not result and not args.std_only:
        result = reverse_packets(
            frames, crc_bytes=args.crc_bytes, crc_byte_order=args.byte_order,
            encoding=args.encoding, std_algo_only=False,
        )

    if not result:
        print(f"No recovery: {result.note}", file=sys.stderr)
        if result.trailer_hint is not None:
            print("Possible non-CRC trailer (heads-up):", file=sys.stderr)
            for line in _format_trailer_lines(result.trailer_hint):
                print(f"  {line}", file=sys.stderr)
        return 1

    print(
        f"status={result.status}  candidates={len(result.candidates)}"
        f"  validated_frames={result.validated_frames}"
    )
    if result.status == "catalogue":
        print(result.catalogue_name)
    for c in result.candidates:
        print(f"--custom {_custom_tokens(c)}")
    if result.note:
        print(f"note: {result.note}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    """Run the ``crcglot verify`` subcommand.

    Checks each frame's trailing CRC against a named algorithm (the CLI face
    of :func:`crcglot.verify`).  Prints one VALID / INVALID line per frame
    with the expected and actual values, so a bad frame shows *how* it is
    wrong.

    Args:
        args: Parsed argparse namespace for the ``verify`` subparser.

    Returns:
        ``0`` when every frame is valid, ``1`` when any is not, ``2`` on
        invalid input or unknown algorithm.
    """
    if args.text is not None:
        packets: list[str] | list[bytes] = _read_text_packets(args.text)
    elif args.hex is not None:
        try:
            packets = [_hex_to_bytes(args.hex)]
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
    else:
        try:
            packets = _read_binary_packets(args.inputs)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2

    from crcglot import verify

    endianness = "little" if args.little else "big"
    all_valid = True
    for pkt in packets:
        try:
            r = verify(pkt, args.algorithm, endianness=endianness,
                       encoding=args.encoding)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        hexw = (r.width + 3) // 4
        status = "VALID" if r.valid else "INVALID"
        print(
            f"{status}  {args.algorithm}  expected=0x{r.expected:0{hexw}X}"
            f"  actual=0x{r.actual:0{hexw}X}"
        )
        all_valid = all_valid and r.valid
    return 0 if all_valid else 1


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
            f"Error: {unknown_algorithm_error(args.algorithm, surface='cli')}",
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
            f"Error: {unknown_algorithm_error(args.algorithm, surface='cli')}",
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


def _cmd_version(args: argparse.Namespace) -> int:
    """Run the ``crcglot version`` subcommand.

    Prints ``crcglot.__version__`` -- the same string stamped into the
    ``Reproduce with crcglot`` block of generated code, so a user can confirm
    which release produced a file.

    Args:
        args: Parsed argparse namespace (unused; included for the uniform
            dispatch shape).

    Returns:
        Always ``0``.
    """
    del args  # uniform handler shape; nothing to read from the namespace.
    import crcglot

    print(crcglot.__version__)
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

    # `name=` and `file=` both name the output (the one naming knob); `file=`
    # additionally writes to disk.  They therefore can't disagree.
    if (
        file_stem is not None and name_override is not None
        and file_stem != name_override
    ):
        print(
            "Error: name= and file= both name the output; use file= to write "
            "to disk, name= for stdout, and symbol= to override just the "
            "identifier",
            file=sys.stderr,
        )
        return 2
    gen_name = file_stem if file_stem is not None else name_override

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
        # No width pre-check here: custom_algorithm validates the full 1..64
        # range plus the field ranges, so the CLI supports every width the
        # generators do (sub-byte and non-byte-aligned included) and reports
        # the same message as the Python API.
        try:
            gen_custom = custom_algorithm(
                width=width, poly=poly, init=init, refin=refin, refout=refout,
                xorout=xorout, desc=kv.get("desc", ""),
            )
        except ValueError as e:
            print(f"Error: custom CRC param: {e}", file=sys.stderr)
            return 2
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
                f"Error: {unknown_algorithm_error(unknown[0], surface='cli')}",
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
            name=gen_name,
            symbol=symbol_override,
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
            # Terminate the file with a trailing newline, matching the stdout
            # path below (and POSIX text-file convention); the generators join
            # lines without a trailing newline, so add one when absent.
            content = f.content if f.content.endswith("\n") else f.content + "\n"
            path.write_text(content, encoding="utf-8")
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

    # crcglot vectors <name> [--json]
    p_vectors = subs.add_parser(
        "vectors",
        help="Show the self-test vectors (independent goldens) for one algorithm",
    )
    p_vectors.add_argument("name", help="Algorithm name (e.g. crc32)")
    p_vectors.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON with each input's bytes and expected CRC",
    )

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
        "--form", metavar="GLOB",
        help="fnmatch glob over named payload forms -- recognise a CRC wrapped "
             "in a text/JSON frame, e.g. a crclink frame 'crclink' (default: "
             "try all forms)",
    )
    p_detect.add_argument(
        "--width", type=int, metavar="BITS",
        help="restrict the scan to algorithms of this CRC bit width (e.g. 16)",
    )
    p_detect.add_argument(
        "--encoding", default="utf-8",
        help="Encoding for text-mode data portion (default: utf-8)",
    )

    # crcglot reverse [packet.bin ...] [--text TEXT|--hex FRAME ...]
    p_rev = subs.add_parser(
        "reverse",
        help="Recover the parameters of an unknown / custom CRC from "
             "captured packets",
    )
    p_rev.add_argument(
        "inputs", nargs="*",
        help="Binary packet files (or '-' for stdin); ignored with "
             "--text/--hex",
    )
    p_rev.add_argument(
        "--text", metavar="TEXT",
        help="Text packets ('data <sep> hex'; '-' reads one per line "
             "on stdin)",
    )
    p_rev.add_argument(
        "--hex", metavar="FRAME", action="append",
        help="Hex-encoded binary frame; repeat the flag for several frames",
    )
    p_rev.add_argument(
        "--crc-bytes", type=int, default=None, metavar="N",
        help="Trailing CRC field size in bytes (default: auto-detect)",
    )
    p_rev.add_argument(
        "--byte-order", choices=["big", "little", "both"], default="both",
        help="Byte order of the CRC field (default: both)",
    )
    p_rev.add_argument(
        "--std-only", action="store_true",
        help="Only match catalogue algorithms; skip the automatic "
             "escalation to algebraic recovery of a custom polynomial",
    )
    p_rev.add_argument(
        "--encoding", default="utf-8",
        help="Encoding for text-mode data portion (default: utf-8)",
    )

    # crcglot identify [packet.bin ...] [--text TEXT|--hex HEX]
    p_cksum = subs.add_parser(
        "identify",
        help="Identify a non-CRC trailing field in a packet -- checksum or "
             "digest (heads-up; no code gen)",
    )
    p_cksum.add_argument(
        "inputs", nargs="*",
        help="Binary packet files (or '-' for stdin); ignored with --text/--hex",
    )
    p_cksum.add_argument(
        "--text", metavar="TEXT",
        help="Text packet ('data <sep> hex', or '-' for stdin)",
    )
    p_cksum.add_argument(
        "--hex", metavar="HEX", help="Binary packet supplied as a hex string",
    )
    p_cksum.add_argument(
        "--endian", choices=["big", "little", "both"], default="both",
        help="Byte order of the trailing field for 16/32-bit checksums "
             "(default: both)",
    )
    p_cksum.add_argument(
        "--trailers", metavar="GLOB",
        help="fnmatch glob to narrow the candidates (e.g. 'fletcher*', 'sha*')",
    )
    p_cksum.add_argument(
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

    # crcglot verify <algorithm> [packet.bin ...] [--text TEXT|--hex HEX]
    p_ver = subs.add_parser(
        "verify",
        help="Check a frame's trailing CRC against a named algorithm",
    )
    p_ver.add_argument("algorithm", help="Catalogue name (e.g. crc32)")
    p_ver.add_argument(
        "inputs", nargs="*",
        help="Binary packet files (or '-' for stdin); ignored with "
             "--text/--hex",
    )
    p_ver.add_argument(
        "--text", metavar="TEXT",
        help="Text packet ('data <sep> hex', or '-' for stdin)",
    )
    p_ver.add_argument(
        "--hex", metavar="HEX", help="Binary packet supplied as a hex string",
    )
    p_ver.add_argument(
        "--little", action="store_true",
        help="Little-endian CRC field byte order (default: big)",
    )
    p_ver.add_argument(
        "--encoding", default="utf-8",
        help="Encoding for text-mode data portion (default: utf-8)",
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

    # crcglot version
    subs.add_parser(
        "version",
        help="Print the installed crcglot version (as stamped into generated code)",
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
    if args.command == "vectors":
        return _cmd_vectors(args)
    if args.command == "reverse":
        return _cmd_reverse(args)
    if args.command == "verify":
        return _cmd_verify(args)
    if args.command == "detect":
        return _cmd_detect(args)
    if args.command == "identify":
        return _cmd_identify(args)
    if args.command == "encode":
        return _cmd_encode(args)
    if args.command == "compute":
        return _cmd_compute(args)
    if args.command == "credits":
        return _cmd_credits(args)
    if args.command == "version":
        return _cmd_version(args)
    if args.command in LANGUAGES:
        return _cmd_codegen(args, args.command)
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
