#!/usr/bin/env bash
# ==============================================================================
# The Sleepy Labs // Sleepy Proton Drive - Installer
# ==============================================================================
# Installs for the current user only (no root needed):
#   ~/.local/bin/sleepy-proton-{sync,photos,wizard,gui,check}
#   ~/.local/share/sleepy-proton-drive/app/   program files (Python)
#   app menu entry + icon
# Run ./install.sh --help for options.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN_DIR="$HOME/.local/bin"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
APP_DIR="$DATA_HOME/applications"
SYSTEMD_USER_DIR="$CONFIG_HOME/systemd/user"
DESKTOP_FILE="$APP_DIR/thesleepylabs-proton-drive.desktop"

CLR_RESET="\033[0m"; CLR_BOLD="\033[1m"; CLR_VIOLET="\033[38;2;139;92;246m"
CLR_LAVENDER="\033[38;2;183;148;255m"; CLR_CYAN="\033[38;2;56;189;248m"
CLR_EMERALD="\033[38;2;16;185;129m"; CLR_AMBER="\033[38;2;245;182;90m"
if [[ ! -t 1 || -n "${NO_COLOR:-}" ]]; then
    CLR_RESET="" CLR_BOLD="" CLR_VIOLET="" CLR_LAVENDER="" CLR_CYAN="" CLR_EMERALD="" CLR_AMBER=""
fi

ASSUME_YES=false
DESKTOP_SHORTCUT=false
SKIP_DEPS=false
for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=true ;;
        --desktop-shortcut) DESKTOP_SHORTCUT=true ;;
        --no-deps) SKIP_DEPS=true ;;
        -h|--help)
            cat <<EOF
Sleepy Proton Drive installer (installs for your user only, no root needed)

Usage: ./install.sh [OPTIONS]

  -y, --yes             Install missing requirements without asking
      --desktop-shortcut  Also put a launcher on your Desktop
      --no-deps         Don't check or install requirements
  -h, --help            Show this help
EOF
            exit 0 ;;
        *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

echo -e "${CLR_VIOLET}${CLR_BOLD}"
echo "   ★  SLEEPY PROTON DRIVE  ★   installer"
echo -e "${CLR_RESET}   by The Sleepy Labs"
echo -e "${CLR_LAVENDER}==============================================================================${CLR_RESET}"

# 1. Requirements
if [[ "$SKIP_DEPS" == "false" ]]; then
    echo -e "${CLR_CYAN}>>> Checking requirements...${CLR_RESET}"
    if ! bash "$SCRIPT_DIR/check-requirements.sh"; then
        do_fix=false
        if [[ "$ASSUME_YES" == "true" ]]; then
            do_fix=true
        elif [[ -t 0 ]]; then
            read -rp "Install the missing requirements now? (uses sudo for system packages) [Y/n]: " ans || ans="n"
            [[ "${ans:-Y}" =~ ^[Yy] ]] && do_fix=true
        fi
        if [[ "$do_fix" == "true" ]]; then
            bash "$SCRIPT_DIR/check-requirements.sh" --fix || \
                echo -e "${CLR_AMBER}Some requirements are still missing; see above. Installing anyway.${CLR_RESET}"
        else
            echo -e "${CLR_AMBER}Continuing. Fix missing items later with: sleepy-proton-check --fix${CLR_RESET}"
        fi
    elif [[ "$ASSUME_YES" == "true" ]] || [[ -t 0 ]]; then
        # rclone is fine; PySide6 (GUI) is optional, offer it if missing.
        if ! python3 -c "import PySide6.QtWidgets" &>/dev/null && \
           ! "$DATA_HOME/sleepy-proton-drive/venv/bin/python" -c "import PySide6.QtWidgets" &>/dev/null; then
            ans="y"
            if [[ "$ASSUME_YES" != "true" ]]; then
                read -rp "The GUI needs PySide6. Install it now? [Y/n]: " ans || ans="n"
                ans="${ans:-Y}"
            fi
            [[ "$ans" =~ ^[Yy] ]] && { bash "$SCRIPT_DIR/check-requirements.sh" --fix >/dev/null || true; }
        fi
    fi
fi

mkdir -p "$BIN_DIR" "$ICON_DIR" "$APP_DIR"

# 2. Programs: the Python program files, then the commands that start them.
PROGRAM_DIR="$DATA_HOME/sleepy-proton-drive/app"
echo -e "\n${CLR_CYAN}>>> Installing program files to $PROGRAM_DIR${CLR_RESET}"
mkdir -p "$PROGRAM_DIR"
new_pkg="$(mktemp -d "$PROGRAM_DIR/.new.XXXXXX")"
cp -r "$SCRIPT_DIR/sleepy_proton" "$new_pkg/sleepy_proton"
find "$new_pkg" -name "__pycache__" -type d -prune -exec rm -rf {} +
rm -rf "$PROGRAM_DIR/sleepy_proton.old"
[[ -d "$PROGRAM_DIR/sleepy_proton" ]] && mv "$PROGRAM_DIR/sleepy_proton" "$PROGRAM_DIR/sleepy_proton.old"
mv "$new_pkg/sleepy_proton" "$PROGRAM_DIR/sleepy_proton"
rm -rf "$new_pkg" "$PROGRAM_DIR/sleepy_proton.old"
mkdir -p "$PROGRAM_DIR/assets"
install -m 644 "$SCRIPT_DIR/assets/sleepy-proton-drive.svg" "$PROGRAM_DIR/assets/sleepy-proton-drive.svg"
install -m 644 "$SCRIPT_DIR/check-requirements.sh" "$PROGRAM_DIR/check-requirements.sh"

