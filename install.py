#!/usr/bin/env python3
"""
ked installer - puts the ked text editor on your PATH.

Usage:
    python install.py                Install for the current user (adds to PATH)
    python install.py --force        Overwrite an existing install
    python install.py --no-path      Skip adding the install dir to PATH
    python install.py --uninstall    Remove ked
    python install.py --system       Install system-wide (Unix, may need sudo)
    python install.py --prefix DIR   Use a custom install directory
    python install.py --check        Verify Python, deps, install, and PATH
    python install.py --help         Show this help

Default install locations:
    Linux/macOS   ~/.local/bin/ked
    Windows       %LOCALAPPDATA%\\Programs\\ked\\ked.py + ked.cmd

Runtime dependencies (installed automatically if missing):
    prompt_toolkit, pygments
"""

import os
import sys
import shutil
import stat
import subprocess
import importlib
from pathlib import Path

MIN_PY = (3, 8)
INSTALL_NAME = "ked"
RUNTIME_DEPS = [
    ("prompt_toolkit", "prompt_toolkit"),
    ("pygments", "Pygments"),
]


# --------------------------------------------------------------------------
# Environment helpers
# --------------------------------------------------------------------------

def is_windows():
    return os.name == "nt"


def source_path():
    return Path(__file__).resolve().parent / "ked.py"


def default_install_dir(system=False, prefix=None):
    if prefix:
        return Path(prefix).expanduser().resolve()
    if system:
        if is_windows():
            base = os.environ.get("ProgramFiles", r"C:\Program Files")
            return Path(base) / "ked"
        return Path("/usr/local/bin")
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            "~/AppData/Local"
        )
        return Path(base) / "Programs" / "ked"
    return Path.home() / ".local" / "bin"


def is_in_path(directory):
    try:
        d = Path(directory).resolve()
    except OSError:
        return False
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).resolve() == d:
                return True
        except OSError:
            continue
    return False


def is_in_user_path_windows(directory):
    """Check HKCU\\Environment Path (the User PATH), not the live env."""
    try:
        import winreg
    except ImportError:
        return False
    d = os.path.normcase(str(Path(directory).resolve()))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment"):
            try:
                current, _ = winreg.QueryValueEx(winreg.QueryValue(""), "Path")
            except FileNotFoundError:
                return False
            except OSError:
                return False
    except OSError:
        return False
    for entry in current.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        expanded = os.path.expandvars(entry)
        if os.path.normcase(expanded) == d:
            return True
    return False


# --------------------------------------------------------------------------
# PATH modification
# --------------------------------------------------------------------------

def _add_to_user_path_windows(directory):
    """
    Append `directory` to HKCU\\Environment\\Path (REG_SZ or REG_EXPAND_SZ),
    then broadcast WM_SETTINGCHANGE so newly spawned processes see it.

    Returns one of: "added", "already", "error: ...".
    """
    import winreg
    import ctypes

    d = str(Path(directory).resolve())
    key_path = "Environment"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            try:
                current, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, kind = "", winreg.REG_EXPAND_SZ

            entries = [e for e in current.split(";") if e]
            for e in entries:
                if os.path.normcase(os.path.expandvars(e)) == os.path.normcase(d):
                    return "already"

            new_path = ";".join(entries + [d])
            if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                kind = winreg.REG_EXPAND_SZ
            winreg.SetValueEx(key, "Path", 0, kind, new_path)
    except OSError as e:
        return f"error: {e}"

    # Notify running processes (Explorer, new shells) that env changed.
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0,
            "Environment", SMTO_ABORTIFHUNG, 5000,
            ctypes.byref(result),
        )
    except Exception:
        pass

    return "added"


def _add_to_user_path_unix(directory):
    """
    Append an `export PATH` line (or fish_add_path) to the right shell rc.
    Returns one of: "added", "already", "error: ...".
    """
    d = str(Path(directory).resolve())
    shell = os.environ.get("SHELL", "")

    if "fish" in shell:
        rc = Path.home() / ".config" / "fish" / "config.fish"
        line = f"fish_add_path {d}\n"
    else:
        rc = Path.home() / (".zshrc" if "zsh" in shell else ".bashrc")
        line = f'export PATH="{d}:$PATH"\n'

    try:
        existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
    except OSError:
        existing = ""

    if d in existing:
        return "already"

    try:
        rc.parent.mkdir(parents=True, exist_ok=True)
        with rc.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("# added by ked installer\n")
            f.write(line)
    except OSError as e:
        return f"error: {e}"

    return "added"


