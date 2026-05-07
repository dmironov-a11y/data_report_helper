#!/usr/bin/env python3
"""Apply per-task synthesis (status / action_items / blocker) to a snapshot.

Usage:
    python3 apply_synthesis.py --snapshot standups/standup_YYYY-MM-DD.json
    < synthesis.json

stdin schema:
    [
      {
        "id": <int>,
        "status_summary": "<one sentence>",
        "action_items": [{"owner": "...", "action": "..."}, ...],
        "blocker": {"is_blocker": <bool>, "description": "<sentence|null>"},
        "released": <bool>
      },
      ...
    ]

Only the fields present in each entry are overwritten — anything else on
the task object is left alone.
"""
import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    args = ap.parse_args()

    syn = json.load(sys.stdin)
    snap_path = Path(args.snapshot)
    snap = json.loads(snap_path.read_text())
    by_id = {t["id"]: t for t in snap["tasks"]}

    for entry in syn:
        tid = entry.get("id")
        if tid not in by_id:
            continue
        t = by_id[tid]
        if "status_summary" in entry:
            t["status_summary"] = entry["status_summary"]
        if "action_items" in entry:
            t["action_items"] = entry["action_items"] or []
        if "blocker" in entry:
            blk = entry["blocker"] or {}
            t["blocker"] = {
                "is_blocker": bool(blk.get("is_blocker", False)),
                "description": blk.get("description"),
            }
        if "released" in entry:
            t["released"] = bool(entry["released"])

    snap_path.write_text(json.dumps(snap, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
