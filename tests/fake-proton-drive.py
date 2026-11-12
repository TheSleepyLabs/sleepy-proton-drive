#!/usr/bin/env python3
"""Stand-in for Proton's proton-drive CLI, used by the smoke test (no Proton account needed).
Photos live in $FAKE_PROTON_DIR/photos.json as [{uid, name, captureTime, tags, content}]."""
import hashlib, json, os, sys
from pathlib import Path

db_dir = Path(os.environ["FAKE_PROTON_DIR"])
db = db_dir / "photos.json"
photos = json.loads(db.read_text()) if db.exists() else []
args = [a for a in sys.argv[1:] if a not in ("--json", "-j")]

if os.environ.get("FAKE_PROTON_LOGGED_OUT"):
    print("You need to login first"); sys.exit(1)

def save():
    db.write_text(json.dumps(photos))

if args[:2] == ["photo", "timeline"]:
    if "--load-details" in args or "-d" in args:
        print(json.dumps([{"uid": p["uid"], "name": {"ok": True, "value": p["name"]}, "type": "photo",
                           "activeRevision": {"claimedDigests": {"sha1": hashlib.sha1(p["content"].encode()).hexdigest()}},
                           "photo": {"captureTime": p["captureTime"], "tags": p["tags"]}} for p in photos]))
    else:
        print(json.dumps([{"nodeUid": p["uid"], "captureTime": p["captureTime"], "tags": p["tags"]} for p in photos]))
elif args[:2] == ["photo", "download"]:
    rest = [a for a in args[2:] if a not in ("--conflict-strategy", "-c", "skip", "rename", "remove")]
    *paths, folder = rest
    by_uid = {p["uid"]: p for p in photos}
    done = 0
    for path in paths:
        p = by_uid.get(path.rsplit("/", 1)[-1])
        if p and not (Path(folder) / p["name"]).exists():
            (Path(folder) / p["name"]).write_text(p["content"]); done += 1
    print(json.dumps({"transferredItems": done, "failedItems": len(paths) - done}))
elif args[:2] == ["filesystem", "trash"]:
    uids = {a.rsplit("/", 1)[-1] for a in args[2:]}
    trash = json.loads((db_dir / "trash.json").read_text()) if (db_dir / "trash.json").exists() else []
    trash += [p for p in photos if p["uid"] in uids]
    photos = [p for p in photos if p["uid"] not in uids]
    (db_dir / "trash.json").write_text(json.dumps(trash)); save()
elif args[:2] == ["filesystem", "list"]:
    print("/my-files\n/photos\n/photos-trash")
else:
    print(f"fake-proton-drive: unsupported {args}", file=sys.stderr); sys.exit(2)
