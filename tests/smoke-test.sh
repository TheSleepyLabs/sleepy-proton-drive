#!/usr/bin/env bash
# Smoke test for the sync engine. Uses a fake "protondrive" remote that points at
# a temporary local folder, so no Proton account is needed and nothing real is touched.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENGINE="$ROOT/bin/sleepy-proton-sync"
T="$(mktemp -d)"
trap '[[ -n "${KEEP:-}" ]] || rm -rf "$T"' EXIT

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

# Proton Photos, against a fake Proton (tests/fake-proton-drive).
PHOTOS="$ROOT/bin/sleepy-proton-photos"
export PROTON_DRIVE_BIN="$ROOT/tests/fake-proton-drive" FAKE_PROTON_DIR="$T/fakeproton" XDG_CACHE_HOME="$T/home/.cache"
mkdir -p "$FAKE_PROTON_DIR"
cat > "$FAKE_PROTON_DIR/photos.json" <<'JSON'
[{"uid":"u1","name":"IMG_1.jpg","captureTime":"2026-11-09T02:38:45.000Z","tags":[],"content":"one"},
 {"uid":"u2","name":"clip.mp4","captureTime":"2026-11-09T03:00:00.000Z","tags":[2],"content":"video"},
 {"uid":"u3","name":"IMG_1.jpg","captureTime":"2026-11-20T03:00:00.000Z","tags":[],"content":"same name"},
 {"uid":"u4","name":"raw.dng","captureTime":"2025-03-01T03:00:00.000Z","tags":[9],"content":"raw"},
 {"uid":"u5","name":"old.jpg","captureTime":"2024-06-15T03:00:00.000Z","tags":[0],"content":"fav"}]
JSON
P="$HOME/Pictures/Proton Photos"
pconf() { # mode, categories, dates
    cat > "$T/ph.conf" <<CONF
REMOTE_NAME="protondrive"
DEVICE_NAME="TestBox"
SYNC_DIRS=()
NOTIFY=false
PHOTOS_ENABLED=true
PHOTOS_MODE="$1"
PHOTOS_CATEGORIES=($2)
PHOTOS_DATES=($3)
CONF
}
nov="$(TZ=${TZ:-} date -d 2026-11-09T02:38:45Z +'%Y/%m %B')"
pconf copy "" ""
"$PHOTOS" -c "$T/ph.conf" --dry-run >/dev/null
check "photos dry run downloads nothing" '[[ ! -e "$P" ]]'
"$PHOTOS" -c "$T/ph.conf" >/dev/null
check "photos land in Year/Month folders" '[[ "$(cat "$P/$nov/IMG_1.jpg")" == one && -f "$P/$nov/clip.mp4" && -f "$P/2025/03 March/raw.dng" ]]'
check "same-named photos are both kept" '[[ "$(cat "$P/2026/11 November/IMG_1.jpg")" == one && "$(cat "$P/2026/11 November/IMG_1 (2).jpg")" == "same name" ]]'
check "photo file date = capture date" '[[ "$(date -r "$P/2024/06 June/old.jpg" +%Y-%m-%d)" == 2024-06-1[45] ]]'
check "photo counts saved for the app" 'grep -q "\"total\": 5" "$XDG_CACHE_HOME/sleepy-proton-drive/photos-index.json"'
rm "$P/2025/03 March/raw.dng"
"$PHOTOS" -c "$T/ph.conf" >/dev/null
check "copy mode: deleted here is not downloaded again or deleted in Proton" '[[ ! -e "$P/2025/03 March/raw.dng" ]] && grep -q u4 "$FAKE_PROTON_DIR/photos.json"'

rm -rf "$P" "$HOME/.local/state/sleepy-proton-drive/photos"
pconf copy '"videos" "raw"' ""
"$PHOTOS" -c "$T/ph.conf" >/dev/null
check "category filter (videos + raw only)" '[[ -f "$P/$nov/clip.mp4" && -f "$P/2025/03 March/raw.dng" && ! -e "$P/$nov/IMG_1.jpg" && ! -e "$P/2024" ]]'
rm -rf "$P" "$HOME/.local/state/sleepy-proton-drive/photos"
pconf copy "" '"2024" "2026-11"'
"$PHOTOS" -c "$T/ph.conf" >/dev/null
check "date filter (a year + a month)" '[[ -f "$P/2024/06 June/old.jpg" && -f "$P/$nov/clip.mp4" && ! -e "$P/2025" ]]'

rm -rf "$P" "$HOME/.local/state/sleepy-proton-drive/photos"
pconf sync "" ""
"$PHOTOS" -c "$T/ph.conf" >/dev/null
rm "$P/2024/06 June/old.jpg"
python3 -c 'import json,sys; f=sys.argv[1]; d=json.load(open(f)); json.dump([p for p in d if p["uid"]!="u2"], open(f,"w"))' "$FAKE_PROTON_DIR/photos.json"
"$PHOTOS" -c "$T/ph.conf" >/dev/null
check "mirror: deleted in Proton -> removed here" '[[ ! -e "$P/$nov/clip.mp4" ]]'
check "mirror: deleted here -> downloaded again" '[[ -f "$P/2024/06 June/old.jpg" ]]'

pconf bisync "" ""
rm "$P/2024/06 June/old.jpg"
"$PHOTOS" -c "$T/ph.conf" >/dev/null
check "two-way: deleted here -> moved to Proton's trash" '! grep -q u5 "$FAKE_PROTON_DIR/photos.json" && grep -q u5 "$FAKE_PROTON_DIR/trash.json"'
mv "$P" "$P.unplugged"
"$PHOTOS" -c "$T/ph.conf" >/dev/null && fail "missing folder not caught" || true
check "two-way: missing photo folder trashes nothing in Proton" 'grep -q u1 "$FAKE_PROTON_DIR/photos.json" && grep -q u3 "$FAKE_PROTON_DIR/photos.json"'
mv "$P.unplugged" "$P"
rm "$P/$nov/IMG_1.jpg" "$P/$nov/IMG_1 (2).jpg"
SLEEPY_PHOTOS_MASS_MIN=1 "$PHOTOS" -c "$T/ph.conf" >/dev/null || true
check "two-way: safety stop blocks mass deletes in Proton" 'grep -q u1 "$FAKE_PROTON_DIR/photos.json" && grep -q "SAFETY STOP" "$HOME/.local/share/proton-sync.log"'
FAKE_PROTON_LOGGED_OUT=1 "$PHOTOS" -c "$T/ph.conf" >/dev/null && fail "signed-out not reported" || ok "signed out of Proton is reported as an error"
"$ENGINE" -c "$T/ph.conf" >/dev/null
check "background sync runs Proton Photos too" 'grep -q "Proton Photos OK" "$HOME/.local/share/proton-sync.log"'

"$ENGINE" --mode nonsense >/dev/null 2>&1 && fail "bad mode accepted" || ok "rejects an unknown mode"
grep -q "TestBox" <<< "$("$ENGINE" --status)" && ok "--status works" || fail "--status"
bash -n "$HOME/.config/proton-sync.conf" && ok "config is valid bash"

echo "All $pass checks passed."
