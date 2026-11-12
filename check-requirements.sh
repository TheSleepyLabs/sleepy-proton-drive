#!/usr/bin/env bash
# ==============================================================================
# The Sleepy Labs // Sleepy Proton Drive - Requirements Check
# ==============================================================================
# Checks rclone (with Proton Drive support), Python + PySide6 for the GUI,
# systemd user timers, notifications and Proton reachability.
# Use --fix to install what's missing.
# ==============================================================================

set -uo pipefail

MIN_RCLONE_VER="1.63.0"
MIN_PYTHON_VER="3.10"
VENV_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/sleepy-proton-drive/venv"
CONFIG_FILE="${PROTON_SYNC_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/proton-sync.conf}"

CLR_RESET="\033[0m"; CLR_BOLD="\033[1m"; CLR_DIM="\033[2m"
CLR_VIOLET="\033[38;2;139;92;246m"; CLR_LAVENDER="\033[38;2;183;148;255m"
CLR_CYAN="\033[38;2;56;189;248m"; CLR_PINK="\033[38;2;236;72;153m"
CLR_EMERALD="\033[38;2;16;185;129m"; CLR_AMBER="\033[38;2;245;182;90m"
if [[ ! -t 1 || -n "${NO_COLOR:-}" ]]; then
    CLR_RESET="" CLR_BOLD="" CLR_DIM="" CLR_VIOLET="" CLR_LAVENDER="" CLR_CYAN=""
    CLR_PINK="" CLR_EMERALD="" CLR_AMBER=""
fi

FIX_MODE=false
JSON_MODE=false
for arg in "$@"; do
    case "$arg" in
        --fix|--install-missing|-i) FIX_MODE=true ;;
        --json) JSON_MODE=true ;;
        -h|--help)
            cat <<EOF
Sleepy Proton Drive requirements check (The Sleepy Labs)

Usage: check-requirements.sh [--fix] [--json]
       (installed as: sleepy-proton-check)

  -i, --fix    Install what's missing (asks for your password via sudo when needed)
      --json   Print a short JSON summary instead of text
  -h, --help   Show this help

Checks: rclone >= $MIN_RCLONE_VER with Proton Drive support, Python >= $MIN_PYTHON_VER,
PySide6 (only for the GUI), systemd user timers, notify-send, xdg-open,
connection to Proton, and your Proton Drive login.
EOF
            exit 0 ;;
        *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

TOTAL=0; PASSED=0; WARNINGS=0; ERRORS=0
out() { [[ "$JSON_MODE" == "true" ]] || echo -e "$@"; }
pass() { TOTAL=$((TOTAL + 1)); PASSED=$((PASSED + 1)); out "  [ ${CLR_EMERALD}✓ PASS${CLR_RESET} ] $1"; }
warn() { TOTAL=$((TOTAL + 1)); WARNINGS=$((WARNINGS + 1)); out "  [ ${CLR_AMBER}! WARN${CLR_RESET} ] $1"; [[ -n "${2:-}" ]] && out "             ${CLR_DIM}↳ $2${CLR_RESET}"; return 0; }
fail() { TOTAL=$((TOTAL + 1)); ERRORS=$((ERRORS + 1)); out "  [ ${CLR_PINK}✗ FAIL${CLR_RESET} ] $1"; [[ -n "${2:-}" ]] && out "             ${CLR_DIM}↳ $2${CLR_RESET}"; return 0; }

version_ge() {  # 0 if $1 >= $2
    [[ "$(printf '%s\n%s\n' "${1#v}" "${2#v}" | sort -V | head -n1)" == "${2#v}" ]]
}

# Package manager detection
PKG=""
for pm in dnf apt-get pacman zypper; do
    command -v "$pm" &>/dev/null && { PKG="$pm"; break; }
done

pkg_install() {  # pkg_install <package...>
    case "$PKG" in
        dnf) sudo dnf install -y "$@" ;;
        apt-get) sudo apt-get update && sudo apt-get install -y "$@" ;;
        pacman) sudo pacman -S --needed --noconfirm "$@" ;;
        zypper) sudo zypper --non-interactive install "$@" ;;
        *) return 1 ;;
    esac
}

