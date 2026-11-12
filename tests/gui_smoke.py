#!/usr/bin/env python3
"""Opens the app window without a screen (Qt "offscreen") and clicks through it:
every tab, saving settings, a dry-run sync, the Proton Photos status check and a
Photos preview, all against test folders. Needs PySide6 and rclone.
Run:  python tests/gui_smoke.py   [--shots DIR] to also save screenshots."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T = Path(tempfile.mkdtemp(prefix="spd-gui-"))
H = T / "home"
os.environ.update({
    "HOME": str(H), "USERPROFILE": str(H),
    "APPDATA": str(H / "AppData" / "Roaming"), "LOCALAPPDATA": str(H / "AppData" / "Local"),
    "XDG_CONFIG_HOME": str(H / ".config"), "XDG_STATE_HOME": str(H / ".local" / "state"),
    "XDG_CACHE_HOME": str(H / ".cache"), "XDG_DATA_HOME": str(H / ".local" / "share"),
    "XDG_RUNTIME_DIR": str(T / "run"), "RCLONE_CONFIG": str(T / "rclone.conf"),
    "PROTON_DRIVE_BIN": str(ROOT / "tests" / "fake-proton-drive.py"), "FAKE_PROTON_DIR": str(T / "fakeproton"),
    "QT_QPA_PLATFORM": "offscreen", "SLEEPY_PROTON_REEXEC": "1",
})
os.environ.pop("PROTON_SYNC_CONFIG", None)
os.environ.pop("SLEEPY_PROTON_SYNC_BIN", None)
for d in (H / "Documents", H / "Pictures", T / "cloud", T / "run", T / "fakeproton"):
    d.mkdir(parents=True, exist_ok=True)
(H / "Documents" / "hello.txt").write_text("hi")
(T / "fakeproton" / "photos.json").write_text(
    '[{"uid":"u1","name":"IMG_1.jpg","captureTime":"2026-11-09T02:38:45.000Z","tags":[],"content":"one"}]')
sys.path.insert(0, str(ROOT))

passed = 0


def ok(msg):
    global passed
    passed += 1
    print(f"  ok   - {msg}", flush=True)


def check(msg, cond, extra=""):
    if cond:
        ok(msg)
        return
    print(f"  FAIL - {msg}\n{extra}", flush=True)
    sys.exit(1)


def main():
    from sleepy_proton import osutil
    rc = shutil.which("rclone") or osutil.find_exe("rclone")
    if not rc:
        sys.exit("rclone is needed for the GUI test.")
    subprocess.run([rc, "config", "create", "protondrive", "alias", "remote", str(T / "cloud")],
                   capture_output=True, check=True)
    cfg_path = osutil.DEFAULT_CONFIG
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('REMOTE_NAME="protondrive"\nDEVICE_NAME="GuiBox"\nSYNC_DIRS=("$HOME/Documents")\n'
                        'SYNC_MODE="copy"\nNOTIFY=false\n', encoding="utf-8")

    from sleepy_proton import gui as g
    from PySide6.QtWidgets import QApplication, QMessageBox, QDialog
    answers = []

    def auto(kind):
        def f(*a, **k):
            answers.append((kind, a[1] if len(a) > 1 else ""))
            return QMessageBox.Yes
        return staticmethod(f)
    for kind in ("question", "information", "warning", "critical"):
        setattr(QMessageBox, kind, auto(kind))
    g.SetupWizardDialog.exec = lambda self: QDialog.Rejected

    app = QApplication([sys.argv[0]])

    def spin(cond, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.05)
        return cond()

    print(f"Sleepy Proton Drive GUI test ({'Windows' if osutil.IS_WINDOWS else 'Linux'})")
    w = g.MainWindow()
    w.show()
    spin(lambda: False, 1)
    check("window opens", w.isVisible())
    check("settings load (device name, folders)",
          w.config.device_name == "GuiBox" and [Path(d) for d in w.config.sync_dirs] == [H / "Documents"],
          f"{w.config.device_name} {w.config.sync_dirs}")
    check("no warnings on start", not [a for a in answers if a[0] in ("warning", "critical")], repr(answers))
    shots = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None
    for i in range(w.tabs.count()):
        w.tabs.setCurrentIndex(i)
        spin(lambda: False, 0.3)
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            w.grab().save(str(Path(shots) / f"tab{i}.png"))
    ok(f"all {w.tabs.count()} tabs open")
    w.resize(w.minimumSize())
    spin(lambda: False, 0.3)
    ok(f"smallest window size works ({w.minimumSize().width()}x{w.minimumSize().height()})")

    # Save settings: exclude a folder, then read the file back.
    ex = str(H / "Documents" / "Private stuff")
    w.config.exclude_dirs = [ex]
    check("settings save", w.save_config_or_warn(), repr(answers))
    again = g.ConfigManager(cfg_path)
    check("saved settings read back the same",
          [Path(d) for d in again.exclude_dirs] == [Path(ex)] and again.device_name == "GuiBox",
          cfg_path.read_text(encoding="utf-8"))
    check("saved file uses $HOME paths", '"$HOME/Documents/Private stuff"' in cfg_path.read_text(encoding="utf-8"))

    # Dry-run sync from the Overview tab.
    w.run_sync_process(dry_run=True)
    check("dry-run starts", w.process is not None, repr(answers))
    spin(lambda: not w.own_sync_running(), 120)
    log = w.overview_log_view.toPlainText()
    check("dry-run finishes and its output shows in the window",
          "DRY RUN" in log and "SUCCESS" in log and "finished successfully" in log, log[-2000:])
    check("dry run uploaded nothing", not (T / "cloud" / "Devices").exists() or
          not any((T / "cloud" / "Devices").rglob("hello.txt")))

    w.update_timer_status()
    w.refresh_systemd_status()
    w.refresh_running_state()
    ok("timer + running status refresh")

    # Proton Photos tab: status check and a preview, against the fake Proton tool.
    w.check_photos_status()
    spin(lambda: w.photos_status_proc is not None and w.photos_status_proc.state() == g.QProcess.NotRunning, 60)
    check("Proton Photos status: tool found and signed in", "Ready" in w.lbl_photos_status.text(),
          w.lbl_photos_status.text())
    w.run_photos(index_only=True)
    spin(lambda: w.photos_proc is not None and w.photos_proc.state() == g.QProcess.NotRunning, 60)
    check("Proton Photos counts load", "Proton Photos has 1 photos" in w.photos_log.toPlainText(),
          w.photos_log.toPlainText())
    w.run_photos(dry_run=True)
    spin(lambda: w.photos_proc is not None and w.photos_proc.state() == g.QProcess.NotRunning, 60)
    plog = w.photos_log.toPlainText()
    check("Proton Photos preview runs", "Would download" in plog and "Proton Photos OK" in plog, plog[-2000:])

    # Setup wizard opens and closes.
    wiz = g.SetupWizardDialog(w, w.config)
    wiz.show()
    spin(lambda: False, 0.3)
    wiz.close()
    ok("setup wizard opens")

    if osutil.IS_WINDOWS:
        w.run_diagnostics()
        spin(lambda: w.diag_proc is not None and w.diag_proc.state() == g.QProcess.NotRunning, 180)
        check("System Check runs", "system check" in w.overview_log_view.toPlainText(),
              w.overview_log_view.toPlainText()[-2000:])

    w.close()
    print(f"All {passed} GUI checks passed.")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(T, ignore_errors=True)
