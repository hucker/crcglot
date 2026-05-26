def _fix_msys2_path_on_windows() -> None:
    """Ensure msys2's mingw64\\bin precedes Git's mingw64\\bin in PATH.

    Symptom: under Git Bash on Windows, the CRC codegen-exec tests
    silently fail -- gcc returns rc=1 with empty stderr.  The fast
    suite, lint, and ty all stay green; only the subprocess-spawning
    codegen-exec tests fall over.

    Root cause: Git Bash prepends ``C:\\Program Files\\Git\\mingw64\\bin``
    to PATH.  When pytest spawns gcc via Python subprocess, gcc finds
    its sub-tool (cc1.exe) but Windows DLL resolution loads Git's
    older libstdc++-6 / libgcc_s_seh-1 DLLs first -- which are
    incompatible with msys2's gcc 15.x.  cc1.exe fails to load with
    NT status 0xC0000139 (STATUS_ENTRYPOINT_NOT_FOUND); gcc reports
    rc=1 with no diagnostic.

    Fix: prepend C:\\msys64\\mingw64\\bin to PATH for the test
    session, AND warn so the user knows it happened (silent fixes
    hide reality -- the user might wonder why their other tooling
    sees one gcc and pytest sees another).  No-op on Linux/macOS
    (msys2 path doesn't exist) and no-op on Windows shells where
    msys2 is already first (PowerShell, cmd with normal config).
    """
    import os
    import sys
    import warnings
    if sys.platform != "win32":
        return
    msys2_bin = r"C:\msys64\mingw64\bin"
    if not os.path.isdir(msys2_bin):
        return
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    norm = [os.path.normcase(p) for p in parts]
    norm_msys2 = os.path.normcase(msys2_bin)
    # Already first?  No-op.
    if norm and norm[0] == norm_msys2:
        return
    # Detect the specific bad condition: Git's mingw64\bin appears
    # in PATH ahead of msys2's.  Worth a loud warning in that case
    # because (a) the user's interactive gcc and pytest's gcc would
    # resolve to different installs, and (b) a permanent fix is a
    # one-line .bashrc edit.
    git_norm = os.path.normcase(r"C:\Program Files\Git\mingw64\bin")
    git_idx = norm.index(git_norm) if git_norm in norm else -1
    msys2_idx = norm.index(norm_msys2) if norm_msys2 in norm else -1
    if git_idx >= 0 and (msys2_idx < 0 or git_idx < msys2_idx):
        warnings.warn(
            f"crcglot tests: prepending {msys2_bin!r} to PATH for the test "
            f"session.  Git's mingw64\\bin appears in PATH at position "
            f"{git_idx} -- ahead of msys2's at position "
            f"{'absent' if msys2_idx < 0 else msys2_idx}.  Without this fix, "
            f"pytest's gcc subprocess loads Git's libstdc++-6 DLL and "
            f"cc1.exe crashes with NT status 0xC0000139.  Make permanent "
            f"by adding 'export PATH=\"/c/msys64/mingw64/bin:$PATH\"' to "
            f"your .bashrc / .zshrc.",
            RuntimeWarning,
            stacklevel=2,
        )
    # Prepend (drop any existing later occurrence so PATH doesn't grow
    # every test session if conftest reloads).
    parts = [p for p, n in zip(parts, norm) if n != norm_msys2]
    os.environ["PATH"] = os.pathsep.join([msys2_bin] + parts)


def _append_to_path_if_present(candidate: str) -> None:
    """Append ``candidate`` to PATH if it exists and isn't already there.

    Used by the per-tool Windows PATH fixups below.  No-op if the
    directory doesn't exist (other-platform / not-installed cases)
    or if it's already on PATH at any position.
    """
    import os
    if not os.path.isdir(candidate):
        return
    path = os.environ.get("PATH", "")
    parts = path.split(os.pathsep)
    norm = [os.path.normcase(p) for p in parts]
    if os.path.normcase(candidate) in norm:
        return
    os.environ["PATH"] = os.pathsep.join(parts + [candidate])


def _add_windows_tool_dirs_to_path() -> None:
    """Add tool dirs to PATH for Windows installers that don't update it.

    Each entry is a directory that some standard Windows installer of a
    test-time tool drops binaries into without amending PATH, or where
    a winget-installed shim lives that an already-open shell hasn't yet
    refreshed PATH to see:

    - ``C:\\iverilog\\bin``: Icarus Verilog winget / official installer.
    - ``C:\\Program Files\\nodejs``: Node.js LTS winget / MSI.
    - ``C:\\Program Files\\Go\\bin``: Go via winget / official MSI.
    - ``%LOCALAPPDATA%\\Microsoft\\WinGet\\Links``: winget's shim
      directory (archive-distributed tools without their own
      installer land here -- safe to include even if no current
      target depends on it).
    - ``%APPDATA%\\npm``: where ``npm install -g <pkg>`` drops shims
      (tsx, etc.).

    All entries are checked for existence first, so this no-ops on
    Linux/macOS and on Windows shells without the tools installed.
    Without this fixup, the slow-tier tests for any of these tools
    would skip after a fresh install -- pytest's subprocess inherits
    the parent shell's pre-install PATH and can't see the binaries
    that the install just added.
    """
    import os
    import sys
    if sys.platform != "win32":
        return
    appdata = os.environ.get("APPDATA", "")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        r"C:\iverilog\bin",
        r"C:\Program Files\nodejs",
        r"C:\Program Files\Go\bin",
    ]
    if local_appdata:
        candidates.append(
            os.path.join(local_appdata, "Microsoft", "WinGet", "Links")
        )
    if appdata:
        candidates.append(os.path.join(appdata, "npm"))
    for c in candidates:
        _append_to_path_if_present(c)


_fix_msys2_path_on_windows()
_add_windows_tool_dirs_to_path()
