"""
==============================================================================
The Sleepy Labs // Sleepy Proton Drive - Proton Photos downloader
==============================================================================
Downloads your Proton Photos (photos and videos from your phone, or uploaded
by hand) into a folder on this computer, arranged as Year/Month, using
Proton's official Proton Drive CLI ("proton-drive").

Modes (PHOTOS_MODE in the config file):
  copy   - download new photos; nothing is ever deleted anywhere
  sync   - mirror: this folder matches Proton Photos (photos deleted in
           Proton are moved to this computer's trash / Recycle Bin)
  bisync - two-way: like sync, and photos you delete here are moved to
           Proton's trash (recoverable there for 30 days)
==============================================================================
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import VERSION
from .config import MODES, ConfigError, Settings, expand_path
from .osutil import (CACHE_DIR, DEFAULT_CONFIG, EXE, IS_WINDOWS, POPEN_GROUP, STATE_DIR, USER_BIN, Lock,
                     find_exe, notify, run_quiet, stop_popen, trash)

PHOTOS_STATE_DIR = STATE_DIR / "photos"
INDEX_FILE = CACHE_DIR / "photos-index.json"
LOCK = Lock("sleepy-proton-photos")
STAGING_NAME = ".sleepy-proton-staging"

# Proton Photos categories (the tag numbers Proton uses) + "other" = no category.
CATEGORIES = {
    "favorites": 0, "screenshots": 1, "videos": 2, "live-photos": 3,
    "motion-photos": 4, "selfies": 5, "portraits": 6, "bursts": 7,
    "panoramas": 8, "raw": 9,
}
OTHER = "other"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
BATCH = 25
MASS_DELETE_MIN = int(os.environ.get("SLEEPY_PHOTOS_MASS_MIN", "20"))  # never block fewer deletions than this...
MASS_DELETE_SHARE = 0.25  # ...but block more than 25% of tracked photos in one run


class Stop(Exception):
    pass


class Config:
    def __init__(self, path: Path):
        try:
            s = Settings(path)
        except (ConfigError, OSError) as e:
            raise SystemExit(f"Error: couldn't read {path} ({e})")
        self.enabled = s.photos_enabled
        self.dir = Path(expand_path(s.photos_dir)).absolute()
        self.mode = s.photos_mode if s.photos_mode in MODES else "copy"
        self.notify = s.notify
        self.log_file = Path(expand_path(s.log_file))
        self.categories = list(s.photos_categories)
        self.dates = list(s.photos_dates)


def name_key(name: str) -> str:
    """File names that clash on this computer (Windows ignores upper/lower case)."""
    return name.lower() if IS_WINDOWS else name


class Runner:
    def __init__(self, cfg: Config, dry_run: bool, allow_mass_delete: bool, quiet_notify: bool):
        self.cfg = cfg
        self.dry = dry_run
        self.allow_mass = allow_mass_delete
        self.notify_on = cfg.notify and not quiet_notify
        self.child = None
        self.stopping = False
        self.cli = find_cli()
        key = hashlib.sha1(str(cfg.dir).encode()).hexdigest()[:12]
        self.state_file = PHOTOS_STATE_DIR / f"state-{key}.json"
        self.state = {"version": 1, "dir": str(cfg.dir), "photos": {}}
        self.counts = {"downloaded": 0, "failed": 0, "trashed_here": 0, "trashed_proton": 0}

    # ---------------------------------------------------------------- logging
    def log(self, msg: str):
        line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        try:
            print(line, flush=True)
        except (OSError, ValueError):
            pass
        try:
            self.cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cfg.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    def notify(self, title, body, urgency="normal"):
        if self.notify_on:
            notify(title, body, urgency)

    # -------------------------------------------------------------- the CLI
    def cli_run(self, args, timeout=3600):
        if not self.cli:
            raise RuntimeError("Proton's 'proton-drive' tool isn't installed. Install it from the app "
                               "(Proton Photos tab) or with: sleepy-proton-photos --install-cli")
        # Output goes to temporary files (the photo list can be big), and we check for Stop while waiting.
        with tempfile.TemporaryFile() as fo, tempfile.TemporaryFile() as fe:
            self.child = subprocess.Popen([*self.cli, *args], stdin=subprocess.DEVNULL, stdout=fo, stderr=fe,
                                          **POPEN_GROUP)
            end = time.monotonic() + timeout
            try:
                while self.child.poll() is None:
                    if self.stopping or (IS_WINDOWS and LOCK.stop_requested()):
                        stop_popen(self.child)
                        try:
                            self.child.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            self.child.kill()
                        raise Stop()
                    if time.monotonic() > end:
                        stop_popen(self.child)
                        try:
                            self.child.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            self.child.kill()
                        raise RuntimeError(f"proton-drive {' '.join(args[:2])} took too long and was stopped.")
                    time.sleep(0.2)
                rc = self.child.returncode
            finally:
                self.child = None
            fo.seek(0)
            fe.seek(0)
            out = fo.read().decode("utf-8", errors="replace")
            err = fe.read().decode("utf-8", errors="replace")
        if "You need to login first" in (out + err):
            raise RuntimeError("Not signed in to Proton Photos. Click 'Sign In' on the Proton Photos tab "
                               "(or run: proton-drive auth login).")
        return rc, out, err

    def cli_json(self, args, timeout=3600):
        rc, out, err = self.cli_run([*args, "--json"], timeout)
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            msg = (err or out).strip().splitlines()
            raise RuntimeError(f"proton-drive {' '.join(args[:2])} failed: {msg[-1] if msg else 'exit ' + str(rc)}")

    def stop_child(self):
        self.stopping = True
        stop_popen(self.child)

    # ------------------------------------------------------------- state
    def load_state(self):
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(data.get("photos"), dict):
                self.state = data
        except (OSError, ValueError):
            pass

    def save_state(self):
        if self.dry:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=0), encoding="utf-8")
        os.replace(tmp, self.state_file)

    # ------------------------------------------------------------- helpers
    def selected(self, item) -> bool:
        tags = set(item.get("tags") or [])
        cats = set(self.cfg.categories)
        if cats and "all" not in cats:
            wanted = {CATEGORIES[c] for c in cats if c in CATEGORIES}
            if not (tags & wanted or (OTHER in cats and not tags)):
                return False
        if self.cfg.dates:
            month = local_month(item.get("captureTime"))
            if not any(month == d or month.startswith(d + "-") for d in self.cfg.dates):
                return False
        return True

    def too_many(self, n: int, what: str) -> bool:
        tracked = len(self.state["photos"])
        limit = max(MASS_DELETE_MIN, int(tracked * MASS_DELETE_SHARE))
        if n > limit and not self.allow_mass:
            self.log(f"SAFETY STOP: {n} photos would be {what} in one go (limit {limit}). Nothing was deleted. "
                     "If this is really what you want, run: sleepy-proton-photos --allow-mass-delete")
            self.notify("Proton Photos: deletions paused",
                        f"{n} photos would be {what}. Nothing was deleted; check the log.", "critical")
            return True
        return False

    # ------------------------------------------------------------- main run
    def index(self, timeline):
        tags, months, other = {}, {}, 0
        for it in timeline:
            t = it.get("tags") or []
            if not t:
                other += 1
            for x in set(t):
                tags[str(x)] = tags.get(str(x), 0) + 1
            m = local_month(it.get("captureTime"))
            months[m] = months.get(m, 0) + 1
        data = {"updated": dt.datetime.now().isoformat(timespec="seconds"), "total": len(timeline),
                "tags": tags, "other": other, "months": dict(sorted(months.items(), reverse=True))}
        INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = INDEX_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, INDEX_FILE)
        return data

    def run(self, index_only=False) -> int:
        cfg = self.cfg
        label = " (DRY RUN - nothing will change)" if self.dry else ""
        self.log(f"Proton Photos v{VERSION} started{label} | Mode: {cfg.mode} | Folder: {cfg.dir}")
        if cfg.categories or cfg.dates:
            self.log(f"Only: categories={','.join(cfg.categories) or 'all'} dates={','.join(cfg.dates) or 'all'}")

        timeline = self.cli_json(["photo", "timeline"], timeout=900)
        if not isinstance(timeline, list):
            raise RuntimeError("Proton sent an unexpected photo list. Nothing was changed.")
        timeline = [t for t in timeline if t.get("nodeUid")]
        idx = self.index(timeline)
        self.log(f"Proton Photos has {idx['total']} photos and videos.")
        if index_only:
            return 0

        self.load_state()
        photos = self.state["photos"]
        remote = {t["nodeUid"]: t for t in timeline}
        root = cfg.dir

        tracked_files = [u for u, p in photos.items() if p.get("path")]
        if tracked_files and (not root.is_dir() or not any(root.iterdir())):
            self.log(f"WARNING: '{root}' is missing or empty but should hold {len(tracked_files)} photos "
                     "(unplugged drive?). Skipping this run so nothing gets deleted. If you removed it on "
                     "purpose, run: sleepy-proton-photos --reset")
            return 1
        if photos and not timeline:
            self.log("WARNING: Proton returned no photos at all. Skipping deletions to be safe.")
            return 1

        # 1. Deleted in Proton
        gone = [u for u in photos if u not in remote]
        if gone:
            if cfg.mode == "copy":
                self.log(f"{len(gone)} photo(s) were deleted in Proton; copy mode keeps them here.")
                for u in gone:
                    photos.pop(u, None)
            elif not self.too_many(len(gone), "deleted from this computer"):
                for u in gone:
                    rel = photos[u].get("path")
                    if rel and (root / rel).exists():
                        self.log(f"Deleted in Proton -> moving to this computer's trash: {rel}")
                        if not self.dry:
                            trash(root / rel)
                        self.counts["trashed_here"] += 1
                    if not self.dry:
                        photos.pop(u, None)
                self.save_state()

        # 2. Deleted here
        missing = [u for u, p in photos.items() if p.get("path") and u in remote and not (root / p["path"]).exists()]
        if missing:
            if cfg.mode == "bisync":
                if not self.too_many(len(missing), "moved to Proton's trash"):
                    for i in range(0, len(missing), BATCH):
                        chunk = missing[i:i + BATCH]
                        for u in chunk:
                            self.log(f"Deleted here -> moving to Proton's trash: {photos[u]['path']}")
                        if not self.dry:
                            rc, out, err = self.cli_run(["filesystem", "trash", *[f"/photos/{u}" for u in chunk]], timeout=600)
                            if rc != 0:
                                self.log(f"FAILED to move {len(chunk)} photo(s) to Proton's trash: {(err or out).strip()[-300:]}")
                                self.counts["failed"] += len(chunk)
                                continue
                            for u in chunk:
                                photos.pop(u, None)
                                remote.pop(u, None)  # now in Proton's trash: don't download it again below
                            self.save_state()
                        self.counts["trashed_proton"] += len(chunk)
            elif cfg.mode == "copy":
                # Copy never deletes, and doesn't bring back photos you deleted here.
                for u in missing:
                    photos[u] = {"path": None, "deleted_here": True}
                self.log(f"{len(missing)} downloaded photo(s) were deleted here; copy mode won't download them again.")
            else:  # sync: this folder matches Proton -> download again
                self.log(f"{len(missing)} photo(s) deleted here will be downloaded again (mirror mode).")
                for u in missing:
                    photos.pop(u, None)
            self.save_state()

        # 3. No longer selected (categories/dates changed): stop tracking, keep the files.
        for u in [u for u in photos if u in remote and not self.selected(remote[u])]:
            photos.pop(u, None)

        # 4. Download what's new
        wanted = [u for u, t in remote.items() if self.selected(t) and u not in photos]
        if not wanted:
            self.log("Proton Photos: nothing new to download.")
        else:
            self.log(f"{len(wanted)} photo(s) to download.")
            self.download(wanted, remote)
        self.save_state()

        c = self.counts
        summary = (f"{c['downloaded']} downloaded, {c['trashed_here']} moved to this computer's trash, "
                   f"{c['trashed_proton']} moved to Proton's trash, {c['failed']} failed")
        if c["failed"]:
            self.log(f"Proton Photos FINISHED WITH ERRORS: {summary}.{label}")
            self.notify("Proton Photos had errors", f"{c['failed']} photo(s) failed. Open the log for details.")
            return 1
        self.log(f"Proton Photos OK: {summary}.{label}")
        if c["downloaded"] and not self.dry:
            self.notify("Proton Photos downloaded", f"{c['downloaded']} new photo(s) saved.", "low")
        return 0

    def download(self, wanted, remote):
        root = self.cfg.dir
        self.log("Getting file names from Proton (this can take a minute)...")
        details = self.cli_json(["photo", "timeline", "--load-details"], timeout=1800)
        info = {}
        for d in details:
            uid = d.get("uid")
            name = (d.get("name") or {}).get("value") if (d.get("name") or {}).get("ok") else None
            if uid:
                rev = d.get("activeRevision") or {}
                info[uid] = {"name": safe_name(name or uid.split("~")[-1][:16]),
                             "sha1": ((rev.get("claimedDigests") or {}).get("sha1"))}

        taken = {name_key(p["path"]) for p in self.state["photos"].values() if p.get("path")}
        plan = []
        for u in sorted(wanted, key=lambda x: remote[x].get("captureTime") or ""):
            if u not in info:
                self.log(f"WARNING: no details for one photo ({u[-12:]}); skipping it this time.")
                continue
            folder = month_folder(remote[u].get("captureTime"))
            rel = unique_rel(root, folder, info[u]["name"], taken)
            taken.add(name_key(rel))
            plan.append((u, rel))

        if self.dry:
            for u, rel in plan[:50]:
                self.log(f"Would download: {rel}")
            if len(plan) > 50:
                self.log(f"... and {len(plan) - 50} more.")
            self.counts["downloaded"] = len(plan)
            return

        staging = root / STAGING_NAME
        # Batches whose Proton names are all different, so each file can be matched to its photo.
        batches, cur, names = [], [], set()
        for u, rel in plan:
            n = name_key(info[u]["name"])
            if len(cur) >= BATCH or n in names:
                batches.append(cur)
                cur, names = [], set()
            cur.append((u, rel))
            names.add(n)
        if cur:
            batches.append(cur)

        for batch in batches:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            self.cli_run(["photo", "download", "--conflict-strategy", "skip", "--json",
                          *[f"/photos/{u}" for u, _ in batch], str(staging)], timeout=6 * 3600)
            found = match_downloads(staging, [(u, info[u]["name"]) for u, _ in batch])
            for u, rel in batch:
                src = found.get(u)
                if src is None:
                    self.log(f"FAILED to download: {rel}")
                    self.counts["failed"] += 1
                    continue
                dst = root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)
                set_mtime(dst, capture_ts(remote[u].get("captureTime")))
                self.state["photos"][u] = {"path": rel, "sha1": info[u]["sha1"],
                                           "captured": remote[u].get("captureTime")}
                self.counts["downloaded"] += 1
            self.save_state()
            self.log(f"Downloaded {self.counts['downloaded']} of {len(plan)}...")
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------- utilities
def find_cli():
    """The command (as a list) that runs Proton's tool, or None."""
    for c in (os.environ.get("PROTON_DRIVE_BIN"), find_exe("proton-drive"), str(USER_BIN / ("proton-drive" + EXE))):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            if c.lower().endswith(".py"):
                return [sys.executable, c]  # stand-in tool used by the tests
            return [c]
    return None


