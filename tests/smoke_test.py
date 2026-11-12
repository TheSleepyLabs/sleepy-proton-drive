#!/usr/bin/env python3
"""Cross-platform smoke test (Linux + Windows) for the sync engine and Proton Photos.
Uses a fake "protondrive" remote (an rclone alias pointing at a temporary folder) and a
fake Proton tool (tests/fake-proton-drive.py), so no account is needed and nothing real
is touched. Run:  python tests/smoke_test.py   [--timer] [--keep]"""

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = os.name == "nt"
T = Path(tempfile.mkdtemp(prefix="spd-test-"))
H = T / "home"
CLOUD = T / "cloud" / "Devices" / "TestBox"
passed = 0


def env_for_test():
    e = dict(os.environ)
    e.update({
        "HOME": str(H), "USERPROFILE": str(H),
        "APPDATA": str(H / "AppData" / "Roaming"), "LOCALAPPDATA": str(H / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(H / ".config"), "XDG_STATE_HOME": str(H / ".local" / "state"),
        "XDG_CACHE_HOME": str(H / ".cache"), "XDG_DATA_HOME": str(H / ".local" / "share"),
        "XDG_RUNTIME_DIR": str(T / "run"), "RCLONE_CONFIG": str(T / "rclone.conf"),
        "PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8",
        "PROTON_DRIVE_BIN": str(ROOT / "tests" / "fake-proton-drive.py"),
        "FAKE_PROTON_DIR": str(T / "fakeproton"),
    })
    for k in ("PROTON_SYNC_CONFIG", "SLEEPY_PROTON_SYNC_BIN", "FAKE_PROTON_LOGGED_OUT", "SLEEPY_PHOTOS_MASS_MIN"):
        e.pop(k, None)
    return e


ENV = env_for_test()
if IS_WINDOWS:
    CONFIG = H / "AppData" / "Roaming" / "Sleepy Proton Drive" / "proton-sync.conf"
    LOG = H / "AppData" / "Local" / "Sleepy Proton Drive" / "proton-sync.log"
    STATE = H / "AppData" / "Local" / "Sleepy Proton Drive" / "state"
    INDEX = H / "AppData" / "Local" / "Sleepy Proton Drive" / "cache" / "photos-index.json"
else:
    CONFIG = H / ".config" / "proton-sync.conf"
    LOG = H / ".local" / "share" / "proton-sync.log"
    STATE = H / ".local" / "state" / "sleepy-proton-drive"
    INDEX = H / ".cache" / "sleepy-proton-drive" / "photos-index.json"


def ok(msg):
    global passed
    passed += 1
    print(f"  ok   - {msg}", flush=True)


def fail(msg, extra=""):
    print(f"  FAIL - {msg}", flush=True)
    if extra:
        print(extra)
    if LOG.exists():
        print("---- last log lines ----")
        print("\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]))
    sys.exit(1)


def check(msg, cond, extra=""):
    ok(msg) if cond else fail(msg, extra)


def tool(name, *args, extra_env=None, timeout=300):
    e = dict(ENV, **(extra_env or {}))
    return subprocess.run([sys.executable, "-m", "sleepy_proton", name, *args], env=e, cwd=str(T),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def engine(*args, **kw):
    return tool("sync", *args, **kw)


def photos(*args, **kw):
    return tool("photos", *args, **kw)


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def rclone():
    for c in (shutil.which("rclone"),):
        if c:
            return c
    sys.exit("rclone is needed for the smoke test (install it first).")


def main():
    print(f"Sleepy Proton Drive smoke test ({'Windows' if IS_WINDOWS else 'Linux'}, Python {sys.version.split()[0]})")
    for d in (H / "Documents" / "sub", H / "Pictures", T / "cloud", T / "run", T / "fakeproton"):
        d.mkdir(parents=True, exist_ok=True)

    r = engine("--no-notify")
    check("fails cleanly without a remote", r.returncode == 1, r.stdout + r.stderr)

    subprocess.run([rclone(), "config", "create", "protondrive", "alias", "remote", str(T / "cloud")],
                   env=ENV, capture_output=True, check=True)
    write(CONFIG, '''REMOTE_NAME="protondrive"
DEVICE_NAME="TestBox"
SYNC_DIRS=("$HOME/Documents" "~/Pictures" "$HOME/Missing")
SYNC_MODE="copy"
NOTIFY=false
''')
    write(H / "Documents" / "a.txt", "one")
    write(H / "Documents" / "sub" / "b.txt", "two")

    engine("--dry-run")
    check("dry run changes nothing", not (CLOUD / "Documents" / "a.txt").exists())
    r = engine()
    check("copy uploads files", (CLOUD / "Documents" / "a.txt").is_file() and (CLOUD / "Documents" / "sub" / "b.txt").is_file(),
          r.stdout + r.stderr)
    (H / "Documents" / "a.txt").unlink()
    engine()
    check("copy keeps files deleted locally", (CLOUD / "Documents" / "a.txt").is_file())
    engine("--mode", "sync")
    check("sync mirrors deletions", not (CLOUD / "Documents" / "a.txt").exists())
    write(CLOUD / "Pictures" / "cloud-only.txt", "keep")
    engine("--mode", "sync")
    check("sync skips an empty local folder (no mass delete)", (CLOUD / "Pictures" / "cloud-only.txt").is_file())

    write(CLOUD / "Documents" / "c.txt", "fromcloud")
    r1 = engine("--mode", "bisync")
    write(CLOUD / "Documents" / "d.txt", "fromcloud2")
    r2 = engine("--mode", "bisync")
    check("bisync brings cloud files down", (H / "Documents" / "c.txt").is_file() and (H / "Documents" / "d.txt").is_file(),
          r1.stdout + r2.stdout)
    markers = list((STATE / "bisync").glob("*.done"))
    check("bisync remembers its baseline", len(markers) == 1)

    # Excluded folders: only that exact folder is skipped (odd characters included), a folder
    # with the same name elsewhere is still backed up, and the cloud copy of an excluded
    # folder is kept even in mirror mode.
    W = H / "Work"
    write(W / "top.txt", "a")
    write(W / "skip me [x]" / "deep" / "secret.txt", "b")
    write(W / "other" / "skip me [x]" / "keep.txt", "c")
    write(W / "Old" / "old.txt", "d")
    ex_conf = T / "ex.conf"
    write(ex_conf, '''REMOTE_NAME="protondrive"
DEVICE_NAME="TestBox"
SYNC_DIRS=("$HOME/Work")
EXCLUDE_DIRS=("$HOME/Work/skip me [x]/" "~/Work/Old" "$HOME/Elsewhere/x")
SYNC_MODE="copy"
NOTIFY=false
''')
    WC = CLOUD / "Work"
    write(WC / "Old" / "old.txt", "cloud")
    engine("-c", str(ex_conf))
    check("excluded folder is not uploaded", (WC / "top.txt").is_file() and not (WC / "skip me [x]").exists())
    check("same-named folder elsewhere is still uploaded", (WC / "other" / "skip me [x]" / "keep.txt").is_file())
    check("excluded folder (written with ~) is not uploaded", read(WC / "Old" / "old.txt") == "cloud")
    engine("-c", str(ex_conf), "--mode", "sync")
    check("mirror mode keeps the cloud copy of an excluded folder",
          (WC / "Old" / "old.txt").is_file() and not (WC / "skip me [x]").exists())
    engine("-c", str(ex_conf), "--mode", "bisync")
    write(W / "skip me [x]" / "new.txt", "e")
    engine("-c", str(ex_conf), "--mode", "bisync")
    check("two-way mode skips the excluded folder both ways",
          not (WC / "skip me [x]").exists() and read(W / "Old" / "old.txt") == "d")
    check("--status lists excluded folders", "Excluded folders" in engine("-c", str(ex_conf), "--status").stdout)

    # ---- Proton Photos against the fake Proton tool
    fake = T / "fakeproton" / "photos.json"
    fake.write_text(json.dumps([
        {"uid": "u1", "name": "IMG_1.jpg", "captureTime": "2026-11-09T02:38:45.000Z", "tags": [], "content": "one"},
        {"uid": "u2", "name": "clip.mp4", "captureTime": "2026-11-09T03:00:00.000Z", "tags": [2], "content": "video"},
        {"uid": "u3", "name": "IMG_1.jpg", "captureTime": "2026-11-20T03:00:00.000Z", "tags": [], "content": "same name"},
        {"uid": "u4", "name": "raw.dng", "captureTime": "2025-03-01T03:00:00.000Z", "tags": [9], "content": "raw"},
        {"uid": "u5", "name": "old.jpg", "captureTime": "2024-06-15T03:00:00.000Z", "tags": [0], "content": "fav"},
    ]))
    P = H / "Pictures" / "Proton Photos"
    ph_conf = T / "ph.conf"

    def pconf(mode, cats="", dates=""):
        write(ph_conf, f'''REMOTE_NAME="protondrive"
DEVICE_NAME="TestBox"
SYNC_DIRS=()
NOTIFY=false
PHOTOS_ENABLED=true
PHOTOS_MODE="{mode}"
PHOTOS_CATEGORIES=({cats})
PHOTOS_DATES=({dates})
''')

    def reset_photos():
        shutil.rmtree(P, ignore_errors=True)
        shutil.rmtree(STATE / "photos", ignore_errors=True)

    t = dt.datetime(2026, 11, 9, 2, 38, 45, tzinfo=dt.timezone.utc).astimezone()
    nov = P / f"{t.year}" / f"{t.month:02d} {t:%B}"
    pconf("copy")
    photos("-c", str(ph_conf), "--dry-run")
    check("photos dry run downloads nothing", not P.exists())
    r = photos("-c", str(ph_conf))
    check("photos land in Year/Month folders",
          (nov / "IMG_1.jpg").is_file() and read(nov / "IMG_1.jpg") == "one" and (nov / "clip.mp4").is_file()
          and (P / "2025" / "03 March" / "raw.dng").is_file(), r.stdout + r.stderr)
    nov20 = P / "2026" / "11 November"
    check("same-named photos are both kept",
          read(nov20 / "IMG_1.jpg") == "one" and read(nov20 / "IMG_1 (2).jpg") == "same name")
    mday = dt.datetime.fromtimestamp((P / "2024" / "06 June" / "old.jpg").stat().st_mtime).strftime("%Y-%m-%d")
    check("photo file date = capture date", mday in ("2024-06-14", "2024-06-15"), mday)
    check("photo counts saved for the app", json.loads(INDEX.read_text())["total"] == 5)
    (P / "2025" / "03 March" / "raw.dng").unlink()
    photos("-c", str(ph_conf))
    check("copy mode: deleted here is not downloaded again or deleted in Proton",
          not (P / "2025" / "03 March" / "raw.dng").exists() and "u4" in fake.read_text())

    reset_photos()
    pconf("copy", '"videos" "raw"')
    photos("-c", str(ph_conf))
    check("category filter (videos + raw only)",
          (nov / "clip.mp4").is_file() and (P / "2025" / "03 March" / "raw.dng").is_file()
          and not (nov / "IMG_1.jpg").exists() and not (P / "2024").exists())
    reset_photos()
    pconf("copy", "", '"2024" "2026-11"')
    photos("-c", str(ph_conf))
    check("date filter (a year + a month)",
          (P / "2024" / "06 June" / "old.jpg").is_file() and (nov / "clip.mp4").is_file() and not (P / "2025").exists())

    reset_photos()
    pconf("sync")
    photos("-c", str(ph_conf))
    (P / "2024" / "06 June" / "old.jpg").unlink()
    fake.write_text(json.dumps([p for p in json.loads(fake.read_text()) if p["uid"] != "u2"]))
    photos("-c", str(ph_conf))
    check("mirror: deleted in Proton -> removed here", not (nov / "clip.mp4").exists())
    check("mirror: deleted here -> downloaded again", (P / "2024" / "06 June" / "old.jpg").is_file())

    pconf("bisync")
    (P / "2024" / "06 June" / "old.jpg").unlink()
    photos("-c", str(ph_conf))
    trash = T / "fakeproton" / "trash.json"
    check("two-way: deleted here -> moved to Proton's trash",
          "u5" not in fake.read_text() and trash.exists() and "u5" in trash.read_text())
    check("two-way: a photo just moved to Proton's trash isn't downloaded again",
          not (P / "2024" / "06 June" / "old.jpg").exists() and "no details" not in LOG.read_text(encoding="utf-8"))
    P.rename(P.with_name("Proton Photos.unplugged"))
    r = photos("-c", str(ph_conf))
    if r.returncode == 0:
        fail("missing folder not caught")
    check("two-way: missing photo folder trashes nothing in Proton", "u1" in fake.read_text() and "u3" in fake.read_text())
    P.with_name("Proton Photos.unplugged").rename(P)
    (nov / "IMG_1.jpg").unlink()
    (nov20 / "IMG_1 (2).jpg").unlink()
    photos("-c", str(ph_conf), extra_env={"SLEEPY_PHOTOS_MASS_MIN": "1"})
    check("two-way: safety stop blocks mass deletes in Proton",
          "u1" in fake.read_text() and "SAFETY STOP" in LOG.read_text(encoding="utf-8"))
    r = photos("-c", str(ph_conf), extra_env={"FAKE_PROTON_LOGGED_OUT": "1"})
    check("signed out of Proton is reported as an error", r.returncode != 0)
    r = engine("-c", str(ph_conf))
    check("background sync runs Proton Photos too", "Proton Photos OK" in LOG.read_text(encoding="utf-8"),
          r.stdout + r.stderr)

    r = engine("--mode", "nonsense")
    check("rejects an unknown mode", r.returncode == 2)
    check("--status works", "TestBox" in engine("--status").stdout)
    check("--running says idle", engine("--running").stdout.strip() == "idle")

    # ---- config file: read without Bash, written back the same
    sys.path.insert(0, str(ROOT))
    os.environ.update(ENV)
    from sleepy_proton import config as C  # noqa: E402  (imported after the test folders are set)
    s = C.Settings(CONFIG)
    check("config reader understands $HOME and ~",
          [Path(C.expand_path(d)) for d in s.sync_dirs[:2]] == [H / "Documents", H / "Pictures"], repr(s.sync_dirs))
    s.exclude_dirs = [str(H / "Documents" / "odd $name \"q\" `x`")]
    s.photos_categories = ["videos", "raw"]
    C.save(s, T / "round.conf")
    back = C.Settings(T / "round.conf")
    check("config round trip keeps every value",
          (back.sync_dirs, back.exclude_dirs, back.photos_categories, back.rclone_flags)
          == (s.sync_dirs, s.exclude_dirs, s.photos_categories, s.rclone_flags),
          f"{back.__dict__}\n{s.__dict__}")
    check("config file stores home paths as $HOME/...", '"$HOME/Documents"' in (T / "round.conf").read_text())
    if shutil.which("bash") and not IS_WINDOWS:
        check("config is valid bash", subprocess.run(["bash", "-n", str(T / "round.conf")]).returncode == 0)

    from sleepy_proton.sync import posix_cksum  # noqa: E402
    check("bisync marker names match v1.x (POSIX cksum)",
          posix_cksum(b"") == 4294967295 and posix_cksum(b"protondrive|my-pc|/home/user/Documents") == 139070889)

    # ---- Stop + Restart a running sync (a deliberately slow upload)
    big = H / "Slow" / "big.bin"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(os.urandom(8 * 1024 * 1024))
    slow = T / "slow.conf"
    write(slow, '''REMOTE_NAME="protondrive"
DEVICE_NAME="TestBox"
SYNC_DIRS=("$HOME/Slow")
NOTIFY=false
RCLONE_FLAGS=("--bwlimit" "100k")
''')
    e = dict(ENV)
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_WINDOWS else {"start_new_session": True}
    bg = subprocess.Popen([sys.executable, "-m", "sleepy_proton", "sync", "-c", str(slow)], env=e, cwd=str(T),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
    for _ in range(60):
        if engine("--running").stdout.startswith("running"):
            break
        time.sleep(0.5)
    check("--running sees a sync in progress", engine("--running").stdout.startswith("running"))
    r = engine("-c", str(slow))
    check("a second sync waits its turn", "already running" in r.stdout and r.returncode == 0, r.stdout)
    r = engine("--stop")
    check("--stop stops it", "Sync stopped" in r.stdout, r.stdout + r.stderr)
    try:
        rc = bg.wait(timeout=60)
    except subprocess.TimeoutExpired:
        bg.kill()
        fail("the stopped sync didn't end")
    check("a stopped sync exits with code 143", rc == 143 or (IS_WINDOWS and rc in (1, 143)), f"exit {rc}")
    check("the lock is released after Stop", engine("--running").stdout.strip() == "idle")
    if IS_WINDOWS:
        time.sleep(2)
    big.write_bytes(b"small now")
    r = engine("-c", str(slow), "--restart")
    check("--restart starts a fresh sync", "fresh sync" in r.stdout, r.stdout + r.stderr)
    done = False
    for _ in range(120):
        if (CLOUD / "Slow" / "big.bin").is_file() and read(CLOUD / "Slow" / "big.bin") == "small now" \
                and engine("--running").stdout.strip() == "idle":
            done = True
            break
        time.sleep(0.5)
    check("the restarted sync finishes in the background", done)

    # ---- background timer (only where it's safe to touch: CI runners)
    if "--timer" in sys.argv:
        r = engine("--enable-timer", "45min")
        check("timer can be turned on", r.returncode == 0, r.stdout + r.stderr)
        st = engine("--timer-status").stdout.strip()
        check("timer reports its interval", st == "active 45min", st)
        r = engine("--disable-timer")
        check("timer can be turned off", r.returncode == 0 and engine("--timer-status").stdout.strip() == "inactive",
              r.stdout + r.stderr)
    r = engine("--enable-timer", "soon")
    check("a bad timer interval is refused", r.returncode == 2)

    print(f"All {passed} checks passed.")


if __name__ == "__main__":
    try:
        main()
    finally:
        if "--keep" in sys.argv:
            print(f"Test files kept in {T}")
        else:
            shutil.rmtree(T, ignore_errors=True)
