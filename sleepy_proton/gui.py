"""
==============================================================================
The Sleepy Labs // Sleepy Proton Drive: app window and setup wizard
==============================================================================
Proton Drive sync manager for Linux and Windows (PySide6), in the same look as
its sister app Sleepy Backup: a header, a left sidebar and one page at a time.
No page ever scrolls: the long forms are split into tabs, and only the lists and
logs scroll inside themselves.
==============================================================================
"""

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from string import Template

from . import VERSION as APP_VERSION
from .osutil import DATA_DIR, IS_WINDOWS, PACKAGE_PARENT

if IS_WINDOWS:
    VENV_PYTHON = DATA_DIR / "venv" / "Scripts" / "python.exe"
else:
    VENV_PYTHON = DATA_DIR / "venv" / "bin" / "python"

try:
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
        QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
        QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QSizePolicy,
        QSlider, QSpinBox, QStackedWidget, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
    )
    from PySide6.QtCore import QProcess, QProcessEnvironment, QSize, Qt, QTimer, Signal
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QTextCursor
except ImportError:
    # PySide6 may live in the private environment made by the installer.
    # Compare environments, not interpreter paths: a venv's python is a symlink
    # to the system python, so resolved paths are always equal.
    if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != VENV_PYTHON.parent.parent.resolve() \
            and not os.environ.get("SLEEPY_PROTON_REEXEC"):
        os.environ["SLEEPY_PROTON_REEXEC"] = "1"
        os.environ["PYTHONPATH"] = str(PACKAGE_PARENT) + (os.pathsep + os.environ["PYTHONPATH"]
                                                          if os.environ.get("PYTHONPATH") else "")
        args = sys.argv[1:]
        if args[:1] == ["gui"]:
            args = args[1:]
        if IS_WINDOWS:
            import subprocess
            sys.exit(subprocess.call([str(VENV_PYTHON), "-m", "sleepy_proton", "gui", *args]))
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), "-m", "sleepy_proton", "gui", *args])
    sys.stderr.write(
        "Sleepy Proton Drive: the app window needs PySide6, which isn't installed.\n"
        + ("Fix it by running install.cmd again.\n" if IS_WINDOWS else
           "Fix it with:   sleepy-proton-check --fix     (or ./check-requirements.sh --fix)\n"
           "The terminal tools work without it: sleepy-proton-wizard, sleepy-proton-sync\n")
    )
    from .osutil import notify as _notify
    _notify("App can't open", "The app window needs PySide6. "
            + ("Run install.cmd again." if IS_WINDOWS else "Run: sleepy-proton-check --fix"))
    sys.exit(1)

from . import scheduler  # noqa: E402
from .config import ConfigError, Settings, expand_path, save as save_config  # noqa: E402
from .config import DEFAULT_EXCLUDES  # noqa: E402
from .osutil import (DEFAULT_CONFIG, DEFAULT_LOG, DEFAULT_PHOTOS_DIR, HOME, CACHE_DIR, find_exe,  # noqa: E402
                     hostname, kill_tree, module_cmd, open_path as os_open_path, run_quiet, same_path)

# The window runs our tools as child processes (`python -m sleepy_proton sync --stop`, Photos, the
# system check...). They inherit this environment, so it must say where the sleepy_proton package is.
# The re-exec above sets it, but when PySide6 comes from the system Python there's no re-exec, and
# without this every Stop/Restart/Sync now/Photos button failed with "No module named sleepy_proton".
_pp = os.environ.get("PYTHONPATH", "")
if str(PACKAGE_PARENT) not in _pp.split(os.pathsep):
    os.environ["PYTHONPATH"] = str(PACKAGE_PARENT) + (os.pathsep + _pp if _pp else "")


APP_NAME = "Sleepy Proton Drive"
MIN_W, MIN_H = 860, 560          # the smallest the window can be: every page fits in this
DEFAULT_W, DEFAULT_H = 980, 640
LOG_LINES = 3000                 # how many lines the on-screen logs keep

# Paths
CONFIG_PATH = DEFAULT_CONFIG
DEFAULT_LOG_PATH = DEFAULT_LOG
PACKAGE_DIR = Path(__file__).resolve().parent
ICON_CANDIDATES = [
    PACKAGE_DIR / "assets" / "sleepy-proton-drive.svg",
    PACKAGE_DIR.parent / "assets" / "sleepy-proton-drive.svg",
    DATA_DIR / "app" / "assets" / "sleepy-proton-drive.svg",
    HOME / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "sleepy-proton-drive.svg",
]
# Linux keeps its Bash requirements checker for the "System Check" button.
CHECK_SCRIPT = None
if not IS_WINDOWS:
    for _c in (HOME / ".local" / "bin" / "sleepy-proton-check", PACKAGE_DIR.parent / "check-requirements.sh"):
        if _c.exists():
            CHECK_SCRIPT = _c
            break

PHOTOS_INDEX = CACHE_DIR / "photos-index.json"
# (key, label, Proton tag number); "other" = photos without a category.
PHOTO_CATEGORIES = [
    ("other", "Regular photos", None), ("videos", "Videos", 2), ("raw", "RAW", 9),
    ("favorites", "Favourites", 0), ("screenshots", "Screenshots", 1), ("live-photos", "Live photos", 3),
    ("motion-photos", "Motion photos", 4), ("selfies", "Selfies", 5), ("portraits", "Portraits", 6),
    ("bursts", "Bursts", 7), ("panoramas", "Panoramas", 8),
]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]

TIMER_INTERVALS = ["15min", "30min", "1h", "2h", "1d"]
TIMER_KIND = "Windows Task Scheduler" if IS_WINDOWS else "systemd user timer"
if IS_WINDOWS:
    FIX_HINT = "Run install.cmd from the Sleepy Proton Drive download again to install it."
    RCLONE_CONF_HINT = "%APPDATA%\\rclone\\rclone.conf"
else:
    FIX_HINT = "Open a terminal and run:\n    sleepy-proton-check --fix"
    RCLONE_CONF_HINT = "~/.config/rclone/rclone.conf"
DEFAULT_OTHER_FLAGS = ["--verbose", "--stats-one-line", "--stats=30s", "--tpslimit=10", "--skip-links"]

# (key, short name, title, what it does) for the three sync modes
SYNC_MODES = [
    ("copy", "Copy", "Copy (safest, recommended)",
     "One-way upload. Files you delete on this computer are never deleted from Proton Drive."),
    ("sync", "Mirror", "Mirror (exact copy)",
     "Proton Drive becomes an exact copy of this computer: files deleted here are also removed there."),
    ("bisync", "Two-way", "Two-way",
     "Changes and deletions on either side are copied to the other side."),
]
SYNC_MODE_NAME = {k: n for k, n, _t, _d in SYNC_MODES}
PHOTO_MODES = [
    ("copy", "Copy (safest, recommended)",
     "Downloads new photos. Nothing is ever deleted, here or in Proton. Photos you delete here aren't downloaded again."),
    ("sync", "Mirror (Proton to this computer)",
     "This folder matches Proton Photos: photos deleted in Proton go to this computer's trash. "
     "Photos you delete here are downloaded again."),
    ("bisync", "Two-way",
     "Like Mirror, and photos you delete here are moved to Proton's trash (you can restore them there for 30 days)."),
]
PHOTO_MODE_NAME = {"copy": "Copy", "sync": "Mirror", "bisync": "Two-way"}

def rclone_bin() -> str:
    return find_exe("rclone") or "rclone"


def have_rclone() -> bool:
    return find_exe("rclone") is not None


def rclone_remotes():
    if not have_rclone():
        return []
    return [r.strip()[:-1] for r in run_quiet([rclone_bin(), "listremotes"]).stdout.splitlines() if r.strip().endswith(":")]


def timer_status():
    """Returns (active: bool, interval: str)."""
    return scheduler.status()


def tool(name: str, *args):
    """(program, arguments) for QProcess to run one of our tools (sync, photos, check)."""
    cmd, _env = module_cmd(name, *args)
    return cmd[0], cmd[1:]


def tidy_folder(folder: str) -> str:
    """A folder picked in a dialog, written the way this computer writes paths."""
    return expand_path(folder) or folder


def path_in_list(folder: str, folders) -> bool:
    return any(same_path(folder, d) for d in folders)


def same_name(a: str, b: str) -> bool:
    return os.path.normcase(Path(a).name) == os.path.normcase(Path(b).name)


def path_inside(child: str, parent: str) -> bool:
    """True if child is a folder somewhere below parent (as written or with links resolved)."""
    from .sync import relative_inside
    return bool(relative_inside(parent, child))


def short_home(path: str) -> str:
    """Shows /home/<you>/x as ~/x (and C:\\Users\\<you>\\x as ~\\x)."""
    home = str(HOME)
    norm = os.path.normcase
    if norm(path) == norm(home) or norm(path).startswith(norm(home + os.sep)) or norm(path).startswith(norm(home + "/")):
        return "~" + path[len(home):]
    return path


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# ---------------------------------------------------------------- look (The Sleepy Labs)
INK, INK2, INK3, LINE = "#07050d", "#0e0a19", "#171127", "#261c3d"
MOON, MIST, LILAC, DEEP, STRONG = "#f3ecff", "#b7a8d6", "#b794ff", "#8b5cf6", "#7c4ddb"
EMBER, GREEN, RED = "#f5b65a", "#6ccf9c", "#f08098"
DIM = "#6f6389"

QSS_TEMPLATE = Template("""
QWidget { color: $moon; font-size: 13px; }
QMainWindow, QDialog, QWidget#root { background: $ink; }
QLabel { background: transparent; }
QToolTip { background: $ink3; color: $moon; border: 1px solid $line; padding: 5px 8px; }

QFrame#header { background: $ink2; border: none; border-bottom: 1px solid $line; }
QFrame#sidebar { background: $ink; border: none; border-right: 1px solid $line; }
QFrame#strip { background: $ink2; border: none; border-top: 1px solid $line; }
QFrame#toast { background: #1d1733; border: 1px solid $line; border-radius: 10px; }
QFrame#toast[kind="ok"] { border-color: $green; }
QFrame#toast[kind="bad"] { border-color: $red; }
QFrame#toast[kind="warn"] { border-color: $ember; }

QPushButton#nav { text-align: left; padding: 10px 14px; border: none; border-radius: 10px;
                  color: $mist; font-weight: 600; font-size: 14px; background: transparent; }
QPushButton#nav:hover { background: $ink3; color: $moon; }
QPushButton#nav:checked { background: $ink3; color: $lilac; }

QFrame#card { background: $ink2; border: 1px solid $line; border-radius: 14px; }
QFrame#tile { background: $ink3; border: 1px solid $line; border-radius: 10px; }
QFrame#tile[sel="true"] { border-color: $deep; background: #1d1636; }
QFrame#row { background: $ink3; border: 1px solid $line; border-radius: 10px; }
QFrame#sep { background: $line; border: none; max-height: 1px; min-height: 1px; }

QLabel[role="h1"] { font-size: 22px; font-weight: 700; }
QLabel[role="h2"] { font-size: 15px; font-weight: 700; }
QLabel[role="big"] { font-size: 24px; font-weight: 700; }
QLabel[role="eyebrow"] { color: $mist; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
QLabel[role="muted"] { color: $mist; }
QLabel[role="small"] { color: $mist; font-size: 12px; }
QLabel[role="ok"] { color: $green; }
QLabel[role="bad"] { color: $red; }
QLabel[role="warn"] { color: $ember; }
QLabel[role="warnbox"] { color: $ember; background: #241a10; border: 1px solid #5a4020; border-radius: 10px;
                         padding: 10px 12px; }
QLabel[role="mono"] { font-family: "DejaVu Sans Mono", Consolas, monospace; }
QLabel[role="chip"] { border-radius: 10px; padding: 3px 10px; font-size: 12px; font-weight: 700;
                      border: 1px solid $line; color: $mist; background: $ink3; }
QLabel[role="chip"][kind="ok"] { color: $green; border-color: #2f6e53; background: #0f2119; }
QLabel[role="chip"][kind="bad"] { color: $red; border-color: #7a3445; background: #26111a; }
QLabel[role="chip"][kind="warn"] { color: $ember; border-color: #6e5226; background: #261b0d; }

QPushButton { background: $ink3; color: $moon; border: 1px solid $line; border-radius: 10px;
              padding: 8px 16px; font-weight: 600; min-height: 20px; }
QPushButton:hover { border-color: $lilac; }
QPushButton:pressed { background: $line; }
QPushButton:disabled { color: $dim; background: $ink2; border-color: $line; }
QPushButton:focus { border-color: $lilac; }
QPushButton[kind="primary"] { background: $deep; border: 1px solid $deep; color: #ffffff; }
QPushButton[kind="primary"]:hover { background: #9a6df8; border-color: #9a6df8; }
QPushButton[kind="primary"]:pressed { background: $strong; border-color: $strong; }
QPushButton[kind="primary"]:disabled { background: #2a2045; border-color: #2a2045; color: $dim; }
QPushButton[kind="big"] { font-size: 16px; padding: 14px 30px; border-radius: 12px; font-weight: 700; }
QPushButton[kind="danger"] { color: $red; border-color: #7a3445; background: #1c0f16; }
QPushButton[kind="danger"]:hover { border-color: $red; }
QPushButton[kind="danger"]:disabled { color: $dim; border-color: $line; background: $ink2; }
QPushButton[kind="link"] { background: transparent; border: none; color: $lilac; padding: 4px 6px; }
QPushButton[kind="link"]:hover { color: #d6c0ff; }
QPushButton[compact="true"] { padding: 4px 12px; min-height: 16px; font-size: 12px; border-radius: 8px; }
QPushButton[kind="step"] { background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 4px 8px; min-height: 18px;
                           font-size: 13px; font-weight: 700; text-align: left; }
QPushButton[kind="step"][state="ok"] { color: $green; }
QPushButton[kind="step"][state="todo"] { color: $ember; background: #241a10; border: 1px solid #5a4020; }
QPushButton[kind="step"][state="todo"]:hover { border-color: $ember; }

QLineEdit, QPlainTextEdit, QSpinBox, QTimeEdit, QComboBox {
    background: $ink3; border: 1px solid $line; border-radius: 8px; padding: 6px 10px;
    selection-background-color: $deep; selection-color: #ffffff; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QTimeEdit:focus, QComboBox:focus { border-color: $lilac; }
QLineEdit:disabled, QSpinBox:disabled, QTimeEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled {
    color: $dim; background: $ink2; }
QPlainTextEdit[role="log"] { background: $ink2; border-radius: 12px; padding: 10px;
                             font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 12px; }
QComboBox QAbstractItemView { background: $ink2; border: 1px solid $line; selection-background-color: $deep;
                              outline: none; padding: 4px; }
QComboBox::drop-down { border: none; width: 22px; }
QSpinBox::up-button, QSpinBox::down-button, QTimeEdit::up-button, QTimeEdit::down-button {
    width: 18px; border: none; background: transparent; }

QCheckBox, QRadioButton { spacing: 9px; background: transparent; }
QCheckBox:disabled, QRadioButton:disabled { color: $dim; }

QProgressBar { background: $ink3; border: none; border-radius: 6px; min-height: 12px; max-height: 12px;
               text-align: center; color: transparent; }
QProgressBar::chunk { border-radius: 6px;
                      background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 $deep, stop:1 $lilac); }

QTableWidget, QTreeWidget, QListWidget { background: $ink2; border: 1px solid $line; border-radius: 12px;
    outline: none; gridline-color: $line; alternate-background-color: $ink2; }
QTableWidget::item, QTreeWidget::item, QListWidget::item { padding: 5px 8px; border: none; }
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {
    background: #2e2250; color: #ffffff; }
QTreeWidget::item:hover, QListWidget::item:hover { background: $ink3; }
QHeaderView { background: transparent; }
QHeaderView::section { background: $ink3; color: $mist; border: none; padding: 9px 10px; font-weight: 700;
                       font-size: 12px; }
QTableCornerButton::section { background: $ink3; border: none; }

QScrollArea { background: transparent; border: none; }

QTabWidget::pane { background: $ink2; border: 1px solid $line; border-radius: 12px; top: -1px; }
QTabBar { background: transparent; }
QTabBar::tab { background: transparent; color: $mist; padding: 7px 20px; margin-right: 6px; border: none;
               border-bottom: 2px solid transparent; font-weight: 700; font-size: 14px; }
QTabBar::tab:hover { color: $moon; }
QTabBar::tab:selected { color: $lilac; border-bottom: 2px solid $deep; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: $line; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #3a2c5c; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle:horizontal { background: $line; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QMessageBox { background: $ink; }
QMessageBox QPushButton, QDialogButtonBox QPushButton { min-width: 84px; }
""")