echo -e "${CLR_CYAN}>>> Installing commands to $BIN_DIR${CLR_RESET}"
install -m 755 "$SCRIPT_DIR/bin/sleepy-proton-sync"   "$BIN_DIR/sleepy-proton-sync"
install -m 755 "$SCRIPT_DIR/bin/sleepy-proton-wizard" "$BIN_DIR/sleepy-proton-wizard"
install -m 755 "$SCRIPT_DIR/bin/sleepy-proton-gui"    "$BIN_DIR/sleepy-proton-gui"
install -m 755 "$SCRIPT_DIR/bin/sleepy-proton-photos" "$BIN_DIR/sleepy-proton-photos"
install -m 755 "$SCRIPT_DIR/check-requirements.sh"    "$BIN_DIR/sleepy-proton-check"

# Clean up command aliases made by v1.0 (only when they point at our programs).
for old in proton-sync proton-sync-gui setup-proton-rclone; do
    if [[ -L "$BIN_DIR/$old" ]] && [[ "$(readlink "$BIN_DIR/$old")" == *sleepy-proton-* ]]; then
        rm -f "$BIN_DIR/$old"
    fi
done

# 3. Icon + app menu entry
echo -e "${CLR_CYAN}>>> Adding the app menu entry${CLR_RESET}"
install -m 644 "$SCRIPT_DIR/assets/sleepy-proton-drive.svg" "$ICON_DIR/sleepy-proton-drive.svg"
rm -f "$DATA_HOME/icons/sleepy-proton-drive.svg"   # old v1.0 location
sed -e "s|^Exec=sleepy-proton-gui|Exec=\"$BIN_DIR/sleepy-proton-gui\"|" \
    -e "s|^Exec=sleepy-proton-sync|Exec=\"$BIN_DIR/sleepy-proton-sync\"|" \
    -e "s|^Exec=sleepy-proton-wizard|Exec=\"$BIN_DIR/sleepy-proton-wizard\"|" \
    -e "s|^Icon=sleepy-proton-drive|Icon=$ICON_DIR/sleepy-proton-drive.svg|" \
    "$SCRIPT_DIR/assets/sleepy-proton-drive.desktop" > "$DESKTOP_FILE"
chmod 644 "$DESKTOP_FILE"
command -v update-desktop-database &>/dev/null && update-desktop-database "$APP_DIR" &>/dev/null || true
command -v gtk-update-icon-cache &>/dev/null && gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" &>/dev/null || true

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [[ "$DESKTOP_SHORTCUT" == "true" && -d "$DESKTOP_DIR" ]]; then
    install -m 755 "$DESKTOP_FILE" "$DESKTOP_DIR/Sleepy-Proton-Drive.desktop"
    command -v gio &>/dev/null && gio set "$DESKTOP_DIR/Sleepy-Proton-Drive.desktop" metadata::trusted true &>/dev/null || true
    echo "    Desktop shortcut added."
fi
# Refresh a v1.0 desktop shortcut if one exists.
if [[ -f "$DESKTOP_DIR/Proton-Drive-Sync.desktop" ]] && grep -q "sleepy-proton-gui" "$DESKTOP_DIR/Proton-Drive-Sync.desktop"; then
    install -m 755 "$DESKTOP_FILE" "$DESKTOP_DIR/Proton-Drive-Sync.desktop"
fi

# 4. Upgrade an existing background timer (v1.0 used different unit names).
if command -v systemctl &>/dev/null && systemctl --user show-environment &>/dev/null; then
    interval=""
    for t in sleepy-proton-sync.timer proton-sync.timer; do
        if systemctl --user is-enabled --quiet "$t" 2>/dev/null || systemctl --user is-active --quiet "$t" 2>/dev/null; then
            interval="$(sed -n 's/^OnUnitActiveSec=//p' "$SYSTEMD_USER_DIR/$t" 2>/dev/null | head -n1)"
            [[ -n "$interval" ]] && break
        fi
    done
    if [[ -n "$interval" ]]; then
        echo -e "${CLR_CYAN}>>> Updating your background sync timer (every $interval)${CLR_RESET}"
        "$BIN_DIR/sleepy-proton-sync" --enable-timer "$interval" | sed 's/^/    /'
    fi
fi

echo -e "\n${CLR_LAVENDER}==============================================================================${CLR_RESET}"
echo -e "${CLR_EMERALD}${CLR_BOLD}★ Installed! ★${CLR_RESET}"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo -e "${CLR_AMBER}Note: $BIN_DIR is not in your PATH. Add this line to ~/.bashrc (or ~/.zshrc), then open a new terminal:${CLR_RESET}"
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
echo -e "Next: open ${CLR_BOLD}Sleepy Proton Drive${CLR_RESET} from your app menu, or run one of:"
echo -e "  ${CLR_CYAN}sleepy-proton-gui${CLR_RESET}      app with setup wizard"
echo -e "  ${CLR_CYAN}sleepy-proton-wizard${CLR_RESET}   setup in the terminal"
echo -e "  ${CLR_CYAN}sleepy-proton-sync${CLR_RESET}     sync now (--help for options)"
echo -e "  ${CLR_CYAN}sleepy-proton-check${CLR_RESET}    check requirements"
