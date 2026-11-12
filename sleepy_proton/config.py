"""Reads and writes proton-sync.conf.

The file is a small Bash file (so Linux users can still `source` it), made only of
NAME="value" and NAME=( "a" "b" ) lines. This module reads that format itself, so
Windows doesn't need Bash. If a hand-edited Linux config uses anything fancier,
it falls back to asking Bash."""

import os
import re
import shutil
import subprocess
from pathlib import Path

from .osutil import DEFAULT_LOG, DEFAULT_PHOTOS_DIR, HOME, IS_WINDOWS, hostname

MODES = ("copy", "sync", "bisync")
DEFAULT_EXCLUDES = [
    ".git/**", "node_modules/**", ".venv/**", "venv/**",
    "__pycache__/**", ".cache/**", "*.tmp", "*.crdownload", "*.part",
]
DEFAULT_RCLONE_FLAGS = [
    "--verbose", "--stats-one-line", "--stats=30s", "--transfers=4", "--checkers=8",
    "--tpslimit=10", "--skip-links",
    *[x for e in DEFAULT_EXCLUDES if e != "venv/**" for x in ("--exclude", e)],
]
LIST_VARS = ("SYNC_DIRS", "EXCLUDE_DIRS", "RCLONE_FLAGS", "PHOTOS_CATEGORIES", "PHOTOS_DATES")
SCALAR_VARS = ("REMOTE_NAME", "DEVICE_NAME", "SYNC_MODE", "NOTIFY", "LOG_FILE", "LOG_MAX_LINES",
               "PHOTOS_ENABLED", "PHOTOS_DIR", "PHOTOS_MODE")


class ConfigError(Exception):
    pass


class Unsupported(ConfigError):
    """The file uses Bash features this reader doesn't handle."""


# ---------------------------------------------------------------------- reader
_NAME_EQ = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WORD_END = set(" \t\r\n;&|<>()")