def match_downloads(staging: Path, items):
    """{photo uid: downloaded file} for one batch. Exact names first, then names the tool had
    to change (e.g. characters Windows doesn't allow), then a single leftover pair."""
    try:
        files = [f for f in staging.iterdir() if f.is_file()]
    except OSError:
        return {}
    by_name = {name_key(f.name): f for f in files}
    by_safe = {name_key(safe_name(f.name)): f for f in files}
    found, used = {}, set()
    for u, n in items:
        f = by_name.get(name_key(n)) or by_safe.get(name_key(safe_name(n)))
        if f is not None and f not in used:
            found[u] = f
            used.add(f)
    left_items = [u for u, _ in items if u not in found]
    left_files = [f for f in files if f not in used]
    if len(left_items) == 1 and len(left_files) == 1:
        found[left_items[0]] = left_files[0]
    return found


def parse_time(s):
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    try:
        return t.astimezone()
    except (OSError, OverflowError, ValueError):
        return t  # very old dates can't be converted on Windows; keep the original time zone


def local_month(s) -> str:
    t = parse_time(s)
    return f"{t:%Y-%m}" if t else "undated"


def capture_ts(s):
    t = parse_time(s)
    try:
        return t.timestamp() if t else None
    except (OSError, OverflowError, ValueError):
        return None