install_rclone() {
    out "\n${CLR_CYAN}Installing rclone...${CLR_RESET}"
    # Debian/Ubuntu packages are usually too old for Proton Drive, so use
    # rclone's official installer there (and when no package manager is known).
    if [[ "$PKG" == "dnf" || "$PKG" == "pacman" || "$PKG" == "zypper" ]]; then
        pkg_install rclone
        if command -v rclone &>/dev/null && rclone help backend protondrive &>/dev/null; then
            return 0
        fi
        out "${CLR_AMBER}The packaged rclone has no Proton Drive support; using rclone's official installer.${CLR_RESET}"
    fi
    if command -v curl &>/dev/null; then
        curl -fsSL https://rclone.org/install.sh | sudo bash
    else
        out "${CLR_PINK}curl is missing. Install rclone by hand: https://rclone.org/install/${CLR_RESET}"
        return 1
    fi
}

install_pyside6() {
    out "\n${CLR_CYAN}Installing PySide6 (for the GUI)...${CLR_RESET}"
    case "$PKG" in
        dnf) pkg_install python3-pyside6 && return 0 ;;
        pacman) pkg_install pyside6 && return 0 ;;
        zypper) pkg_install python3-pyside6 && return 0 ;;
    esac
    # Fallback that works everywhere: a private virtual environment that the
    # GUI uses automatically. Nothing is installed system-wide.
    out "${CLR_DIM}Creating a private Python environment at $VENV_DIR ...${CLR_RESET}"
    if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        if [[ "$PKG" == "apt-get" ]]; then
            pkg_install python3-venv && python3 -m venv "$VENV_DIR"
        else
            return 1
        fi
    fi
    "$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
    "$VENV_DIR/bin/python" -m pip install PySide6
}

if [[ "$JSON_MODE" != "true" ]]; then
    out "${CLR_VIOLET}${CLR_BOLD}"
    out "   ★  SLEEPY PROTON DRIVE  ★   requirements check"
    out "${CLR_RESET}${CLR_DIM}   by The Sleepy Labs${CLR_RESET}"
    out "${CLR_LAVENDER}------------------------------------------------------------------------------${CLR_RESET}"
fi

# 1. System
OS_NAME="$(uname -s)"
DISTRO="$OS_NAME"
if [[ -f /etc/os-release ]]; then
    DISTRO="$(. /etc/os-release; echo "${PRETTY_NAME:-$NAME}")"
fi
if [[ "$OS_NAME" == "Linux" ]]; then
    pass "System: ${CLR_BOLD}${DISTRO}${CLR_RESET} ($(uname -m))"
else
    warn "System: $DISTRO is not Linux." "Sleepy Proton Drive is made for Linux; other systems are untested."
fi

# 2. rclone
rclone_ok() {
    command -v rclone &>/dev/null || return 1
    local v
    v="$(rclone version 2>/dev/null | head -n1 | awk '{print $2}')"
    version_ge "${v#v}" "$MIN_RCLONE_VER" && rclone help backend protondrive &>/dev/null
}
if ! rclone_ok && [[ "$FIX_MODE" == "true" ]]; then
    install_rclone || true
    hash -r
fi
if command -v rclone &>/dev/null; then
    RCLONE_VER="$(rclone version 2>/dev/null | head -n1 | awk '{print $2}')"
    if version_ge "${RCLONE_VER#v}" "$MIN_RCLONE_VER"; then
        pass "rclone ${CLR_BOLD}${RCLONE_VER}${CLR_RESET} ($(command -v rclone))"
    else
        fail "rclone ${RCLONE_VER} is too old (Proton Drive needs v${MIN_RCLONE_VER}+)." "Run: sleepy-proton-check --fix"
    fi
    if rclone help backend protondrive &>/dev/null; then
        pass "rclone has Proton Drive support."
    else
        fail "This rclone build has no Proton Drive support." "Run: sleepy-proton-check --fix   (or see https://rclone.org/install/)"
    fi
else
    fail "rclone is not installed." "Run: sleepy-proton-check --fix   (or see https://rclone.org/install/)"
fi

