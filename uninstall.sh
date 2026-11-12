#!/usr/bin/env bash
# ==============================================================================
# The Sleepy Labs // Sleepy Proton Drive - Uninstaller
# ==============================================================================
# Removes the programs, app menu entry and background timer.
# Your files in Proton Drive and on this computer are never touched.
# ==============================================================================

set -euo pipefail

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
BIN_DIR="$HOME/.local/bin"
SYSTEMD_USER_DIR="$CONFIG_HOME/systemd/user"

PURGE=false
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=true ;;
        -h|--help)
            cat <<EOF
Sleepy Proton Drive uninstaller

Usage: ./uninstall.sh [--purge]

  --purge   Also delete settings (~/.config/proton-sync.conf), the log, saved
            two-way-sync state and the private GUI Python environment.

Your Proton login (in rclone) is kept. To remove it too: rclone config delete protondrive
Files in Proton Drive and on this computer are never deleted.
EOF
            exit 0 ;;
        *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

echo ">>> Uninstalling Sleepy Proton Drive..."

# 1. Background timer (current and v1.0 names; v1.0 names only if they're ours)
if command -v systemctl &>/dev/null && systemctl --user show-environment &>/dev/null; then
    systemctl --user disable --now sleepy-proton-sync.timer &>/dev/null || true
    systemctl --user stop sleepy-proton-sync.service &>/dev/null || true
fi
rm -f "$SYSTEMD_USER_DIR/sleepy-proton-sync.timer" "$SYSTEMD_USER_DIR/sleepy-proton-sync.service"
for u in proton-sync.timer proton-sync.service; do
    f="$SYSTEMD_USER_DIR/$u"
    if [[ -L "$f" ]] || { [[ -f "$f" ]] && grep -q "Sleepy Labs" "$f"; }; then
        systemctl --user disable --now "$u" &>/dev/null || true
        rm -f "$f"
    fi
done
command -v systemctl &>/dev/null && systemctl --user daemon-reload &>/dev/null || true

# 2. Programs (+ v1.0 aliases that point at them)
rm -f "$BIN_DIR/sleepy-proton-sync" "$BIN_DIR/sleepy-proton-gui" \
      "$BIN_DIR/sleepy-proton-wizard" "$BIN_DIR/sleepy-proton-check" \
      "$BIN_DIR/sleepy-proton-photos"
for old in proton-sync proton-sync-gui setup-proton-rclone; do
    if [[ -L "$BIN_DIR/$old" ]] && [[ "$(readlink "$BIN_DIR/$old")" == *sleepy-proton-* ]]; then
        rm -f "$BIN_DIR/$old"
    fi
done

# 3. App menu entry, desktop shortcuts and icons
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
rm -f "$DATA_HOME/applications/thesleepylabs-proton-drive.desktop" \
      "$DATA_HOME/icons/hicolor/scalable/apps/sleepy-proton-drive.svg" \
      "$DATA_HOME/icons/sleepy-proton-drive.svg" \
      "$DESKTOP_DIR/Sleepy-Proton-Drive.desktop"
if [[ -f "$DESKTOP_DIR/Proton-Drive-Sync.desktop" ]] && grep -q "sleepy-proton-gui" "$DESKTOP_DIR/Proton-Drive-Sync.desktop"; then
    rm -f "$DESKTOP_DIR/Proton-Drive-Sync.desktop"
fi
command -v update-desktop-database &>/dev/null && update-desktop-database "$DATA_HOME/applications" &>/dev/null || true

# 4. Program files (the private GUI Python environment is kept unless --purge)
rm -rf "$DATA_HOME/sleepy-proton-drive/app"

# 5. Optional: settings and data
if [[ "$PURGE" == "true" ]]; then
    rm -f "$CONFIG_HOME/proton-sync.conf" "$CONFIG_HOME/proton-sync.conf.bak" \
          "$DATA_HOME/proton-sync.log"
    rm -rf "$STATE_HOME/sleepy-proton-drive" "$DATA_HOME/sleepy-proton-drive" \
           "${XDG_CACHE_HOME:-$HOME/.cache}/sleepy-proton-drive"
    echo "✓ Settings, log and saved state removed."
fi

echo "✓ Sleepy Proton Drive is uninstalled."
if [[ "$PURGE" == "false" ]]; then
    echo "  Your settings (~/.config/proton-sync.conf) were kept. Use --purge to remove them too."
fi
echo "  Your Proton login is still saved in rclone. Remove it with: rclone config delete protondrive"
if [[ -x "$BIN_DIR/proton-drive" ]]; then
    echo "  Proton's own proton-drive tool (used for Photos) was kept in $BIN_DIR."
    echo "  Remove it with: proton-drive auth logout && rm $BIN_DIR/proton-drive"
fi
