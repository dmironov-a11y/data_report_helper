#!/usr/bin/env python3
"""Generate sprint review summary when sprint changes between snapshots.

Usage:
    python3 sprint_review.py \
        --today  standups/standup_YYYY-MM-DD.json \
        --prev   standups/standup_YYYY-MM-DD.json \
        [--slack]

Prints nothing if sprint name hasn't changed.
Outputs sprint review block otherwise.
"""
import argparse
import json
from pathlib import Path


def fmt_date(iso: str) -> str:
    from datetime import datetime
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %d")


def task_id_label(t: dict, slack: bool) -> str:
    label = f"[#{t['id']}]"
    url = t.get("url") if slack else None
    return f"<{url}|{label}>" if url else label


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", required=True)
    ap.add_argument("--prev", required=True)
    ap.add_argument("--slack", action="store_true", help="emit mrkdwn with <url|[#ID]> links")
    args = ap.parse_args()

    today = json.loads(Path(args.today).read_text())
    prev = json.loads(Path(args.prev).read_text())

    prev_sprint = prev["sprint"]["name"]
    today_sprint = today["sprint"]["name"]

    if prev_sprint == today_sprint:
        return

    prev_by_id = {t["id"]: t for t in prev.get("tasks", [])}
    today_ids = {t["id"] for t in today.get("tasks", [])}

    shipped = [t for t in prev.get("tasks", []) if t["group"] == "done"]
    carried_over = [t for t in prev.get("tasks", []) if t["group"] != "done" and t["id"] in today_ids]
    newly_added = [t for t in today.get("tasks", []) if t["id"] not in prev_by_id]

    if not shipped and not carried_over and not newly_added:
        return

    completion_rate = (
        100 * len(shipped) / (len(shipped) + len(carried_over))
        if shipped or carried_over else 0
    )

    def lbl(t: dict) -> str:
        return task_id_label(t, args.slack)

    lines = [
        f"📊 *{prev_sprint} Review* ({fmt_date(prev['sprint']['start'])}–{fmt_date(prev['sprint']['end'])})",
        f"`Closed: {len(shipped)} · Carried over: {len(carried_over)} · New in {today_sprint}: {len(newly_added)} · Completion rate: {completion_rate:.0f}%`",
        "",
    ]

    if shipped:
        lines.append("✅ *Shipped in " + prev_sprint + "*")
        for t in shipped:
            lines.append(f"• {lbl(t)} {t['name']}")
        lines.append("")

    if carried_over:
        lines.append(f"↩️ *Carried over to {today_sprint}*")
        for t in carried_over:
            lines.append(f"• {lbl(t)} {t['name']}")
        lines.append("")

    if newly_added:
        lines.append(f"🆕 *Added to {today_sprint}*")
        for t in newly_added:
            lines.append(f"• {lbl(t)} {t['name']}")
        lines.append("")

    print("\n".join(lines).rstrip())


if __name__ == "__main__":
    main()