# 3. Python (runs the sync engine) + PySide6 (GUI only)
if command -v python3 &>/dev/null; then
    PY_VER="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo 0)"
    if version_ge "$PY_VER" "$MIN_PYTHON_VER"; then
        pass "Python ${CLR_BOLD}${PY_VER}${CLR_RESET}"
    else
        fail "Python $PY_VER is too old (needs $MIN_PYTHON_VER+)." "Install a newer python3 with your package manager."
    fi
    pyside_ok() {
        python3 -c "import PySide6.QtWidgets" &>/dev/null || \
            { [[ -x "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -c "import PySide6.QtWidgets" &>/dev/null; }
    }
    if ! pyside_ok && [[ "$FIX_MODE" == "true" ]]; then
        install_pyside6 || true
    fi
    if python3 -c "import PySide6.QtWidgets" &>/dev/null; then
        pass "PySide6 $(python3 -c 'import PySide6; print(PySide6.__version__)' 2>/dev/null) (for the GUI)"
    elif [[ -x "$VENV_DIR/bin/python" ]] && "$VENV_DIR/bin/python" -c "import PySide6.QtWidgets" &>/dev/null; then
        pass "PySide6 (private environment in $VENV_DIR, for the GUI)"
    else
        warn "PySide6 is not installed, so the GUI won't open." "The terminal tools work without it. To add it: sleepy-proton-check --fix"
    fi
else
    fail "Python 3 not found (it runs the sync)." "Install it with your package manager, e.g. sudo dnf install python3"
fi

# 4. systemd user timers
if command -v systemctl &>/dev/null && systemctl --user show-environment &>/dev/null; then
    pass "systemd user session (automatic background sync available)"
else
    warn "No systemd user session." "Automatic sync won't work; run sleepy-proton-sync by hand or from cron."
fi

# 5. Desktop extras
if command -v notify-send &>/dev/null; then
    pass "notify-send (desktop notifications)"
else
    warn "notify-send not found, so no desktop notifications." "Install libnotify (Fedora/Arch) or libnotify-bin (Debian/Ubuntu)."
fi
if command -v xdg-open &>/dev/null; then
    pass "xdg-open (open folders/logs from the GUI)"
else
    warn "xdg-open not found." "Install xdg-utils to open folders from the GUI."
fi

# 6. Network
NET_OK=false
if command -v curl &>/dev/null; then
    curl -s -o /dev/null --connect-timeout 6 --max-time 15 https://proton.me && NET_OK=true
fi
if [[ "$NET_OK" == "true" ]]; then
    pass "Can reach Proton's servers"
else
    warn "Couldn't reach Proton's servers." "Check your internet connection, VPN or firewall."
fi

# 7. Login
REMOTE_NAME="protondrive"
if [[ -f "$CONFIG_FILE" ]]; then
    REMOTE_NAME="$(bash -c 'REMOTE_NAME=protondrive; source "$1" >/dev/null 2>&1; printf %s "$REMOTE_NAME"' _ "$CONFIG_FILE" 2>/dev/null || echo protondrive)"
fi
if command -v rclone &>/dev/null; then
    if grep -qxF "${REMOTE_NAME}:" <<< "$(rclone listremotes 2>/dev/null)"; then
        if timeout 45 rclone lsd "${REMOTE_NAME}:" --contimeout 15s --timeout 30s &>/dev/null; then
            pass "Logged in to Proton Drive (remote '${REMOTE_NAME}')"
        else
            warn "The Proton Drive login '${REMOTE_NAME}' isn't working." "Log in again with: sleepy-proton-wizard (or the GUI)"
        fi
    else
        warn "Not logged in to Proton Drive yet." "Next step: sleepy-proton-wizard (or sleepy-proton-gui)"
    fi
fi

if [[ "$JSON_MODE" == "true" ]]; then
    printf '{"total": %d, "passed": %d, "warnings": %d, "errors": %d, "status": "%s"}\n' \
        "$TOTAL" "$PASSED" "$WARNINGS" "$ERRORS" "$([[ $ERRORS -eq 0 ]] && echo ready || echo error)"
    [[ $ERRORS -eq 0 ]]; exit $?
fi

out "${CLR_LAVENDER}------------------------------------------------------------------------------${CLR_RESET}"
if [[ $ERRORS -eq 0 ]]; then
    out "${CLR_EMERALD}${CLR_BOLD}✓ Ready to go.${CLR_RESET}$([[ $WARNINGS -gt 0 ]] && echo " ($WARNINGS optional note(s) above)")"
    exit 0
else
    out "${CLR_PINK}${CLR_BOLD}✗ $ERRORS required item(s) missing.${CLR_RESET} Run ${CLR_CYAN}sleepy-proton-check --fix${CLR_RESET} (or ./check-requirements.sh --fix)."
    exit 1
fi