def set_mtime(path: Path, ts):
    if ts is None:
        return
    try:
        os.utime(path, (ts, ts))
    except (OSError, OverflowError, ValueError):
        pass


def month_folder(s) -> str:
    t = parse_time(s)
    if not t:
        return "Undated"
    return f"{t.year}/{t.month:02d} {MONTHS[t.month - 1]}"


_WIN_BAD = set('<>:"/\\|?*')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name: str) -> str:
    if IS_WINDOWS:
        name = "".join("_" if c in _WIN_BAD or ord(c) < 32 else c for c in name).strip().rstrip(". ")
        if name.split(".")[0].upper() in _WIN_RESERVED:
            name = "_" + name
    else:
        name = "".join("_" if c in '/\0' or ord(c) < 32 else c for c in name).strip()
    return name if name not in ("", ".", "..") else "photo"


def unique_rel(root: Path, folder: str, name: str, taken: set) -> str:
    stem, ext = os.path.splitext(name)
    rel, n = f"{folder}/{name}", 2
    while name_key(rel) in taken or (root / rel).exists():
        rel = f"{folder}/{stem} ({n}){ext}"
        n += 1
    return rel


# ---------------------------------------------------------------- CLI install
CLI_VERSION = "0.9.0"
CLI_DOWNLOADS = {
    # (system, machine): (folder on proton.me, file name, SHA-512 published by Proton)
    ("linux", "x86_64"): ("linux-x64", "proton-drive",
                          "3533025ba69ae112b64e3e01fbcc1ad0688136a4043f6cf6a72886967d85fdcd9ec235479c2e113171614be5225bfba93427509a05ca5aa6071d924fa7e91ca8"),
    ("windows", "amd64"): ("windows-x64", "proton-drive.exe",
                           "c68f2725b21bd8eddf83b816a059beeab4f1d2f0bb459e6bb53d1b1b972ea971e9d9f191d9cb2aa41f6560c04936aa60f28bb6382191cb0e4e51dd41d36c8bed"),
    ("windows", "arm64"): ("windows-arm64", "proton-drive.exe",
                           "dbb64152dfb3b180f3f49c3e0a33d7daf159b42b041e0aa83b63fd008cadac4a7bedd252014c426e0a9c06a2c5d355b88a697acc2dcfc32543ecaa6cad9cc0fd"),
}
CLI_DOWNLOADS[("windows", "x86_64")] = CLI_DOWNLOADS[("windows", "amd64")]


