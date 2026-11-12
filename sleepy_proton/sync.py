"""Sync engine: backs up local folders to Proton Drive with rclone. Supports copy,
sync (mirror) and bisync (two-way), with locking, notifications, logging and a
background timer (systemd on Linux, Task Scheduler on Windows)."""

import datetime as dt
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import VERSION, scheduler
from .config import MODES, ConfigError, Settings, expand_path
from .osutil import (DEFAULT_CONFIG, IS_WINDOWS, POPEN_GROUP, STATE_DIR, Lock, find_exe, kill_tree,
                     module_cmd, notify, pid_alive, run_quiet, same_path, stop_popen, wait_until)

BISYNC_STATE_DIR = STATE_DIR / "bisync"
LOCK = Lock("sleepy-proton-sync")

USAGE = f"""Sleepy Proton Drive sync engine v{VERSION} (The Sleepy Labs)

Usage:
  sleepy-proton-sync [OPTIONS]

Sync options:
  -c, --config PATH        Use another config file (default: {DEFAULT_CONFIG})
  -n, --dry-run            Show what would change without touching any files
  -m, --mode MODE          Override the sync mode for this run (copy, sync, bisync)
  -v, --verbose            Extra rclone debug output
      --no-notify          No desktop notifications for this run

Info:
      --status             Show the current configuration and timer state
      --running            Print "running <PID>" if a sync is running, else "idle"

Control a running sync (works for background and app syncs):
      --stop               Stop the running sync (files already copied are kept)
      --restart            Stop the running sync and start a fresh one in the background
      --version            Show the version
  -h, --help               Show this help

Background timer ({'Task Scheduler' if IS_WINDOWS else 'systemd user timer'}, no admin rights needed):
      --enable-timer [INTERVAL]   Install/update and start the timer (default 30min;
                                  examples: 15min, 30min, 1h, 2h, 1d)
      --disable-timer             Stop and remove the timer
      --timer-status              Print "active <interval>" or "inactive"
"""


class Stopped(Exception):
    pass


# ---------------------------------------------------------------- helpers
def glob_escape(s: str) -> str:
    """Escape rclone glob characters so a folder name is matched literally."""
    return "".join("\\" + c if c in "\\*?[]{}" else c for c in s)


def relative_inside(src: str, ex: str) -> str:
    """Path of excluded folder ex relative to synced folder src ('' if it isn't inside it).
    Checks the path as written and with links resolved."""
    norm = os.path.normcase
    for a, b in ((src, ex), (os.path.realpath(src), os.path.realpath(ex))):
        a = a.rstrip("/\\") or a
        for sep in {"/", os.sep}:
            if norm(b).startswith(norm(a + sep)) and len(b) > len(a) + 1:
                return b[len(a) + 1:].replace("\\", "/")
    return ""


_CRC_TABLE = []
for _i in range(256):
    _c = _i << 24
    for _ in range(8):
        _c = ((_c << 1) ^ 0x04C11DB7) if _c & 0x80000000 else (_c << 1)
    _CRC_TABLE.append(_c & 0xFFFFFFFF)


def posix_cksum(data: bytes) -> int:
    """Same number as the `cksum` command (v1.x named its bisync markers with it)."""
    crc = 0
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) ^ b) & 0xFF]
    n = len(data)
    while n:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) ^ (n & 0xFF)) & 0xFF]
        n >>= 8
    return (~crc) & 0xFFFFFFFF


def dir_is_empty(path: str) -> bool:
    try:
        with os.scandir(path) as it:
            return next(it, None) is None
    except OSError:
        return True


# ---------------------------------------------------------------- stop / restart
def running_pid():
    return LOCK.owner()


def stop_sync() -> str:
    pid = running_pid()
    stopped = False
    if not IS_WINDOWS:
        from .osutil import descendants
        tree = descendants(pid) if pid else []
        if scheduler.systemd_service_active():
            run_quiet(["systemctl", "--user", "stop", "--no-block", f"{scheduler.UNIT_NAME}.service"])
            stopped = True
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
            stopped = True
            if not wait_until(lambda: not pid_alive(pid), 15):
                for p in [pid, *tree]:
                    try:
                        os.kill(p, signal.SIGKILL)
                    except OSError:
                        pass
        for p in tree:
            try:
                os.kill(p, signal.SIGTERM)
            except OSError:
                pass
    elif pid:
        LOCK.request_stop()
        stopped = True
        if not wait_until(lambda: not pid_alive(pid), 15):
            kill_tree(pid, force=True)
    if not running_pid():
        LOCK.release()
    return "Sync stopped. Files already copied are kept." if stopped else "No sync is running."


