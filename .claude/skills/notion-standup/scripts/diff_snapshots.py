#!/usr/bin/env python3
"""Compute follow-up text by diffing today's snapshot with the previous day.

Usage:
    python3 diff_snapshots.py \
        --today  standups/standup_YYYY-MM-DD.json \
        --prev   standups/standup_YYYY-MM-DD.json

Prints a Slack-friendly mrkdwn block to stdout. If the previous snapshot
doesn't exist, prints nothing and exits 0.

Today's snapshot is expected to contain only NEW action_items and blockers
(yesterday's were filtered out at synthesis time). The diff therefore
reconstructs "still pending" items from yesterday: actions that were open
yesterday, the task isn't done now, and today didn't re-emit them.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def fmt_date(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%A, %d %b %Y")


def norm(s: str | None) -> str:
    return (s or "").strip().lower()


def task_id_label(t: dict, slack: bool) -> str:
    label = f"[#{t['id']}]"
    url = t.get("url") if slack else None
    return f"<{url}|{label}>" if url else label


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--today", required=True)
    ap.add_argument("--prev", required=True)
    ap.add_argument("--slack", action="store_true", help="emit mrkdwn with <url|[#ID]> links")
    ap.add_argument("--sprint-review", action="store_true", help="suppress dropped/added sections (shown in sprint review instead)")
    args = ap.parse_args()

    prev_path = Path(args.prev)
    if not prev_path.exists():
        return

    today = json.loads(Path(args.today).read_text())
    prev = json.loads(prev_path.read_text())

    prev_by_id = {t["id"]: t for t in prev["tasks"]}
    today_by_id = {t["id"]: t for t in today["tasks"]}

    closed: list[dict] = []
    stale: list[tuple[dict, str | None]] = []
    pending: list[tuple[str, str, int]] = []
    new_blockers: list[tuple[dict, str | None]] = []
    resolved_blockers: list[dict] = []
    newly_released: list[dict] = []
    dropped: list[dict] = []
    added: list[dict] = []

    for tid, t in today_by_id.items():
        p = prev_by_id.get(tid)
        if p is None:
            added.append(t)
            continue

        # Closed since yesterday
        if p["group"] in {"in_progress", "updated"} and t["group"] == "done":
            closed.append(t)

        # Stale: had an update yesterday, no fresh comment today
        if (
            p["group"] == "updated"
            and t["group"] == "in_progress"
            and not t.get("recent_comments")
        ):
            stale.append((t, p.get("status_summary")))

        # Pending: yesterday's actions still relevant (task not done, not re-emitted today)
        if t["group"] != "done":
            today_actions = {norm(ai.get("action")) for ai in (t.get("action_items") or [])}
            for pa in p.get("action_items") or []:
                if norm(pa.get("action")) not in today_actions:
                    pending.append((pa.get("owner") or "Team", pa.get("action") or "", tid))

        # Blockers: new vs resolved
        pb = bool((p.get("blocker") or {}).get("is_blocker"))
        tb = bool((t.get("blocker") or {}).get("is_blocker"))
        if not pb and tb:
            new_blockers.append((t, (t.get("blocker") or {}).get("description")))
        elif pb and not tb:
            resolved_blockers.append(t)

        # Newly released: transition from not released to released
        if not p.get("released", False) and t.get("released", False):
            newly_released.append(t)

    # Dropped: tasks removed from sprint focus (were in prev, not done, and not in today)
    for tid, p in prev_by_id.items():
        if tid not in today_by_id and p["group"] != "done":
            dropped.append(p)

    if not any([closed, stale, pending, new_blockers, resolved_blockers, newly_released, dropped, added]):
        print(f"📋 Follow-up — {fmt_date(today['date'])}: no changes since yesterday.")
        return

    out: list[str] = [f"📋 Follow-up — {fmt_date(today['date'])}", ""]

    def lbl(t: dict) -> str:
        return task_id_label(t, args.slack)

    if closed:
        out.append("✅ Closed since yesterday")
        for t in closed:
            out.append(f"• {lbl(t)} {t['name']}")
        out.append("")
    if stale:
        out.append("⚠️ No update (follow-up needed)")
        for t, s in stale:
            out.append(f"• {lbl(t)} {t['name']} — last seen: {s or 'in progress'}")
        out.append("")
    if pending:
        out.append("🔁 Pending actions (carried from yesterday)")
        for owner, action, tid in pending:
            t = today_by_id.get(tid) or {"id": tid}
            out.append(f"• {owner}: {action} — {lbl(t)}")
        out.append("")
    if dropped and not args.sprint_review:
        out.append("🚫 Removed from sprint focus")
        for t in dropped:
            out.append(f"• {lbl(t)} {t['name']}")
        out.append("")
    if new_blockers:
        out.append("🚨 New blockers")
        for t, d in new_blockers:
            suffix = f": {d}" if d else ""
            out.append(f"• {lbl(t)} {t['name']}{suffix}")
        out.append("")
    if resolved_blockers:
        out.append("✅ Blockers resolved")
        for t in resolved_blockers:
            out.append(f"• {lbl(t)} {t['name']}")
        out.append("")
    if newly_released:
        out.append("🚀 Newly released since yesterday")
        for t in newly_released:
            out.append(f"• {lbl(t)} {t['name']}")
        out.append("")
    if added and not args.sprint_review:
        out.append("✅ Added to sprint focus")
        for t in added:
            out.append(f"• {lbl(t)} {t['name']}")
        out.append("")

    sys.stdout.write("\n".join(out).rstrip() + "\n")


if __name__ == "__main__":
    main()
