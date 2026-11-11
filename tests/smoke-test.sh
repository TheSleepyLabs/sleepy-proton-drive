#!/usr/bin/env bash
# Smoke test for the sync engine. Uses a fake "protondrive" remote that points at
# a temporary local folder, so no Proton account is needed and nothing real is touched.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE="$ROOT/bin/sleepy-proton-sync"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

export HOME="$T/home" XDG_RUNTIME_DIR="$T/run" XDG_CONFIG_HOME="$T/home/.config"
export XDG_STATE_HOME="$T/home/.local/state" RCLONE_CONFIG="$T/rclone.conf"
mkdir -p "$HOME/Documents/sub" "$HOME/Pictures" "$HOME/.config" "$T/cloud" "$XDG_RUNTIME_DIR"
CLOUD="$T/cloud/Devices/TestBox"

pass=0
ok()   { echo "  ok   - $1"; pass=$((pass + 1)); }
fail() { echo "  FAIL - $1"; exit 1; }
check() { if eval "$2"; then ok "$1"; else fail "$1"; fi; }

echo "Sleepy Proton Drive smoke test"
"$ENGINE" --no-notify >/dev/null 2>&1 && fail "should fail without a remote" || ok "fails cleanly without a remote"

rclone config create protondrive alias remote "$T/cloud" >/dev/null 2>&1
cat > "$HOME/.config/proton-sync.conf" <<'CONF'
REMOTE_NAME="protondrive"
DEVICE_NAME="TestBox"
SYNC_DIRS=("$HOME/Documents" "~/Pictures" "$HOME/Missing")
SYNC_MODE="copy"
NOTIFY=false
CONF
echo one > "$HOME/Documents/a.txt"
echo two > "$HOME/Documents/sub/b.txt"

"$ENGINE" --dry-run >/dev/null
check "dry run changes nothing" '[[ ! -e "$CLOUD/Documents/a.txt" ]]'

"$ENGINE" >/dev/null
check "copy uploads files" '[[ -f "$CLOUD/Documents/a.txt" && -f "$CLOUD/Documents/sub/b.txt" ]]'

rm "$HOME/Documents/a.txt"
"$ENGINE" >/dev/null
check "copy keeps files deleted locally" '[[ -f "$CLOUD/Documents/a.txt" ]]'

"$ENGINE" --mode sync >/dev/null
check "sync mirrors deletions" '[[ ! -e "$CLOUD/Documents/a.txt" ]]'

mkdir -p "$CLOUD/Pictures" && echo keep > "$CLOUD/Pictures/cloud-only.txt"
"$ENGINE" --mode sync >/dev/null
check "sync skips an empty local folder (no mass delete)" '[[ -f "$CLOUD/Pictures/cloud-only.txt" ]]'

echo fromcloud > "$CLOUD/Documents/c.txt"
"$ENGINE" --mode bisync >/dev/null
echo fromcloud2 > "$CLOUD/Documents/d.txt"
"$ENGINE" --mode bisync >/dev/null
check "bisync brings cloud files down" '[[ -f "$HOME/Documents/c.txt" && -f "$HOME/Documents/d.txt" ]]'

# Excluded folders: only that exact folder is skipped (odd characters included),
# a folder with the same name elsewhere is still backed up, and the cloud copy
# of an excluded folder is kept even in mirror mode.
W="$HOME/Work"
mkdir -p "$W/skip me [x]/deep" "$W/other/skip me [x]" "$W/Old"
echo a > "$W/top.txt"
echo b > "$W/skip me [x]/deep/secret.txt"
echo c > "$W/other/skip me [x]/keep.txt"
echo d > "$W/Old/old.txt"
cat > "$T/ex.conf" <<'CONF'
REMOTE_NAME="protondrive"
DEVICE_NAME="TestBox"
SYNC_DIRS=("$HOME/Work")
EXCLUDE_DIRS=("$HOME/Work/skip me [x]/" "~/Work/Old" "$HOME/Elsewhere/x")
SYNC_MODE="copy"
NOTIFY=false
CONF
WC="$CLOUD/Work"
mkdir -p "$WC/Old" && echo cloud > "$WC/Old/old.txt"
"$ENGINE" -c "$T/ex.conf" >/dev/null
check "excluded folder is not uploaded" '[[ -f "$WC/top.txt" && ! -e "$WC/skip me [x]" ]]'
check "same-named folder elsewhere is still uploaded" '[[ -f "$WC/other/skip me [x]/keep.txt" ]]'
check "excluded folder (written with ~) is not uploaded" '[[ "$(cat "$WC/Old/old.txt")" == cloud ]]'
"$ENGINE" -c "$T/ex.conf" --mode sync >/dev/null
check "mirror mode keeps the cloud copy of an excluded folder" '[[ -f "$WC/Old/old.txt" && ! -e "$WC/skip me [x]" ]]'
"$ENGINE" -c "$T/ex.conf" --mode bisync >/dev/null
echo e > "$W/skip me [x]/new.txt"
"$ENGINE" -c "$T/ex.conf" --mode bisync >/dev/null
check "two-way mode skips the excluded folder both ways" '[[ ! -e "$WC/skip me [x]" && "$(cat "$W/Old/old.txt")" == d ]]'
grep -q "Excluded folders" <<< "$("$ENGINE" -c "$T/ex.conf" --status)" && ok "--status lists excluded folders" || fail "--status excluded"

"$ENGINE" --mode nonsense >/dev/null 2>&1 && fail "bad mode accepted" || ok "rejects an unknown mode"
grep -q "TestBox" <<< "$("$ENGINE" --status)" && ok "--status works" || fail "--status"
bash -n "$HOME/.config/proton-sync.conf" && ok "config is valid bash"

echo "All $pass checks passed."