def start_background_sync(config: Path):
    default = same_path(config, DEFAULT_CONFIG)
    if IS_WINDOWS:
        if default and scheduler.task_installed() and scheduler.run_task_now():
            return
        cmd, env = module_cmd("sync", "--config", str(config), windowless=True)
        subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
                         | subprocess.CREATE_NEW_PROCESS_GROUP)
        return
    if scheduler.have_user_systemd() and default and scheduler.systemd_service_installed():
        run_quiet(["systemctl", "--user", "reset-failed", f"{scheduler.UNIT_NAME}.service"])
        run_quiet(["systemctl", "--user", "start", "--no-block", f"{scheduler.UNIT_NAME}.service"])
        return
    own = os.environ.get("SLEEPY_PROTON_SYNC_BIN")
    if own and Path(own).is_file():
        cmd, env = [own, "--config", str(config)], None
    else:
        cmd, env = module_cmd("sync", "--config", str(config))
    subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


# ---------------------------------------------------------------- the run
class Run:
    def __init__(self, cfg: Settings, config_path: Path, dry_run: bool, verbose: bool):
        self.cfg = cfg
        self.config_path = config_path
        self.dry = dry_run
        self.verbose = verbose
        self.log_file = Path(expand_path(cfg.log_file))
        self.child = None
        self.stop = False
        self.log_lock = threading.Lock()

    def log(self, msg: str):
        line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        self._out(line)

    def _out(self, line: str):
        with self.log_lock:
            try:
                print(line, flush=True)
            except (OSError, ValueError):
                pass
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def notify(self, title, body, urgency="normal"):
        if self.cfg.notify:
            notify(title, body, urgency)

    def check_stop(self):
        if self.stop or (IS_WINDOWS and LOCK.stop_requested()):
            self.stop = True
            raise Stopped()

    def wait_child(self, proc, reader=None, gentle_stop=None) -> int:
        self.child = proc
        try:
            while proc.poll() is None:
                if self.stop or (IS_WINDOWS and LOCK.stop_requested()):
                    if gentle_stop and IS_WINDOWS:
                        gentle_stop()  # lets it save its progress first
                        wait_until(lambda: proc.poll() is not None, 20)
                    stop_popen(proc)
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        kill_tree(proc.pid, force=True)
                    self.stop = True
                    raise Stopped()
                time.sleep(0.2)
            if reader:
                reader.join(timeout=10)
            return proc.returncode
        finally:
            self.child = None

    def rclone(self, rclone_bin, *args) -> int:
        """Runs rclone, copying its output to the screen and the log; Stop ends it right away."""
        self.check_stop()
        try:
            proc = subprocess.Popen([rclone_bin, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, **POPEN_GROUP)
        except OSError as e:
            self.log(f"ERROR: couldn't start rclone: {e}")
            return 1

        def pump():
            for raw in proc.stdout:
                self._out(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        t = threading.Thread(target=pump, daemon=True)
        t.start()
        return self.wait_child(proc, t)

    def trim_log(self):
        try:
            with open(self.log_file, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return
        if len(lines) > self.cfg.log_max_lines:
            keep = lines[-(self.cfg.log_max_lines * 3 // 4):] if self.cfg.log_max_lines >= 4 else []
            tmp = self.log_file.with_name(self.log_file.name + ".trim")
            try:
                tmp.write_text("".join(keep), encoding="utf-8")
                os.replace(tmp, self.log_file)
            except OSError:
                pass

    def main(self) -> int:
        cfg = self.cfg
        label = " (DRY RUN - nothing will change)" if self.dry else ""
        bar = "=" * 72
        self.log(bar)
        self.log(f"Sleepy Proton Drive v{VERSION} - sync started{label}")
        self.log(f"Remote: {cfg.remote_name}: | Device: {cfg.device} | Mode: {cfg.sync_mode}")
        self.log(bar)

        rclone_bin = find_exe("rclone")
        if not rclone_bin:
            self.log("ERROR: rclone is not installed (or not in PATH). Run: sleepy-proton-check --fix")
            self.notify("Proton Drive sync failed", "rclone is not installed.", "critical")
            return 1
        remotes = run_quiet([rclone_bin, "listremotes"], timeout=60).stdout.splitlines()
        if f"{cfg.remote_name}:" not in [r.strip() for r in remotes]:
            self.log(f"ERROR: rclone has no remote called '{cfg.remote_name}'. Run the setup wizard"
                     f"{' (Account Wizard in the app)' if IS_WINDOWS else ': sleepy-proton-wizard'}")
            self.notify("Proton Drive sync failed", "Not logged in yet. Run the setup wizard.", "critical")
            return 1
        if not cfg.sync_dirs and not cfg.photos_enabled:
            self.log("Nothing to do: no folders are selected (SYNC_DIRS is empty).")
            return 0

        flags = list(cfg.rclone_flags)
        if self.verbose:
            flags.append("-vv")
        if self.dry:
            flags.append("--dry-run")

        ok = failed = skipped = 0
        seen = set()
        for raw in cfg.sync_dirs:
            src = expand_path(raw)
            if not os.path.isdir(src):
                self.log(f"WARNING: Skipping '{src}' (folder not found).")
                skipped += 1
                continue
            name = os.path.basename(src.rstrip("/\\")) or src
            key = os.path.normcase(name)
            if key in seen:
                self.log(f"WARNING: Skipping '{src}': another folder called '{name}' is already synced, and both "
                         "would land in the same cloud folder. Rename one of them.")
                skipped += 1
                continue
            seen.add(key)
            dest = f"{cfg.remote_name}:Devices/{cfg.device}/{name}"

            # Safety net: an empty source (e.g. an unmounted drive) would wipe the cloud copy.
            if cfg.sync_mode != "copy" and dir_is_empty(src):
                self.log(f"WARNING: Skipping '{src}': it is empty, and syncing it in '{cfg.sync_mode}' mode "
                         "could delete the cloud copy.")
                skipped += 1
                continue

            self.log(f">>> '{src}' -> '{dest}' [{cfg.sync_mode}]")
            dir_flags = []
            for raw_ex in cfg.exclude_dirs:
                ex = expand_path(raw_ex)
                if not ex:
                    continue
                rel = relative_inside(src, ex)
                if rel:
                    dir_flags += ["--exclude", f"/{glob_escape(rel)}/**"]
                    self.log(f"Excluding folder: '{ex}'")

            if cfg.sync_mode in ("copy", "sync"):
                rc = self.rclone(rclone_bin, cfg.sync_mode, src, dest, *dir_flags, *flags)
            else:
                k = posix_cksum(f"{cfg.remote_name}|{cfg.device}|{src}".encode("utf-8"))
                marker = BISYNC_STATE_DIR / f"{k}.done"
                extra = []
                if not marker.exists():
                    self.log(f"First two-way sync for '{name}': running a one-time --resync to set the baseline.")
                    extra.append("--resync")
                self.rclone(rclone_bin, "mkdir", dest)
                rc = self.rclone(rclone_bin, "bisync", src, dest, *extra, *dir_flags, *flags)
                if rc == 0 and not self.dry:
                    BISYNC_STATE_DIR.mkdir(parents=True, exist_ok=True)
                    marker.touch()
                elif rc != 0:
                    self.log(f"Tip: if rclone says bisync needs a resync, delete '{marker}' and run again.")

            if rc == 0:
                self.log(f"OK: '{name}' done.")
                ok += 1
            else:
                self.log(f"FAILED: '{name}' (rclone exit code {rc}).")
                failed += 1

        if cfg.photos_enabled:
            self.log(">>> Proton Photos")
            self.check_stop()
            args = ["--config", str(self.config_path), "--no-notify"] + (["--dry-run"] if self.dry else [])
            cmd, env = module_cmd("photos", *args)
            try:
                # The photos tool writes to the log itself.
                proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, **POPEN_GROUP)
                from .photos import LOCK as PHOTOS_LOCK
                rc = self.wait_child(proc, gentle_stop=PHOTOS_LOCK.request_stop)
            except OSError as e:
                self.log(f"WARNING: couldn't start Proton Photos: {e}")
                rc = 1
            if rc == 0:
                ok += 1
            else:
                self.log(f"FAILED: Proton Photos (exit code {rc}). See the lines above.")
                failed += 1

        self.log(bar)
        if failed == 0:
            self.log(f"SUCCESS: {ok} folder(s)/Photos synced, {skipped} skipped.{label}")
            if not self.dry:
                self.notify("Proton Drive synced", f"{ok} folder(s) backed up.", "low")
            self.log(bar)
            return 0
        self.log(f"FINISHED WITH ERRORS: {ok} ok, {failed} failed, {skipped} skipped. See {self.log_file}")
        self.notify("Proton Drive sync had errors", f"{failed} folder(s) failed. Open the log for details.")
        self.log(bar)
        return 1


# ---------------------------------------------------------------- command line
def parse_args(argv):
    o = {"config": str(DEFAULT_CONFIG), "dry": False, "verbose": False, "mode": "", "no_notify": False,
         "action": "sync", "interval": "30min"}
    i = 0
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if a in ("-c", "--config"):
            if nxt is None:
                raise SystemExit(f"Error: {a} needs a path")
            o["config"] = nxt
            i += 1
        elif a.startswith("--config="):
            o["config"] = a.split("=", 1)[1]
        elif a in ("-n", "--dry-run"):
            o["dry"] = True
        elif a in ("-m", "--mode"):
            if nxt is None:
                raise SystemExit(f"Error: {a} needs a mode")
            o["mode"] = nxt
            i += 1
        elif a.startswith("--mode="):
            o["mode"] = a.split("=", 1)[1]
        elif a in ("-v", "--verbose"):
            o["verbose"] = True
        elif a == "--no-notify":
            o["no_notify"] = True
        elif a == "--sync-now":
            pass  # accepted for older desktop launchers
        elif a in ("--status", "--disable-timer", "--timer-status", "--running", "--stop", "--restart"):
            o["action"] = a[2:]
        elif a == "--enable-timer":
            o["action"] = "enable-timer"
            if nxt is not None and not nxt.startswith("-"):
                o["interval"] = nxt
                i += 1
        elif a.startswith("--enable-timer="):
            o["action"] = "enable-timer"
            o["interval"] = a.split("=", 1)[1]
        elif a == "--version":
            o["action"] = "version"
        elif a in ("-h", "--help"):
            o["action"] = "help"
        else:
            print(f"Unknown option: {a} (see --help)", file=sys.stderr)
            raise SystemExit(2)
        i += 1
    return o


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        o = parse_args(argv)
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 2
        return e.code
    action = o["action"]
    if action == "version":
        print(f"sleepy-proton-sync {VERSION}")
        return 0
    if action == "help":
        print(USAGE, end="")
        return 0
    if action == "running":
        pid = running_pid()
        print(f"running {pid}" if pid else "idle")
        return 0
    if action == "stop":
        print(stop_sync())
        return 0

    config_path = Path(o["config"])
    try:
        cfg = Settings(config_path)
    except (ConfigError, OSError) as e:
        print(f"Error: couldn't read {config_path}: {e}", file=sys.stderr)
        return 2
    if o["mode"]:
        cfg.sync_mode = o["mode"]
    if o["no_notify"]:
        cfg.notify = False

    if action == "restart":
        stop_sync()
        wait_until(lambda: not running_pid(), 20)
        start_background_sync(config_path)
        print("A fresh sync has started in the background.")
        return 0
    if action == "enable-timer":
        try:
            scheduler.enable(o["interval"], config_path)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        except scheduler.TimerError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(f"Background sync enabled: every {o['interval']} ({scheduler.describe()}).")
        return 0
    if action == "disable-timer":
        scheduler.disable()
        print("Background sync disabled.")
        return 0
    if action == "timer-status":
        active, interval = scheduler.status()
        print(f"active {interval}" if active else "inactive")
        return 0
    if action == "status":
        active, interval = scheduler.status()
        made = "" if config_path.exists() else " (not created yet - run the setup wizard)"
        print(f"Sleepy Proton Drive v{VERSION}")
        print(f"  Config: {config_path}{made}")
        print(f"  Remote: {cfg.remote_name}:")
        print(f"  Device: {cfg.device}  (files go to {cfg.remote_name}:Devices/{cfg.device}/<folder>)")
        print(f"  Mode:   {cfg.sync_mode}")
        print("  Folders:")
        for d in cfg.sync_dirs:
            print(f"    - {expand_path(d)}")
        if cfg.exclude_dirs:
            print("  Excluded folders (never backed up):")
            for d in cfg.exclude_dirs:
                print(f"    - {expand_path(d)}")
        if cfg.photos_enabled:
            print(f"  Proton Photos: on ({cfg.photos_mode or 'copy'}) -> {expand_path(cfg.photos_dir)}")
        else:
            print("  Proton Photos: off")
        print(f"  Log:    {expand_path(cfg.log_file)}")
        print(f"  Timer:  {'active ' + interval if active else 'inactive'}")
        return 0

    # ---- a sync run
    if cfg.sync_mode not in MODES:
        print(f"Error: SYNC_MODE must be copy, sync or bisync (got '{cfg.sync_mode}').", file=sys.stderr)
        return 2
    run = Run(cfg, config_path, o["dry"], o["verbose"])
    run.log_file.parent.mkdir(parents=True, exist_ok=True)
    if not LOCK.acquire():
        run.log(f"Another sync is already running (PID {LOCK.owner()}). Skipping this run.")
        return 0

    def on_signal(*_):
        if run.stop:
            return
        run.stop = True
        if run.child is not None:
            stop_popen(run.child)
        raise Stopped()
    old = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            old[sig] = signal.signal(sig, on_signal)
        except (ValueError, OSError):
            pass
    try:
        run.trim_log()
        return run.main()
    except Stopped:
        run.log("Sync stopped by request.")
        return 143
    except KeyboardInterrupt:
        run.log("Sync stopped by request.")
        return 143
    finally:
        LOCK.release()


if __name__ == "__main__":
    sys.exit(main())
