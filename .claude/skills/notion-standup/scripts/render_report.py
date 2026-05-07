#!/usr/bin/env python3
"""Render a standup JSON snapshot as a formatted text report.

Usage:
    python3 render_report.py standups/standup_YYYY-MM-DD.json [--prev path]

If --prev is given, action items and blockers that already appeared in the
previous snapshot for the same task are hidden — the goal is "don't repeat
yesterday's items unless they were freshly re-stated today". The snapshot
itself stays complete so the diff can compare apples to apples; dedup is
purely a display concern.
"""
import argparse
import json
import sys
from datetime import datetime


def fmt_date(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%A, %d %b %Y")


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def task_id_label(t: dict, slack: bool) -> str:
    label = f"[#{t['id']}]"
    url = t.get("url") if slack else None
    return f"<{url}|{label}>" if url else label


def render(path: str, prev_path: str | None = None, slack: bool = False) -> str:
    with open(path) as f:
        data = json.load(f)

    prev_actions: dict[int, set[str]] = {}
    prev_blocker_desc: dict[int, str] = {}
    if prev_path:
        try:
            with open(prev_path) as f:
                prev = json.load(f)
            for pt in prev.get("tasks", []):
                tid = pt.get("id")
                prev_actions[tid] = {_norm(a.get("action")) for a in (pt.get("action_items") or [])}
                blk = pt.get("blocker") or {}
                if blk.get("is_blocker"):
                    prev_blocker_desc[tid] = _norm(blk.get("description"))
        except FileNotFoundError:
            pass

    sprint = data["sprint"]
    tasks = data["tasks"]

    by_id = {t["id"]: t for t in tasks}
    children: dict[int, list[int]] = {}
    for t in tasks:
        pid = t.get("parent_id")
        if pid is not None and pid in by_id:
            children.setdefault(pid, []).append(t["id"])

    def is_top_level(t):
        pid = t.get("parent_id")
        return pid is None or pid not in by_id

    def render_task_block(t: dict, group_set: set[int], indent: str = "", show_details: bool = True) -> list[str]:
        lines = [f"{indent}• {task_id_label(t, slack)} {t['name']}"]
        if show_details:
            summary = t.get("status_summary")
            if summary:
                lines.append(f"{indent}  · {summary}")
            seen_actions = prev_actions.get(t["id"], set())
            for ai in t.get("action_items") or []:
                if _norm(ai.get("action")) in seen_actions:
                    continue
                owner = ai.get("owner") or "Team"
                action = ai.get("action") or ""
                lines.append(f"{indent}  → {owner}: {action}")
            blk = t.get("blocker") or {}
            if blk.get("is_blocker"):
                desc = blk.get("description") or ""
                if _norm(desc) != prev_blocker_desc.get(t["id"]):
                    lines.append(f"{indent}  ⚠️ {desc}")
        for cid in children.get(t["id"], []):
            child = by_id.get(cid)
            if child and child["id"] in group_set:
                lines += render_task_block(child, group_set, indent + "  ↳ ", show_details=show_details)
        return lines

    lines: list[str] = [
        f"Daily Standup — {fmt_date(data['date'])}",
        f"Sprint: {sprint['name']}  ({sprint['start']} → {sprint['end']})",
        "",
    ]

    done = [t for t in tasks if t["group"] == "done" and is_top_level(t)]
    if done:
        lines.append("✅ Done")
        done_set = {t["id"] for t in tasks if t["group"] == "done"}
        for t in done:
            lines += render_task_block(t, done_set, show_details=False)
        lines.append("")

    released = [t for t in tasks if t.get("released", False) and is_top_level(t)]
    if released:
        lines.append("🚀 Released")
        released_set = {t["id"] for t in tasks if t.get("released", False)}
        for t in released:
            lines += render_task_block(t, released_set, show_details=True)
        lines.append("")

    updated = [t for t in tasks if t["group"] == "updated" and not t.get("released", False) and is_top_level(t)]
    if updated:
        lines.append("💬 Updated since last standup")
        updated_set = {t["id"] for t in tasks if t["group"] == "updated"}
        for t in updated:
            lines += render_task_block(t, updated_set, show_details=True)
        lines.append("")

    in_progress = [t for t in tasks if t["group"] == "in_progress" and not t.get("released", False) and is_top_level(t)]
    if in_progress:
        lines.append("🔄 In Progress (no recent update)")
        ip_set = {t["id"] for t in tasks if t["group"] == "in_progress"}
        for t in in_progress:
            lines += render_task_block(t, ip_set, show_details=False)
        lines.append("")

    return "\n".join(lines).rstrip()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot")
    ap.add_argument("--prev", default=None)
    ap.add_argument("--slack", action="store_true", help="emit mrkdwn with <url|[#ID]> links")
    args = ap.parse_args()
    print(render(args.snapshot, args.prev, slack=args.slack))
