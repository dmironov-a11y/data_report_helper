#!/usr/bin/env python3
"""Filter and apply Notion comments to a standup snapshot.

Usage:
    python3 apply_comments.py \
        --snapshot standups/standup_YYYY-MM-DD.json \
        --window-start ISO8601 \
        --window-end ISO8601
    < comments.json

stdin: {"<task_id>": [{"text": "...", "datetime": "<iso>"}, ...], ...}
        — pass ALL comments returned by notion-get-comments; the script
          discards anything outside the window.

Mutates the snapshot in place: populates recent_comments (newest first),
labels each as "today" / "yesterday", and reclassifies tasks with at least
one in-window comment to group="updated".
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--window-start", required=True)
    ap.add_argument("--window-end", required=True)
    args = ap.parse_args()

    raw = json.load(sys.stdin)
    ws = parse_iso(args.window_start)
    we = parse_iso(args.window_end)

    snap_path = Path(args.snapshot)
    snap = json.loads(snap_path.read_text())
    by_id = {t["id"]: t for t in snap["tasks"]}
    today = snap["date"]

    for tid_str, comments in raw.items():
        try:
            tid = int(tid_str)
        except (TypeError, ValueError):
            continue
        if tid not in by_id:
            continue

        recent = []
        for c in comments or []:
            try:
                ts = parse_iso(c["datetime"])
            except Exception:
                continue
            if ws <= ts <= we:
                day_label = "today" if ts.date().isoformat() == today else "yesterday"
                recent.append(
                    {"text": c.get("text", ""), "datetime": c["datetime"], "day": day_label}
                )

        if recent:
            recent.sort(key=lambda x: x["datetime"], reverse=True)
            by_id[tid]["recent_comments"] = recent
            if by_id[tid]["group"] in {"in_progress", "skipped"}:
                by_id[tid]["group"] = "updated"

    snap_path.write_text(json.dumps(snap, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
