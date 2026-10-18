#!/usr/bin/env python3
"""
ked installer - downloads the latest ked from GitHub and puts it on your PATH.

Usage:
    python install.py                Download and install the latest ked
    python install.py --force        Overwrite an existing install
    python install.py --local        Use a local ked.py instead of downloading
    python install.py --branch NAME  Download from a specific branch (default: main)
    python install.py --no-path      Skip adding the install dir to PATH
    python install.py --uninstall    Remove ked and its PATH entry
    python install.py --keep-path    With --uninstall, leave PATH untouched
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
import stat
import subprocess
import importlib
import urllib.request
import urllib.error
from pathlib import Path

MIN_PY = (3, 8)
INSTALL_NAME = "ked"
RUNTIME_DEPS = [
    ("prompt_toolkit", "prompt_toolkit"),
    ("pygments", "Pygments"),
]

DEFAULT_BRANCH = "main"
GITHUB_RAW = "https://raw.githubusercontent.com/fuckzahru/ked/{branch}/ked.py"
SHEBANG = b"#!/usr/bin/env python3\n"


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
                current, _ = winreg.QueryValueEx(
                    winreg.QueryValue(""), "Path"
                )
            except (FileNotFoundError, OSError):
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
# Download
# --------------------------------------------------------------------------

def download_ked(branch=DEFAULT_BRANCH):
    """Fetch ked.py from GitHub. Returns bytes or None on failure."""
    url = GITHUB_RAW.format(branch=branch)
    print(f"Downloading {url}")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        print(f"Error: HTTP {e.code} for {url}")
        if e.code == 404:
            print(f"Check the branch name (--branch) — '{branch}' may "
                  f"not exist.")
        return None
    except urllib.error.URLError as e:
        print(f"Error: cannot reach GitHub: {e.reason}")
        return None
    except Exception as e:
        print(f"Error downloading: {e}")
        return None

    if not is_windows() and not data.startswith(b"#!"):
        data = SHEBANG + data
    return data


def write_file(dest, data):
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except PermissionError:
        print(f"Error: permission denied writing to {dest}")
        return False
    except OSError as e:
        print(f"Error writing {dest}: {e}")
        return False


# --------------------------------------------------------------------------
# PATH modification
# --------------------------------------------------------------------------

def _broadcast_env_change():
    try:
        import ctypes
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


def _add_to_user_path_windows(directory):
    import winreg
    d = str(Path(directory).resolve())
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0,
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

    _broadcast_env_change()
    return "added"


def _remove_from_user_path_windows(directory):
    import winreg
    d = os.path.normcase(str(Path(directory).resolve()))
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            try:
                current, kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return "not-present"

            entries = [e for e in current.split(";") if e]
            kept = []
            found = False
            for e in entries:
                if os.path.normcase(os.path.expandvars(e)) == d:
                    found = True
                    continue
                kept.append(e)

            if not found:
                return "not-present"

            new_path = ";".join(kept)
            if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                kind = winreg.REG_EXPAND_SZ
            winreg.SetValueEx(key, "Path", 0, kind, new_path)
    except OSError as e:
        return f"error: {e}"

    _broadcast_env_change()
    return "removed"


def _shell_rc_path():
    shell = os.environ.get("SHELL", "")
    if "fish" in shell:
        return Path.home() / ".config" / "fish" / "config.fish"
    return Path.home() / (".zshrc" if "zsh" in shell else ".bashrc")


def _add_to_user_path_unix(directory):
    d = str(Path(directory).resolve())
    shell = os.environ.get("SHELL", "")
    rc = _shell_rc_path()

    if "fish" in shell:
        line = f"fish_add_path {d}\n"
    else:
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


def _remove_from_user_path_unix(directory):
    d = str(Path(directory).resolve())
    rc = _shell_rc_path()
    if not rc.exists():
        return "not-present"

    try:
        lines = rc.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as e:
        return f"error: {e}"

    kept = []
    removed = False
    prev_was_marker = False
    for line in lines:
        stripped = line.strip()
        is_marker = stripped == "# added by ked installer"
        is_entry = (
            f'export PATH="{d}:' in line
            or f"fish_add_path {d}" in line
        )
        if is_entry:
            removed = True
            if prev_was_marker and kept:
                kept.pop()
            prev_was_marker = False
            continue
        kept.append(line)
        prev_was_marker = is_marker

    if not removed:
        return "not-present"

    try:
        rc.write_text("".join(kept), encoding="utf-8")
    except OSError as e:
        return f"error: {e}"

    return "removed"


def add_to_user_path(directory):
    if is_windows():
        return _add_to_user_path_windows(directory)
    return _add_to_user_path_unix(directory)


def remove_from_user_path(directory):
    if is_windows():
        return _remove_from_user_path_windows(directory)
    return _remove_from_user_path_unix(directory)


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
    print(f'Add this line to {_shell_rc_path()}:')
    print(f'  export PATH="{d}:$PATH"')


# --------------------------------------------------------------------------
# Requirements
# --------------------------------------------------------------------------

def check_python():
    if sys.version_info < MIN_PY:
        print(f"Error: Python {MIN_PY[0]}.{MIN_PY[1]}+ is required. "
              f"Found {sys.version.split()[0]}.")
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


def install(system=False, prefix=None, force=False, add_path=True,
            local=False, branch=DEFAULT_BRANCH):
    if not check_python():
        return 1

    if not ensure_runtime_deps():
        print("Continuing anyway; ked will warn about missing modules at "
              "startup.", file=sys.stderr)

    install_dir = default_install_dir(system=system, prefix=prefix)

    if not ensure_dir(install_dir):
        print(f"Error: cannot create {install_dir} (permission denied)")
        if not system and not is_windows():
            print("Try: sudo python3 install.py --system")
        return 1

    # ---- obtain ked.py --------------------------------------------------
    if local:
        src = source_path()
        if not src.is_file():
            print(f"Error: --local requested but {src} does not exist.")
            print("Run install.py from the same directory as ked.py, "
                  "or omit --local to download from GitHub.")
            return 1
        print(f"Using local file: {src}")
        data = src.read_bytes()
        if not is_windows() and not data.startswith(b"#!"):
            data = SHEBANG + data
    else:
        data = download_ked(branch=branch)
        if data is None:
            src = source_path()
            if src.is_file():
                print(f"Download failed. Falling back to local file: {src}")
                data = src.read_bytes()
                if not is_windows() and not data.startswith(b"#!"):
                    data = SHEBANG + data
            else:
                print("No local ked.py to fall back to. Install aborted.")
                return 1

    # ---- install --------------------------------------------------------
    if is_windows():
        target_script = install_dir / "ked.py"
        target_wrapper = install_dir / "ked.cmd"

        if target_script.exists() and not force:
            print(f"ked is already installed at {target_script}")
            print("Use --force to overwrite.")
            return 1

        if not write_file(target_script, data):
            return 1

        try:
            wrapper = (
                "@echo off\r\n"
                f'"{sys.executable}" "%~dp0ked.py" %*\r\n'
            )
            target_wrapper.write_text(wrapper, encoding="utf-8", newline="")
        except PermissionError:
            print(f"Error: permission denied writing to {target_wrapper}")
            return 1

        print(f"Installed: {target_script}")
        print(f"Wrapper:   {target_wrapper}")
    else:
        target = install_dir / INSTALL_NAME

        if target.exists() and not force:
            print(f"ked is already installed at {target}")
            print("Use --force to overwrite.")
            return 1

        if not write_file(target, data):
            return 1

        try:
            st = target.stat()
            os.chmod(
                target,
                st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
            )
        except PermissionError:
            print(f"Warning: could not make {target} executable.")
            print("Try: chmod +x " + str(target))

        print(f"Installed: {target}")

    # ---- PATH handling --------------------------------------------------
    if is_in_path(install_dir):
        print(f"\n{install_dir} is already on PATH for this terminal.")
    elif system:
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
        print(f"\nSkipped PATH update (--no-path). "
              f"{install_dir} is not on PATH.")
        print_manual_path_hint(install_dir)

    print(f"\nWrapper interpreter: {sys.executable}")
    print("If you upgrade or move Python, re-run install.py.")
    return 0


def uninstall(system=False, prefix=None, remove_path=True):
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

    try:
        install_dir.rmdir()
        print(f"Removed empty directory {install_dir}")
    except OSError:
        pass

    if not remove_path:
        print("Skipped PATH cleanup (--keep-path).")
    elif system:
        print(f"Note: {install_dir} was left in the system PATH.")
        print("Remove it manually if you added it.")
    else:
        result = remove_from_user_path(install_dir)
        if result == "removed":
            print(f"Removed {install_dir} from your user PATH.")
            print("Close and reopen your terminal for the change to apply.")
        elif result == "not-present":
            print(f"{install_dir} was not in your user PATH.")
        else:
            print(f"Could not update PATH ({result}).")
            print_manual_path_hint(install_dir)

    print("\nRuntime dependencies (prompt_toolkit, pygments) were left "
          "installed.")
    print("To remove them: "
          f"{sys.executable} -m pip uninstall prompt_toolkit pygments")
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
    installed = target.exists()
    print(f"Installed: {'yes' if installed else 'no'}")

    live = is_in_path(install_dir)
    if is_windows():
        persisted = is_in_user_path_windows(install_dir)
    else:
        rc = _shell_rc_path()
        persisted = (
            rc.exists()
            and str(install_dir) in rc.read_text(
                encoding="utf-8", errors="replace"
            )
        )

    print(f"On PATH (current session): {'yes' if live else 'no'}")
    if not live and persisted:
        print("On PATH (persisted): yes -- close and reopen your terminal.")
    elif not live:
        print("On PATH (persisted): no")

    return 0 if ok and installed and live else 1


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

    branch = DEFAULT_BRANCH
    if "--branch" in args:
        i = args.index("--branch")
        if i + 1 >= len(args):
            print("Error: --branch needs a name")
            return 2
        branch = args[i + 1]

    system = "--system" in args
    force = "--force" in args or "-f" in args
    add_path = "--no-path" not in args
    local = "--local" in args
    keep_path = "--keep-path" in args

    if "--check" in args:
        return check()

    if "--uninstall" in args:
        return uninstall(
            system=system,
            prefix=prefix,
            remove_path=not keep_path,
        )

    return install(
        system=system,
        prefix=prefix,
        force=force,
        add_path=add_path,
        local=local,
        branch=branch,
    )


if __name__ == "__main__":
    sys.exit(main() or 0)