def add_to_user_path(directory):
    if is_windows():
        return _add_to_user_path_windows(directory)
    return _add_to_user_path_unix(directory)


def print_manual_path_hint(directory):
    d = str(directory)
    if is_windows():
        print("Add it to your user PATH with PowerShell:")
        print(f'  [Environment]::SetEnvironmentVariable('
              f'"Path", $env:Path + ";{d}", "User")')
        return
    shell = os.environ.get("SHELL", "")
    if "fish" in shell:
        print(f"  fish_add_path {d}")
        return
    rc = "~/.zshrc" if "zsh" in shell else "~/.bashrc"
    print(f"Add this line to {rc}:")
    print(f'  export PATH="{d}:$PATH"')


# --------------------------------------------------------------------------
# Requirements
# --------------------------------------------------------------------------

def check_python_and_source():
    if sys.version_info < MIN_PY:
        print(f"Error: Python {MIN_PY[0]}.{MIN_PY[1]}+ is required. "
              f"Found {sys.version.split()[0]}.")
        return False
    src = source_path()
    if not src.is_file():
        print(f"Error: cannot find {src}")
        print("Run install.py from the same directory as ked.py.")
        return False
    return True


def check_runtime_deps(verbose=True):
    missing = []
    for mod, pkg in RUNTIME_DEPS:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if verbose:
        if missing:
            print("Missing runtime dependencies: " + ", ".join(missing))
        else:
            print("Runtime dependencies OK: " +
                  ", ".join(p for _, p in RUNTIME_DEPS))
    return missing


def ensure_runtime_deps():
    missing = check_runtime_deps(verbose=False)
    if not missing:
        return True

    print("Installing runtime dependencies: " + ", ".join(missing))
    base = [sys.executable, "-m", "pip", "install", "--upgrade"]
    attempts = [
        base + missing,
        base + ["--user"] + missing,  # PEP 668 / read-only site-packages
    ]
    last_err = None
    for cmd in attempts:
        try:
            subprocess.check_call(cmd)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            last_err = e

    print("Automatic install failed.", file=sys.stderr)
    print("Install manually:", file=sys.stderr)
    print(f"  {sys.executable} -m pip install {' '.join(missing)}",
          file=sys.stderr)
    if last_err:
        print(f"Last error: {last_err}", file=sys.stderr)
    print("If your system blocks pip (PEP 668), create a virtual "
          "environment and install ked there.", file=sys.stderr)
    return False


# --------------------------------------------------------------------------
# Install / uninstall
# --------------------------------------------------------------------------

def ensure_dir(directory):
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return True
    except PermissionError:
        return False


def install(system=False, prefix=None, force=False, add_path=True):
    if not check_python_and_source():
        return 1

    if not ensure_runtime_deps():
        print("Continuing anyway; ked will warn about missing modules at "
              "startup.", file=sys.stderr)

    src = source_path()
    install_dir = default_install_dir(system=system, prefix=prefix)

    if not ensure_dir(install_dir):
        print(f"Error: cannot create {install_dir} (permission denied)")
        if not system and not is_windows():
            print("Try: sudo python3 install.py --system")
        return 1

    if is_windows():
        target_script = install_dir / "ked.py"
        target_wrapper = install_dir / "ked.cmd"

        if target_script.exists() and not force:
            print(f"ked is already installed at {target_script}")
            print("Use --force to overwrite.")
            return 1

        try:
            shutil.copy2(src, target_script)
            wrapper = (
                "@echo off\r\n"
                f'"{sys.executable}" "%~dp0ked.py" %*\r\n'
            )
            target_wrapper.write_text(wrapper, encoding="utf-8", newline="")
        except PermissionError:
            print(f"Error: permission denied writing to {install_dir}")
            return 1

        print(f"Installed: {target_script}")
        print(f"Wrapper:   {target_wrapper}")
    else:
        target = install_dir / INSTALL_NAME

        if target.exists() and not force:
            print(f"ked is already installed at {target}")
            print("Use --force to overwrite.")
            return 1

        try:
            shutil.copy2(src, target)
            st = target.stat()
            os.chmod(
                target,
                st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
            )
        except PermissionError:
            print(f"Error: permission denied writing to {install_dir}")
            if not system:
                print("Try: sudo python3 install.py --system")
            return 1

        print(f"Installed: {target}")

    # ---- PATH handling --------------------------------------------------
    path_in_live_session = is_in_path(install_dir)

    if path_in_live_session:
        print(f"\n{install_dir} is already on PATH for this terminal.")
    elif system:
        # System-wide installs: assume the user manages PATH.
        print(f"\n{install_dir} is not on your PATH.")
        print_manual_path_hint(install_dir)
    elif add_path:
        result = add_to_user_path(install_dir)
        if result == "added":
            print(f"\nAdded {install_dir} to your user PATH.")
            print("Close and reopen your terminal for 'ked' to be found.")
        elif result == "already":
            print(f"\n{install_dir} was already in your user PATH.")
            print("Close and reopen your terminal if 'ked' isn't found.")
        else:
            print(f"\nCould not update PATH ({result}).")
            print_manual_path_hint(install_dir)
    else:
        print(f"\nSkipped PATH update (--no-path). {install_dir} is not on PATH.")
        print_manual_path_hint(install_dir)

    print(f"\nWrapper interpreter: {sys.executable}")
    print("If you upgrade or move Python, re-run install.py.")
    return 0


