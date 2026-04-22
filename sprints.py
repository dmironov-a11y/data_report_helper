#!/usr/bin/env python3
"""
Sprint cycles viewer and task rename tool.

Usage:
    uv run sprints.py                            # show current + next cycle in terminal
    uv run sprints.py --slack                    # send cycles report to Slack DM
    uv run sprints.py --rename-tasks --dry-run   # AI rename proposals (no changes)
    uv run sprints.py --rename-tasks             # propose + confirm + apply renames
    uv run sprints.py --rename-tasks --cycle current
    uv run sprints.py --rename-tasks --cycle both
"""

import argparse
import sys
from datetime import datetime, timezone

import requests

from lib.config import (
    PLANE_WORKSPACE_SLUG, PLANE_PROJECT_ID,
    SLACK_BOT_TOKEN, SLACK_USER_ID,
    validate_config,
)
from lib.plane import (
    get_me, get_projects, get_states, get_cycles, get_cycle_issues,
    get_workspace_members, plane_get,
)
from lib.cycles import build_cycle_messages
from lib.rename import run_rename_mode
from lib.slack import send_to_slack


def _cycle_status(c: dict, now_iso: str) -> str:
    s = c.get("start_date") or ""
    e = c.get("end_date") or ""
    st = c.get("status")
    if st:
        return st
    if s and e:
        if s <= now_iso <= e:
            return "CURRENT"
        elif s > now_iso:
            return "UPCOMING"
        else:
            return "COMPLETED"
    return "DRAFT"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint cycles viewer and task rename tool.")
    parser.add_argument(
        "--slack",
        action="store_true",
        default=False,
        help="Send cycles report to your Slack DM. Requires SLACK_BOT_TOKEN and SLACK_USER_ID.",
    )
    parser.add_argument(
        "--rename-tasks",
        action="store_true",
        default=False,
        help=(
            "AI-powered rename of issues in the selected cycle. "
            "Shows proposals and asks for confirmation before applying."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="With --rename-tasks: show proposals but do not apply changes.",
    )
    parser.add_argument(
        "--cycle",
        choices=["next", "current", "both"],
        default="next",
        help="With --rename-tasks: which cycle(s) to rename. Default: next.",
    )
    args = parser.parse_args()

    if not validate_config():
        sys.exit(1)

    try:
        me = get_me()
        print(f"Authenticated as: {me.get('display_name', me.get('email'))}", file=sys.stderr)
        if PLANE_PROJECT_ID:
            project_data = plane_get(f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{PLANE_PROJECT_ID}/")
            projects = [project_data]
        else:
            projects = get_projects()
    except requests.HTTPError as exc:
        print(f"[ERROR] Plane API error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.rename_tasks:
        try:
            run_rename_mode(projects, dry_run=args.dry_run, cycle_filter=args.cycle)
        except requests.HTTPError as exc:
            print(f"[ERROR] Plane API error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    # Default: cycles view
    print("\nFetching Plane cycles...", file=sys.stderr)
    try:
        for project in projects:
            project_id = project["id"]
            states = get_states(project_id)
            cycles = get_cycles(project_id)
            now_iso = datetime.now(timezone.utc).isoformat()

            current_cycle = next((c for c in cycles if _cycle_status(c, now_iso) == "CURRENT"), None)
            upcoming = [c for c in cycles if _cycle_status(c, now_iso) == "UPCOMING"]
            next_cycle = min(upcoming, key=lambda c: c.get("start_date") or "", default=None)

            current_issues = get_cycle_issues(project_id, current_cycle["id"]) if current_cycle else []
            next_issues = get_cycle_issues(project_id, next_cycle["id"]) if next_cycle else []

            workspace_members = get_workspace_members()

            current_msg, next_msg = build_cycle_messages(
                current_cycle, current_issues,
                next_cycle, next_issues,
                project, states,
                members=workspace_members,
            )

            for msg in (current_msg, next_msg):
                print("\n" + msg.replace("*", "").replace("_", "").replace("`", ""))

            if args.slack:
                token = SLACK_BOT_TOKEN
                user_id = SLACK_USER_ID
                if not token:
                    print("[ERROR] SLACK_BOT_TOKEN is not set", file=sys.stderr)
                    sys.exit(1)
                if not user_id:
                    print("[ERROR] SLACK_USER_ID is not set", file=sys.stderr)
                    sys.exit(1)
                try:
                    send_to_slack(current_msg, token, user_id)
                    send_to_slack(next_msg, token, user_id)
                    print("✓ Cycles report sent to Slack (2 messages)", file=sys.stderr)
                except Exception as exc:
                    print(f"[ERROR] Failed to send cycles report to Slack: {exc}", file=sys.stderr)
                    sys.exit(1)
    except requests.HTTPError as exc:
        print(f"[ERROR] Plane API error fetching cycles: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
