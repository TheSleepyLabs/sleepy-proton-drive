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
os.environ.pop("PYTHONPATH", None)   # like a real start: the window itself must make its tools findable
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
    from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QScrollArea
    answers = []
    reply = {"question": QMessageBox.Yes}     # what the next Yes/No question gets (nothing may block)

    def auto(kind):
        def f(*a, **k):
            answers.append((kind, a[1:3] if len(a) > 1 else ""))
            return reply["question"] if kind == "question" else QMessageBox.Yes
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
    # Stop/Restart/Sync now/Photos run `python -m sleepy_proton ...`: it must work from any folder,
    # not only from the source folder (that's where the tests run, which hid this bug in v2.0.0).
    prog, targs = g.tool("sync", "--config", str(cfg_path), "--running")
    r = subprocess.run([prog, *targs], cwd=str(T), capture_output=True, text=True)
    check("the window's tools start from any folder (Stop/Restart work)",
          r.returncode == 0 and "No module" not in r.stderr, r.stdout + r.stderr)
    shots = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None
    pages = ["Overview", "Folders", "Photos", "Settings", "Log"]
    check("sidebar has every page", w.stack.count() == len(pages), str(w.stack.count()))
    for i in range(w.stack.count()):
        w.go(i)
        spin(lambda: False, 0.3)
        # no page-level scroll area anywhere: only lists and logs scroll, inside themselves
        check(f"{pages[i]} page has no scroll area", not w.stack.widget(i).findChildren(QScrollArea))
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            w.grab().save(str(Path(shots) / f"page{i}.png"))
    w.go(0)
    ok(f"all {w.stack.count()} pages open")
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

    # ---- header Stop button stops exactly the job the strip shows
    stopped = []
    w.cancel_sync_process = lambda: stopped.append("sync")
    w.stop_photos = lambda: stopped.append("photos")
    w.go(w.PAGE_FOLDERS)
    w.sync_state = lambda: ("Syncing", "x", True)
    w.photos_busy = lambda: True
    w.update_progress()
    check("strip shows the sync first", w._strip_job == "sync", str(w._strip_job))
    w.stop_current()
    check("header Stop stops the sync, not Photos", stopped == ["sync"], str(stopped))
    del w.sync_state
    stopped.clear()
    w.update_progress()
    check("strip shows Photos when only Photos runs", w._strip_job == "photos", str(w._strip_job))
    w.stop_current()
    check("header Stop stops Photos, not the sync", stopped == ["photos"], str(stopped))
    del w.photos_busy
    stopped.clear()
    w.diag_busy = lambda: True
    w.update_progress()
    check("strip shows the system check (no Stop)", w._strip_job == "diag" and w.strip.stop.isHidden(),
          str(w._strip_job))
    w.stop_current()
    check("header Stop does nothing for the system check", stopped == [], str(stopped))
    del w.diag_busy
    w.update_progress()
    check("strip is empty when nothing runs", w._strip_job is None)
    del w.cancel_sync_process, w.stop_photos

    # ---- the timer combo keeps an unsaved pick across the 30 s poll
    real_timer_status = g.timer_status
    g.timer_status = lambda: (False, "30min")
    w._shown_interval = None
    w.update_timer_status()
    check("timer combo follows the real timer", w.combo_timer_int.currentText() == "30min")
    w.combo_timer_int.setCurrentText("2h")
    w.update_timer_status()
    w.update_timer_status()
    check("unsaved timer pick survives the poll", w.combo_timer_int.currentText() == "2h",
          w.combo_timer_int.currentText())
    g.timer_status = lambda: (True, "1h")
    w.update_timer_status()
    check("timer combo follows a real change", w.combo_timer_int.currentText() == "1h")
    g.timer_status = real_timer_status
    w.update_timer_status()

    # ---- Skip list edits stay on screen until Settings is saved
    w.go(w.PAGE_SETTINGS)
    check("settings start clean", not w.settings_dirty() and w.saved_lbl.text() != "Unsaved changes",
          w.saved_lbl.text())
    base = list(w.config.excludes)
    g.QInputDialog.getText = staticmethod(lambda *a, **k: ("*.zzz", True))
    w.add_exclusion_pattern()
    check("added pattern shows in the list", "*.zzz" in w.skip_patterns())
    check("added pattern is not in the config yet", w.config.excludes == base, str(w.config.excludes))
    check("Skip edit marks settings unsaved", w.settings_dirty() and w.saved_lbl.text() == "Unsaved changes",
          w.saved_lbl.text())
    check("another save does not write the pattern", w.save_config_or_warn()
          and "*.zzz" not in g.ConfigManager(cfg_path).excludes)
    w.undo_settings()
    check("undo drops the pattern and the unsaved mark",
          "*.zzz" not in w.skip_patterns() and not w.settings_dirty() and w.saved_lbl.text() != "Unsaved changes",
          w.saved_lbl.text())
    if w.list_excludes.count():
        w.list_excludes.item(0).setSelected(True)
        gone = w.list_excludes.item(0).text()
        w.remove_exclusion_pattern()
        check("removed pattern leaves the config alone until saved",
              gone not in w.skip_patterns() and w.config.excludes == base and w.settings_dirty())
        w.undo_settings()
    w.restore_preset_excludes()
    check("Restore recommended changes only the list", w.config.excludes == base)
    w.add_exclusion_pattern()
    check("Save writes the Skip list", w.save_settings(show_popup=False)
          and "*.zzz" in g.ConfigManager(cfg_path).excludes and w.config.excludes == w.skip_patterns()
          and not w.settings_dirty(), str(g.ConfigManager(cfg_path).excludes))

    # ---- unsaved-changes tracking
    w.spin_transfers.setValue(w.spin_transfers.value() + 1)
    check("changing a setting marks it unsaved", w.settings_dirty() and w.saved_lbl.text() == "Unsaved changes")
    w.undo_settings()
    check("undo clears the unsaved mark", not w.settings_dirty())
    w.chk_photos_enabled.setChecked(not w.chk_photos_enabled.isChecked())
    photos_unsaved = w.chk_photos_enabled.isChecked()
    w.undo_settings()
    check("undo on Settings keeps unsaved Photos edits", w.chk_photos_enabled.isChecked() == photos_unsaved)
    w.chk_photos_enabled.setChecked(not photos_unsaved)
    saved_transfers = g.ConfigManager(cfg_path).transfers
    w.spin_transfers.setValue(saved_transfers + 2)
    reply["question"] = QMessageBox.No
    n = len(answers)
    w.run_sync_process(dry_run=True)
    check("Sync asks about unsaved settings and No cancels",
          any("didn't save" in str(a[1]) for a in answers[n:]) and not w.own_sync_running()
          and g.ConfigManager(cfg_path).transfers == saved_transfers and w.settings_dirty(), repr(answers[n:]))
    w.restart_sync_process()
    check("Restart asks too and No cancels", w._ctl_proc is None and g.ConfigManager(cfg_path).transfers
          == saved_transfers)
    w.close()
    check("closing with unsaved changes asks, No keeps the window", w.isVisible())
    reply["question"] = QMessageBox.Yes
    w.run_sync_process(dry_run=True)
    check("Yes saves the settings and starts the sync",
          g.ConfigManager(cfg_path).transfers == saved_transfers + 2 and w.own_sync_running() and not w.settings_dirty())
    spin(lambda: not w.own_sync_running(), 120)
    w.spin_transfers.setValue(saved_transfers)
    w.save_settings(show_popup=False)
    check("Save clears the unsaved mark", not w.settings_dirty() and w.saved_lbl.text() != "Unsaved changes")

    # ---- Photos date filter: saving unchanged gives the same dates, with and without the counts
    w.go(w.PAGE_PHOTOS)
    real_index = g.PHOTOS_INDEX
    saved_dates = ["2025", "2026-03"]
    for label, index in (("without counts", None),
                         ("with counts", {"months": {"2025-01": 1, "2025-02": 2, "2026-03": 3, "2026-04": 1}})):
        if index is None:
            g.PHOTOS_INDEX = T / "no-such-index.json"
        else:
            g.PHOTOS_INDEX = T / "index.json"
            g.PHOTOS_INDEX.write_text(__import__("json").dumps(index))
        w.config.photos_dates = list(saved_dates)
        w.load_photo_filters()
        check(f"date filter reads back unchanged ({label})", w.selected_dates() == saved_dates,
              str(w.selected_dates()))
        check(f"saving the Photos page changes no dates ({label})",
              w.save_photo_settings(show_popup=False) and g.ConfigManager(cfg_path).photos_dates == saved_dates
              and cfg_path.read_text(encoding="utf-8").count('PHOTOS_DATES=("2025" "2026-03")') == 1,
              cfg_path.read_text(encoding="utf-8"))
    # with the counts, ticking every month of a year means the whole year
    ti = w.tree_dates
    y26 = next(ti.topLevelItem(i) for i in range(ti.topLevelItemCount())
               if ti.topLevelItem(i).data(0, g.Qt.UserRole) == "2026")
    y26.setCheckState(0, g.Qt.Checked)
    check("every known month ticked saves the whole year", w.selected_dates() == ["2025", "2026"],
          str(w.selected_dates()))
    g.PHOTOS_INDEX = real_index
    w.config.photos_dates = []
    w.load_photo_filters()
    w.save_photo_settings(show_popup=False)
    w.go(w.PAGE_OVERVIEW)

    reply["question"] = QMessageBox.Yes
    w.close()
    print(f"All {passed} GUI checks passed.")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(T, ignore_errors=True)