QSS_ICONS = """
QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; }
QCheckBox::indicator:unchecked { image: url("$box"); }
QCheckBox::indicator:unchecked:hover { image: url("$box_hover"); }
QCheckBox::indicator:checked { image: url("$box_on"); }
QCheckBox::indicator:disabled { image: url("$box_off"); }
QRadioButton::indicator:unchecked { image: url("$radio"); }
QRadioButton::indicator:unchecked:hover { image: url("$radio_hover"); }
QRadioButton::indicator:checked { image: url("$radio_on"); }
QRadioButton::indicator:disabled { image: url("$box_off"); }
QTreeWidget::indicator, QListWidget::indicator { width: 18px; height: 18px; }
QTreeWidget::indicator:unchecked { image: url("$box"); }
QTreeWidget::indicator:unchecked:hover { image: url("$box_hover"); }
QTreeWidget::indicator:checked { image: url("$box_on"); }
QTreeWidget::indicator:indeterminate { image: url("$box_part"); }
QTreeWidget::indicator:disabled { image: url("$box_off"); }
QSpinBox::up-arrow, QTimeEdit::up-arrow { image: url("$up"); width: 10px; height: 10px; }
QSpinBox::down-arrow, QTimeEdit::down-arrow { image: url("$down"); width: 10px; height: 10px; }
QSpinBox::up-arrow:disabled, QSpinBox::down-arrow:disabled, QTimeEdit::up-arrow:disabled,
QTimeEdit::down-arrow:disabled { image: none; }
QComboBox::down-arrow { image: url("$down"); width: 10px; height: 10px; }
"""


def _svg(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">'
            + body + "</svg>")


def write_icons() -> dict:
    """Little check boxes, radio buttons and arrows for the style sheet (Qt can't draw them dark on its own)."""
    d = CACHE_DIR / "ui-icons"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {}
    box = f'<rect x="1.5" y="1.5" width="15" height="15" rx="4.5" fill="{INK3}" stroke="%s" stroke-width="1.5"/>'
    tick = '<path d="M5 9.4l2.7 2.7L13 6.4" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    rad = f'<circle cx="9" cy="9" r="7.5" fill="{INK3}" stroke="%s" stroke-width="1.5"/>'
    icons = {
        "box": _svg(box % "#4a3b73"), "box_hover": _svg(box % LILAC),
        "box_on": _svg(f'<rect x="1" y="1" width="16" height="16" rx="5" fill="{DEEP}"/>' + tick),
        "box_part": _svg(f'<rect x="1" y="1" width="16" height="16" rx="5" fill="#4a3b8f"/>'
                         '<path d="M5 9h8" stroke="#fff" stroke-width="2" stroke-linecap="round"/>'),
        "box_off": _svg(f'<rect x="1.5" y="1.5" width="15" height="15" rx="4.5" fill="{INK2}" stroke="{LINE}" '
                        'stroke-width="1.5"/>'),
        "radio": _svg(rad % "#4a3b73"), "radio_hover": _svg(rad % LILAC),
        "radio_on": _svg(rad % DEEP + f'<circle cx="9" cy="9" r="4" fill="{LILAC}"/>'),
        "up": '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10"><path d="M1.5 6.5L5 3l3.5 3.5" '
              f'fill="none" stroke="{MIST}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        "down": '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10"><path d="M1.5 3.5L5 7l3.5-3.5" '
                f'fill="none" stroke="{MIST}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    }
    out = {}
    for name, svg in icons.items():
        f = d / f"{name}.svg"
        try:
            f.write_text(svg)
        except OSError:
            return {}
        out[name] = f.as_posix()
    return out


def build_qss() -> str:
    tokens = dict(ink=INK, ink2=INK2, ink3=INK3, line=LINE, moon=MOON, mist=MIST, lilac=LILAC, deep=DEEP,
                  strong=STRONG, ember=EMBER, green=GREEN, red=RED, dim=DIM)
    css = QSS_TEMPLATE.safe_substitute(**tokens)
    icons = write_icons()
    if icons:
        css += Template(QSS_ICONS).safe_substitute(**icons)
    return css


_THEMED = False


def apply_theme(app):
    """Dark Sleepy Labs look for the whole app (windows, dialogs and message boxes)."""
    app.setStyle("Fusion")
    families = set(QFontDatabase.families())
    font = QFont("Archivo" if "Archivo" in families else app.font().family())
    font.setPointSize(10)
    app.setFont(font)
    pal = QPalette()
    for role, col in ((QPalette.Window, INK), (QPalette.WindowText, MOON), (QPalette.Base, INK3),
                      (QPalette.AlternateBase, INK2), (QPalette.Text, MOON), (QPalette.Button, INK3),
                      (QPalette.ButtonText, MOON), (QPalette.Highlight, DEEP), (QPalette.HighlightedText, "#ffffff"),
                      (QPalette.ToolTipBase, INK3), (QPalette.ToolTipText, MOON),
                      (QPalette.PlaceholderText, DIM), (QPalette.Link, LILAC), (QPalette.BrightText, "#ffffff")):
        pal.setColor(role, QColor(col))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(DIM))
    app.setPalette(pal)
    app.setStyleSheet(build_qss())


def ensure_theme():
    """Applies the look once (also when a window is made without main(), like in the tests)."""
    global _THEMED
    app = QApplication.instance()
    if app is not None and not _THEMED:
        _THEMED = True
        apply_theme(app)


# ---------------------------------------------------------------- small helpers
def fmt_short(d) -> str:
    return f"{d:%a} {d.day} {d:%b}, {d:%H:%M}"      # no %-d: Windows doesn't know it


def fmt_full(d) -> str:
    return f"{d:%a} {d.day} {d:%b %Y}, {d:%H:%M:%S}"


_LOG_TS = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]\s*(.*)$")