class _Parser:
    def __init__(self, text: str, env: dict):
        self.t = text
        self.i = 0
        self.env = env
        self.vars = {}

    def eof(self):
        return self.i >= len(self.t)

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else ""

    def line(self):
        return self.t.count("\n", 0, self.i) + 1

    def fail(self, msg, cls=ConfigError):
        raise cls(f"line {self.line()}: {msg}")

    def skip(self, newlines=True):
        while not self.eof():
            c = self.peek()
            if c in " \t\r" or (newlines and c in "\n;"):
                self.i += 1
            elif c == "\\" and self.t[self.i + 1:self.i + 2] == "\n":
                self.i += 2
            elif c == "#":
                while not self.eof() and self.peek() != "\n":
                    self.i += 1
            else:
                break

    def parse(self):
        while True:
            self.skip()
            if self.eof():
                return self.vars
            m = _NAME_EQ.match(self.t, self.i)
            if not m:
                self.fail("only NAME=value lines are supported", Unsupported)
            name = m.group()[:-1]
            self.i = m.end()
            if self.peek() == "(":
                self.i += 1
                items = []
                while True:
                    self.skip()
                    if self.eof():
                        self.fail(f"missing ')' for {name}")
                    if self.peek() == ")":
                        self.i += 1
                        break
                    items.append(self.word(start=True))
                self.vars[name] = items
            elif self.eof() or self.peek() in " \t\r\n;":
                self.vars[name] = ""
            else:
                self.vars[name] = self.word(start=True, assignment=True)
            self.skip(newlines=False)
            if not self.eof() and self.peek() not in "\n;" and not _NAME_EQ.match(self.t, self.i):
                self.fail("unexpected text after an assignment", Unsupported)

    def lookup(self, name):
        if name in self.vars:
            v = self.vars[name]
            return v[0] if isinstance(v, list) and v else ("" if isinstance(v, list) else v)
        if name == "HOME":
            return str(HOME)
        return self.env.get(name, "")

    def dollar(self):
        """At a '$': returns the expanded text."""
        self.i += 1
        c = self.peek()
        if c == "{":
            end = self.t.find("}", self.i)
            if end < 0:
                self.fail("missing '}'")
            inner = self.t[self.i + 1:end]
            self.i = end + 1
            m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)(?::?-(.*))?", inner, re.S)
            if not m:
                self.fail(f"${{{inner}}} isn't supported", Unsupported)
            val = self.lookup(m.group(1))
            if m.group(2) is not None and not val:
                val = m.group(2)
            return val
        if c == "(" or c == "`":
            self.fail("commands in the config ($(...)) aren't supported", Unsupported)
        m = _NAME.match(self.t, self.i)
        if m:
            self.i = m.end()
            return self.lookup(m.group())
        return "$"

    def word(self, start=False, assignment=False):
        out = []
        first = True
        while not self.eof():
            c = self.peek()
            if c in _WORD_END:
                if c in "()" and first:
                    self.fail(f"unexpected '{c}'")
                if c in "&|<>":
                    self.fail(f"'{c}' isn't supported", Unsupported)
                break
            if c == "'":
                end = self.t.find("'", self.i + 1)
                if end < 0:
                    self.fail("missing closing '")
                out.append(self.t[self.i + 1:end])
                self.i = end + 1
            elif c == '"':
                self.i += 1
                while True:
                    if self.eof():
                        self.fail('missing closing "')
                    c = self.peek()
                    if c == '"':
                        self.i += 1
                        break
                    if c == "\\":
                        n = self.t[self.i + 1:self.i + 2]
                        if n in ('"', "\\", "$", "`"):
                            out.append(n)
                            self.i += 2
                        elif n == "\n":
                            self.i += 2
                        else:
                            out.append("\\")
                            self.i += 1
                    elif c == "$":
                        out.append(self.dollar())
                    elif c == "`":
                        self.fail("commands in the config (`...`) aren't supported", Unsupported)
                    else:
                        out.append(c)
                        self.i += 1
            elif c == "\\":
                n = self.t[self.i + 1:self.i + 2]
                if n == "\n":
                    self.i += 2
                else:
                    out.append(n)
                    self.i += 2
            elif c == "$":
                out.append(self.dollar())
            elif c == "`":
                self.fail("commands in the config (`...`) aren't supported", Unsupported)
            elif c == "~" and first and (start or assignment):
                rest = self.t[self.i + 1:self.i + 2]
                if rest in ("", "/") or rest in _WORD_END:
                    out.append(str(HOME))
                else:
                    out.append("~")
                self.i += 1
            else:
                out.append(c)
                self.i += 1
            first = False
        return "".join(out)


def parse(text: str, env=None) -> dict:
    """Parses config text into {NAME: str | [str, ...]}."""
    return _Parser(text, dict(os.environ) if env is None else env).parse()


_BASH_DUMP = r"""
source "$1" >/dev/null 2>&1 || exit 3
for n in %(scalars)s; do [[ -v $n ]] && printf 's\0%%s\0%%s\0' "$n" "${!n}"; done
for n in %(lists)s; do
    if [[ -v $n ]]; then declare -n a=$n; printf 'l\0%%s\0%%s\0' "$n" "${#a[@]}"; printf '%%s\0' "${a[@]}"; unset -n a; fi
done
""" % {"scalars": " ".join(SCALAR_VARS), "lists": " ".join(LIST_VARS)}


