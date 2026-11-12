"""System check: is everything Sleepy Proton Drive needs installed and working?
`--fix` installs what's missing (Windows: winget; Linux: the Bash checker does it)."""

import os
import sys
from pathlib import Path

from . import VERSION, scheduler
from .config import ConfigError, Settings
from .osutil import DATA_DIR, DEFAULT_CONFIG, IS_WINDOWS, PACKAGE_PARENT, find_exe, run_quiet

OK, WARN, BAD = "[ OK ]", "[WARN]", "[FAIL]"


def line(tag, msg):
    print(f"  {tag} {msg}", flush=True)


def winget_install(package_id: str) -> bool:
    winget = find_exe("winget")
    if not winget:
        line(BAD, "winget (App Installer) isn't available. Install it from the Microsoft Store, then try again.")
        return False
    print(f"  Installing {package_id} with winget ...", flush=True)
    r = run_quiet([winget, "install", "--id", package_id, "--exact", "--silent", "--scope", "user",
                   "--accept-package-agreements", "--accept-source-agreements"], timeout=900)
    if r.returncode != 0:  # some packages have no per-user installer
        r = run_quiet([winget, "install", "--id", package_id, "--exact", "--silent",
                       "--accept-package-agreements", "--accept-source-agreements"], timeout=900)
    return r.returncode == 0 or "already installed" in (r.stdout + r.stderr).lower()


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "-h" in argv or "--help" in argv:
        print("Usage: sleepy-proton-check [--fix]\n\n  --fix   Install anything that's missing")
        return 0
    if not IS_WINDOWS:
        for c in (Path.home() / ".local" / "bin" / "sleepy-proton-check", PACKAGE_PARENT / "check-requirements.sh"):
            if c.exists() and find_exe("bash"):
                os.execv(find_exe("bash"), ["bash", str(c), *argv])
    fix = "--fix" in argv
    problems = 0
    print(f"Sleepy Proton Drive v{VERSION} - system check")
    print(f"  Python {sys.version.split()[0]} ({sys.executable})")

    rclone = find_exe("rclone")
    if not rclone and fix and IS_WINDOWS and winget_install("Rclone.Rclone"):
        rclone = find_exe("rclone")
    if rclone:
        v = run_quiet([rclone, "version"], timeout=30).stdout.splitlines()
        line(OK, f"rclone: {v[0] if v else rclone}")
    else:
        line(BAD, "rclone is missing" + ("" if fix else " (run with --fix to install it)"))
        problems += 1

    try:
        cfg = Settings(DEFAULT_CONFIG)
        line(OK if DEFAULT_CONFIG.exists() else WARN,
             f"settings: {DEFAULT_CONFIG}" + ("" if DEFAULT_CONFIG.exists() else " (not made yet: open the app's Setup Wizard)"))
    except (ConfigError, OSError) as e:
        line(BAD, f"settings file can't be read: {e}")
        cfg = Settings()
        problems += 1

    if rclone:
        remotes = [r.strip() for r in run_quiet([rclone, "listremotes"], timeout=60).stdout.splitlines()]
        if f"{cfg.remote_name}:" in remotes:
            line(OK, f"Proton Drive sign-in: rclone remote '{cfg.remote_name}' exists")
            r = run_quiet([rclone, "about", f"{cfg.remote_name}:", "--contimeout", "15s", "--timeout", "45s"], timeout=90)
            line(OK if r.returncode == 0 else WARN,
                 "Proton Drive answers" if r.returncode == 0 else "Proton Drive didn't answer (offline, or sign in again)")
        else:
            line(WARN, f"not signed in yet (no rclone remote '{cfg.remote_name}'): use the app's Setup Wizard")

    try:
        import PySide6  # noqa: F401
        line(OK, f"app window (PySide6 {PySide6.__version__})")
    except ImportError:
        venv = DATA_DIR / "venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
        if venv.exists() and run_quiet([str(venv), "-c", "import PySide6"], timeout=60).returncode == 0:
            line(OK, f"app window (PySide6 in {venv.parent.parent})")
        else:
            line(WARN, "PySide6 is missing, so the app window can't open" +
                 (" (run install.cmd again)" if IS_WINDOWS else ""))

    from .photos import find_cli
    cli = find_cli()
    if cfg.photos_enabled or cli:
        line(OK if cli else WARN, f"Proton Photos tool: {cli[-1] if cli else 'not installed (Proton Photos tab -> Install)'}")

    active, interval = scheduler.status()
    line(OK if active else WARN, f"background sync: every {interval}" if active else "background sync is off")

    print("All good." if problems == 0 else f"{problems} problem(s) found.")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
