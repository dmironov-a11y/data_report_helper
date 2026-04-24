#!/usr/bin/env python3
"""
Daily standup report generator.
Reads tasks from Plane.so and git commits, outputs a formatted standup message.

Usage:
    uv run standup.py [--standup-date YYYY-MM-DD] [--slack] [--add-links] [--commits GROUP...]
"""

import argparse
import subprocess
import sys
from datetime import date

import requests

from lib.config import (
    PLANE_WORKSPACE_SLUG, PLANE_PROJECT_ID,
    SLACK_BOT_TOKEN, SLACK_USER_ID,
    validate_config, parse_date_arg,
)
from lib.plane import (
    get_me, get_projects, get_states, get_my_issues, plane_get,
    get_issue_identifier, build_issue_url, build_browse_url,
)
from lib.github import get_github_commits, title_from_commits
from lib.report import (
    prev_workday, workday_range,
    build_report, build_slack_report,
)
from lib.slack import send_to_slack


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily standup report.")
    parser.add_argument(
        "--standup-date",
        metavar="YYYY-MM-DD",
        type=parse_date_arg,
        default=None,
        help=(
            "Report on this specific date. "
            "Without this flag, the report covers the previous working day."
        ),
    )
    parser.add_argument(
        "--add-links",
        action="store_true",
        default=False,
        help="Include URLs to Plane issues and GitHub commits in the output.",
    )
    parser.add_argument(
        "--slack",
        action="store_true",
        default=False,
        help=(
            "Send the report body as a Slack DM to yourself via a bot. "
            "Requires SLACK_BOT_TOKEN and SLACK_USER_ID env vars."
        ),
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
    args = parser.parse_args()
    show_commits: set[str] = {"done", "in_progress", "orphan"} if "all" in args.commits else set(args.commits)

    if not validate_config():
        sys.exit(1)

    today = date.today()
    if args.standup_date:
        date_from = date_to = args.standup_date
    else:
        workday = prev_workday(today)
        date_from, date_to = workday_range(workday, today)
    period_str = f"{date_from} – {date_to}" if date_from != date_to else str(date_from)
    print(f"Reporting period: {period_str}", file=sys.stderr)

    try:
        me = get_me()
        member_id: str = me["id"]
        print(f"Authenticated as: {me.get('display_name', me.get('email'))} ({member_id})", file=sys.stderr)
        if PLANE_PROJECT_ID:
            project_data = plane_get(f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{PLANE_PROJECT_ID}/")
            projects = [project_data]
        else:
            projects = get_projects()
    except requests.HTTPError as exc:
        print(f"[ERROR] Plane API error: {exc}", file=sys.stderr)
        sys.exit(1)

    done_issues: list[tuple[str, str, str]] = []
    review_issues: list[tuple[str, str, str]] = []
    blocked_issues: list[tuple[str, str, str]] = []
    backlog_issues: list[tuple[str, str, str]] = []
    plane_active: dict[str, dict] = {}
    all_issues_lookup: dict[str, dict] = {}

    for project in projects:
        project_id = project["id"]
        print(f"  Project: {project.get('name', project_id)}", file=sys.stderr)

        try:
            states = get_states(project_id)
            issues = get_my_issues(project_id, member_id)
        except requests.HTTPError as exc:
            print(f"  [WARN] Skipping project {project_id}: {exc}", file=sys.stderr)
            continue

        for issue in issues:
            title = issue.get("name", issue.get("title", "Untitled"))
            identifier = get_issue_identifier(project, issue)
            url = build_issue_url(project_id, issue["id"])

            all_issues_lookup[identifier] = {"title": title, "url": url}

            updated_at = issue.get("updated_at", "")
            updated_date = updated_at[:10] if updated_at else ""
            updated_in_range = date_from.isoformat() <= updated_date <= date_to.isoformat()

            state_id = issue.get("state")
            state = states.get(state_id, {})
            state_group = state.get("group", state.get("type", ""))
            state_name = state.get("name", "").lower()
            label_names = [lbl.get("name", "").lower() for lbl in issue.get("label_details", [])]
            is_blocked = "blocked" in label_names

            if state_group in ("backlog", "unstarted"):
                backlog_issues.append((identifier, title, url))
                continue

            if state_group == "completed":
                completed_at = issue.get("completed_at", "")
                completed_date = completed_at[:10] if completed_at else ""
                completed_in_range = (
                    completed_date
                    and date_from.isoformat() <= completed_date <= date_to.isoformat()
                )
                if completed_in_range:
                    done_issues.append((identifier, title, url))
                continue
            elif "review" in state_name:
                completed_at = issue.get("completed_at", "")
                completed_date = completed_at[:10] if completed_at else ""
                completed_in_range = (
                    completed_date
                    and date_from.isoformat() <= completed_date <= date_to.isoformat()
                )
                if not completed_in_range:
                    continue
                review_issues.append((identifier, title, url))
            elif is_blocked:
                blocked_issues.append((identifier, title, url))
            elif state_group == "started":
                plane_active[identifier] = {"title": title, "url": url}

    print("Fetching GitHub commits...", file=sys.stderr)
    commits_by_ticket, orphan_commits = get_github_commits(date_from, date_to)

    worked_on: dict[str, dict] = {}
    done_ids = {i for i, _, _ in done_issues} | {i for i, _, _ in review_issues}
    done_commits: dict[str, list[str]] = {}
    for ticket, commit_lines in commits_by_ticket.items():
        if ticket in done_ids:
            done_commits[ticket] = commit_lines
        else:
            info = plane_active.pop(ticket, None) or all_issues_lookup.get(ticket) or {
                "title": title_from_commits(ticket, commit_lines),
                "url": build_browse_url(ticket),
            }
            worked_on[ticket] = {**info, "commits": commit_lines}

    for ticket, info in plane_active.items():
        if ticket not in done_ids:
            worked_on[ticket] = {**info, "commits": []}

    report = build_report(
        done_issues, review_issues, worked_on, blocked_issues, date_from,
        add_links=args.add_links, show_commits=show_commits,
        done_commits=done_commits, orphan_commits=orphan_commits,
    )
    print()
    print(report)

    body = "\n".join(report.splitlines()[2:])
    subprocess.run("pbcopy", input=body.encode(), check=False)
    print("✓ Copied to clipboard", file=sys.stderr)

    if args.slack:
        token = SLACK_BOT_TOKEN
        user_id = SLACK_USER_ID
        if not token:
            print("[ERROR] SLACK_BOT_TOKEN is not set", file=sys.stderr)
            sys.exit(1)
        if not user_id:
            print("[ERROR] SLACK_USER_ID is not set", file=sys.stderr)
            sys.exit(1)
        slack_text = build_slack_report(
            done_issues, review_issues, worked_on, blocked_issues,
            backlog_issues, commits_by_ticket, orphan_commits, date_from,
            show_commits=show_commits, done_commits=done_commits,
        )
        try:
            send_to_slack(slack_text, token, user_id)
            print("✓ Sent to Slack", file=sys.stderr)
        except Exception as exc:
            print(f"[ERROR] Failed to send to Slack: {exc}", file=sys.stderr)
            sys.exit(1)

    if backlog_issues:
        print("\n--- Backlog (assigned, not started) ---")
        for ident, title, url in sorted(backlog_issues):
            link = f" {url}" if args.add_links else ""
            print(f"• {ident} — {title}{link}")


if __name__ == "__main__":
    main()