def _parse_with_bash(path: Path) -> dict:
    bash = shutil.which("bash")
    if not bash or IS_WINDOWS:
        raise Unsupported("needs Bash")
    try:
        res = subprocess.run([bash, "-c", _BASH_DUMP, "_", str(path)], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ConfigError(str(e))
    if res.returncode != 0:
        raise ConfigError("Bash couldn't read it")
    v = res.stdout.decode("utf-8", errors="replace").split("\0")
    out, i = {}, 0
    while i + 2 < len(v):
        kind, name = v[i], v[i + 1]
        if kind == "s":
            out[name] = v[i + 2]
            i += 3
        elif kind == "l":
            n = int(v[i + 2])
            out[name] = v[i + 3:i + 3 + n]
            i += 3 + n
        else:
            break
    return out


def read_vars(path: Path) -> dict:
    """The NAME -> value pairs set by a config file (empty if there is no file)."""
    path = Path(path)
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        return parse(text)
    except Unsupported:
        return _parse_with_bash(path)


# ---------------------------------------------------------------- settings
def expand_path(p: str) -> str:
    """Leading ~ -> home folder; tidy slashes; no trailing slash."""
    p = (p or "").strip() if IS_WINDOWS else (p or "")
    if p == "~":
        p = str(HOME)
    elif p.startswith("~/") or (IS_WINDOWS and p.startswith("~\\")):
        p = str(HOME) + p[1:]
    if IS_WINDOWS and p:
        p = os.path.normpath(p)
    if len(p) > 1 and p.endswith("/") and not (IS_WINDOWS and re.fullmatch(r"[A-Za-z]:[\\/]", p)):
        p = p.rstrip("/") or "/"
    if IS_WINDOWS and len(p) > 3 and p.endswith("\\"):
        p = p.rstrip("\\")
    return p


class Settings:
    """All settings with their defaults; values come from read_vars()."""

    def __init__(self, path: Path = None, defaults: dict = None):
        self.path = Path(path) if path else None
        self.remote_name = "protondrive"
        self.device_name = ""
        self.sync_dirs = [str(HOME / "Documents")]
        self.exclude_dirs = []
        self.sync_mode = "copy"
        self.photos_enabled = False
        self.photos_dir = str(DEFAULT_PHOTOS_DIR)
        self.photos_mode = "copy"
        self.photos_categories = []
        self.photos_dates = []
        self.notify = True
        self.log_file = str(DEFAULT_LOG)
        self.log_max_lines = 5000
        self.rclone_flags = list(DEFAULT_RCLONE_FLAGS)
        for k, v in (defaults or {}).items():
            setattr(self, k, v)
        if self.path:
            self.apply(read_vars(self.path))

    def apply(self, v: dict):
        def s(name, cur):
            x = v.get(name)
            if isinstance(x, list):
                x = x[0] if x else ""
            return cur if x is None else x

        def lst(name, cur):
            x = v.get(name)
            if x is None:
                return cur
            return list(x) if isinstance(x, list) else ([x] if x else [])

        self.remote_name = s("REMOTE_NAME", self.remote_name) or "protondrive"
        self.device_name = s("DEVICE_NAME", self.device_name)
        self.sync_dirs = lst("SYNC_DIRS", self.sync_dirs)
        self.exclude_dirs = lst("EXCLUDE_DIRS", self.exclude_dirs)
        self.sync_mode = s("SYNC_MODE", self.sync_mode)
        self.photos_enabled = str(s("PHOTOS_ENABLED", self.photos_enabled)).strip().lower() == "true"
        self.photos_dir = s("PHOTOS_DIR", self.photos_dir) or str(DEFAULT_PHOTOS_DIR)
        self.photos_mode = s("PHOTOS_MODE", self.photos_mode)
        self.photos_categories = [c.strip().lower() for c in lst("PHOTOS_CATEGORIES", self.photos_categories) if c.strip()]
        self.photos_dates = [d.strip() for d in lst("PHOTOS_DATES", self.photos_dates) if d.strip()]
        self.notify = str(s("NOTIFY", self.notify)).strip().lower() == "true"
        self.log_file = s("LOG_FILE", self.log_file) or str(DEFAULT_LOG)
        try:
            self.log_max_lines = int(s("LOG_MAX_LINES", self.log_max_lines))
        except ValueError:
            self.log_max_lines = 5000
        self.rclone_flags = lst("RCLONE_FLAGS", self.rclone_flags)

    @property
    def device(self) -> str:
        d = self.device_name if self.device_name.strip() else hostname()
        return d.replace("/", "-")


# ---------------------------------------------------------------- writer
def dq(value) -> str:
    """Quote a string for a double-quoted Bash word."""
    value = str(value)
    for a, b in (("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"), ("`", "\\`")):
        value = value.replace(a, b)
    return f'"{value}"'


def dq_path(path) -> str:
    """Like dq, but writes paths inside the home folder as "$HOME/..." (with / on every system)."""
    path = str(path)
    home = str(HOME)
    norm = os.path.normcase
    if norm(path) == norm(home):
        return '"$HOME"'
    for sep in ("/", "\\"):
        if norm(path).startswith(norm(home + sep)):
            rest = path[len(home):]
            if IS_WINDOWS:
                rest = rest.replace("\\", "/")
            return '"$HOME' + dq(rest)[1:]
    return dq(path)


PAIR_FLAGS = ("--exclude", "--include", "--filter", "--filter-from", "--exclude-from", "--include-from")


def render(s: Settings, written_by: str) -> str:
    lines = [
        "# ==============================================================================",
        "# The Sleepy Labs // Sleepy Proton Drive configuration",
        f"# Written by {written_by}. This is a Bash file.",
        "# Options explained: https://github.com/TheSleepyLabs/sleepy-proton-drive#configuration",
        "# ==============================================================================",
        "",
        f"REMOTE_NAME={dq(s.remote_name)}",
        f"DEVICE_NAME={dq(s.device_name.replace('/', '-'))}",
        "",
        "SYNC_DIRS=(",
        *[f"    {dq_path(d)}" for d in s.sync_dirs],
        ")",
        "",
        "# Folders inside the ones above that are never backed up.",
        "# Copies already in Proton Drive are kept.",
        "EXCLUDE_DIRS=(",
        *[f"    {dq_path(d)}" for d in s.exclude_dirs],
        ")",
        "",
        "# copy = safest (never deletes in Proton) | sync = mirror | bisync = two-way",
        f"SYNC_MODE={dq(s.sync_mode)}",
        "",
        "# Proton Photos download (needs Proton's proton-drive tool).",
        "# copy = download only | sync = this folder matches Proton Photos | bisync = two-way",
        f"PHOTOS_ENABLED={'true' if s.photos_enabled else 'false'}",
        f"PHOTOS_DIR={dq_path(s.photos_dir)}",
        f"PHOTOS_MODE={dq(s.photos_mode)}",
        "# Empty = everything. Categories: other favorites screenshots videos live-photos",
        "# motion-photos selfies portraits bursts panoramas raw. Dates: \"2026\" or \"2026-11\".",
        "PHOTOS_CATEGORIES=(" + " ".join(dq(c) for c in s.photos_categories) + ")",
        "PHOTOS_DATES=(" + " ".join(dq(d) for d in s.photos_dates) + ")",
        "",
        f"NOTIFY={'true' if s.notify else 'false'}",
        f"LOG_FILE={dq_path(s.log_file)}",
        f"LOG_MAX_LINES={int(s.log_max_lines)}",
        "",
        "RCLONE_FLAGS=(",
    ]
    f, i = s.rclone_flags, 0
    while i < len(f):
        if f[i] in PAIR_FLAGS and i + 1 < len(f):
            lines.append(f"    {dq(f[i])} {dq(f[i + 1])}")
            i += 2
        else:
            lines.append(f"    {dq(f[i])}")
            i += 1
    lines += [")", ""]
    return "\n".join(lines)


def save(s: Settings, path: Path, written_by: str = "sleepy-proton-gui"):
    """Writes the config atomically, after checking it reads back the same; keeps a .bak."""
    path = Path(path)
    text = render(s, written_by)
    back = Settings()
    back.apply(parse(text))
    for k in ("remote_name", "sync_dirs", "exclude_dirs", "sync_mode", "photos_dir", "photos_categories",
              "photos_dates", "log_file", "rclone_flags"):
        a, b = getattr(back, k), getattr(s, k)
        if k in ("sync_dirs", "exclude_dirs", "photos_dir", "log_file"):
            a = [os.path.normcase(os.path.normpath(x)) for x in (a if isinstance(a, list) else [a])]
            b = [os.path.normcase(os.path.normpath(x)) for x in (b if isinstance(b, list) else [b])]
        if a != b:
            raise ConfigError(f"Refusing to save: {k} wouldn't read back the same ({b!r} -> {a!r}).")
    if not IS_WINDOWS and shutil.which("bash"):
        chk = subprocess.run(["bash", "-n"], input=text, capture_output=True, text=True)
        if chk.returncode != 0:
            raise ConfigError(f"Refusing to save an invalid config:\n{chk.stderr}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