def cli_download():
    """(url, file name, sha512) for this computer, or None."""
    key = (platform.system().lower(), platform.machine().lower())
    if IS_WINDOWS:
        # An x64 Python on an ARM PC reports AMD64; ask Windows for the real CPU.
        real = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").lower()
        if real == "arm64":
            key = ("windows", "arm64")
    hit = CLI_DOWNLOADS.get(key)
    if not hit:
        return None
    folder, fname, sha = hit
    return f"https://proton.me/download/drive/cli/{CLI_VERSION}/{folder}/{fname}", fname, sha


def install_cli() -> int:
    hit = cli_download()
    if not hit:
        print(f"No verified download for this computer type ({platform.system()} {platform.machine()}). "
              "Get it from https://proton.me/download/drive/cli/index.html")
        return 1
    url, fname, sha = hit
    dest = USER_BIN / fname
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(fname + ".download")
    print(f"Downloading Proton Drive CLI {CLI_VERSION} from proton.me ...", flush=True)
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" if IS_WINDOWS else "Mozilla/5.0 (X11; Linux x86_64)"
    h = hashlib.sha512()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got, last = 0, -10
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                pct = got * 100 // total if total else 0
                if total and pct >= last + 10:
                    print(f"  {pct}% ({got // (1 << 20)} MB)", flush=True)
                    last = pct
    except OSError as e:
        tmp.unlink(missing_ok=True)
        print(f"Download failed: {e}")
        return 1
    if h.hexdigest() != sha:
        tmp.unlink(missing_ok=True)
        print("The download doesn't match Proton's published checksum, so it was deleted. Nothing installed.")
        return 1
    if not IS_WINDOWS:
        tmp.chmod(0o755)
    os.replace(tmp, dest)
    print(f"Installed: {dest} (checksum verified). Next: sign in with 'proton-drive auth login'.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sleepy-proton-photos",
                                 description="Download Proton Photos to this computer (Sleepy Proton Drive).")
    ap.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help=f"config file (default {DEFAULT_CONFIG})")
    ap.add_argument("-n", "--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("-m", "--mode", choices=list(MODES), help="override the photos mode for this run")
    ap.add_argument("--index-only", action="store_true", help="only refresh the photo counts shown in the app")
    ap.add_argument("--allow-mass-delete", action="store_true", help="allow more deletions than the safety limit")
    ap.add_argument("--no-notify", action="store_true", help="no desktop notifications")
    ap.add_argument("--status", action="store_true", help="show whether the Proton tool is installed and signed in")
    ap.add_argument("--install-cli", action="store_true", help="download and install Proton's proton-drive tool")
    ap.add_argument("--reset", action="store_true", help="forget which photos were downloaded (files are kept)")
    ap.add_argument("--stop", action="store_true", help="stop a running Proton Photos download")
    ap.add_argument("--version", action="version", version=f"sleepy-proton-photos {VERSION}")
    a = ap.parse_args(argv)

    if a.install_cli:
        return install_cli()
    if a.stop:
        return stop_photos()
    cfg = Config(Path(a.config))
    if a.mode:
        cfg.mode = a.mode
    if a.status:
        cli = find_cli()
        if not cli:
            print("cli missing")
            return 1
        r = run_quiet([*cli, "filesystem", "list", "/"], timeout=120, stdin=subprocess.DEVNULL)
        print("signed-in" if r.returncode == 0 and "/photos" in r.stdout else "signed-out")
        return 0

    if not LOCK.acquire():
        print(f"Proton Photos is already running (PID {LOCK.owner()}). Skipping this run.")
        return 0

    r = Runner(cfg, a.dry_run, a.allow_mass_delete, a.no_notify)
    if a.reset:
        r.state_file.unlink(missing_ok=True)
        print("Forgot the download history for this folder. Files on disk were not touched.")
        LOCK.release()
        return 0

    def on_signal(*_):
        if r.stopping:
            return
        r.stop_child()
        raise Stop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError):
            pass
    try:
        return r.run(index_only=a.index_only)
    except (Stop, KeyboardInterrupt):
        r.save_state()
        r.log("Proton Photos stopped by request. Photos already downloaded are kept.")
        return 143
    except RuntimeError as e:
        r.log(f"Proton Photos ERROR: {e}")
        r.notify("Proton Photos failed", str(e)[:200], "critical")
        return 1
    finally:
        LOCK.release()


def stop_photos() -> int:
    """Asks a running download to stop (it saves its progress first)."""
    from .osutil import kill_tree, pid_alive, wait_until
    pid = LOCK.owner()
    if not pid:
        print("Proton Photos isn't running.")
        return 0
    if IS_WINDOWS:
        LOCK.request_stop()
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if not wait_until(lambda: not pid_alive(pid), 20):
        kill_tree(pid, force=True)
        LOCK.release()
    print("Proton Photos stopped. Photos already downloaded are kept.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
