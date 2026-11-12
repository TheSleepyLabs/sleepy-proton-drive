"""Background sync timer: a systemd user timer on Linux, a Task Scheduler task on Windows."""

import datetime as dt
import os
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .osutil import (CONFIG_DIR, DEFAULT_CONFIG, IS_WINDOWS, PACKAGE_PARENT, USER_BIN, run_quiet,
                     same_path, windowless_python)

UNIT_NAME = "sleepy-proton-sync"
SYSTEMD_USER_DIR = CONFIG_DIR / "systemd" / "user"
TASK_NAME = "Sleepy Proton Drive Sync"
INTERVAL_RE = re.compile(r"^([0-9]+)(s|sec|m|min|h|hr|d|day|days|w|week|weeks)$")


class TimerError(Exception):
    pass


def valid_interval(interval: str) -> bool:
    return bool(INTERVAL_RE.match(interval or ""))


def interval_minutes(interval: str) -> int:
    m = INTERVAL_RE.match(interval)
    n, unit = int(m.group(1)), m.group(2)
    if unit in ("s", "sec"):
        return max(1, (n + 59) // 60)
    if unit in ("m", "min"):
        return max(1, n)
    if unit in ("h", "hr"):
        return n * 60
    if unit in ("d", "day", "days"):
        return n * 1440
    return n * 7 * 1440


def _launcher() -> str:
    """The sync command for the timer, as a systemd ExecStart / quoted string."""
    own = os.environ.get("SLEEPY_PROTON_SYNC_BIN")
    if own and Path(own).is_file():
        return f'"{own}"'
    installed = USER_BIN / "sleepy-proton-sync"
    if installed.is_file():
        return f'"{installed}"'
    return f'"{sys.executable}" -m sleepy_proton sync'


# ------------------------------------------------------------------ systemd
def have_user_systemd() -> bool:
    return (not IS_WINDOWS) and run_quiet(["systemctl", "--user", "show-environment"], timeout=10).returncode == 0


def _installed_interval_systemd() -> str:
    f = SYSTEMD_USER_DIR / f"{UNIT_NAME}.timer"
    try:
        for line in f.read_text().splitlines():
            if line.startswith("OnUnitActiveSec="):
                return line.split("=", 1)[1]
    except OSError:
        pass
    return ""


def _remove_legacy_units():
    for u in ("proton-sync.timer", "proton-sync.service"):
        f = SYSTEMD_USER_DIR / u
        try:
            ours = f.is_symlink() or (f.is_file() and "Sleepy Labs" in f.read_text())
        except OSError:
            ours = False
        if ours:
            run_quiet(["systemctl", "--user", "disable", "--now", u])
            f.unlink(missing_ok=True)


def _enable_systemd(interval: str, config: Path):
    if not have_user_systemd():
        raise TimerError("no systemd user session found, so the background timer can't be used.\n"
                         "You can still sync by hand (sleepy-proton-sync) or from cron.")
    exec_line = _launcher()
    env_line = ""
    if "-m sleepy_proton" in exec_line:
        env_line = f"Environment=PYTHONPATH={PACKAGE_PARENT}\n"
    if not same_path(Path(config).resolve(), Path(DEFAULT_CONFIG).resolve()):
        exec_line += f' --config "{Path(config).resolve()}"'
    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    _remove_legacy_units()
    for ext in ("service", "timer"):
        (SYSTEMD_USER_DIR / f"{UNIT_NAME}.{ext}").unlink(missing_ok=True)
    (SYSTEMD_USER_DIR / f"{UNIT_NAME}.service").write_text(f"""[Unit]
Description=The Sleepy Labs - Proton Drive Sync Engine
Documentation=https://github.com/TheSleepyLabs/sleepy-proton-drive
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={exec_line}
{env_line}TimeoutStartSec=6h
# 143 = stopped on purpose (Stop/Restart button), not a failure.
SuccessExitStatus=143
Nice=10
IOSchedulingClass=idle
""")
    (SYSTEMD_USER_DIR / f"{UNIT_NAME}.timer").write_text(f"""[Unit]
Description=The Sleepy Labs - Periodic Proton Drive Sync (every {interval})
Documentation=https://github.com/TheSleepyLabs/sleepy-proton-drive

[Timer]
OnBootSec=5min
OnUnitActiveSec={interval}
RandomizedDelaySec=60

[Install]
WantedBy=timers.target
""")
    run_quiet(["systemctl", "--user", "daemon-reload"])
    run_quiet(["systemctl", "--user", "enable", f"{UNIT_NAME}.timer"])
    r = run_quiet(["systemctl", "--user", "restart", f"{UNIT_NAME}.timer"])
    if r.returncode != 0:
        raise TimerError(f"systemd couldn't start the timer: {(r.stderr or r.stdout).strip()}")


def _disable_systemd():
    if have_user_systemd():
        run_quiet(["systemctl", "--user", "disable", "--now", f"{UNIT_NAME}.timer"])
        _remove_legacy_units()
    for ext in ("timer", "service"):
        (SYSTEMD_USER_DIR / f"{UNIT_NAME}.{ext}").unlink(missing_ok=True)
    if have_user_systemd():
        run_quiet(["systemctl", "--user", "daemon-reload"])


def _status_systemd():
    if have_user_systemd() and run_quiet(["systemctl", "--user", "is-active", "--quiet",
                                           f"{UNIT_NAME}.timer"]).returncode == 0:
        return True, _installed_interval_systemd()
    return False, ""


def systemd_service_active() -> bool:
    return have_user_systemd() and run_quiet(["systemctl", "--user", "is-active", "--quiet",
                                              f"{UNIT_NAME}.service"]).returncode == 0


def systemd_service_installed() -> bool:
    return (SYSTEMD_USER_DIR / f"{UNIT_NAME}.service").is_file()


# ------------------------------------------------------------------ Windows
def _iso_interval(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"P{min(minutes // 1440, 31)}D"
    if minutes % 60 == 0:
        return f"PT{minutes // 60}H"
    return f"PT{minutes}M"


def _task_xml(interval: str, config: Path) -> str:
    minutes = interval_minutes(interval)
    args = "-m sleepy_proton sync"
    if not same_path(Path(config).resolve(), Path(DEFAULT_CONFIG).resolve()):
        args += f' --config "{Path(config).resolve()}"'
    start = (dt.datetime.now() + dt.timedelta(minutes=2)).replace(microsecond=0).isoformat()
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>The Sleepy Labs</Author>
    <Description>The Sleepy Labs - Periodic Proton Drive Sync (every {escape(interval)})</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>{_iso_interval(minutes)}</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT6H</ExecutionTimeLimit>
    <Priority>7</Priority>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(windowless_python())}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(str(PACKAGE_PARENT))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _enable_windows(interval: str, config: Path):
    from .osutil import RUNTIME_DIR
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    xml_file = RUNTIME_DIR / "sleepy-proton-task.xml"
    xml_file.write_text(_task_xml(interval, config), encoding="utf-16")
    try:
        r = run_quiet(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_file), "/F"], timeout=60)
    finally:
        xml_file.unlink(missing_ok=True)
    if r.returncode != 0:
        raise TimerError(f"Task Scheduler refused the task: {(r.stderr or r.stdout).strip()}")


