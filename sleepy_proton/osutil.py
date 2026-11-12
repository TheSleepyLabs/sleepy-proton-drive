"""Everything that differs between Linux and Windows lives here: folders, process
control, notifications, the trash and finding programs."""

import glob
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

IS_WINDOWS = os.name == "nt"
APP_NAME = "Sleepy Proton Drive"
HOME = Path.home()
PACKAGE_DIR = Path(__file__).resolve().parent          # .../sleepy_proton
PACKAGE_PARENT = PACKAGE_DIR.parent                     # put this on sys.path / PYTHONPATH


def _env_path(var, default):
    v = os.environ.get(var)
    return Path(v) if v else default


if IS_WINDOWS:
    _ROAMING = _env_path("APPDATA", HOME / "AppData" / "Roaming") / APP_NAME
    _LOCAL = _env_path("LOCALAPPDATA", HOME / "AppData" / "Local") / APP_NAME
    CONFIG_DIR = _ROAMING
    STATE_DIR = _LOCAL / "state"
    CACHE_DIR = _LOCAL / "cache"
    DATA_DIR = _LOCAL                     # venv + installed app
    RUNTIME_DIR = _LOCAL / "run"
    DEFAULT_LOG = _LOCAL / "proton-sync.log"
    USER_BIN = _LOCAL / "bin"
else:
    CONFIG_DIR = _env_path("XDG_CONFIG_HOME", HOME / ".config")
    STATE_DIR = _env_path("XDG_STATE_HOME", HOME / ".local" / "state") / "sleepy-proton-drive"
    CACHE_DIR = _env_path("XDG_CACHE_HOME", HOME / ".cache") / "sleepy-proton-drive"
    DATA_DIR = _env_path("XDG_DATA_HOME", HOME / ".local" / "share") / "sleepy-proton-drive"
    RUNTIME_DIR = _env_path("XDG_RUNTIME_DIR", Path("/tmp"))
    DEFAULT_LOG = HOME / ".local" / "share" / "proton-sync.log"
    USER_BIN = HOME / ".local" / "bin"

DEFAULT_CONFIG = Path(os.environ.get("PROTON_SYNC_CONFIG") or CONFIG_DIR / "proton-sync.conf")
DEFAULT_PHOTOS_DIR = HOME / "Pictures" / "Proton Photos"
EXE = ".exe" if IS_WINDOWS else ""

# Child processes: no console window on Windows, own process group everywhere.
if IS_WINDOWS:
    NO_WINDOW = subprocess.CREATE_NO_WINDOW
    POPEN_GROUP = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    POPEN_QUIET = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    NO_WINDOW = 0
    POPEN_GROUP = {"start_new_session": True}
    POPEN_QUIET = {}


def hostname() -> str:
    return (os.environ.get("COMPUTERNAME") if IS_WINDOWS else None) or socket.gethostname() or "computer"


def same_path(a, b) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def run_quiet(cmd, timeout=30, **kw):
    """subprocess.run that never raises (missing program, timeout) and shows no window."""
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    if kw.get("text"):
        kw.setdefault("encoding", "utf-8")
        kw.setdefault("errors", "replace")
    for k, v in POPEN_QUIET.items():
        kw.setdefault(k, v)
    try:
        return subprocess.run(cmd, timeout=timeout, **kw)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


# ------------------------------------------------------------------ programs
def find_exe(name: str):
    """Finds a program in PATH or the usual per-user install folders."""
    found = shutil.which(name)
    if found:
        return found
    candidates = [USER_BIN / (name + EXE)]
    if IS_WINDOWS:
        local = _env_path("LOCALAPPDATA", HOME / "AppData" / "Local")
        candidates.append(local / "Microsoft" / "WinGet" / "Links" / (name + EXE))
        candidates += [Path(p) for p in glob.glob(str(local / "Microsoft" / "WinGet" / "Packages" / "*" / "*" / (name + EXE)))]
        candidates += [Path(p) for p in glob.glob(str(local / "Microsoft" / "WinGet" / "Packages" / "*" / (name + EXE)))]
        candidates.append(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "chocolatey" / "bin" / (name + EXE))
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def console_python() -> str:
    """A python.exe (not pythonw.exe) next to the running interpreter, for child processes."""
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return sys.executable