def read_last_sync(path: Path):
    """The newest finished (not preview) sync in the log: {"when", "ok", "detail"} or None."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - 200_000))
            text = f.read().decode("utf-8", "ignore")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        m = _LOG_TS.match(line.strip())
        if not m:
            continue
        when_s, msg = m.groups()
        ok = None
        if msg.startswith("SUCCESS:") and "DRY RUN" not in msg:
            ok, detail = True, msg[len("SUCCESS:"):].strip()
        elif msg.startswith("FINISHED WITH ERRORS"):
            ok, detail = False, msg
        elif msg.startswith("ERROR:"):
            ok, detail = False, msg[len("ERROR:"):].strip()
        if ok is None:
            continue
        try:
            when = dt.datetime.strptime(when_s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        return {"when": when, "ok": ok, "detail": detail}
    return None


def mk_label(text="", role=None, wrap=False, selectable=False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def set_role(w, role):
    w.setProperty("role", role)
    w.style().unpolish(w)
    w.style().polish(w)


class ElideLabel(QLabel):
    """A one-line label that shortens its text with "..." instead of growing or wrapping."""

    def __init__(self, text="", role=None):
        super().__init__()
        self._full = ""
        if role:
            self.setProperty("role", role)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full = text or ""
        super().setText(self._full)
        self.setToolTip(self._full)

    def text(self):
        return self._full

    def minimumSizeHint(self):
        return QSize(16, super().sizeHint().height())

    def paintEvent(self, e):
        QFrame.paintEvent(self, e)          # the style sheet's background / border
        r = self.contentsRect()
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(QPalette.Disabled if not self.isEnabled() else QPalette.Active,
                                      QPalette.WindowText))
        p.drawText(r, int(Qt.AlignLeft | Qt.AlignVCenter),
                   self.fontMetrics().elidedText(self._full, Qt.ElideRight, r.width()))


def mk_button(text, kind=None, slot=None, tip=None, compact=False) -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    if kind:
        b.setProperty("kind", kind)
    if compact:
        b.setProperty("compact", True)
    if slot:
        b.clicked.connect(lambda _=False: slot())
    if tip:
        b.setToolTip(tip)
    return b


def mk_card(spacing=12, margins=(20, 18, 20, 18)):
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return f, lay


def mk_sep():
    f = QFrame()
    f.setObjectName("sep")
    f.setFixedHeight(1)
    return f


def set_kind(w, kind):
    w.setProperty("kind", kind)
    w.style().unpolish(w)
    w.style().polish(w)


def chip(text, kind=None) -> QLabel:
    lb = mk_label(text, "chip")
    lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    set_kind(lb, kind or "")
    return lb


class LogView(QPlainTextEdit):
    """A read-only, monospace log box that keeps the last few thousand lines and scrolls inside itself."""

    def __init__(self, placeholder=""):
        super().__init__()
        self.setReadOnly(True)
        self.setProperty("role", "log")
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setMaximumBlockCount(LOG_LINES)
        if placeholder:
            self.setPlaceholderText(placeholder)

    def append(self, text):
        self.appendPlainText(text)

    def add_raw(self, text):
        """Streams text onto the end (it need not end in a newline)."""
        self.moveCursor(QTextCursor.End)
        self.insertPlainText(text)
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


class Tile(QFrame):
    """A small stat: a tiny title, a bold value and one line of detail."""

    def __init__(self, title, tip=""):
        super().__init__()
        self.setObjectName("tile")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(2)
        self.title = ElideLabel(title.upper(), "eyebrow")
        lay.addWidget(self.title)
        self.value = ElideLabel("-")
        self.value.setStyleSheet("font-size: 17px; font-weight: 700;")
        self.sub = ElideLabel("", "small")
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        if tip:
            self.setToolTip(tip)

    def set(self, value, sub=""):
        self.value.setText(value)
        self.sub.setText(sub)
        self.setToolTip(self.title.text().capitalize() + ": " + value + (f". {sub}" if sub else ""))


class SetupStep(QPushButton):
    """One 'finish setting up' item: a green tick when done, otherwise an amber button that fixes it."""

    def __init__(self, name, todo_text, slot):
        super().__init__()
        self.name, self.todo_text, self.slot = name, todo_text, slot
        self.done = False
        self.setProperty("kind", "step")
        self.clicked.connect(self._clicked)
        self.set_done(False)

    def _clicked(self):
        if not self.done:
            self.slot()

    def set_done(self, done, tip=""):
        self.done = done
        self.setProperty("state", "ok" if done else "todo")
        self.setText(f"\u2713 {self.name}" if done else f"\u26a0 {self.name}  \u00b7  {self.todo_text}")
        self.setCursor(Qt.ArrowCursor if done else Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus if done else Qt.StrongFocus)
        self.setToolTip(tip)
        self.style().unpolish(self)
        self.style().polish(self)


class ProgressPanel(QFrame):
    """What's running now, in one slim row: [title - message] [bar] [Stop]. It sits inside the Overview card,
    and in the header on the other pages."""
    stop_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("inlineprog")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.setMinimumHeight(30)
        self.title = ElideLabel("")
        self.title.setStyleSheet("font-weight: 700;")
        self.title.setMinimumWidth(120)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setMinimumWidth(80)
        self.bar.setMaximumWidth(220)
        self.stop = mk_button("Stop", "danger", self.stop_clicked.emit, compact=True)
        lay.addWidget(self.title, 3)
        lay.addWidget(self.bar, 1, Qt.AlignVCenter)
        lay.addWidget(self.stop)

    def show_state(self, title, message_="", can_stop=True, stop_text="Stop"):
        self.title.setText(title + (f"  \u00b7  {message_}" if message_ else ""))
        self.stop.setText(stop_text)
        self.stop.setVisible(can_stop is not None)
        self.stop.setEnabled(bool(can_stop))


class RadioCard(QFrame):
    """A choice with a title and a line of explanation. The whole card is clickable."""

    def __init__(self, title, desc, group=None):
        super().__init__()
        self.setObjectName("tile")
        self.setCursor(Qt.PointingHandCursor)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 8, 14, 8)
        v.setSpacing(2)
        self.radio = QRadioButton(title)
        self.radio.setStyleSheet("font-weight: 700;")
        self.desc = mk_label(desc, "small", wrap=True)
        self.desc.setIndent(27)
        self.desc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        v.addWidget(self.radio)
        v.addWidget(self.desc)
        if group is not None:
            group.addButton(self.radio)
        self.radio.toggled.connect(self._sel)
        self._sel(False)

    def _sel(self, on):
        self.setProperty("sel", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        if self.radio.isEnabled():
            self.radio.setChecked(True)
        super().mousePressEvent(e)


class Pane(QWidget):
    """A page or tab page. Its minimum height is what its content needs when the window is at its smallest
    (Qt's own guess for wrapped text is the tallest possible one, which would make every page look too tall)."""

    def __init__(self, ref_width=MIN_W - 172):
        super().__init__()
        self.ref_width = ref_width      # how wide this pane is when the window is at its minimum size

    def minimumSizeHint(self):
        lay = self.layout()
        if lay is None:
            return super().minimumSizeHint()
        size = lay.minimumSize()
        if lay.hasHeightForWidth():
            size.setHeight(min(size.height(), lay.minimumHeightForWidth(max(self.ref_width, size.width()))))
        return size


def mk_page(title, margins=(24, 16, 24, 12), spacing=12):
    """A page (a widget with a layout) with its big title row; returns (page, layout, title_row)."""
    page = Pane()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    head = QHBoxLayout()
    head.setSpacing(14)
    head.addWidget(mk_label(title, "h1"))
    lay.addLayout(head)
    return page, lay, head


def mk_tabs():
    tabs = QTabWidget()
    tabs.setDocumentMode(False)
    tabs.tabBar().setDrawBase(False)
    tabs.tabBar().setExpanding(False)
    return tabs


def add_tab(tabs, name, margins=(18, 14, 18, 14), spacing=10):
    w = Pane(MIN_W - 172 - 48 - 2)
    v = QVBoxLayout(w)
    v.setContentsMargins(*margins)
    v.setSpacing(spacing)
    tabs.addTab(w, name)
    return v


def small_list(selection=QAbstractItemView.SingleSelection) -> QListWidget:
    lw = QListWidget()
    lw.setSelectionMode(selection)
    lw.setMinimumHeight(44)
    lw.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    lw.setTextElideMode(Qt.ElideMiddle)
    return lw

class ConfigManager:
    """Loads and saves the config file (a small Bash file, read without Bash)."""

    def __init__(self, path: Path):
        self.path = path
        self.load_error = ""
        self.set_defaults()
        self.load()

    def set_defaults(self):
        self.remote_name = "protondrive"
        self.device_name = hostname()
        self.sync_dirs = [str(HOME / "Documents"), str(HOME / "Pictures")]
        self.exclude_dirs = []
        self.photos_enabled = False
        self.photos_dir = str(DEFAULT_PHOTOS_DIR)
        self.photos_mode = "copy"
        self.photos_categories = []
        self.photos_dates = []
        self.sync_mode = "copy"
        self.notify = True
        self.log_file = str(DEFAULT_LOG_PATH)
        self.log_max_lines = 5000
        self.transfers = 4
        self.checkers = 8
        self.bwlimit = ""
        self.excludes = list(DEFAULT_EXCLUDES)
        self.other_flags = list(DEFAULT_OTHER_FLAGS)

    @property
    def log_path(self) -> Path:
        return Path(self.log_file)

    def load(self):
        self.load_error = ""
        if not self.path.exists():
            return
        try:
            cfg = Settings(self.path, defaults={"sync_dirs": [], "rclone_flags": []})
        except (ConfigError, OSError) as e:
            self.load_error = f"Couldn't read {self.path} ({e}). Defaults are shown."
            return

        self.remote_name = cfg.remote_name or "protondrive"
        self.device_name = cfg.device_name.strip() or hostname()
        self.sync_mode = cfg.sync_mode if cfg.sync_mode in ("copy", "sync", "bisync") else "copy"
        self.notify = cfg.notify
        self.log_file = expand_path(cfg.log_file) or str(DEFAULT_LOG_PATH)
        self.log_max_lines = cfg.log_max_lines
        self.sync_dirs = [expand_path(d) or "/" for d in cfg.sync_dirs]
        self.exclude_dirs = [expand_path(d) for d in cfg.exclude_dirs if d.strip("/\\ ")]
        self.photos_enabled = cfg.photos_enabled
        self.photos_dir = expand_path(cfg.photos_dir) or str(DEFAULT_PHOTOS_DIR)
        self.photos_mode = cfg.photos_mode if cfg.photos_mode in ("copy", "sync", "bisync") else "copy"
        self.photos_categories = list(cfg.photos_categories)
        self.photos_dates = list(cfg.photos_dates)

        flags = cfg.rclone_flags
        self.excludes, self.other_flags, self.bwlimit = [], [], ""
        i = 0
        while i < len(flags):
            t = flags[i]
            if t == "--exclude" and i + 1 < len(flags):
                self.excludes.append(flags[i + 1]); i += 2; continue
            if t.startswith("--exclude="):
                self.excludes.append(t.split("=", 1)[1])
            elif t.startswith("--transfers="):
                self.transfers = int(t.split("=", 1)[1]) if t.split("=", 1)[1].isdigit() else self.transfers
            elif t.startswith("--checkers="):
                self.checkers = int(t.split("=", 1)[1]) if t.split("=", 1)[1].isdigit() else self.checkers
            elif t.startswith("--bwlimit="):
                self.bwlimit = t.split("=", 1)[1]
            elif t in ("--include", "--filter", "--filter-from", "--exclude-from", "--include-from") and i + 1 < len(flags):
                self.other_flags += [t, flags[i + 1]]; i += 2; continue
            else:
                self.other_flags.append(t)
            i += 1

    def to_settings(self) -> Settings:
        flags = [f"--transfers={int(self.transfers)}", f"--checkers={int(self.checkers)}"]
        if self.bwlimit:
            flags.append("--bwlimit=" + self.bwlimit)
        i = 0
        while i < len(self.other_flags):
            f = self.other_flags[i]
            if f.startswith(("--transfers", "--checkers", "--bwlimit")):
                i += 1; continue
            if f in ("--include", "--filter", "--filter-from", "--exclude-from", "--include-from") and i + 1 < len(self.other_flags):
                flags += [f, self.other_flags[i + 1]]; i += 2; continue
            flags.append(f); i += 1
        for exc in self.excludes:
            flags += ["--exclude", exc]
        return Settings(defaults={
            "remote_name": self.remote_name, "device_name": self.device_name, "sync_dirs": list(self.sync_dirs),
            "exclude_dirs": list(self.exclude_dirs), "sync_mode": self.sync_mode,
            "photos_enabled": self.photos_enabled, "photos_dir": self.photos_dir, "photos_mode": self.photos_mode,
            "photos_categories": list(self.photos_categories), "photos_dates": list(self.photos_dates),
            "notify": self.notify, "log_file": self.log_file, "log_max_lines": int(self.log_max_lines),
            "rclone_flags": flags})

    def save(self):
        """Writes the config atomically and keeps a .bak of the previous one."""
        try:
            save_config(self.to_settings(), self.path, "sleepy-proton-gui")
        except ConfigError as e:
            raise RuntimeError(str(e))



def field(label_text, *widgets, row=False):
    """A small label above one or more inputs, as one widget."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(4)
    v.addWidget(mk_label(label_text, "muted"))
    if len(widgets) == 1 and not row:
        v.addWidget(widgets[0])
    else:
        h = QHBoxLayout()
        h.setSpacing(8)
        for x in widgets:
            h.addWidget(x, 1 if isinstance(x, QLineEdit) else 0)
        v.addLayout(h)
    return w


class SetupWizardDialog(QDialog):
    """Four steps (account, folders, sync settings, automation), each small enough to fit without scrolling."""

    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        ensure_theme()
        self.config = config or ConfigManager(CONFIG_PATH)
        self.setWindowTitle("Sleepy Proton Drive: Setup wizard")

        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        w, h = (min(820, geo.width() - 40), min(620, geo.height() - 60)) if geo else (820, 620)
        self.setMinimumSize(720, 540)
        self.resize(max(w, 720), max(h, 540))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # header (same look as the main window's)
        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 10, 24, 10)
        hl.setSpacing(14)
        col = QVBoxLayout()
        col.setSpacing(0)
        t = mk_label("Setup wizard")
        t.setStyleSheet("font-size: 20px; font-weight: 800;")
        self.h_desc = mk_label("Log in to your Proton Drive account", "small")
        col.addWidget(t)
        col.addWidget(self.h_desc)
        hl.addLayout(col, 1)
        self.step_label = chip("Step 1 of 4", "ok")
        hl.addWidget(self.step_label)
        outer.addWidget(header)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.create_page_account()
        self.create_page_folders()
        self.create_page_sync_settings()
        self.create_page_automation()

        # bottom navigation bar
        bar = QFrame()
        bar.setObjectName("strip")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(24, 8, 24, 8)
        self.btn_cancel = mk_button("Cancel", slot=self.reject)
        self.btn_back = mk_button("← Back", slot=self.prev_page)
        self.btn_back.setEnabled(False)
        self.btn_next = mk_button("Next step →", "primary", self.next_page)
        bl.addWidget(self.btn_cancel)
        bl.addStretch(1)
        bl.addWidget(self.btn_back)
        bl.addWidget(self.btn_next)
        outer.addWidget(bar)

    def _page(self, spacing=12):
        page = Pane(720)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 16, 24, 12)
        lay.setSpacing(spacing)
        self.stack.addWidget(page)
        return lay

    # ---- step 1: account
    def create_page_account(self):
        layout = self._page(12)
        layout.addWidget(mk_label(
            "Log in to Proton Drive. Your login is handed to rclone on this computer and saved in "
            f"{RCLONE_CONF_HINT}. Sleepy Proton Drive never sends it anywhere else.", "muted", wrap=True))

        card, cl = mk_card(12, (18, 14, 18, 14))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.wizard_user = QLineEdit()
        self.wizard_user.setPlaceholderText("username@pm.me or username")
        grid.addWidget(field("Proton email or username", self.wizard_user), 0, 0)

        self.wizard_pass = QLineEdit()
        self.wizard_pass.setEchoMode(QLineEdit.Password)
        self.wizard_pass.setPlaceholderText("Your account password")
        self.btn_toggle_pass = mk_button("Show", compact=True)
        self.btn_toggle_pass.setFixedWidth(64)
        self.btn_toggle_pass.clicked.connect(self.toggle_password_visibility)
        grid.addWidget(field("Proton password", self.wizard_pass, self.btn_toggle_pass), 0, 1)

        self.wizard_2fa = QLineEdit()
        self.wizard_2fa.setPlaceholderText("6 digits, just before you log in")
        self.wizard_2fa.setMaxLength(8)
        self.wizard_2fa.setToolTip("The 6 digits from your authenticator app. Leave blank if you don't use 2FA.")
        grid.addWidget(field("2FA code (blank if none)", self.wizard_2fa), 1, 0)

        self.wizard_mailbox_pass = QLineEdit()
        self.wizard_mailbox_pass.setEchoMode(QLineEdit.Password)
        self.wizard_mailbox_pass.setPlaceholderText("Your legacy mailbox password")
        self.box_mailbox = field("Mailbox password", self.wizard_mailbox_pass)
        self.box_mailbox.hide()
        grid.addWidget(self.box_mailbox, 1, 1)

        self.chk_two_pass = QCheckBox("Two-password mode")
        self.chk_two_pass.setToolTip("Tick this if your Proton account uses Two-Password Mode (a separate mailbox password).")
        self.chk_two_pass.toggled.connect(self.toggle_mailbox_field)
        grid.addWidget(self.chk_two_pass, 2, 0)

        self.chk_otp_secret = QCheckBox("Save my 2FA secret (less secure)")
        self.chk_otp_secret.setToolTip("Optional. Lets rclone log in again by itself when Proton asks for a new 2FA code.")
        self.chk_otp_secret.toggled.connect(lambda c: self.box_otp.setVisible(c))
        grid.addWidget(self.chk_otp_secret, 2, 1)

        self.wizard_otp_secret = QLineEdit()
        self.wizard_otp_secret.setEchoMode(QLineEdit.Password)
        self.wizard_otp_secret.setPlaceholderText("Base32 secret key shown when you set up 2FA")
        self.box_otp = field("2FA secret key (lets rclone log in again by itself)", self.wizard_otp_secret)
        self.box_otp.hide()
        grid.addWidget(self.box_otp, 3, 0, 1, 2)
        cl.addLayout(grid)
        layout.addWidget(card)

        row = QHBoxLayout()
        row.setSpacing(14)
        self.btn_test_conn = mk_button("Log in && test", "primary")
        self.btn_test_conn.clicked.connect(self.authenticate_rclone)
        self.lbl_auth_status = mk_label("Not logged in yet", "muted", wrap=True)
        self.auth_ok = False
        self.auth_proc = None
        row.addWidget(self.btn_test_conn)
        row.addWidget(self.lbl_auth_status, 1)
        layout.addLayout(row)
        layout.addStretch(1)
        QTimer.singleShot(0, self.check_existing_login)

    def set_auth_status(self, text, color, bold=False):
        """color: one of the status looks ("ok", "bad", "warn", "muted")."""
        role = {"#10b981": "ok", "#ec4899": "bad", "#38bdf8": "warn", "ok": "ok", "bad": "bad",
                "warn": "warn"}.get(color, "muted")
        self.lbl_auth_status.setText(text)
        set_role(self.lbl_auth_status, role)

    def check_existing_login(self):
        if not have_rclone():
            self.set_auth_status(f"rclone is not installed. {FIX_HINT}", "bad")
            self.btn_test_conn.setEnabled(False)
            return
        if self.config.remote_name in rclone_remotes():
            self.set_auth_status(f"Checking your existing login ('{self.config.remote_name}')...", "warn")
            self.run_login_test()

    def toggle_password_visibility(self):
        if self.wizard_pass.echoMode() == QLineEdit.Password:
            self.wizard_pass.setEchoMode(QLineEdit.Normal)
            self.btn_toggle_pass.setText("Hide")
        else:
            self.wizard_pass.setEchoMode(QLineEdit.Password)
            self.btn_toggle_pass.setText("Show")

    def toggle_mailbox_field(self, checked):
        self.box_mailbox.setVisible(checked)

    def authenticate_rclone(self):
        user = self.wizard_user.text().strip()
        pwd = self.wizard_pass.text()
        totp = self.wizard_2fa.text().replace(" ", "")
        mailbox_pwd = self.wizard_mailbox_pass.text() if self.chk_two_pass.isChecked() else ""
        otp_secret = self.wizard_otp_secret.text().replace(" ", "") if self.chk_otp_secret.isChecked() else ""

        if not have_rclone():
            QMessageBox.critical(self, "rclone missing", f"rclone is not installed.\n{FIX_HINT}")
            return
        if not user or not pwd:
            QMessageBox.warning(self, "Missing details", "Please enter your Proton email/username and password.")
            return
        if totp and not totp.isdigit():
            QMessageBox.warning(self, "2FA code", "The 2FA code should be numbers only.")
            return

        def obscure(secret):
            return run_quiet([rclone_bin(), "obscure", "-"], input=secret, timeout=15).stdout.strip()

        # Secrets are obscured first so they never show up in plain text in the process list.
        args = ["config", "create", self.config.remote_name, "protondrive", "--no-obscure",
                "username", user, "password", obscure(pwd)]
        if mailbox_pwd:
            args += ["mailbox_password", obscure(mailbox_pwd)]
        if otp_secret:
            args += ["otp_secret_key", obscure(otp_secret)]
        if totp:
            args += ["2fa", totp]

        self.set_auth_status("Logging in to Proton (can take up to a minute)...", "warn")
        self.btn_test_conn.setEnabled(False)
        self.btn_next.setEnabled(False)
        self.auth_proc = QProcess(self)
        self.auth_proc.setProcessChannelMode(QProcess.MergedChannels)
        self.auth_proc.finished.connect(self.on_login_created)
        self.auth_proc.start(rclone_bin(), args)
        self.wizard_pass.clear()
        self.wizard_2fa.clear()

    def on_login_created(self, code, _status):
        output = bytes(self.auth_proc.readAll()).decode("utf-8", "ignore")
        if code != 0:
            self.btn_test_conn.setEnabled(True)
            self.btn_next.setEnabled(True)
            self.set_auth_status("Login failed. Check your password / 2FA code and try again.", "bad")
            safe = "\n".join(l for l in output.splitlines()[-8:] if not re.search(r"password|otp|token|salted", l, re.I))
            QMessageBox.critical(self, "Login failed", f"rclone couldn't log in:\n\n{safe or 'No details given.'}")
            return
        self.set_auth_status("Logged in. Testing access to your Drive...", "warn")
        self.run_login_test()

    def run_login_test(self):
        self.btn_test_conn.setEnabled(False)
        self.test_proc = QProcess(self)
        self.test_proc.setProcessChannelMode(QProcess.MergedChannels)
        self.test_proc.finished.connect(self.on_login_tested)
        self.test_proc.start(rclone_bin(), ["lsd", f"{self.config.remote_name}:", "--contimeout", "15s", "--timeout", "60s"])
        QTimer.singleShot(90_000, lambda: self.test_proc.state() != QProcess.NotRunning and self.test_proc.kill())

    def on_login_tested(self, code, _status):
        self.btn_test_conn.setEnabled(True)
        self.btn_next.setEnabled(True)
        if code == 0:
            self.auth_ok = True
            self.set_auth_status(f"Logged in to Proton Drive (remote '{self.config.remote_name}'). Click Next.", "ok")
        else:
            self.auth_ok = False
            self.set_auth_status("Couldn't open your Drive. Log in again above.", "bad")

    # ---- step 2: folders
    def create_page_folders(self):
        layout = self._page(10)
        layout.addWidget(mk_label("Pick the folders on this computer to keep in sync with Proton Drive.", "muted",
                                  wrap=True))

        card, cl = mk_card(8, (18, 12, 18, 12))
        cl.addWidget(mk_label("QUICK ADD", "eyebrow"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self.preset_checks = {}
        standard_dirs = [
            ("Documents", str(Path.home() / "Documents")),
            ("Pictures", str(Path.home() / "Pictures")),
            ("Desktop", str(Path.home() / "Desktop")),
            ("Downloads", str(Path.home() / "Downloads")),
            ("Music", str(Path.home() / "Music")),
            ("Videos", str(Path.home() / "Videos")),
        ]
        for idx, (name, path) in enumerate(standard_dirs):
            chk = QCheckBox(f"{name}  ({short_home(path)})")
            chk.setToolTip(path)
            chk.setChecked(path_in_list(path, self.config.sync_dirs))
            chk.toggled.connect(self.sync_preset_toggled)
            self.preset_checks[path] = chk
            row, col = divmod(idx, 2)
            grid.addWidget(chk, row, col)
        cl.addLayout(grid)
        layout.addWidget(card)

        layout.addWidget(mk_label("Folders that will be synced:", "muted"))
        self.folders_list_wizard = small_list()
        self.folders_list_wizard.setMinimumHeight(60)
        for folder in self.config.sync_dirs:
            self.folders_list_wizard.addItem(folder)
        layout.addWidget(self.folders_list_wizard, 1)

        h_btns = QHBoxLayout()
        h_btns.setSpacing(8)
        h_btns.addWidget(mk_button("Add custom folder...", slot=self.wizard_add_folder, compact=True))
        h_btns.addWidget(mk_button("Remove selected", slot=self.wizard_rem_folder, compact=True))
        h_btns.addStretch(1)
        layout.addLayout(h_btns)

    def sync_preset_toggled(self):
        current_in_list = [self.folders_list_wizard.item(i).text() for i in range(self.folders_list_wizard.count())]
        for path, chk in self.preset_checks.items():
            if chk.isChecked() and path not in current_in_list:
                self.folders_list_wizard.addItem(path)
            elif not chk.isChecked() and path in current_in_list:
                for i in range(self.folders_list_wizard.count()):
                    if self.folders_list_wizard.item(i).text() == path:
                        self.folders_list_wizard.takeItem(i)
                        break

    def wizard_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Sync", str(Path.home()))
        if folder:
            folder = tidy_folder(folder)
            existing = [self.folders_list_wizard.item(i).text() for i in range(self.folders_list_wizard.count())]
            clash = [d for d in existing if same_name(d, folder) and not same_path(d, folder)]
            if clash:
                QMessageBox.warning(self, "Same folder name",
                                    f"'{clash[0]}' has the same name, and both would go to the same cloud folder.")
                return
            if folder not in existing:
                self.folders_list_wizard.addItem(folder)
                if folder in self.preset_checks:
                    self.preset_checks[folder].setChecked(True)

    def wizard_rem_folder(self):
        row = self.folders_list_wizard.currentRow()
        if row >= 0:
            item = self.folders_list_wizard.takeItem(row)
            if item.text() in self.preset_checks:
                self.preset_checks[item.text()].setChecked(False)

    # ---- step 3: sync settings
    def create_page_sync_settings(self):
        layout = self._page(12)
        cols = QHBoxLayout()
        cols.setSpacing(16)
        layout.addLayout(cols, 1)

        left = QVBoxLayout()
        left.setSpacing(12)
        card, cl = mk_card(8, (18, 14, 18, 14))
        cl.addWidget(mk_label("DEVICE NAME", "eyebrow"))
        self.wizard_device = QLineEdit(self.config.device_name)
        self.wizard_device.setPlaceholderText("e.g. Laptop, Desktop, Work-PC")
        cl.addWidget(self.wizard_device)
        cl.addWidget(mk_label("Your files go to protondrive:Devices/<name>/...", "small", wrap=True))
        left.addWidget(card)

        card, cl = mk_card(8, (18, 14, 18, 14))
        cl.addWidget(mk_label("TRANSFERS", "eyebrow"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.addWidget(mk_label("Parallel transfers", "muted"), 0, 0)
        self.w_spin_trans = QSpinBox()
        self.w_spin_trans.setRange(1, 16)
        self.w_spin_trans.setValue(self.config.transfers)
        grid.addWidget(self.w_spin_trans, 0, 1)
        grid.addWidget(mk_label("Checker threads", "muted"), 1, 0)
        self.w_spin_check = QSpinBox()
        self.w_spin_check.setRange(1, 32)
        self.w_spin_check.setValue(self.config.checkers)
        grid.addWidget(self.w_spin_check, 1, 1)
        grid.setColumnStretch(0, 1)
        cl.addLayout(grid)
        left.addWidget(card)
        left.addStretch(1)
        cols.addLayout(left, 1)

        card, cl = mk_card(8, (18, 14, 18, 14))
        cl.addWidget(mk_label("HOW TO SYNC", "eyebrow"))
        self.w_mode_group = QButtonGroup(self)
        self.w_mode_cards = {}
        for key, _name, title, desc in SYNC_MODES:
            rc = RadioCard(title, desc, self.w_mode_group)
            self.w_mode_cards[key] = rc
            cl.addWidget(rc)
        cl.addStretch(1)
        self.w_radio_copy = self.w_mode_cards["copy"].radio
        self.w_radio_sync = self.w_mode_cards["sync"].radio
        self.w_radio_bisync = self.w_mode_cards["bisync"].radio
        self.w_mode_cards.get(self.config.sync_mode, self.w_mode_cards["copy"]).radio.setChecked(True)
        cols.addWidget(card, 1)

    # ---- step 4: automation
    def create_page_automation(self):
        layout = self._page(12)
        card, cl = mk_card(12, (18, 14, 18, 14))
        cl.addWidget(mk_label(f"BACKGROUND SYNC  ({TIMER_KIND})".upper(), "eyebrow"))
        active, current_interval = timer_status()
        self.w_chk_timer = QCheckBox("Sync automatically in the background")
        self.w_chk_timer.setChecked(active or not CONFIG_PATH.exists())
        cl.addWidget(self.w_chk_timer)
        h_int = QHBoxLayout()
        h_int.setSpacing(10)
        h_int.addWidget(mk_label("Sync every", "muted"))
        self.w_combo_interval = QComboBox()
        self.w_combo_interval.addItems(TIMER_INTERVALS)
        self.w_combo_interval.setCurrentText(current_interval if current_interval in TIMER_INTERVALS else "30min")
        self.w_combo_interval.setMinimumWidth(110)
        h_int.addWidget(self.w_combo_interval)
        h_int.addStretch(1)
        cl.addLayout(h_int)
        self.w_chk_notify = QCheckBox("Show a desktop notification when a sync finishes")
        self.w_chk_notify.setChecked(self.config.notify)
        cl.addWidget(self.w_chk_notify)
        layout.addWidget(card)

        card_finish = QFrame()
        card_finish.setObjectName("row")
        cf = QVBoxLayout(card_finish)
        cf.setContentsMargins(18, 12, 18, 12)
        cf.setSpacing(4)
        cf.addWidget(mk_label("All set", "ok"))
        cf.addWidget(mk_label(
            "Click 'Complete setup' to save your settings and (if ticked) turn on background syncing. "
            "Tip: use Preview on the Overview page first to see what will be uploaded.", "muted", wrap=True))
        layout.addWidget(card_finish)
        layout.addStretch(1)

    def next_page(self):
        idx = self.stack.currentIndex()
        if idx == 0 and not self.auth_ok:
            reply = QMessageBox.question(
                self, "Not logged in",
                "You haven't logged in to Proton Drive yet, so syncing won't work.\n\nContinue anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        if idx == self.stack.count() - 1:
            self.finish_wizard()
        else:
            self.stack.setCurrentIndex(idx + 1)
            self.update_nav()

    def prev_page(self):
        idx = self.stack.currentIndex()
        if idx > 0:
            self.stack.setCurrentIndex(idx - 1)
            self.update_nav()

    def update_nav(self):
        idx = self.stack.currentIndex()
        count = self.stack.count()
        self.step_label.setText(f"Step {idx + 1} of {count}")
        self.btn_back.setEnabled(idx > 0)

        step_descriptions = [
            "Log in to your Proton Drive account",
            "Choose the folders to keep in Proton Drive",
            "Choose how to sync, the device name and the speed",
            "Turn on automatic background syncing and notifications",
        ]
        if idx < len(step_descriptions):
            self.h_desc.setText(step_descriptions[idx])
        self.btn_next.setText("Complete setup" if idx == count - 1 else "Next step →")

    def finish_wizard(self):
        dirs = [self.folders_list_wizard.item(i).text() for i in range(self.folders_list_wizard.count())]
        self.config.sync_dirs = dirs
        self.config.device_name = self.wizard_device.text().strip().replace("/", "-") or hostname()

        if self.w_radio_sync.isChecked():
            self.config.sync_mode = "sync"
        elif self.w_radio_bisync.isChecked():
            self.config.sync_mode = "bisync"
        else:
            self.config.sync_mode = "copy"

        self.config.transfers = self.w_spin_trans.value()
        self.config.checkers = self.w_spin_check.value()
        self.config.notify = self.w_chk_notify.isChecked()
        try:
            self.config.save()
        except (OSError, RuntimeError) as e:
            QMessageBox.critical(self, "Couldn't save", f"Your settings couldn't be saved:\n{e}")
            return

        ok, msg = set_timer(self.w_chk_timer.isChecked(), self.w_combo_interval.currentText())
        if not ok:
            QMessageBox.warning(self, "Background sync", f"Settings saved, but the background timer couldn't be set:\n{msg}")
        self.accept()



def set_timer(enabled: bool, interval: str = "30min"):
    """Turns the background timer (systemd / Task Scheduler) on or off. Returns (ok, message)."""
    try:
        if enabled:
            scheduler.enable(interval, CONFIG_PATH)
            return True, f"Background sync enabled: every {interval} ({scheduler.describe()})."
        scheduler.disable()
        return True, "Background sync disabled."
    except (ValueError, scheduler.TimerError, OSError) as e:
        return False, str(e)


def stop_tool_process(proc, name):
    """Asks one of our tools started with QProcess to stop. Linux: a normal stop signal.
    Windows has none for background programs, so the tool watches for a stop request."""
    if IS_WINDOWS:
        if name == "photos":
            from .photos import LOCK
        else:
            from .sync import LOCK
        LOCK.request_stop()
        pid = proc.processId()
        QTimer.singleShot(25_000, lambda: proc.state() != QProcess.NotRunning and kill_tree(pid, force=True))
    else:
        proc.terminate()



class MainWindow(QMainWindow):
    """Header + left sidebar + one page at a time (Overview, Folders, Photos, Settings, Log)."""
    PAGE_OVERVIEW, PAGE_FOLDERS, PAGE_PHOTOS, PAGE_SETTINGS, PAGE_LOG = range(5)

    def __init__(self):
        super().__init__()
        ensure_theme()
        self.setWindowTitle("Sleepy Proton Drive")

        # Default size, but never bigger than the screen (and never smaller than the minimum)
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen else None
        w, h = DEFAULT_W, DEFAULT_H
        if geo:
            w, h = min(w, geo.width() - 40), min(h, geo.height() - 60)
        self.resize(max(w, MIN_W), max(h, MIN_H))
        self.setMinimumSize(MIN_W, MIN_H)

        icon = next((p for p in ICON_CANDIDATES if p.exists()), None)
        self.icon = QIcon(str(icon)) if icon else QIcon.fromTheme("sleepy-proton-drive")
        self.setWindowIcon(self.icon)

        self.config = ConfigManager(CONFIG_PATH)
        self.process = None
        self.diag_proc = None
        self._ctl_proc = None
        self._log_mtime = None
        self.photos_proc = None
        self.photos_status_proc = None
        self._photos_index = {}
        self._photos_last = ""        # the newest line Proton Photos printed
        self.run_label = ""            # what our own sync is doing ("Syncing", "Preview ...")
        self.last_line = ""            # the newest line the sync printed
        self._bg = None                # PID of a sync started outside this window
        self.login_ok = None           # None = not checked yet
        self.photos_state = None       # "signed-in" / "cli missing" / "other" / None
        self.timer_active, self.timer_interval = False, ""
        self._last_sync, self._last_sync_key = None, None
        self._ticks = 0
        self._strip_job = None         # which job the header strip shows: "sync" / "photos" / "diag" / None
        self._shown_interval = None    # the timer interval the Settings combo last showed
        self._saved_snapshot = None    # Settings as last saved (to spot unsaved changes)
        self._built = False            # becomes True once every page exists

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.head_stack = QStackedWidget()
        outer.addWidget(self.build_header())

        # the middle of the header shows (when needed) the running job, or a short message for a few seconds
        self.toast_box = QFrame()
        self.toast_box.setObjectName("toast")
        tl = QHBoxLayout(self.toast_box)
        tl.setContentsMargins(12, 2, 4, 2)
        self.toast_lbl = ElideLabel("")
        tl.addWidget(self.toast_lbl, 1)
        tl.addWidget(mk_button("Dismiss", "link", self._hide_toast, compact=True))
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self._hide_toast)
        self.strip = ProgressPanel()
        self.strip.stop_clicked.connect(self.stop_current)
        self.head_stack.addWidget(QWidget())            # 0: nothing to show
        self.head_stack.addWidget(self.strip)           # 1: a job that is running
        self.head_stack.addWidget(self.toast_box)       # 2: a message

        # body: sidebar | pages
        body = QHBoxLayout()
        body.setSpacing(0)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(172)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 14, 12, 12)
        sl.setSpacing(4)
        self.nav_group = QButtonGroup(self)
        self.stack = QStackedWidget()
        pages = (("Overview", self.build_page_overview), ("Folders", self.build_page_folders),
                 ("Photos", self.build_page_photos), ("Settings", self.build_page_settings),
                 ("Log", self.build_page_log))
        for i, (name, build) in enumerate(pages):
            b = QPushButton(name)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.nav_group.addButton(b, i)
            sl.addWidget(b)
            self.stack.addWidget(build())
        sl.addStretch(1)
        self.btn_wizard = mk_button("Setup wizard...", "link", self.open_setup_wizard,
                                    "Log in to Proton Drive and choose your folders step by step")
        self.btn_diag = mk_button("System check", "link", self.run_diagnostics,
                                  "Checks that rclone and everything else the app needs is working")
        for b in (self.btn_wizard, self.btn_diag):
            b.setStyleSheet("text-align: left;")
            sl.addWidget(b)
        sl.addSpacing(4)
        sl.addWidget(mk_label(f"Version {APP_VERSION}", "small"))
        self.nav_group.idClicked.connect(self.go)
        body.addWidget(side)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.nav_group.button(0).setChecked(True)
        self._built = True

        # background checks: log + running state every 3 seconds
        self.log_timer = QTimer(self)
        self.log_timer.setInterval(3000)
        self.log_timer.timeout.connect(self.tick)
        self.log_timer.start()
        self.update_timer_status()
        self.refresh_last_sync()
        self.update_overview()
        self.update_header()
        QTimer.singleShot(200, self.refresh_running_state)
        QTimer.singleShot(250, self.refresh_setup_state)

        # First start: offer the wizard (or open it straight away with --wizard)
        if "--wizard" in sys.argv:
            QTimer.singleShot(300, self.open_setup_wizard)
        else:
            QTimer.singleShot(400, self.check_first_run)

    def check_first_run(self):
        if self.config.load_error:
            QMessageBox.warning(self, "Config problem", self.config.load_error)
        if not have_rclone():
            QMessageBox.warning(
                self, "rclone is missing",
                "Sleepy Proton Drive uses rclone to talk to Proton Drive, and it isn't installed.\n\n"
                f"{FIX_HINT}\n\n"
                "Then start this app again.")
            return
        if self.config.remote_name not in rclone_remotes():
            reply = QMessageBox.question(
                self, "Welcome to Sleepy Proton Drive",
                "You're not logged in to Proton Drive yet.\n\nOpen the setup wizard now?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.open_setup_wizard()

    def build_header(self):
        header = QFrame()
        header.setObjectName("header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 8, 20, 8)
        hl.setSpacing(14)
        ic = QLabel()
        if not self.icon.isNull():
            ic.setPixmap(self.icon.pixmap(QSize(40, 40)))
        ic.setFixedSize(40, 40)
        hl.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(0)
        t = mk_label(APP_NAME)
        t.setStyleSheet("font-size: 20px; font-weight: 800;")
        col.addWidget(t)
        col.addWidget(mk_label("by The Sleepy Labs", "small"))
        hl.addLayout(col)
        hl.addSpacing(10)
        hl.addWidget(self.head_stack, 1)
        self.head_chip = chip("")
        hl.addWidget(self.head_chip)
        return header

    # -- navigation, messages
    def go(self, i):
        self.stack.setCurrentIndex(i)
        self.nav_group.button(i).setChecked(True)
        if i == self.PAGE_OVERVIEW:
            self.update_timer_status()
            self.refresh_last_sync()
            self.update_overview()
        elif i == self.PAGE_LOG:
            self.load_full_log(force=False)
        self.update_progress()

    def show_output(self):
        """Opens Log > This window, where the output of a preview / system check shows up."""
        self.log_tabs.setCurrentIndex(1)
        self.go(self.PAGE_LOG)

    def _hide_toast(self):
        self.toast_timer.stop()
        self.update_progress()

    def toast(self, text, kind="ok", seconds=6):
        self.toast_lbl.setText(str(text).replace("\n", " "))
        set_kind(self.toast_box, kind)
        self.toast_timer.start(int(seconds * 1000))
        self.update_progress()

    def tick(self):
        self._ticks += 1
        before = self._last_sync_key
        self.refresh_last_sync()
        if self._last_sync_key != before:
            self.update_header()          # a background sync finished: the chip follows (Overview updates below)
        self.auto_refresh_log()
        self.refresh_running_state()
        if self._ticks % 10 == 0:
            self.update_timer_status()
        if self._ticks % 20 == 0:
            self.refresh_setup_state()

    # -- status: busy / progress / header chip
    def sync_busy(self) -> bool:
        return self.own_sync_running() or bool(self._bg) or bool(self._ctl_proc and self._ctl_proc.state() != QProcess.NotRunning)

    def photos_busy(self) -> bool:
        return bool(self.photos_proc and self.photos_proc.state() != QProcess.NotRunning)

    def diag_busy(self) -> bool:
        return bool(self.diag_proc and self.diag_proc.state() != QProcess.NotRunning)

    def sync_state(self):
        """(title, message, can_stop) for the sync that is running, or None."""
        ctl_busy = bool(self._ctl_proc and self._ctl_proc.state() != QProcess.NotRunning)
        if self.own_sync_running():
            return (self.run_label or "Syncing", self.last_line, not ctl_busy)
        if self._bg:
            return ("Background sync running", "started automatically", not ctl_busy)
        if ctl_busy:
            return ("Working", "", False)
        return None

    def update_progress(self):
        if not self._built:
            return
        st = self.sync_state()
        self.prog_ov.setVisible(bool(st))
        self.ov_detail.setVisible(not st and bool(self.ov_detail.text()))
        if st:
            self.prog_ov.show_state(st[0] + "...", st[1], st[2], "Stop sync")
        # The header shows the job on the pages that don't show it themselves (and a message for a few seconds).
        page = self.stack.currentIndex()
        job, self._strip_job = None, None
        if st and page != self.PAGE_OVERVIEW:
            job, self._strip_job = (st[0] + "...", st[1], st[2], "Stop sync"), "sync"
        elif self.photos_busy() and page != self.PAGE_PHOTOS:
            job, self._strip_job = ("Downloading Proton Photos...", self._photos_last, True, "Stop"), "photos"
        elif self.diag_busy() and page != self.PAGE_LOG:
            job, self._strip_job = ("Running the system check...", "", None, "Stop"), "diag"   # no Stop button
        if job:
            self.strip.show_state(*job)
        if self.toast_timer.isActive():
            self.head_stack.setCurrentIndex(2)
        else:
            self.head_stack.setCurrentIndex(1 if job else 0)

    def stop_current(self):
        """The Stop button of the header strip: stops exactly the job the strip is showing."""
        kind = self._strip_job
        if kind == "sync":
            st = self.sync_state()
            if st and st[2]:
                self.cancel_sync_process()
        elif kind == "photos":
            self.stop_photos()
        # "diag": the strip shows no Stop button for the system check

    def update_header(self):
        if not self._built:
            return
        if self.sync_busy() or self.photos_busy() or self.diag_busy():
            text, kind = "Working...", "warn"
        elif self.login_ok is False:
            text, kind = "Not set up", "warn"
        elif self._last_sync and not self._last_sync["ok"]:
            text, kind = "Last sync failed", "bad"
        else:
            text, kind = "Ready", "ok"
        self.head_chip.setText(text)
        set_kind(self.head_chip, kind)

    def refresh_setup_state(self):
        """Is rclone there and logged in? (a quick look at rclone's config)"""
        self.login_ok = bool(have_rclone() and self.config.remote_name in rclone_remotes())
        self.update_overview()
        self.update_header()

    def refresh_last_sync(self):
        path = self.config.log_path
        try:
            st = path.stat()
            key = (st.st_mtime, st.st_size)
        except OSError:
            key = None
        if key != self._last_sync_key:
            self._last_sync_key = key
            self._last_sync = read_last_sync(path) if key else None

    # --------------------------------------------------------------------------
    # Page: Overview
    # --------------------------------------------------------------------------
    def build_page_overview(self):
        page, lay, head = mk_page("Overview", spacing=14)
        head.addStretch(1)

        card, cl = mk_card(0, (20, 18, 20, 18))
        top = QHBoxLayout()
        top.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(5)
        left.addWidget(mk_label("LAST SYNC", "eyebrow"))
        row = QHBoxLayout()
        row.setSpacing(12)
        self.last_title = mk_label("No sync yet")
        self.last_title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.last_title.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.last_chip = chip("")
        row.addWidget(self.last_title, 0)
        row.addWidget(self.last_chip, 0, Qt.AlignVCenter)
        row.addStretch(1)
        left.addLayout(row)
        left.addStretch(1)
        top.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(6)
        self.btn_sync_now = mk_button("Sync now", "primary", lambda: self.run_sync_process(dry_run=False),
                                      "Background syncs keep going if you close this window; a sync started here asks first.")
        self.btn_sync_now.setStyleSheet("font-size: 16px; padding: 12px 20px; font-weight: 700; border-radius: 12px;")
        right.addWidget(self.btn_sync_now)
        small = QHBoxLayout()
        small.setSpacing(6)
        self.btn_dry_run = mk_button("Preview", slot=lambda: self.run_sync_process(dry_run=True), compact=True,
                                     tip="Dry run: shows what would be copied and changes nothing")
        self.btn_restart_sync = mk_button("Restart sync", slot=self.restart_sync_process, compact=True,
                                          tip="Stop the running sync (if any) and start a fresh one")
        small.addWidget(self.btn_dry_run)
        small.addWidget(self.btn_restart_sync)
        right.addLayout(small)
        right_w = QWidget()
        right_w.setLayout(right)
        right_w.setFixedWidth(224)
        top.addWidget(right_w, 0, Qt.AlignVCenter)
        cl.addLayout(top)
        cl.addSpacing(10)

        # one slot, same height always: the detail text, or (while a sync runs) the progress row
        slot = QHBoxLayout()
        self.ov_detail = ElideLabel("", "muted")
        self.ov_detail.setMinimumHeight(30)
        slot.addWidget(self.ov_detail, 1)
        self.prog_ov = ProgressPanel()
        self.prog_ov.hide()
        self.prog_ov.stop_clicked.connect(self.cancel_sync_process)
        self.btn_cancel_sync = self.prog_ov.stop          # "Stop sync" lives in the progress row
        self.btn_cancel_sync.setToolTip("Stop any running sync, including the automatic background one")
        slot.addWidget(self.prog_ov, 1)
        cl.addLayout(slot)
        lay.addWidget(card)

        # four tiles
        grid = QHBoxLayout()
        grid.setSpacing(10)
        self.t_next = Tile("Auto sync")
        self.t_mode = Tile("Sync mode")
        self.t_cloud = Tile("This device")
        self.t_photos = Tile("Photos")
        for t in (self.t_next, self.t_mode, self.t_cloud, self.t_photos):
            grid.addWidget(t, 1)
        lay.addLayout(grid)

        # setup: one slim row of ticks (hidden when everything is set up)
        self.setup = QFrame()
        self.setup.setObjectName("row")
        sl = QHBoxLayout(self.setup)
        sl.setContentsMargins(14, 10, 14, 10)
        sl.setSpacing(6)
        sl.addWidget(mk_label("SETUP", "eyebrow"))
        sl.addSpacing(2)
        self.step_login = SetupStep("Proton login", "Set up...", self.open_setup_wizard)
        self.step_folders = SetupStep("Folders", "Add one...", lambda: self.go(self.PAGE_FOLDERS))
        self.step_auto = SetupStep("Auto sync", "Turn on", self.setup_auto_sync)
        for r in (self.step_login, self.step_folders, self.step_auto):
            sl.addWidget(r)
        sl.addStretch(1)
        lay.addWidget(self.setup)
        lay.addStretch(1)
        return page

    def setup_auto_sync(self):
        self.enable_timer()

    def update_timer_status(self):
        self.timer_active, self.timer_interval = timer_status()
        if (hasattr(self, "combo_timer_int") and self.timer_interval in TIMER_INTERVALS
                and self.timer_interval != self._shown_interval):
            # only follow the real timer when it changed, so a pick that isn't applied yet survives the 30 s poll
            self._shown_interval = self.timer_interval
            self.combo_timer_int.setCurrentText(self.timer_interval)
        if hasattr(self, "lbl_timer_state"):
            if self.timer_active:
                self.lbl_timer_state.setText(f"On: every {self.timer_interval}" if self.timer_interval else "On")
                set_kind(self.lbl_timer_state, "ok")
            else:
                self.lbl_timer_state.setText("Off")
                set_kind(self.lbl_timer_state, "")
        self.update_overview()

    def update_overview(self):
        """Fills the Overview page (status card, tiles, setup row) from what we know right now."""
        if not self._built:
            return
        cfg = self.config
        st = self.sync_state()
        info = self._last_sync
        # headline + chip
        if st:
            self.last_title.setText("Syncing now" if "Preview" not in (self.run_label or "") else "Preview running")
            self.last_title.setToolTip("")
            self.last_chip.setText("In progress")
            set_kind(self.last_chip, "warn")
            detail = ""
        elif info:
            self.last_title.setText(fmt_short(info["when"]))
            self.last_title.setToolTip(fmt_full(info["when"]))
            self.last_chip.setText("OK" if info["ok"] else "Failed")
            set_kind(self.last_chip, "ok" if info["ok"] else "bad")
            detail = (f"Finished OK. {info['detail']}" if info["ok"] else info["detail"]).replace("\n", " ")
        else:
            self.last_title.setText("No sync yet")
            self.last_title.setToolTip("")
            self.last_chip.setText("")
            set_kind(self.last_chip, "")
            if self.login_ok is False:
                detail = "Log in to Proton Drive first: use the Setup wizard in the sidebar."
            elif not cfg.sync_dirs:
                detail = "Add a folder to sync on the Folders page."
            else:
                detail = "Press \"Sync now\" to make your first sync."
        self.last_chip.setVisible(bool(self.last_chip.text()))
        self.ov_detail.setText(detail)
        self.ov_detail.setVisible(not st and bool(detail))

        # tiles
        if self.timer_active:
            self.t_next.set(f"Every {self.timer_interval}" if self.timer_interval else "On", "Runs by itself")
        else:
            self.t_next.set("Off", "Turn it on in Settings")
        brief = {"copy": "Cloud files are kept", "sync": "Deletions are copied", "bisync": "Changes go both ways"}
        self.t_mode.set(SYNC_MODE_NAME.get(cfg.sync_mode, cfg.sync_mode), brief.get(cfg.sync_mode, ""))
        n = len(cfg.sync_dirs)
        self.t_cloud.set(cfg.device_name, f"{n} folder{'' if n == 1 else 's'} synced")
        if cfg.photos_enabled:
            tool_txt = {"signed-in": "Proton tool signed in", "cli missing": "Proton tool needed",
                        "other": "Not signed in to Proton"}.get(self.photos_state, "Checking...")
            self.t_photos.set(PHOTO_MODE_NAME.get(cfg.photos_mode, "Copy"), tool_txt)
        else:
            self.t_photos.set("Off", "Turn it on in Photos")

        # setup row
        login_ok = self.login_ok is not False
        self.step_login.set_done(login_ok, "You're logged in to Proton Drive." if login_ok else
                                 "Log in to Proton Drive with the setup wizard.")
        self.step_folders.set_done(bool(cfg.sync_dirs), f"{n} folder(s) are synced." if n else
                                   "Pick at least one folder to sync.")
        self.step_auto.set_done(self.timer_active, "Syncs by itself in the background." if self.timer_active else
                                "Optional: sync by itself in the background (every 30 minutes).")
        self.setup.setVisible(not (login_ok and cfg.sync_dirs and self.timer_active))
        busy = self.sync_busy()
        for r in (self.step_login, self.step_auto):
            r.setEnabled(not busy or r.done)
        self.update_progress()
        self.update_header()

    def refresh_overview_cards(self):
        self.update_overview()

    # --------------------------------------------------------------------------
    # Page: Folders (two lists side by side)
    # --------------------------------------------------------------------------
    def build_page_folders(self):
        page, lay, _head = mk_page("Folders")
        cols = QHBoxLayout()
        cols.setSpacing(14)

        # backed-up folders
        card, cl = mk_card(8, (16, 14, 16, 14))
        hrow = QHBoxLayout()
        hrow.addWidget(mk_label("Backed-up folders", "h2"))
        hrow.addStretch(1)
        hrow.addWidget(mk_button("Open selected", "link", self.open_selected_folder, compact=True,
                                 tip="Open the selected folder in your file manager"))
        cl.addLayout(hrow)
        cl.addWidget(mk_label("Each folder is stored in Proton Drive under Devices/<computer name>/<folder name>.",
                              "small", wrap=True))
        self.folders_list = small_list()
        self.folders_list.itemDoubleClicked.connect(lambda *_: self.open_selected_folder())
        cl.addWidget(self.folders_list, 1)
        b = QHBoxLayout()
        b.setSpacing(6)
        b.addWidget(mk_button("Add folder...", "primary", self.add_sync_folder, compact=True))
        b.addWidget(mk_button("Remove", slot=self.remove_sync_folder, compact=True))
        b.addStretch(1)
        cl.addLayout(b)
        cols.addWidget(card, 1)

        # excluded folders
        card, cl = mk_card(8, (16, 14, 16, 14))
        hrow = QHBoxLayout()
        hrow.addWidget(mk_label("Excluded folders", "h2"))
        hrow.addStretch(1)
        hrow.addWidget(mk_button("Open selected", "link", self.open_selected_excluded, compact=True,
                                 tip="Open the selected folder in your file manager"))
        cl.addLayout(hrow)
        cl.addWidget(mk_label("Pick a folder inside a backed-up one to stop backing it up. Its copy already in "
                              "Proton Drive is kept.", "small", wrap=True))
        self.excluded_list = small_list()
        self.excluded_list.itemDoubleClicked.connect(lambda *_: self.open_selected_excluded())
        cl.addWidget(self.excluded_list, 1)
        b = QHBoxLayout()
        b.setSpacing(6)
        b.addWidget(mk_button("Exclude folder...", "primary", self.add_excluded_folder, compact=True))
        b.addWidget(mk_button("Stop excluding", slot=self.remove_excluded_folder, compact=True,
                              tip="Back up the selected excluded folder again"))
        b.addStretch(1)
        cl.addLayout(b)
        cols.addWidget(card, 1)
        lay.addLayout(cols, 1)

        # tip row
        tip = QFrame()
        tip.setObjectName("row")
        tl = QHBoxLayout(tip)
        tl.setContentsMargins(14, 6, 10, 6)
        tl.setSpacing(10)
        tl.addWidget(ElideLabel("Tip: to skip files by name everywhere (like *.iso), use the Skip list.", "muted"), 1)
        tl.addWidget(mk_button("Open Skip list", "link", self.open_skip_settings, compact=True))
        lay.addWidget(tip)
        self.refresh_folder_list()
        return page

    def open_skip_settings(self):
        self.settings_tabs.setCurrentIndex(3)
        self.go(self.PAGE_SETTINGS)

    def refresh_folder_list(self):
        self.folders_list.clear()
        for f in self.config.sync_dirs:
            item = QListWidgetItem(short_home(f))
            if not os.path.exists(f):
                item.setText(f"{short_home(f)}\nfolder is missing")
                item.setForeground(QColor(RED))
            item.setToolTip(f)
            item.setData(Qt.UserRole, f)
            self.folders_list.addItem(item)
        self.refresh_excluded_list()
        self.update_overview()

    def refresh_excluded_list(self):
        self.excluded_list.clear()
        if not self.config.exclude_dirs:
            item = QListWidgetItem("Nothing excluded. Everything in the folders on the left is backed up.")
            item.setForeground(QColor(MIST))
            item.setFlags(Qt.NoItemFlags)
            self.excluded_list.addItem(item)
            return
        for f in self.config.exclude_dirs:
            item = QListWidgetItem(short_home(f))
            if not any(path_inside(f, d) for d in self.config.sync_dirs):
                item.setText(f"{short_home(f)}\nnot inside a backed-up folder, so it has no effect")
                item.setForeground(QColor(EMBER))
            elif not os.path.isdir(f):
                item.setText(f"{short_home(f)}\nfolder not found right now")
                item.setForeground(QColor(MIST))
            item.setToolTip(f)
            item.setData(Qt.UserRole, f)
            self.excluded_list.addItem(item)

    def add_excluded_folder(self):
        if not self.config.sync_dirs:
            QMessageBox.information(self, "No backed-up folders", "Add a folder to back up first, then you can exclude folders inside it.")
            return
        cur = self.folders_list.currentItem()
        start = cur.data(Qt.UserRole) if cur else self.config.sync_dirs[0]
        if not os.path.isdir(start):
            start = str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Pick a folder to stop backing up", start)
        if not folder:
            return
        folder = tidy_folder(folder)
        if any(same_path(folder, d) or same_path(os.path.realpath(folder), os.path.realpath(d)) for d in self.config.sync_dirs):
            QMessageBox.information(
                self, "That's a backed-up folder",
                f"'{short_home(folder)}' is one of your backed-up folders.\n\n"
                "To stop backing it up completely, select it in the list and click \"Remove\".")
            return
        if not any(path_inside(folder, d) for d in self.config.sync_dirs):
            QMessageBox.warning(
                self, "Not inside a backed-up folder",
                f"'{short_home(folder)}' isn't inside any of your backed-up folders, so it isn't being backed up anyway.\n\n"
                "Pick a folder inside one of the folders in the list.")
            return
        if path_in_list(folder, self.config.exclude_dirs) or any(path_inside(folder, e) for e in self.config.exclude_dirs):
            self.toast(f"Already excluded: {short_home(folder)}", "warn")
            return
        # Excluding a parent makes excluded folders inside it redundant.
        self.config.exclude_dirs = [e for e in self.config.exclude_dirs if not path_inside(e, folder)]
        self.config.exclude_dirs.append(folder)
        self.config.exclude_dirs.sort(key=str.lower)
        if self.save_config_or_warn():
            self.toast(f"Excluded {short_home(folder)}. It won't be backed up from the next sync.")
        self.refresh_excluded_list()

    def remove_excluded_folder(self):
        item = self.excluded_list.currentItem()
        folder = item.data(Qt.UserRole) if item else None
        if not folder:
            self.toast("Select an excluded folder first.", "warn")
            return
        if folder in self.config.exclude_dirs:
            self.config.exclude_dirs.remove(folder)
            if self.save_config_or_warn():
                self.toast(f"{short_home(folder)} will be backed up again from the next sync.")
        self.refresh_excluded_list()

    def open_selected_excluded(self):
        item = self.excluded_list.currentItem()
        folder = item.data(Qt.UserRole) if item else None
        if not folder:
            return
        if os.path.isdir(folder):
            self.open_path(folder)
        else:
            QMessageBox.warning(self, "Folder not found", f"This folder doesn't exist right now:\n{folder}")

    def add_sync_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Directory to Sync", str(Path.home()))
        if not folder:
            return
        folder = tidy_folder(folder)
        if path_in_list(folder, self.config.sync_dirs):
            return
        clash = [d for d in self.config.sync_dirs if same_name(d, folder)]
        if clash:
            QMessageBox.warning(
                self, "Same folder name",
                f"'{clash[0]}' is already synced, and it has the same name ('{Path(folder).name}').\n"
                "Both would go to the same cloud folder, so this one can't be added. Rename one of them first.")
            return
        self.config.sync_dirs.append(folder)
        self.save_config_or_warn()
        self.refresh_folder_list()
        self.toast(f"Added folder: {folder}")

    def save_config_or_warn(self) -> bool:
        try:
            self.config.save()
            return True
        except (OSError, RuntimeError) as e:
            QMessageBox.critical(self, "Couldn't save", f"Your settings couldn't be saved:\n{e}")
            return False

    def open_path(self, path):
        if not IS_WINDOWS and not find_exe("xdg-open"):
            QMessageBox.information(self, "Open", f"Install xdg-utils to open files from here.\nPath: {path}")
            return
        os_open_path(path)

    def remove_sync_folder(self):
        row = self.folders_list.currentRow()
        if row >= 0:
            item = self.folders_list.item(row)
            folder = item.data(Qt.UserRole)
            if folder in self.config.sync_dirs:
                self.config.sync_dirs.remove(folder)
                # Exclusions inside a folder that's no longer backed up do nothing; drop them.
                self.config.exclude_dirs = [
                    e for e in self.config.exclude_dirs
                    if not path_inside(e, folder) or any(path_inside(e, d) for d in self.config.sync_dirs)]
                self.save_config_or_warn()
                self.refresh_folder_list()
                self.toast(f"Removed folder: {folder}")

    def open_selected_folder(self):
        item = self.folders_list.currentItem()
        if item:
            folder = item.data(Qt.UserRole)
            if os.path.exists(folder):
                self.open_path(folder)
            else:
                QMessageBox.warning(self, "Directory Not Found", f"Cannot open non-existent directory:\n{folder}")

    # --------------------------------------------------------------------------
    # Page: Settings (tabs: Account / Mode / Speed / Skip / Automatic)
    # --------------------------------------------------------------------------
    def build_page_settings(self):
        page = Pane()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        top = QVBoxLayout()
        top.setContentsMargins(24, 16, 24, 10)
        top.setSpacing(10)
        top.addWidget(mk_label("Settings", "h1"))
        self.settings_tabs = tabs = mk_tabs()
        top.addWidget(tabs, 1)
        outer.addLayout(top, 1)

        # -- Account
        v = add_tab(tabs, "Account")
        v.addWidget(mk_label("The Proton login rclone uses, and the name this computer has in Proton Drive.",
                             "muted", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(16)
        self.edit_remote = QLineEdit(self.config.remote_name)
        self.edit_device = QLineEdit(self.config.device_name)
        row.addWidget(field("rclone remote (your Proton login)", self.edit_remote), 1)
        row.addWidget(field("Device name", self.edit_device), 1)
        v.addLayout(row)
        self.lbl_cloud_path = ElideLabel("", "small")
        v.addWidget(self.lbl_cloud_path)
        self.edit_remote.textChanged.connect(self.update_cloud_path)
        self.edit_device.textChanged.connect(self.update_cloud_path)
        self.update_cloud_path()
        v.addWidget(mk_label("Not logged in yet, or logged in with another account? Use \"Setup wizard...\" "
                             "in the sidebar.", "small", wrap=True))
        v.addStretch(1)

        # -- Mode
        v = add_tab(tabs, "Mode", spacing=8)
        self.mode_group = QButtonGroup(self)
        self.mode_cards = {}
        for key, _name, title, desc in SYNC_MODES:
            rc = RadioCard(title, desc, self.mode_group)
            self.mode_cards[key] = rc
            v.addWidget(rc)
        self.radio_copy = self.mode_cards["copy"].radio
        self.radio_sync = self.mode_cards["sync"].radio
        self.radio_bisync = self.mode_cards["bisync"].radio
        self.mode_cards.get(self.config.sync_mode, self.mode_cards["copy"]).radio.setChecked(True)
        v.addStretch(1)

        # -- Speed
        v = add_tab(tabs, "Speed", spacing=12)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)

        def slider_row(r, label, lo, hi, value, tip):
            lb = mk_label(label, "muted")
            lb.setToolTip(tip)
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(value)
            spin.setFixedWidth(80)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(value)
            spin.valueChanged.connect(sl.setValue)
            sl.valueChanged.connect(spin.setValue)
            grid.addWidget(lb, r, 0)
            grid.addWidget(sl, r, 1)
            grid.addWidget(spin, r, 2)
            return spin, sl

        self.spin_transfers, self.slider_transfers = slider_row(0, "Transfers", 1, 16, self.config.transfers,
                                                                "How many files are copied at the same time")
        self.spin_checkers, self.slider_checkers = slider_row(1, "Checkers", 1, 32, self.config.checkers,
                                                              "How many files are compared at the same time")
        grid.addWidget(mk_label("Bandwidth limit", "muted"), 2, 0)
        self.edit_bw = QLineEdit(self.config.bwlimit)
        self.edit_bw.setPlaceholderText("e.g. 10M, 5M (leave blank for unlimited)")
        grid.addWidget(self.edit_bw, 2, 1, 1, 2)
        v.addLayout(grid)
        v.addWidget(mk_sep())
        self.chk_settings_notify = QCheckBox("Desktop notifications when a sync finishes or fails")
        self.chk_settings_notify.setChecked(self.config.notify)
        v.addWidget(self.chk_settings_notify)
        v.addStretch(1)

        # -- Skip
        v = add_tab(tabs, "Skip")
        v.addWidget(mk_label("Files and folders matching these patterns are never uploaded "
                             "(like *.iso, cache/** or node_modules/**).", "muted", wrap=True))
        self.list_excludes = small_list(QAbstractItemView.ExtendedSelection)
        for exc in self.config.excludes:
            self.list_excludes.addItem(exc)
        v.addWidget(self.list_excludes, 1)
        b = QHBoxLayout()
        b.setSpacing(6)
        b.addWidget(mk_button("Add pattern...", slot=self.add_exclusion_pattern, compact=True))
        b.addWidget(mk_button("Remove", slot=self.remove_exclusion_pattern, compact=True))
        b.addWidget(mk_button("Restore recommended", "link", self.restore_preset_excludes, compact=True))
        b.addStretch(1)
        v.addLayout(b)

        # -- Automatic (the background timer)
        v = add_tab(tabs, "Automatic")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(mk_label("Sync in the background every", "muted"))
        self.combo_timer_int = QComboBox()
        self.combo_timer_int.addItems(TIMER_INTERVALS)
        self.combo_timer_int.setCurrentText("30min")
        self.combo_timer_int.setMinimumWidth(110)
        row.addWidget(self.combo_timer_int)
        self.lbl_timer_state = chip("Off")
        row.addWidget(self.lbl_timer_state)
        row.addStretch(1)
        v.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(mk_button("Enable / update", "primary", self.enable_timer, compact=True))
        row.addWidget(mk_button("Disable", "danger", self.disable_timer, compact=True))
        row.addStretch(1)
        v.addLayout(row)
        head = QHBoxLayout()
        head.addWidget(mk_label(f"{TIMER_KIND} status", "muted"))
        head.addStretch(1)
        head.addWidget(mk_button("Refresh", slot=self.refresh_systemd_status, compact=True))
        v.addLayout(head)
        self.systemd_status_view = LogView()
        v.addWidget(self.systemd_status_view, 1)
        tabs.currentChanged.connect(lambda i: i == 4 and self.refresh_systemd_status())

        # -- save bar (applies to the Account / Mode / Speed / Skip tabs)
        bar = QFrame()
        bar.setObjectName("strip")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(24, 8, 24, 8)
        self.saved_lbl = mk_label("", "ok")
        bl.addWidget(self.saved_lbl, 1)
        bl.addWidget(mk_button("Undo changes", slot=self.undo_settings))
        bl.addWidget(mk_button("Save settings", "primary", lambda: self.save_settings(True)))
        outer.addWidget(bar)
        self.saved_timer = QTimer(self)
        self.saved_timer.setSingleShot(True)
        self.saved_timer.timeout.connect(self._clear_saved_label)
        self.refresh_systemd_status()
        self.connect_settings_signals()
        self.take_settings_snapshot()
        return page

    # ---- unsaved-changes tracking (Account / Mode / Speed / Skip tabs)
    def current_sync_mode(self) -> str:
        if self.radio_sync.isChecked():
            return "sync"
        return "bisync" if self.radio_bisync.isChecked() else "copy"

    def settings_snapshot(self) -> tuple:
        """What the Settings widgets hold right now, normalised the way Save would store it."""
        return (self.edit_remote.text().strip() or "protondrive",
                self.edit_device.text().strip().replace("/", "-") or hostname(),
                self.current_sync_mode(),
                self.spin_transfers.value(), self.spin_checkers.value(),
                self.edit_bw.text().strip(), self.chk_settings_notify.isChecked(),
                tuple(self.skip_patterns()))

    def settings_dirty(self) -> bool:
        return self._saved_snapshot is not None and self.settings_snapshot() != self._saved_snapshot

    def take_settings_snapshot(self):
        """Call whenever the widgets and the saved config are known to match."""
        self._saved_snapshot = self.settings_snapshot()
        self.on_settings_changed()

    def on_settings_changed(self, *_):
        if self._saved_snapshot is None or not hasattr(self, "saved_lbl"):
            return
        if self.settings_dirty():
            self.saved_timer.stop()
            self.saved_lbl.setText("Unsaved changes")
            set_role(self.saved_lbl, "warn")
        else:
            if self.saved_lbl.text() == "Unsaved changes":
                self.saved_lbl.setText("")
            set_role(self.saved_lbl, "ok")

    def _clear_saved_label(self):
        if not self.settings_dirty():
            self.saved_lbl.setText("")

    def connect_settings_signals(self):
        for le in (self.edit_remote, self.edit_device, self.edit_bw):
            le.textChanged.connect(self.on_settings_changed)
        for rc in self.mode_cards.values():
            rc.radio.toggled.connect(self.on_settings_changed)
        for sp in (self.spin_transfers, self.spin_checkers):
            sp.valueChanged.connect(self.on_settings_changed)
        self.chk_settings_notify.toggled.connect(self.on_settings_changed)

    def confirm_save_before_sync(self) -> bool:
        """A sync uses the saved settings. If some are changed but unsaved, ask before saving them."""
        if self.settings_dirty():
            if QMessageBox.question(
                    self, "Unsaved settings",
                    "You changed settings but didn't save them. Save them and sync?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return False
        return self.save_settings(show_popup=False)

    def update_cloud_path(self):
        remote = self.edit_remote.text().strip() or "protondrive"
        device = self.edit_device.text().strip().replace("/", "-") or hostname()
        self.lbl_cloud_path.setText(f"Your files go to {remote}:Devices/{device}/<folder name>")

    def add_exclusion_pattern(self):
        text, ok = QInputDialog.getText(self, "Add Exclusion Pattern", "Enter glob pattern (e.g. *.iso, cache/**, *.part):")
        if ok and text.strip():
            clean = text.strip()
            if clean not in self.skip_patterns():
                self.list_excludes.addItem(clean)
                self.on_settings_changed()

    def remove_exclusion_pattern(self):
        for item in self.list_excludes.selectedItems():
            self.list_excludes.takeItem(self.list_excludes.row(item))
        self.on_settings_changed()

    def restore_preset_excludes(self):
        presets = [
            ".git/**", "node_modules/**", ".venv/**", "venv/**",
            "__pycache__/**", ".cache/**", "*.tmp", "*.crdownload"
        ]
        self.list_excludes.clear()
        for p in presets:
            self.list_excludes.addItem(p)
        self.on_settings_changed()

    def skip_patterns(self):
        """The Skip list as shown on screen (it only reaches the config when Settings is saved)."""
        return [self.list_excludes.item(i).text().strip() for i in range(self.list_excludes.count())
                if self.list_excludes.item(i).text().strip()]

    def save_settings(self, show_popup=True):
        self.config.remote_name = self.edit_remote.text().strip() or "protondrive"
        self.config.device_name = self.edit_device.text().strip().replace("/", "-") or hostname()

        if self.radio_sync.isChecked():
            self.config.sync_mode = "sync"
        elif self.radio_bisync.isChecked():
            self.config.sync_mode = "bisync"
        else:
            self.config.sync_mode = "copy"

        bw = self.edit_bw.text().strip()
        if bw and not re.fullmatch(r"[0-9.]+[BKMGbkmg]?", bw):
            self.settings_tabs.setCurrentIndex(2)
            QMessageBox.warning(self, "Bandwidth limit", "Use a number with an optional unit, e.g. 5M or 500K. Leave empty for no limit.")
            return False

        self.config.transfers = self.spin_transfers.value()
        self.config.checkers = self.spin_checkers.value()
        self.config.bwlimit = bw
        self.config.notify = self.chk_settings_notify.isChecked()
        self.config.excludes = self.skip_patterns()
        if not self.save_config_or_warn():
            return False

        self.edit_device.setText(self.config.device_name)
        self.refresh_overview_cards()
        self.take_settings_snapshot()
        if show_popup:
            self.saved_lbl.setText("✓ Settings saved")
            self.saved_timer.start(6000)
            self.toast("Settings saved.")
        QTimer.singleShot(0, self.refresh_setup_state)
        return True

    def undo_settings(self):
        keep = (self.config.photos_enabled, self.config.photos_dir, self.config.photos_mode,
                list(self.config.photos_categories), list(self.config.photos_dates))
        self.config.load()
        # the Photos page has its own Save button: its unsaved edits (widgets and values) stay as they are
        (self.config.photos_enabled, self.config.photos_dir, self.config.photos_mode,
         self.config.photos_categories, self.config.photos_dates) = keep
        self.refresh_settings_widgets(photos=False)
        self.saved_lbl.setText("Changes undone")
        self.saved_timer.start(4000)

    def refresh_settings_widgets(self, photos=True):
        """Puts the current config back into every settings widget (e.g. after the wizard)."""
        self.edit_remote.setText(self.config.remote_name)
        self.edit_device.setText(self.config.device_name)
        self.mode_cards.get(self.config.sync_mode, self.mode_cards["copy"]).radio.setChecked(True)
        self.spin_transfers.setValue(self.config.transfers)
        self.spin_checkers.setValue(self.config.checkers)
        self.edit_bw.setText(self.config.bwlimit)
        self.chk_settings_notify.setChecked(self.config.notify)
        self.list_excludes.clear()
        for exc in self.config.excludes:
            self.list_excludes.addItem(exc)
        self.refresh_folder_list()
        self.refresh_overview_cards()
        self.take_settings_snapshot()
        if photos and hasattr(self, "chk_photos_enabled"):
            self.chk_photos_enabled.setChecked(self.config.photos_enabled)
            self.edit_photos_dir.setText(self.config.photos_dir)
            self.photo_mode_cards.get(self.config.photos_mode, self.photo_mode_cards["copy"]).radio.setChecked(True)
            self.load_photo_filters()

    # ---- automatic timer
    def enable_timer(self):
        interval = self.combo_timer_int.currentText()
        ok, msg = set_timer(True, interval)
        self.refresh_systemd_status()
        self.update_timer_status()
        if ok:
            self.toast(f"Background sync on: every {interval}")
        else:
            QMessageBox.warning(self, "Background sync", f"Couldn't turn on the timer:\n{msg}")

    def disable_timer(self):
        ok, msg = set_timer(False)
        self.refresh_systemd_status()
        self.update_timer_status()
        self.toast("Background sync off." if ok else msg, "ok" if ok else "warn")

    def refresh_systemd_status(self):
        if IS_WINDOWS:
            res = run_quiet(["schtasks", "/Query", "/TN", scheduler.TASK_NAME, "/V", "/FO", "LIST"])
            text = (res.stdout if res.returncode == 0 else "").strip()
            self.systemd_status_view.setPlainText(text or "Background sync is off (no Task Scheduler task).")
            return
        if not find_exe("systemctl"):
            self.systemd_status_view.setPlainText("systemd isn't available on this system, so background sync can't be used.")
            return
        res = run_quiet(["systemctl", "--user", "status", "--no-pager", "sleepy-proton-sync.timer", "sleepy-proton-sync.service"])
        text = (res.stdout or res.stderr).strip()
        self.systemd_status_view.setPlainText(text or "Background sync is off (no timer installed).")

    # --------------------------------------------------------------------------
    # Page: Photos (tabs: Where / Mode / What / Activity; the buttons stay at the bottom)
    # --------------------------------------------------------------------------
    def build_page_photos(self):
        page = Pane()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        top = QVBoxLayout()
        top.setContentsMargins(24, 16, 24, 10)
        top.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(mk_label("Photos", "h1"))
        head.addWidget(mk_label("Proton Photos to this computer", "small"))
        head.addStretch(1)
        top.addLayout(head)

        # status row: Proton's tool + sign in
        st = QFrame()
        st.setObjectName("row")
        sl = QHBoxLayout(st)
        sl.setContentsMargins(14, 6, 10, 6)
        sl.setSpacing(10)
        self.lbl_photos_status = ElideLabel("Checking Proton's tool...", "muted")
        sl.addWidget(self.lbl_photos_status, 1)
        self.btn_photos_install = mk_button("Install Proton's tool", "primary", self.install_photos_cli, compact=True)
        self.btn_photos_signin = mk_button("Sign in", slot=self.photos_sign_in, compact=True)
        btn_check = mk_button("Check", slot=self.check_photos_status, compact=True)
        for b in (self.btn_photos_install, self.btn_photos_signin, btn_check):
            sl.addWidget(b, 0, Qt.AlignVCenter)
        self.btn_photos_install.hide()
        top.addWidget(st)

        self.photos_tabs = tabs = mk_tabs()
        top.addWidget(tabs, 1)
        outer.addLayout(top, 1)

        # -- Where
        v = add_tab(tabs, "Where")
        self.chk_photos_enabled = QCheckBox("Download my Proton Photos automatically with every background sync")
        self.chk_photos_enabled.setChecked(self.config.photos_enabled)
        v.addWidget(self.chk_photos_enabled)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(mk_label("Save to", "muted"))
        self.edit_photos_dir = QLineEdit(self.config.photos_dir)
        row.addWidget(self.edit_photos_dir, 1)
        b_reset = mk_button("Default", compact=True, tip=str(DEFAULT_PHOTOS_DIR))
        b_reset.clicked.connect(lambda: self.edit_photos_dir.setText(str(DEFAULT_PHOTOS_DIR)))
        row.addWidget(mk_button("Browse...", slot=self.browse_photos_dir, compact=True))
        row.addWidget(b_reset)
        row.addWidget(mk_button("Open", slot=self.open_photos_dir, compact=True))
        v.addLayout(row)
        v.addWidget(mk_label("Photos are arranged like the Proton Photos timeline: Year / Month "
                             "(e.g. 2026 / 11 November). Each file keeps the date it was taken.", "small", wrap=True))
        v.addStretch(1)

        # -- Mode
        v = add_tab(tabs, "Mode", spacing=8)
        self.photo_mode_group = QButtonGroup(self)
        self.photo_mode_cards = {}
        self.photo_mode_radios = {}
        for key, title, desc in PHOTO_MODES:
            rc = RadioCard(title, desc, self.photo_mode_group)
            self.photo_mode_cards[key] = rc
            self.photo_mode_radios[key] = rc.radio
            v.addWidget(rc)
        self.photo_mode_cards.get(self.config.photos_mode, self.photo_mode_cards["copy"]).radio.setChecked(True)
        v.addStretch(1)

        # -- What to download
        v = add_tab(tabs, "What")
        what = QHBoxLayout()
        what.setSpacing(18)
        cat_box = QVBoxLayout()
        cat_box.setSpacing(6)
        cat_box.addWidget(mk_label("CATEGORIES", "eyebrow"))
        self.chk_cat_all = QCheckBox("Everything")
        self.chk_cat_all.toggled.connect(self.on_cat_all_toggled)
        cat_box.addWidget(self.chk_cat_all)
        cat_grid = QGridLayout()
        cat_grid.setHorizontalSpacing(14)
        cat_grid.setVerticalSpacing(6)
        self.cat_checks = {}
        for i, (key, label, _tag) in enumerate(PHOTO_CATEGORIES):
            cb = QCheckBox(label)
            cb.toggled.connect(self.on_cat_toggled)
            self.cat_checks[key] = cb
            cat_grid.addWidget(cb, i // 2, i % 2)
        cat_box.addLayout(cat_grid)
        cat_box.addWidget(mk_label("A photo is downloaded if it's in any ticked category.", "small"))
        cat_box.addStretch(1)

        date_box = QVBoxLayout()
        date_box.setSpacing(6)
        date_box.addWidget(mk_label("DATES", "eyebrow"))
        self.chk_dates_all = QCheckBox("All dates (new months too)")
        self.chk_dates_all.toggled.connect(lambda on: self.tree_dates.setEnabled(not on))
        date_box.addWidget(self.chk_dates_all)
        self.tree_dates = QTreeWidget()
        self.tree_dates.setHeaderHidden(True)
        self.tree_dates.setMinimumHeight(60)
        self.tree_dates.setRootIsDecorated(True)
        self.tree_dates.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree_dates.itemChanged.connect(self.on_date_item_changed)
        date_box.addWidget(self.tree_dates, 1)
        self.btn_photos_refresh = mk_button("Load dates && counts", compact=True,
                                            tip="Ask Proton for the months you have photos in, and how many")
        self.btn_photos_refresh.clicked.connect(lambda: self.run_photos(index_only=True))
        date_box.addWidget(self.btn_photos_refresh, 0, Qt.AlignLeft)
        self.lbl_photos_index = ElideLabel("", "small")
        date_box.addWidget(self.lbl_photos_index)
        what.addLayout(cat_box, 3)
        what.addLayout(date_box, 2)
        v.addLayout(what, 1)

        # -- Activity
        v = add_tab(tabs, "Activity")
        self.photos_log = LogView("What Proton Photos is doing shows up here.")
        v.addWidget(self.photos_log, 1)

        # -- bottom bar: the actions
        bar = QFrame()
        bar.setObjectName("strip")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(24, 8, 24, 8)
        bl.setSpacing(8)
        bl.addStretch(1)
        b_save = mk_button("Save settings", slot=lambda: self.save_photo_settings(True))
        self.btn_photos_preview = mk_button("Preview", tip="Shows what would be downloaded and changes nothing")
        self.btn_photos_preview.clicked.connect(lambda: self.run_photos(dry_run=True))
        self.btn_photos_now = mk_button("Download now", "primary")
        self.btn_photos_now.clicked.connect(lambda: self.run_photos())
        self.btn_photos_stop = mk_button("Stop", "danger")
        self.btn_photos_stop.setEnabled(False)
        self.btn_photos_stop.clicked.connect(self.stop_photos)
        for b in (b_save, self.btn_photos_preview, self.btn_photos_now, self.btn_photos_stop):
            bl.addWidget(b)
        outer.addWidget(bar)

        self.load_photo_filters()
        QTimer.singleShot(700, self.check_photos_status)
        return page

    # ---- filters <-> widgets
    def load_photo_filters(self):
        cats = set(self.config.photos_categories)
        everything = not cats or "all" in cats
        self.chk_cat_all.blockSignals(True)
        self.chk_cat_all.setChecked(everything)
        self.chk_cat_all.blockSignals(False)
        for key, cb in self.cat_checks.items():
            cb.blockSignals(True)
            cb.setChecked(everything or key in cats)
            cb.blockSignals(False)
        self.chk_dates_all.setChecked(not self.config.photos_dates)
        self.tree_dates.setEnabled(bool(self.config.photos_dates))
        self.load_photos_index()

    def load_photos_index(self):
        try:
            self._photos_index = json.loads(PHOTOS_INDEX.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._photos_index = {}
        idx = self._photos_index
        tags = idx.get("tags", {})
        for key, label, tag in PHOTO_CATEGORIES:
            n = idx.get("other") if tag is None else tags.get(str(tag), 0)
            self.cat_checks[key].setText(f"{label}  ({n})" if idx else label)
        if idx:
            self.chk_cat_all.setText(f"Everything  ({idx.get('total', 0)})")
            self.lbl_photos_index.setText(f"Counts from Proton, {idx.get('updated', '?').replace('T', ' ')[:16]}.")
        else:
            self.lbl_photos_index.setText("Load dates to see the counts.")
        self.fill_date_tree()

    def fill_date_tree(self):
        chosen = set(self.config.photos_dates)
        months = dict(self._photos_index.get("months", {}))
        for d in chosen:  # keep saved choices visible even before counts are loaded
            if len(d) == 7:
                months.setdefault(d, None)
            elif len(d) == 4 and not any(m.startswith(d) for m in months):
                months.setdefault(d + "-00", None)
        years = {}
        for m, n in months.items():
            if m == "undated":
                continue
            years.setdefault(m[:4], []).append((m, n))
        self.tree_dates.blockSignals(True)
        self.tree_dates.clear()
        for y in sorted(years, reverse=True):
            total = sum(n or 0 for _, n in years[y])
            yi = QTreeWidgetItem([f"{y}  ({total})" if total else y])
            yi.setData(0, Qt.UserRole, y)
            yi.setFlags(yi.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            self.tree_dates.addTopLevelItem(yi)
            for m, n in sorted(years[y], reverse=True):
                if m.endswith("-00"):
                    continue
                label = MONTH_NAMES[int(m[5:7]) - 1]
                mi = QTreeWidgetItem([f"{label}  ({n})" if n else label])
                mi.setData(0, Qt.UserRole, m)
                mi.setFlags(mi.flags() | Qt.ItemIsUserCheckable)
                mi.setCheckState(0, Qt.Checked if (y in chosen or m in chosen) else Qt.Unchecked)
                yi.addChild(mi)
            if yi.childCount() == 0:
                yi.setCheckState(0, Qt.Checked if y in chosen else Qt.Unchecked)
        self.tree_dates.blockSignals(False)
        if self.tree_dates.topLevelItemCount() == 0:
            empty = QTreeWidgetItem(["No dates loaded yet"])
            empty.setFlags(Qt.NoItemFlags)
            self.tree_dates.addTopLevelItem(empty)

    def on_date_item_changed(self, *_):
        pass  # read on save; Qt handles year/month tick sync (auto-tristate)

    def selected_dates(self):
        if self.chk_dates_all.isChecked():
            return []
        saved = list(self.config.photos_dates)
        saved_years = set(saved)
        known = {m[:4] for m in self._photos_index.get("months", {}) if m != "undated"}   # years whose months we know
        out = []
        for i in range(self.tree_dates.topLevelItemCount()):
            yi = self.tree_dates.topLevelItem(i)
            y = yi.data(0, Qt.UserRole)
            if not y:
                continue
            state = yi.checkState(0)
            months = [yi.child(j).data(0, Qt.UserRole) for j in range(yi.childCount())
                      if yi.child(j).checkState(0) == Qt.Checked]
            # A whole year (new months of it are included too) only if it was saved that way before, or if
            # every month of it is known and ticked. Before the counts are loaded the year node lists just the
            # saved months, so "all ticked" there says nothing about the rest of the year.
            if state == Qt.Checked and (y in saved_years or yi.childCount() == 0 or y in known):
                out.append(y)
            elif state != Qt.Unchecked:
                out += months
        order = {d: n for n, d in enumerate(saved)}
        out.sort(key=lambda d: (order.get(d, len(order)), d))     # unchanged picks save in the same order
        return out

    def on_cat_all_toggled(self, on):
        for cb in self.cat_checks.values():
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)

    def on_cat_toggled(self, *_):
        self.chk_cat_all.blockSignals(True)
        self.chk_cat_all.setChecked(all(cb.isChecked() for cb in self.cat_checks.values()))
        self.chk_cat_all.blockSignals(False)

    def browse_photos_dir(self):
        start = self.edit_photos_dir.text().strip() or str(DEFAULT_PHOTOS_DIR)
        while start and not os.path.isdir(start):
            start = os.path.dirname(start)
        folder = QFileDialog.getExistingDirectory(self, "Where should Proton Photos be saved?", start or str(Path.home()))
        if folder:
            self.edit_photos_dir.setText(tidy_folder(folder))

    def open_photos_dir(self):
        d = self.edit_photos_dir.text().strip() or str(DEFAULT_PHOTOS_DIR)
        if os.path.isdir(d):
            self.open_path(d)
        else:
            QMessageBox.information(self, "Not there yet", f"This folder will be made on the first download:\n{d}")

    def save_photo_settings(self, show_popup=True) -> bool:
        d = expand_path(self.edit_photos_dir.text().strip()) or str(DEFAULT_PHOTOS_DIR)
        d = os.path.abspath(d)
        if (any(same_path(d, s) or path_inside(d, s) for s in self.config.sync_dirs)
                and not any(same_path(d, e) or path_inside(d, e) for e in self.config.exclude_dirs)
                and getattr(self, "_photos_backup_ok", None) != d):
            if any(same_path(d, s) for s in self.config.sync_dirs):
                QMessageBox.information(
                    self, "Heads up",
                    f"'{short_home(d)}' is one of your backed-up folders, so downloaded photos will "
                    "also be uploaded to Proton Drive again (as normal files).\n\n"
                    "To avoid that, pick a folder inside it instead (like the default).")
            else:
                box = QMessageBox(self)
                box.setWindowTitle("Skip these in your backup?")
                box.setIcon(QMessageBox.Question)
                box.setText(f"'{short_home(d)}' is inside a folder you back up.")
                box.setInformativeText(
                    "Your photos are already in Proton Photos, so uploading them to Proton Drive "
                    "again would use double the space.\n\nExclude this folder from the backup?")
                b_ex = box.addButton("Exclude It (Recommended)", QMessageBox.AcceptRole)
                box.addButton("Back It Up Too", QMessageBox.RejectRole)
                box.setDefaultButton(b_ex)
                box.exec()
                if box.clickedButton() is b_ex:
                    self.config.exclude_dirs = [e for e in self.config.exclude_dirs
                                                if not path_inside(e, d)] + [d]
                    self.refresh_excluded_list()
                else:
                    self._photos_backup_ok = d
        cats = [k for k, cb in self.cat_checks.items() if cb.isChecked()]
        if not cats:
            self.photos_tabs.setCurrentIndex(2)
            QMessageBox.warning(self, "Nothing ticked", "Tick at least one category (or Everything).")
            return False
        dates = self.selected_dates()
        if not self.chk_dates_all.isChecked() and not dates:
            self.photos_tabs.setCurrentIndex(2)
            QMessageBox.warning(self, "No dates ticked", "Tick at least one year or month, or choose All dates.")
            return False
        self.config.photos_enabled = self.chk_photos_enabled.isChecked()
        self.config.photos_dir = d
        self.config.photos_mode = next(k for k, r in self.photo_mode_radios.items() if r.isChecked())
        self.config.photos_categories = [] if len(cats) == len(self.cat_checks) else cats
        self.config.photos_dates = dates
        if not self.save_config_or_warn():
            return False
        self.edit_photos_dir.setText(d)
        self.update_overview()
        if show_popup:
            self.toast("Proton Photos settings saved.")
        return True

    # ---- Proton tool status / install / sign in
    def check_photos_status(self):
        if self.photos_status_proc and self.photos_status_proc.state() != QProcess.NotRunning:
            return
        self.lbl_photos_status.setText("Checking Proton's tool...")
        set_role(self.lbl_photos_status, "muted")
        p = QProcess(self)

        def done(*_):
            try:
                self.on_photos_status(bytes(p.readAllStandardOutput()).decode(errors="replace"))
            except RuntimeError:
                pass  # window closed while checking
        p.finished.connect(done)
        self.photos_status_proc = p
        p.start(*tool("photos", "--status"))

    def on_photos_status(self, out):
        out = out.strip()
        self.btn_photos_install.setVisible(out == "cli missing")
        self.btn_photos_signin.setVisible(out != "cli missing")
        if out == "signed-in":
            self.photos_state = "signed-in"
            self.lbl_photos_status.setText("Ready: Proton's tool is installed and signed in.")
            self.lbl_photos_status.setToolTip("")
            set_role(self.lbl_photos_status, "ok")
            self.btn_photos_signin.setText("Sign in again")
        elif out == "cli missing":
            self.photos_state = "cli missing"
            self.lbl_photos_status.setText("Proton's Drive tool is needed to read Photos. Click Install.")
            self.lbl_photos_status.setToolTip("Proton's official Drive tool is needed to read Proton Photos. "
                                              "Click Install (it comes from proton.me and its checksum is verified).")
            set_role(self.lbl_photos_status, "warn")
        else:
            self.photos_state = "other"
            self.lbl_photos_status.setText("Not signed in. Click Sign in (your browser opens Proton's page).")
            self.lbl_photos_status.setToolTip("")
            set_role(self.lbl_photos_status, "warn")
            self.btn_photos_signin.setText("Sign in")
        self.update_overview()

    def install_photos_cli(self):
        self.run_photos_cmd(["--install-cli"], "Installing Proton's tool...")

    def photos_sign_in(self):
        from .photos import find_cli
        cli = find_cli()
        if not cli:
            self.check_photos_status()
            return
        self.photos_tabs.setCurrentIndex(3)
        self.photos_log.append(">>> Opening Proton's sign-in page in your browser. Sign in there, then come back.")
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.MergedChannels)
        p.readyReadStandardOutput.connect(lambda: self.photos_log.append(
            ANSI_RE.sub("", bytes(p.readAllStandardOutput()).decode(errors="replace")).strip()))
        p.finished.connect(lambda *_: self.check_photos_status())
        self._signin_proc = p
        p.start(cli[0], [*cli[1:], "auth", "login"])

    # ---- running
    def run_photos(self, dry_run=False, index_only=False):
        if self.photos_proc and self.photos_proc.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Busy", "Proton Photos is already working. Wait for it or click Stop.")
            return
        if not index_only:
            if not self.save_photo_settings(show_popup=False):
                return
            if self.config.photos_mode == "bisync" and not dry_run:
                if QMessageBox.question(
                        self, "Two-way mode",
                        "Photos you deleted from the photos folder on this computer will be moved to Proton's trash "
                        "(restorable for 30 days).\n\nTip: click Preview first to see what would happen.\n\nContinue?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
                    return
        args = ["--config", str(CONFIG_PATH)]
        if dry_run:
            args.append("--dry-run")
        if index_only:
            args.append("--index-only")
        label = ("Loading dates and counts from Proton..." if index_only else
                 "Previewing (nothing will change)..." if dry_run else "Downloading Proton Photos...")
        self.run_photos_cmd(args, label)

    def run_photos_cmd(self, args, label):
        self.photos_tabs.setCurrentIndex(3)        # show what it's doing
        self.photos_log.append(f"\n>>> {label}")
        self._photos_last = label
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.MergedChannels)
        p.readyReadStandardOutput.connect(self.on_photos_output)
        p.finished.connect(self.on_photos_finished)
        self.photos_proc = p
        for b in (self.btn_photos_now, self.btn_photos_preview, self.btn_photos_refresh, self.btn_photos_install):
            b.setEnabled(False)
        self.btn_photos_stop.setEnabled(True)
        p.start(*tool("photos", *args))
        self.update_progress()
        self.update_header()

    def on_photos_output(self):
        if self.photos_proc:
            text = bytes(self.photos_proc.readAllStandardOutput()).decode(errors="replace")
            for line in ANSI_RE.sub("", text).splitlines():
                if line.strip():
                    self.photos_log.append(line)
                    self._photos_last = line.strip()
            self.update_progress()

    def on_photos_finished(self, code, _status):
        self.photos_log.append(">>> Done." if code == 0 else
                               ">>> Stopped." if code == 143 else f">>> Finished with a problem (code {code}). See above.")
        for b in (self.btn_photos_now, self.btn_photos_preview, self.btn_photos_refresh, self.btn_photos_install):
            b.setEnabled(True)
        self.btn_photos_stop.setEnabled(False)
        self.load_photos_index()
        self.check_photos_status()
        self.update_progress()
        self.update_header()

    def stop_photos(self):
        if self.photos_proc and self.photos_proc.state() != QProcess.NotRunning:
            stop_tool_process(self.photos_proc, "photos")

    # --------------------------------------------------------------------------
    # Page: Log (tabs: History = the sync log file, This window = what this window started)
    # --------------------------------------------------------------------------
    def build_page_log(self):
        page = Pane()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 16, 24, 12)
        lay.setSpacing(10)
        lay.addWidget(mk_label("Log", "h1"))
        self.log_tabs = tabs = mk_tabs()
        lay.addWidget(tabs, 1)

        v = add_tab(tabs, "History", margins=(14, 12, 14, 12), spacing=8)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(mk_button("Refresh", slot=self.load_full_log, compact=True))
        row.addWidget(mk_button("Open log file", slot=self.open_log_file, compact=True))
        row.addWidget(mk_button("Clear history", "danger", self.clear_log, compact=True))
        row.addStretch(1)
        self.log_info = mk_label("The last 500 lines", "small")
        row.addWidget(self.log_info)
        v.addLayout(row)
        self.full_log_view = LogView()
        v.addWidget(self.full_log_view, 1)

        v = add_tab(tabs, "This window", margins=(14, 12, 14, 12), spacing=8)
        row = QHBoxLayout()
        row.addWidget(mk_label("What you started from this window: previews, syncs, restarts and the system check.",
                               "small"))
        row.addStretch(1)
        row.addWidget(mk_button("Clear", slot=lambda: self.overview_log_view.clear(), compact=True))
        v.addLayout(row)
        self.overview_log_view = LogView("Nothing started from this window yet.")
        v.addWidget(self.overview_log_view, 1)
        self.load_full_log()
        return page

    def auto_refresh_log(self):
        if self.stack.currentIndex() == self.PAGE_LOG and self.log_tabs.currentIndex() == 0:
            self.load_full_log(force=False)

    def load_full_log(self, force=True):
        path = self.config.log_path
        try:
            mtime = path.stat().st_mtime if path.exists() else None
        except OSError:
            mtime = None
        if not force and mtime == self._log_mtime:
            return
        self._log_mtime = mtime
        self.refresh_last_sync()
        self.update_overview()
        if mtime is None:
            self.full_log_view.setPlainText(f"No sync log yet ({path}). It appears after the first sync.")
            return
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()[-500:]
            self.full_log_view.setPlainText("".join(lines))
            self.full_log_view.verticalScrollBar().setValue(self.full_log_view.verticalScrollBar().maximum())
            self.log_info.setText(f"The last 500 lines. Updated {dt.datetime.now():%H:%M:%S}")
        except OSError as e:
            self.full_log_view.setPlainText(f"Couldn't read the log: {e}")

    def clear_log(self):
        path = self.config.log_path
        if path.exists():
            if QMessageBox.question(self, "Clear log", "Delete all log history?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            path.write_text("", encoding="utf-8")
            self.load_full_log()
            self.toast("Log history cleared.")

    def open_log_file(self):
        if self.config.log_path.exists():
            self.open_path(self.config.log_path)

    # --------------------------------------------------------------------------
    # Sync Execution
    # --------------------------------------------------------------------------
    def run_sync_process(self, dry_run=False, confirmed=False):
        if self.process and self.process.state() == QProcess.Running:
            QMessageBox.information(self, "Sync Active", "A synchronization task is already running in the background.")
            return
        if self.background_sync_pid():
            QMessageBox.information(self, "Sync Active",
                                    "A background sync is already running. Use Restart Sync to start a fresh one.")
            return

        if not self.confirm_save_before_sync():
            return
        if self.config.sync_mode != "copy" and not dry_run and not confirmed:
            what = ("Files you deleted on this computer will also be DELETED in Proton Drive."
                    if self.config.sync_mode == "sync" else
                    "Deletions on either side will be copied to the other side.")
            if QMessageBox.question(
                    self, "Check before syncing",
                    f"Mode is '{self.config.sync_mode}'. {what}\n\nTip: run a Preview first.\n\nSync now?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
                return

        self.btn_sync_now.setEnabled(False)
        self.btn_dry_run.setEnabled(False)
        self.btn_cancel_sync.setEnabled(True)

        mode_str = "DRY-RUN PREVIEW" if dry_run else "LIVE SYNC"
        self.run_label = "Preview (nothing changes)" if dry_run else "Syncing"
        self.last_line = ""
        self.overview_log_view.append(f"\n>>> Starting {mode_str}...")
        if dry_run:
            self.show_output()      # the preview's list of what would happen is the point of it

        self.process = QProcess(self)
        self.process.errorOccurred.connect(self.on_sync_start_error)
        self.process.readyReadStandardOutput.connect(self.on_sync_output)
        self.process.readyReadStandardError.connect(self.on_sync_error)
        self.process.finished.connect(self.on_sync_finished)

        args = []
        if dry_run:
            args.append("--dry-run")

        args += ["--config", str(CONFIG_PATH)]
        self.process.start(*tool("sync", *args))
        self.update_overview()

    def on_sync_start_error(self, err):
        if err == QProcess.FailedToStart:
            self.overview_log_view.append("\n>>> Couldn't start the sync engine. Try reinstalling Sleepy Proton Drive.")
            self.btn_sync_now.setEnabled(True)
            self.btn_dry_run.setEnabled(True)
            self.btn_cancel_sync.setEnabled(False)
            self.update_overview()

    def _take_output(self, data):
        self.overview_log_view.add_raw(data)
        lines = [l.strip() for l in ANSI_RE.sub("", data).replace("\r", "\n").splitlines() if l.strip()]
        if lines:
            self.last_line = re.sub(r"^(\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\]\s*)?(>>>\s*)?", "", lines[-1])
            self.update_progress()

    def on_sync_output(self):
        self._take_output(self.process.readAllStandardOutput().data().decode("utf-8", errors="ignore"))

    def on_sync_error(self):
        self._take_output(self.process.readAllStandardError().data().decode("utf-8", errors="ignore"))

    def own_sync_running(self):
        return bool(self.process and self.process.state() != QProcess.NotRunning)

    def background_sync_pid(self):
        """PID of a sync started outside this window (timer, terminal), or None."""
        from .sync import running_pid
        pid = running_pid()
        if pid:
            own = self.process.processId() if self.own_sync_running() else 0
            if pid != own:
                return str(pid)
        return None

    def refresh_running_state(self):
        if self._ctl_proc and self._ctl_proc.state() != QProcess.NotRunning:
            return  # a stop/restart is in progress
        own = self.own_sync_running()
        bg = None if own else self.background_sync_pid()
        self._bg = bg
        busy = own or bool(bg)
        self.btn_cancel_sync.setEnabled(busy)
        self.btn_restart_sync.setEnabled(True)
        if not own:
            self.btn_sync_now.setEnabled(not bg)
            self.btn_dry_run.setEnabled(not bg)
        self.btn_sync_now.setText("Syncing..." if busy else "Sync now")
        self.update_overview()

    def run_sync_control(self, action, on_done=None):
        """Run 'sleepy-proton-sync --stop/--restart' without freezing the window."""
        if self._ctl_proc and self._ctl_proc.state() != QProcess.NotRunning:
            return
        self.btn_cancel_sync.setEnabled(False)
        self.btn_restart_sync.setEnabled(False)
        self.btn_sync_now.setEnabled(False)
        self.btn_dry_run.setEnabled(False)
        self._ctl_proc = QProcess(self)
        self._ctl_proc.setProcessChannelMode(QProcess.MergedChannels)

        def finished(*_):
            out = bytes(self._ctl_proc.readAll()).decode("utf-8", "ignore").strip()
            if out:
                self.overview_log_view.append(f">>> {out}")
                self.toast(out.splitlines()[-1])
            self._ctl_proc = None
            if on_done:
                on_done()
            self.refresh_running_state()
            self.load_full_log()
            self.update_timer_status()

        self._ctl_proc.finished.connect(finished)
        self._ctl_proc.errorOccurred.connect(lambda err: err == QProcess.FailedToStart and finished())
        self._ctl_proc.start(*tool("sync", "--config", str(CONFIG_PATH), f"--{action}"))
        self.update_overview()

    def cancel_sync_process(self):
        self.overview_log_view.append("\n>>> Stopping the sync...")
        # Stops the window's own sync and any background one (the engine handles both).
        self.run_sync_control("stop")

    def restart_sync_process(self):
        if not self.confirm_save_before_sync():
            return
        if self.config.sync_mode != "copy":
            what = ("Files you deleted on this computer will also be DELETED in Proton Drive."
                    if self.config.sync_mode == "sync" else
                    "Deletions on either side will be copied to the other side.")
            if QMessageBox.question(
                    self, "Check before syncing",
                    f"Mode is '{self.config.sync_mode}'. {what}\n\nRestart the sync?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
                return
        self.overview_log_view.append("\n>>> Restarting the sync (stop, then start fresh)...")
        if self.own_sync_running():
            # Stop our own run first, then start a fresh one here so its output shows live.
            def start_fresh():
                if self.own_sync_running():
                    self.process.waitForFinished(20000)
                self.run_sync_process(dry_run=False, confirmed=True)
            self.run_sync_control("stop", on_done=start_fresh)
        else:
            self.run_sync_control("restart")

    def on_sync_finished(self, exit_code, exit_status):
        self.btn_sync_now.setEnabled(True)
        self.btn_sync_now.setText("Sync now")
        self.btn_dry_run.setEnabled(True)
        self.btn_cancel_sync.setEnabled(False)

        if exit_code == 0:
            self.overview_log_view.append("\n>>> Sync finished successfully (Exit Code 0).")
            self.toast("Sync completed successfully!")
        else:
            self.overview_log_view.append(f"\n>>> Sync finished with exit code {exit_code}.")
            self.toast(f"Sync finished with code {exit_code}.", "warn")

        self.load_full_log()
        self.update_timer_status()
        self.update_overview()

    # --------------------------------------------------------------------------
    # External Dialogs
    # --------------------------------------------------------------------------
    def open_setup_wizard(self):
        wizard = SetupWizardDialog(self, self.config)
        if wizard.exec() == QDialog.Accepted:
            self.config.load()
            self.refresh_settings_widgets()
            self.update_timer_status()
            self.refresh_systemd_status()
            self.refresh_setup_state()
            self.toast("Setup wizard completed!")

    def run_diagnostics(self):
        if self.diag_proc and self.diag_proc.state() != QProcess.NotRunning:
            return
        self.show_output()
        self.overview_log_view.append("\n>>> Running system check (can take ~30 seconds)...")
        self.btn_diag.setEnabled(False)
        self.diag_proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("NO_COLOR", "1")
        self.diag_proc.setProcessEnvironment(env)
        self.diag_proc.setProcessChannelMode(QProcess.MergedChannels)
        self.diag_proc.readyRead.connect(
            lambda: self.overview_log_view.add_raw(
                ANSI_RE.sub("", bytes(self.diag_proc.readAll()).decode("utf-8", "ignore"))))

        def diag_done(*_):
            self.btn_diag.setEnabled(True)
            self.update_progress()
            self.update_header()
        self.diag_proc.finished.connect(diag_done)
        self.diag_proc.errorOccurred.connect(diag_done)
        if CHECK_SCRIPT:
            self.diag_proc.start("bash", [str(CHECK_SCRIPT)])
        else:
            self.diag_proc.start(*tool("check"))
        self.update_progress()
        self.update_header()

    def closeEvent(self, event):
        if self.settings_dirty():
            if QMessageBox.question(self, "Unsaved settings", "Close without saving?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                event.ignore()
                return
        if getattr(self, "photos_proc", None) and self.photos_proc.state() != QProcess.NotRunning:
            if QMessageBox.question(self, "Proton Photos running", "Proton Photos is still working. Stop it and quit?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                event.ignore()
                return
            stop_tool_process(self.photos_proc, "photos")
            self.photos_proc.waitForFinished(20000)
        if self.process and self.process.state() != QProcess.NotRunning:
            reply = QMessageBox.question(
                self, "Sync running",
                "A sync is still running. Stop it and quit?\n\n(Background syncs keep working either way.)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            stop_tool_process(self.process, "sync")
            if not self.process.waitForFinished(20000):
                kill_tree(self.process.processId(), force=True)
        event.accept()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--help" in argv or "-h" in argv:
        print("Usage: sleepy-proton-gui [--wizard]\n\n  --wizard   Open the setup wizard straight away\n  --version  Show the version")
        return
    if "--version" in argv:
        print(f"sleepy-proton-gui {APP_VERSION}")
        return
    # Enable high-DPI scaling attributes before creating QApplication
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    if IS_WINDOWS:
        try:  # own taskbar icon instead of Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TheSleepyLabs.SleepyProtonDrive")
        except (OSError, AttributeError):
            pass
    app = QApplication([sys.argv[0], *argv])
    app.setApplicationName("Sleepy Proton Drive")
    app.setOrganizationName("The Sleepy Labs")
    app.setDesktopFileName("thesleepylabs-proton-drive")
    ensure_theme()
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