def _disable_windows():
    run_quiet(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], timeout=60)


def task_installed() -> bool:
    return IS_WINDOWS and run_quiet(["schtasks", "/Query", "/TN", TASK_NAME], timeout=30).returncode == 0


def _status_windows():
    r = run_quiet(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"], timeout=30)
    if r.returncode != 0:
        return False, ""
    xml = r.stdout
    enabled = re.search(r"<Settings>.*?<Enabled>(\w+)</Enabled>", xml, re.S)
    if enabled and enabled.group(1).lower() == "false":
        return False, ""
    m = re.search(r"\(every ([^)]+)\)", xml)
    if m:
        return True, m.group(1)
    m = re.search(r"<Interval>P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?</Interval>", xml)
    if m:
        d, h, mi = (int(x) if x else 0 for x in m.groups())
        return True, f"{d}d" if d else (f"{h}h" if h else f"{mi}min")
    return True, ""


def task_running() -> bool:
    r = run_quiet(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "CSV", "/NH"], timeout=30)
    return r.returncode == 0 and "Running" in r.stdout


def run_task_now() -> bool:
    return run_quiet(["schtasks", "/Run", "/TN", TASK_NAME], timeout=30).returncode == 0


# ------------------------------------------------------------------ public
def enable(interval: str, config: Path):
    if not valid_interval(interval):
        raise ValueError(f"'{interval}' is not a valid interval (try 15min, 30min, 1h, 2h, 1d).")
    if IS_WINDOWS:
        _enable_windows(interval, config)
    else:
        _enable_systemd(interval, config)


def disable():
    if IS_WINDOWS:
        _disable_windows()
    else:
        _disable_systemd()


def status():
    """(active, interval) of the background timer."""
    return _status_windows() if IS_WINDOWS else _status_systemd()


def describe() -> str:
    return "Task Scheduler task" if IS_WINDOWS else f"{UNIT_NAME}.timer"