def uninstall(system=False, prefix=None, remove_path=False):
    install_dir = default_install_dir(system=system, prefix=prefix)
    names = ["ked.py", "ked.cmd"] if is_windows() else [INSTALL_NAME]
    removed = []
    for name in names:
        p = install_dir / name
        if p.exists():
            try:
                p.unlink()
                removed.append(p)
            except PermissionError:
                print(f"Error: cannot remove {p}")
                if not system and not is_windows():
                    print("Try: sudo python3 install.py --uninstall --system")
                return 1

    if not removed:
        print(f"ked is not installed in {install_dir}")
        return 1

    for p in removed:
        print(f"Removed {p}")
    print("Runtime dependencies (prompt_toolkit, pygments) were left "
          "installed.")
    if remove_path:
        print("Note: the install dir was not removed from PATH. Edit your "
              "PATH manually if you want it gone.")
    return 0


# --------------------------------------------------------------------------
# Check mode
# --------------------------------------------------------------------------

def check():
    ok = True
    print(f"Python: {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < MIN_PY:
        print(f"  ERROR: need Python {MIN_PY[0]}.{MIN_PY[1]}+")
        ok = False

    src = source_path()
    if src.is_file():
        print(f"ked.py: {src}")
    else:
        print(f"ked.py: MISSING (expected {src})")
        ok = False

    missing = check_runtime_deps(verbose=False)
    if missing:
        print("Runtime dependencies:")
        for _, pkg in RUNTIME_DEPS:
            print(f"  {pkg}: {'MISSING' if pkg in missing else 'OK'}")
        ok = False
    else:
        print("Runtime dependencies: OK ("
              + ", ".join(p for _, p in RUNTIME_DEPS) + ")")

    install_dir = default_install_dir()
    target = install_dir / ("ked.py" if is_windows() else INSTALL_NAME)
    print(f"Install directory: {install_dir}")
    print(f"Installed: {'yes' if target.exists() else 'no'}")

    live = is_in_path(install_dir)
    if is_windows():
        persisted = is_in_user_path_windows(install_dir)
    else:
        shell = os.environ.get("SHELL", "")
        if "fish" in shell:
            rc = Path.home() / ".config" / "fish" / "config.fish"
        else:
            rc = Path.home() / (".zshrc" if "zsh" in shell else ".bashrc")
        persisted = rc.exists() and str(install_dir) in rc.read_text(
            encoding="utf-8", errors="replace"
        )

    print(f"On PATH (current session): {'yes' if live else 'no'}")
    if not live and persisted:
        print("On PATH (persisted): yes -- close and reopen your terminal.")
    elif not live:
        print("On PATH (persisted): no")

    return 0 if ok and live else 1


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main():
    args = sys.argv[1:]

    if "-h" in args or "--help" in args:
        print(__doc__.strip())
        return 0

    prefix = None
    if "--prefix" in args:
        i = args.index("--prefix")
        if i + 1 >= len(args):
            print("Error: --prefix needs a directory")
            return 2
        prefix = args[i + 1]

    system = "--system" in args
    force = "--force" in args or "-f" in args
    add_path = "--no-path" not in args

    if "--check" in args:
        return check()

    if "--uninstall" in args:
        return uninstall(system=system, prefix=prefix)

    return install(system=system, prefix=prefix, force=force, add_path=add_path)


if __name__ == "__main__":
    sys.exit(main() or 0)