#!/usr/bin/env python3
"""
Daily standup report generator.
Reads tasks from the latest snapshot.json and git commits, outputs a formatted standup message.

Usage:
    uv run standup.py [--standup-date YYYY-MM-DD] [--slack] [--add-links] [--commits GROUP...]
    uv run standup.py --snapshot-dir snapshots/2026-05-20_084636
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import date

from lib.config import GITHUB_TOKEN, NOTION_USER_ID, SLACK_BOT_TOKEN, SLACK_USER_ID, parse_date_arg
from lib.github import get_github_commits, title_from_commits
from lib.report import prev_workday, workday_range, build_report, build_slack_report
from lib.slack import send_to_slack

COMPLETE_STATES = {"to release", "done", "cancelled"}
IN_PROGRESS_STATES = {"in progress", "in review", "monitoring", "ready for integration", "in integration"}
BACKLOG_STATES = {"todo", "inbox", "backlog", "paused"}

_DATA_TICKET_RE = re.compile(r'(?:PNXT-)?DATA-(\d+)$', re.IGNORECASE)


def _ticket_to_ident(ticket: str) -> str:
    """'DATA-166' or 'PNXT-DATA-166' → '#166' for matching snapshot task IDs."""
    m = _DATA_TICKET_RE.match(ticket)
    return f"#{m.group(1)}" if m else ticket


def _find_prev_snapshot(current_path: str) -> str | None:
    """Return the latest snapshot.json from the previous working day of the current snapshot."""
    try:
        with open(current_path) as f:
            snap_date = date.fromisoformat(json.load(f).get("date", ""))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    prev_day = prev_workday(snap_date)
    candidates = sorted(glob.glob(f"snapshots/{prev_day.isoformat()}_*/snapshot.json"))
    return candidates[-1] if candidates else None


def _load_prev_done_idents(prev_path: str) -> set[str]:
    """Return set of task idents (#ID) that were already done in the previous snapshot."""
    try:
        with open(prev_path) as f:
            snap = json.load(f)
        return {f"#{t['id']}" for t in snap.get("tasks", [])
                if t.get("state", "").lower() in ("done", "to release", "cancelled")}
    except (OSError, json.JSONDecodeError, KeyError):
        return set()


def _load_assignee_ids(snapshot_dir: str, user_id: str) -> set[int]:
    """Return set of userDefined:ID values assigned to user_id."""
    tasks_path = os.path.join(snapshot_dir, "notion_tasks.json")
    if not os.path.exists(tasks_path):
        return set()
    with open(tasks_path) as f:
        data = json.load(f)
    raw = data.get("results", data) if isinstance(data, dict) else data
    my_ids: set[int] = set()
    for t in raw:
        assignees_raw = t.get("Assignees") or ""
        try:
            assignees = json.loads(assignees_raw) if assignees_raw else []
        except (json.JSONDecodeError, ValueError):
            assignees = []
        if user_id in assignees:
            uid = t.get("userDefined:ID")
            if uid is not None:
                my_ids.add(uid)
    return my_ids


def find_latest_snapshot(snapshot_dir: str | None) -> str:
    if snapshot_dir:
        p = os.path.join(snapshot_dir, "snapshot.json")
        if not os.path.exists(p):
            print(f"[ERROR] No snapshot.json in {snapshot_dir}", file=sys.stderr)
            sys.exit(1)
        return p
    candidates = sorted(glob.glob("snapshots/*/snapshot.json"))
    if not candidates:
        print("[ERROR] No snapshots found. Run /snapshot first.", file=sys.stderr)
        sys.exit(1)
    return candidates[-1]


def load_snapshot(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def classify_tasks(tasks: list[dict]) -> tuple[
    list[dict],
    list[dict],
    list[tuple[str, str, str]],
    list[tuple[str, str, str]],
    dict[str, dict],
]:
    done_issues: list[dict] = []
    review_issues: list[dict] = []
    blocked_issues: list[tuple[str, str, str]] = []
    backlog_issues: list[tuple[str, str, str]] = []
    active: dict[str, dict] = {}

    for task in tasks:
        if task.get("parent_id") or not task.get("name"):
            continue
        ident = f"#{task['id']}"
        name = task["name"]
        url = task.get("url", "")
        state = task.get("state") or ""
        state_lower = state.lower()
        is_blocked = task.get("blocked_by") or task.get("action_required_from")

        # Classify by state from Notion
        if state_lower in COMPLETE_STATES:
            done_issues.append({"id": ident, "title": name, "url": url, "state": state})
        elif is_blocked:
            blocked_issues.append((ident, name, url))
        elif state_lower in IN_PROGRESS_STATES:
            active[ident] = {"title": name, "url": url, "state": state, "prs": task.get("prs", [])}
        elif state_lower in BACKLOG_STATES:
            backlog_issues.append((ident, name, url))

    return done_issues, review_issues, blocked_issues, backlog_issues, active


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily standup report.")
    parser.add_argument(
        "--standup-date",
        metavar="YYYY-MM-DD",
        type=parse_date_arg,
        default=None,
        help="Report on this specific date (used for GitHub commits). Defaults to previous working day.",
    )
    parser.add_argument(
        "--snapshot-dir",
        metavar="PATH",
        default=None,
        help="Snapshot directory containing snapshot.json. Defaults to the latest snapshot.",
    )
    parser.add_argument(
        "--add-links",
        action="store_true",
        default=False,
        help="Include Notion and GitHub URLs in the output.",
    )
    parser.add_argument(
        "--slack",
        action="store_true",
        default=False,
        help="Send the report as a Slack DM. Requires SLACK_BOT_TOKEN and SLACK_USER_ID.",
    )
    parser.add_argument(
        "--commits",
        nargs="+",
        metavar="GROUP",
        default=[],
        choices=["all", "done", "in_progress", "orphan"],
        help=(
            "Show commits for specified groups. Choices: all, done, in_progress, orphan. "
            "Can combine: --commits in_progress orphan. Default: no commits shown."
        ),
    )
    parser.add_argument(
        "--user",
        default=None,
        metavar="NOTION_USER_ID",
        help="Notion user ID to filter tasks (overrides NOTION_USER_ID env var).",
    )
    args = parser.parse_args()

    effective_user = args.user or NOTION_USER_ID
    if not effective_user:
        print(
            "[ERROR] No user ID. Set NOTION_USER_ID in .env or pass --user <notion_user_id>",
            file=sys.stderr,
        )
        sys.exit(1)
    show_commits: set[str] = {"done", "in_progress", "orphan"} if "all" in args.commits else set(args.commits)

    snapshot_path = find_latest_snapshot(args.snapshot_dir)
    snap = load_snapshot(snapshot_path)
    print(f"Snapshot: {snapshot_path}  (date: {snap.get('date', '?')})", file=sys.stderr)

    tasks = snap.get("tasks", [])
    snapshot_dir = os.path.dirname(snapshot_path)
    my_task_ids = _load_assignee_ids(snapshot_dir, effective_user)
    tasks = [t for t in tasks if t.get("id") in my_task_ids or t.get("parent_id")]
    print(f"Filtered to {len(tasks)} assigned tasks", file=sys.stderr)
    done_issues, review_issues, blocked_issues, backlog_issues, active = classify_tasks(tasks)

    prev_snap = _find_prev_snapshot(snapshot_path)
    if prev_snap:
        prev_done = _load_prev_done_idents(prev_snap)
        done_issues = [t for t in done_issues if t["id"] not in prev_done]
        review_issues = [t for t in review_issues if t["id"] not in prev_done]
        print(f"Done diff vs {prev_snap}: {len(done_issues) + len(review_issues)} newly completed", file=sys.stderr)
    else:
        print("[WARN] No snapshot found for previous working day — showing all done tasks", file=sys.stderr)

    today = date.today()
    if args.standup_date:
        date_from = date_to = args.standup_date
    else:
        workday = prev_workday(today)
        date_from, date_to = workday_range(workday, today)
    period_str = f"{date_from} – {date_to}" if date_from != date_to else str(date_from)
    print(f"Commit period: {period_str}", file=sys.stderr)

    # GitHub commit scanning temporarily disabled (PRs from Notion cover this now)
    # if not GITHUB_TOKEN:
    #     print("[WARN] GITHUB_TOKEN not set — commits will be skipped", file=sys.stderr)
    # print("Fetching GitHub commits...", file=sys.stderr)
    # commits_by_ticket, orphan_commits = get_github_commits(date_from, date_to)
    commits_by_ticket, orphan_commits = {}, []

    worked_on: dict[str, dict] = {}
    done_ids = {t["id"] for t in done_issues} | {t["id"] for t in review_issues}
    done_commits: dict[str, list[str]] = {}

    for ticket, commit_lines in commits_by_ticket.items():
        snap_ident = _ticket_to_ident(ticket)  # DATA-166 → #166
        if snap_ident in done_ids or ticket in done_ids:
            done_commits[snap_ident] = commit_lines
        elif snap_ident in active:
            active[snap_ident]["commits"] = commit_lines  # attach to snapshot task
        else:
            worked_on[ticket] = {
                "title": title_from_commits(ticket, commit_lines),
                "url": "",
                "commits": commit_lines,
            }

    for ident, info in active.items():
        if ident not in done_ids and ident not in worked_on:
            if "commits" not in info:
                info["commits"] = []
            worked_on[ident] = info

    report = build_report(
        done_issues, review_issues, worked_on, blocked_issues, date_from,
        add_links=args.add_links, show_commits=show_commits,
        done_commits=done_commits, orphan_commits=orphan_commits,
    )
    print()
    print(report)

    if args.slack:
        if not SLACK_BOT_TOKEN:
            print("[ERROR] SLACK_BOT_TOKEN is not set", file=sys.stderr)
            sys.exit(1)
        if not SLACK_USER_ID:
            print("[ERROR] SLACK_USER_ID is not set", file=sys.stderr)
            sys.exit(1)
        slack_text = build_slack_report(
            done_issues, review_issues, worked_on, blocked_issues,
            backlog_issues, commits_by_ticket, orphan_commits, date_from,
            show_commits=show_commits, done_commits=done_commits,
        )
        try:
            send_to_slack(slack_text, SLACK_BOT_TOKEN, SLACK_USER_ID)
            print("✓ Sent to Slack", file=sys.stderr)
        except Exception as exc:
            print(f"[ERROR] Failed to send to Slack: {exc}", file=sys.stderr)
            sys.exit(1)

    if backlog_issues:
        print("\n--- Backlog (not started) ---")
        for ident, title, url in sorted(backlog_issues):
            link = f" {url}" if args.add_links else ""
            print(f"• {ident} — {title}{link}")


if __name__ == "__main__":
    main()
