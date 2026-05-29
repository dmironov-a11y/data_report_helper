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
import subprocess
import sys
from datetime import date
from pathlib import Path

from lib.config import GITHUB_TOKEN, NOTION_USER_ID, SLACK_BOT_TOKEN, SLACK_USER_ID, parse_date_arg

# Import get_state_group from synthesize for consistent group classification
sys.path.insert(0, str(Path(__file__).parent / "notion"))
from synthesize import get_state_group
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


def _detect_sprint_transition(current_snap_dir: str, prev_snap_path: str | None) -> bool:
    """Return True if the current and previous snapshots are from different sprints."""
    if not prev_snap_path:
        return False
    prev_dir = os.path.dirname(prev_snap_path)
    try:
        with open(os.path.join(current_snap_dir, "notion_sprints.json")) as f:
            cur_sprint = json.load(f).get("current", {}).get("sprint_id")
        with open(os.path.join(prev_dir, "notion_sprints.json")) as f:
            prev_sprint = json.load(f).get("current", {}).get("sprint_id")
        return cur_sprint != prev_sprint
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def _find_missing_active_tasks(
    current_snap_dir: str, prev_snap_path: str, user_id: str, prev_done: set[str]
) -> list[dict]:
    """On sprint transition: find last-sprint tasks that were active (not done) in prev snapshot
    but are absent from the current sprint. These may have been completed after the last snapshot."""
    prev_dir = os.path.dirname(prev_snap_path)
    prev_tasks_path = os.path.join(prev_dir, "notion_tasks.json")
    cur_tasks_path = os.path.join(current_snap_dir, "notion_tasks.json")
    try:
        with open(prev_tasks_path) as f:
            prev_raw = json.load(f)
        prev_raw = prev_raw if isinstance(prev_raw, list) else prev_raw.get("results", [])
        with open(cur_tasks_path) as f:
            cur_raw = json.load(f)
        cur_raw = cur_raw if isinstance(cur_raw, list) else cur_raw.get("results", [])
    except (OSError, json.JSONDecodeError):
        return []

    cur_ids = {t.get("userDefined:ID") for t in cur_raw}
    missing = []
    for t in prev_raw:
        try:
            assignees = json.loads(t.get("Assignees") or "[]")
        except (json.JSONDecodeError, ValueError):
            assignees = []
        if user_id not in assignees:
            continue
        uid = t.get("userDefined:ID")
        ident = f"#{uid}" if uid else None
        if not ident or ident in prev_done or uid in cur_ids:
            continue
        # Task was assigned to user, not already done, and is gone from current sprint
        state = t.get("State", "")
        if get_state_group(state) not in ("done", "review", "cancelled"):
            missing.append({"id": ident, "title": t.get("Task name", "?"), "state": state})
    return missing


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
    parser.add_argument(
        "--show-prs",
        action="store_true",
        default=False,
        help="Include GitHub PRs in the standup report.",
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

    # Load prev snapshot early so we can detect sprint transitions before fetching tasks
    prev_snap = _find_prev_snapshot(snapshot_path)

    # On sprint transition, re-fetch notion_tasks.json to include last-sprint done tasks
    # (covers tasks completed in the old sprint after the previous snapshot was taken)
    if _detect_sprint_transition(snapshot_dir, prev_snap):
        print("Sprint transition detected: fetching last-sprint done tasks from Notion...", file=sys.stderr)
        res = subprocess.run(
            ["uv", "run", "notion/tasks.py", "--dir", snapshot_dir, "--include-last-sprint-done"],
            text=True,
        )
        if res.returncode != 0:
            print("[WARN] Failed to fetch last-sprint done tasks — standup may be incomplete", file=sys.stderr)

    my_task_ids = _load_assignee_ids(snapshot_dir, effective_user)
    tasks = [t for t in tasks if t.get("id") in my_task_ids or t.get("parent_id")]
    print(f"Filtered to {len(tasks)} assigned tasks", file=sys.stderr)
    done_issues, review_issues, blocked_issues, backlog_issues, active = classify_tasks(tasks)

    if not args.show_prs:
        for task_info in active.values():
            task_info["prs"] = []

    prev_done = set()
    if prev_snap:
        prev_done = _load_prev_done_idents(prev_snap)
        done_issues = [t for t in done_issues if t["id"] not in prev_done]
        review_issues = [t for t in review_issues if t["id"] not in prev_done]
        print(f"Done diff vs {prev_snap}: {len(done_issues) + len(review_issues)} newly completed", file=sys.stderr)
        # Warn on sprint transition: tasks that were active in prev sprint but absent from current
        if _detect_sprint_transition(snapshot_dir, prev_snap):
            missing = _find_missing_active_tasks(snapshot_dir, prev_snap, effective_user, prev_done)
            if missing:
                print(
                    f"[WARN] Sprint transition detected. {len(missing)} task(s) from previous sprint "
                    f"were active but not in current sprint — may have been completed after last snapshot:\n"
                    + "\n".join(f"       {t['id']} {t['title']} (was: {t['state']})" for t in missing),
                    file=sys.stderr,
                )
    else:
        print("[WARN] No snapshot found for previous working day — showing all done tasks", file=sys.stderr)

    # Load Done tasks from raw notion_tasks.json to include recently completed tasks
    # only if they transitioned to Done (not already done in previous snapshot)
    tasks_path = os.path.join(snapshot_dir, "notion_tasks.json")
    if os.path.exists(tasks_path) and prev_snap:
        try:
            with open(tasks_path) as f:
                raw_tasks = json.load(f)
            raw_tasks = raw_tasks if isinstance(raw_tasks, list) else raw_tasks.get("results", [])

            # Add Done tasks that weren't done in previous snapshot (newly completed)
            done_ids = {t["id"] for t in done_issues}
            for raw_task in raw_tasks:
                group = get_state_group(raw_task.get("State", ""))
                if group in ("done", "review"):
                    task_id = raw_task.get("userDefined:ID")
                    task_ident = f"#{task_id}" if task_id else None
                    # Only add if: it's for current user, not already in done_issues, and wasn't done before
                    if task_ident and task_ident not in done_ids and task_ident not in prev_done:
                        if task_id in my_task_ids:
                            done_issues.append({
                                "id": task_ident,
                                "title": raw_task.get("Task name", "Unknown"),
                                "url": raw_task.get("url", ""),
                                "state": raw_task.get("State", "Done")
                            })
        except (OSError, json.JSONDecodeError):
            pass

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