def windowless_python() -> str:
    """pythonw.exe on Windows (no console window flashing up), python elsewhere."""
    exe = Path(sys.executable)
    if IS_WINDOWS and (exe.parent / "pythonw.exe").exists():
        return str(exe.parent / "pythonw.exe")
    return sys.executable


def module_cmd(tool: str, *args, windowless=False):
    """Command + environment to run one of our tools (sync, photos, check) as a child process."""
    py = windowless_python() if windowless else console_python()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PACKAGE_PARENT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return [py, "-m", "sleepy_proton", tool, *args], env


# ------------------------------------------------------------------ processes
def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # access denied = exists
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def descendants(pid: int):
    """All child processes of pid (Linux, via /proc)."""
    if IS_WINDOWS:
        return []
    kids = {}
    for d in glob.glob("/proc/[0-9]*/stat"):
        try:
            with open(d, encoding="utf-8", errors="replace") as f:
                stat = f.read()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            kids.setdefault(ppid, []).append(int(d.split("/")[2]))
        except (OSError, ValueError, IndexError):
            continue
    out, todo = [], [int(pid)]
    while todo:
        p = todo.pop()
        for c in kids.get(p, []):
            out.append(c)
            todo.append(c)
    return out


def kill_tree(pid, force=False):
    """Ends a process and everything it started."""
    if not pid:
        return
    if IS_WINDOWS:
        run_quiet(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=30)
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    for p in [int(pid), *descendants(pid)]:
        try:
            os.kill(p, sig)
        except OSError:
            pass


def stop_popen(proc):
    """Stops a child started with POPEN_GROUP, including its own children."""
    if proc is None or proc.poll() is not None:
        return
    if IS_WINDOWS:
        kill_tree(proc.pid)
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        kill_tree(proc.pid)


class Lock:
    """One run at a time: a lock folder holding the owner's PID (same place as v1.x on Linux).
    Windows can't send "stop" signals, so a stop request is a file the running sync watches."""

    def __init__(self, name: str):
        suffix = "" if IS_WINDOWS else f"-{os.getuid()}"
        self.dir = RUNTIME_DIR / f"{name}{suffix}.lock"

    def owner(self):
        try:
            pid = int((self.dir / "pid").read_text().strip())
        except (OSError, ValueError):
            return None
        return pid if pid_alive(pid) else None

    def acquire(self) -> bool:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self.dir.mkdir()
        except FileExistsError:
            if self.owner():
                return False
            shutil.rmtree(self.dir, ignore_errors=True)
            try:
                self.dir.mkdir()
            except FileExistsError:
                return False
        (self.dir / "pid").write_text(str(os.getpid()))
        return True

    def release(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def request_stop(self):
        try:
            (self.dir / "stop").touch()
        except OSError:
            pass

    def stop_requested(self) -> bool:
        return (self.dir / "stop").exists()


# ------------------------------------------------------------------ desktop
_TOAST_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:SPD_TITLE)) > $null
$x.Item(1).AppendChild($t.CreateTextNode($env:SPD_BODY)) > $null
$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def notify(title: str, body: str, urgency: str = "normal"):
    """Desktop notification (never raises, never waits)."""
    try:
        if IS_WINDOWS:
            env = dict(os.environ, SPD_TITLE=f"{APP_NAME}: {title}", SPD_BODY=body)
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                              "-Command", _TOAST_PS], env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **POPEN_QUIET)
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", APP_NAME, "-i", "sleepy-proton-drive", "-u", urgency, title, body],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _windows_recycle(path: Path) -> bool:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = str(Path(path).resolve()) + "\0"  # the struct adds the second NUL
    op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return rc == 0 and not op.fAnyOperationsAborted and not Path(path).exists()


def trash(path: Path):
    """Moves a file to the Recycle Bin / desktop trash (recoverable); deletes it if there's no trash."""
    path = Path(path)
    if IS_WINDOWS:
        try:
            if _windows_recycle(path):
                return
        except (OSError, AttributeError):
            pass
    else:
        for cmd in (["gio", "trash", str(path)], ["kioclient", "move", str(path), "trash:/"]):
            if shutil.which(cmd[0]) and run_quiet(cmd, timeout=60).returncode == 0 and not path.exists():
                return
    path.unlink(missing_ok=True)


def open_path(path):
    try:
        if IS_WINDOWS:
            os.startfile(str(path))  # noqa: S606 (opens in Explorer)
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def wait_until(check, seconds: float, step: float = 0.5) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(step)
    return check()
