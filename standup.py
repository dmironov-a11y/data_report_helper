#!/usr/bin/env python3
"""
Daily standup report generator.
Reads tasks from Plane.so and git commits, outputs a formatted standup message.

Usage:
    python standup.py [--date YYYY-MM-DD]

    --date   Override the "previous working day" date manually.
             Useful if you missed a standup or want to look at a specific day.
             If not provided, the script auto-detects:
               - Monday → uses last Friday
               - Any other weekday → uses yesterday

Environment variables:
    PLANE_API_KEY        - Plane.so personal access token
    PLANE_WORKSPACE_SLUG - e.g. "my-workspace"
    GITHUB_TOKEN         - personal access token (scopes: repo, read:org)
    GITHUB_ORG           - organization login, e.g. "my-org"
    GITHUB_USERNAME      - your GitHub login, e.g. "dmironov-a11y"
"""

import argparse
import os
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from github import Github
import requests

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PLANE_API_KEY = os.environ.get("PLANE_API_KEY", "")
PLANE_BASE_URL = "https://api.plane.so/api/v1"
PLANE_WORKSPACE_SLUG = os.environ.get("PLANE_WORKSPACE_SLUG", "")
PLANE_PROJECT_ID = os.environ.get("PLANE_PROJECT_ID", "")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "")

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_USER_ID = os.environ.get("SLACK_USER_ID", "")



# ---------------------------------------------------------------------------
# Plane helpers
# ---------------------------------------------------------------------------

def plane_headers() -> dict:
    return {
        "X-API-Key": PLANE_API_KEY,
        "Content-Type": "application/json",
    }


def plane_get(path: str, params: Optional[dict] = None) -> dict:
    url = f"{PLANE_BASE_URL}{path}"
    resp = requests.get(url, headers=plane_headers(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_me() -> dict:
    """Return the current authenticated user."""
    return plane_get("/users/me/")


def get_projects() -> list[dict]:
    """Return all projects in the workspace."""
    data = plane_get(f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/")
    return data.get("results", data) if isinstance(data, dict) else data


def get_states(project_id: str) -> dict[str, dict]:
    """Return a dict of state_id -> state object."""
    data = plane_get(f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/states/")
    states = data.get("results", data) if isinstance(data, dict) else data
    return {s["id"]: s for s in states}


def get_my_issues(project_id: str, member_id: str) -> list[dict]:
    """Fetch all issues assigned to member_id in a project.

    The API ignores the assignees filter param, so we paginate all issues
    and filter client-side by matching member_id in the assignees list.
    """
    all_issues = []
    page = 1
    while True:
        data = plane_get(
            f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/work-items/",
            params={"page": page},
        )
        if isinstance(data, list):
            all_issues.extend(data)
            break
        results = data.get("results", [])
        all_issues.extend(results)
        if not data.get("next"):
            break
        page += 1

    # Filter client-side: assignees can be list of dicts {"id": ...} or plain UUID strings
    def is_assigned(issue: dict) -> bool:
        for a in issue.get("assignees", []):
            if isinstance(a, dict):
                if a.get("id") == member_id:
                    return True
            elif a == member_id:
                return True
        return False

    return [issue for issue in all_issues if is_assigned(issue)]


def get_issue_identifier(project: dict, issue: dict) -> str:
    """Build e.g. DATA-123 from project identifier + issue sequence."""
    identifier = project.get("identifier", project.get("name", "??"))
    seq = issue.get("sequence_id", issue.get("id", "?"))
    return f"{identifier}-{seq}"


def build_issue_url(project_id: str, issue_id: str) -> str:
    return f"https://app.plane.so/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/issues/{issue_id}"


def build_browse_url(ticket: str) -> str:
    """Fallback URL for tickets not found in Plane, using the browse shortlink."""
    return f"https://app.plane.so/{PLANE_WORKSPACE_SLUG}/browse/{ticket}/"


def title_from_commits(ticket: str, commit_lines: list[str]) -> str:
    """Extract a human-readable title from commit messages for tickets not found in Plane.

    Strips the ticket prefix (e.g. 'DATA-1567 - ') from the first commit message.
    """
    prefix_re = re.compile(rf'^\s*{re.escape(ticket)}\s*[-–—:]\s*', re.IGNORECASE)
    for line in commit_lines:
        # commit_lines are formatted as "`sha` message (url)" — extract the message part
        m = COMMIT_RE.match(line)
        msg = m.group(2) if m else line
        cleaned = prefix_re.sub("", msg).strip()
        if cleaned:
            return cleaned
    return ""


# ---------------------------------------------------------------------------
# Working day helpers
# ---------------------------------------------------------------------------

def prev_workday(ref: date) -> date:
    """Return the start of the previous working period before ref.

    Monday  → Friday (covers Fri + weekend)
    Tuesday → Monday
    ...
    Friday  → Thursday
    """
    delta = 3 if ref.weekday() == 0 else 1  # Monday = 0
    return ref - timedelta(days=delta)


def workday_range(workday: date, today: date) -> tuple[date, date]:
    """Return (start, end) date range for filtering Plane issues and GitHub commits.

    Monday: Friday → Sunday (covers the full weekend)
    Other days: workday → workday
    """
    if today.weekday() == 0:  # Monday
        end = today - timedelta(days=1)  # Sunday
        return workday, end
    return workday, workday


def parse_date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{value}'. Expected YYYY-MM-DD.")


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _date_window(start: date, end: date) -> tuple[datetime, datetime]:
    """Return UTC datetime range from start 00:00 to end 23:59:59."""
    since = datetime(start.year, start.month, start.day, 0, 0, 0, tzinfo=timezone.utc)
    until = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    return since, until


TICKET_RE = re.compile(r'\b(DATA-\d+)\b', re.IGNORECASE)
MERGE_RE = re.compile(r'^Merge ', re.IGNORECASE)
COMMIT_RE = re.compile(r'^(`[0-9a-f]+`) (.+) \((https?://\S+)\)$')

# Section headers shared between plain-text and Slack reports
SEC_DONE = ":white_check_mark: Done:"
SEC_REVIEW = ":eyes: Moved to review:"
SEC_IN_PROGRESS = ":arrows_counterclockwise: In progress / planned (with ETA):"
SEC_ORPHAN_COMMITS = ":ghost: Commits without ticket:"
SEC_BLOCKED = ":no_entry: Blocked:"
SEC_NEED_TASKS = ":jigsaw: Need tasks (Optional):"
SEC_BACKLOG = ":card_index: Backlog (assigned, not started):"
SEC_UNKNOWN_TICKETS = ":spiral_note_pad: Commits linked to unknown ticket:"


def get_github_commits(
    date_from: date,
    date_to: date,
) -> tuple[dict[str, list[str]], list[str]]:
    """Return ({ticket_id: [commit_msg, ...]}, [orphan_commit, ...]) for non-merge commits in [date_from, date_to].

    Scans the default branch of every repo in the org.
    """
    if not GITHUB_TOKEN:
        return {}, []

    from github import Auth
    gh = Github(auth=Auth.Token(GITHUB_TOKEN))
    since, until = _date_window(date_from, date_to)

    by_ticket: dict[str, set[str]] = {}
    orphans: set[str] = set()

    def process(commit) -> None:
        msg = commit.commit.message.splitlines()[0]
        if MERGE_RE.match(msg):
            return
        short = f"`{commit.sha[:7]}` {msg} ({commit.html_url})"
        tickets = TICKET_RE.findall(msg)
        if not tickets:
            orphans.add(short)
        else:
            for t in tickets:
                by_ticket.setdefault(t.upper(), set()).add(short)

    org = gh.get_organization(GITHUB_ORG)
    repos = list(org.get_repos(type="all"))
    print(f"  GitHub: scanning {len(repos)} repos in {GITHUB_ORG} (default branch, {date_from}–{date_to})...", file=sys.stderr)
    for repo in repos:
        try:
            for commit in repo.get_commits(author=GITHUB_USERNAME, since=since, until=until):
                process(commit)
        except Exception:
            pass

    return {t: sorted(msgs) for t, msgs in sorted(by_ticket.items())}, sorted(orphans)


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------

def _slack_commit_line(c: str) -> tuple[str, str, str] | None:
    """Parse a commit string into (sha, msg, url). Returns None if unparseable."""
    m = COMMIT_RE.match(c)
    if not m:
        return None
    sha = m.group(1).strip("`")
    raw_msg = TICKET_RE.sub("", m.group(2)).lstrip(" -–—").strip()
    commit_url = m.group(3)
    return sha, raw_msg, commit_url


def _render_commits_slack(commits: list[str]) -> list[str]:
    """Render commit strings as indented Slack mrkdwn lines with linked SHAs."""
    by_msg: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    for c in commits:
        parsed = _slack_commit_line(c)
        if not parsed:
            continue
        sha, raw_msg, commit_url = parsed
        by_msg.setdefault(raw_msg, []).append((sha, commit_url))
    lines = []
    for msg, shas in by_msg.items():
        sha_str = ", ".join(f"<{curl}|{sha}>" for sha, curl in shas)
        lines.append(f"  ↳ {msg} ({sha_str})")
    return lines


def build_slack_report(
    done_issues: list[tuple[str, str, str]],
    review_issues: list[tuple[str, str, str]],
    worked_on: dict[str, dict],
    blocked_issues: list[tuple[str, str, str]],
    backlog_issues: list[tuple[str, str, str]],
    commits_by_ticket: dict[str, list[str]],
    orphan_commits: list[str],
    workday: date,
    show_commits: set[str] | None = None,
    done_commits: dict[str, list[str]] | None = None,
) -> str:
    """Build a Slack mrkdwn-formatted report with short linked issue names and full content."""
    show_commits = show_commits or set()
    done_commits = done_commits or {}

    today = date.today().strftime("%Y-%m-%d")
    lines = [f"*Daily standup — {today}*\n"]

    def issue_line(i: str, t: str, u: str, suffix: str = "") -> str:
        linked = f"<{u}|{i}>" if u else i
        label = f" — {t}" if t else ""
        return f"• {linked}{label}{suffix}"

    def bold(s: str) -> str:
        # Wrap section header text (after emoji) in Slack bold markers
        parts = s.split(" ", 1)
        return f"{parts[0]} *{parts[1]}*" if len(parts) == 2 else f"*{s}*"

    lines.append(bold(SEC_DONE))
    if done_issues:
        for iss_id, iss_title, iss_url in done_issues:
            lines.append(issue_line(iss_id, iss_title, iss_url))
            if "done" in show_commits:
                lines.extend(_render_commits_slack(done_commits.get(iss_id, [])))
    else:
        lines.append("• —")

    if review_issues:
        lines.append(f"\n{bold(SEC_REVIEW)}")
        for iss_id, iss_title, iss_url in review_issues:
            lines.append(issue_line(iss_id, iss_title, iss_url))
            if "done" in show_commits:
                lines.extend(_render_commits_slack(done_commits.get(iss_id, [])))

    lines.append(f"\n{bold(SEC_IN_PROGRESS)}")
    if worked_on:
        for ticket, info in sorted(worked_on.items()):
            lines.append(issue_line(ticket, info.get("title", ""), info.get("url", "")))
            if "in_progress" in show_commits:
                lines.extend(_render_commits_slack(info.get("commits", [])))
    else:
        lines.append("• —")

    # Tickets with commits but no Plane task (unknown tickets)
    if "in_progress" in show_commits:
        done_ids = {iss_id for iss_id, _, _ in done_issues} | {iss_id for iss_id, _, _ in review_issues}
        orphan_tickets = {t for t in commits_by_ticket if t not in worked_on and t not in done_ids}
        if orphan_tickets:
            lines.append(f"\n{bold(SEC_UNKNOWN_TICKETS)}")
            for ticket in sorted(orphan_tickets):
                for line in _render_commits_slack(commits_by_ticket[ticket]):
                    lines.append(f"  {ticket}:{line.lstrip()}")

    # Commits with no ticket at all
    if "orphan" in show_commits and orphan_commits:
        lines.append(f"\n{bold(SEC_ORPHAN_COMMITS)}")
        lines.extend(_render_commits_slack(orphan_commits))

    lines.append(f"\n{bold(SEC_BLOCKED)}")
    if blocked_issues:
        for iss_id, iss_title, iss_url in blocked_issues:
            lines.append(issue_line(iss_id, iss_title, iss_url, suffix=" — blocked"))
    else:
        lines.append("• No")

    lines.append(f"\n{bold(SEC_NEED_TASKS)}")
    lines.append("• Need tasks: no")

    if backlog_issues:
        lines.append(f"\n---\n{bold(SEC_BACKLOG)}")
        for iss_id, iss_title, iss_url in sorted(backlog_issues):
            lines.append(issue_line(iss_id, iss_title, iss_url))

    return "\n".join(lines)


def send_to_slack(text: str, bot_token: str, user_id: str) -> None:
    """Send a DM to user_id via Slack Web API (user ID used directly as channel)."""
    headers = {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers=headers,
        json={"channel": user_id, "text": text},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"chat.postMessage failed: {data.get('error')}")


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def _render_commits_plain(commits: list[str], add_links: bool) -> list[str]:
    """Render a list of raw commit strings as indented plain-text lines."""
    by_msg: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()
    for c in commits:
        m = COMMIT_RE.match(c)
        if not m:
            continue
        sha = m.group(1).strip("`")
        raw_msg = TICKET_RE.sub("", m.group(2)).lstrip(" -–—").strip()
        commit_url = m.group(3)
        by_msg.setdefault(raw_msg, []).append((sha, commit_url))
    lines = []
    for msg, shas in by_msg.items():
        if add_links:
            sha_str = ", ".join(f"{sha} {curl}" for sha, curl in shas)
        else:
            sha_str = ", ".join(sha for sha, _ in shas)
        lines.append(f"  ↳ {msg} ({sha_str})")
    return lines


def build_report(
    done_issues: list[tuple[str, str, str]],
    review_issues: list[tuple[str, str, str]],
    worked_on: dict[str, dict],
    blocked_issues: list[tuple[str, str, str]],
    workday: date,
    add_links: bool = False,
    show_commits: set[str] | None = None,
    done_commits: dict[str, list[str]] | None = None,
    orphan_commits: list[str] | None = None,
) -> str:
    show_commits = show_commits or set()
    done_commits = done_commits or {}
    orphan_commits = orphan_commits or []

    today = date.today().strftime("%Y-%m-%d")
    lines = [f"Daily standup — {today}\n"]

    def issue_line(ident: str, title: str, url: str, suffix: str = "") -> str:
        link = f" {url}" if add_links and url else ""
        return f"• {ident}{' — ' + title if title else ''}{suffix}{link}"

    lines.append(SEC_DONE)
    if done_issues:
        for iss_id, iss_title, iss_url in done_issues:
            lines.append(issue_line(iss_id, iss_title, iss_url))
            if "done" in show_commits:
                lines.extend(_render_commits_plain(done_commits.get(iss_id, []), add_links))
    else:
        lines.append("• —")

    if review_issues:
        lines.append(f"\n{SEC_REVIEW}")
        for iss_id, iss_title, iss_url in review_issues:
            lines.append(issue_line(iss_id, iss_title, iss_url))
            if "done" in show_commits:
                lines.extend(_render_commits_plain(done_commits.get(iss_id, []), add_links))

    lines.append(f"\n{SEC_IN_PROGRESS}")
    if worked_on:
        for ticket, info in sorted(worked_on.items()):
            lines.append(issue_line(ticket, info.get("title", ""), info.get("url", "")))
            if "in_progress" in show_commits:
                lines.extend(_render_commits_plain(info.get("commits", []), add_links))
    else:
        lines.append("• —")

    if "orphan" in show_commits and orphan_commits:
        lines.append(f"\n{SEC_ORPHAN_COMMITS}")
        lines.extend(_render_commits_plain(orphan_commits, add_links))

    lines.append(f"\n{SEC_BLOCKED}")
    if blocked_issues:
        for iss_id, iss_title, iss_url in blocked_issues:
            lines.append(issue_line(iss_id, iss_title, iss_url, suffix=" blocked"))
    else:
        lines.append("• No")

    lines.append(f"\n{SEC_NEED_TASKS}")
    lines.append("• Need tasks: no")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def validate_config() -> bool:
    errors = []
    if not PLANE_API_KEY:
        errors.append("PLANE_API_KEY is not set")
    if not PLANE_WORKSPACE_SLUG:
        errors.append("PLANE_WORKSPACE_SLUG is not set")
    if errors:
        for e in errors:
            print(f"[ERROR] {e}", file=sys.stderr)
        print("\nSet the required environment variables and re-run.", file=sys.stderr)
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate daily standup report.")
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        type=parse_date_arg,
        default=None,
        help=(
            "The working day to report on (commits + done tasks). "
            "Defaults to the previous working day: yesterday, or last Friday on Mondays."
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
    # Expand 'all' into all groups
    show_commits: set[str] = {"done", "in_progress", "orphan"} if "all" in args.commits else set(args.commits)

    if not validate_config():
        sys.exit(1)

    today = date.today()
    workday: date = args.date if args.date else prev_workday(today)
    date_from, date_to = workday_range(workday, today if not args.date else workday)
    period_str = f"{date_from} – {date_to}" if date_from != date_to else str(date_from)
    print(f"Reporting period: {period_str}", file=sys.stderr)

    try:
        me = get_me()
        member_id: str = me["id"]
        print(f"Authenticated as: {me.get('display_name', me.get('email'))} ({member_id})", file=sys.stderr)
        if PLANE_PROJECT_ID:
            # Fetch only the specified project
            project_data = plane_get(f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{PLANE_PROJECT_ID}/")
            projects = [project_data]
        else:
            projects = get_projects()
    except requests.HTTPError as exc:
        print(f"[ERROR] Plane API error: {exc}", file=sys.stderr)
        sys.exit(1)

    done_issues: list[tuple[str, str, str]] = []    # (identifier, title, url)
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
                continue  # backlog shown regardless of update date

            if not updated_in_range:
                continue

            if state_group == "completed":
                done_issues.append((identifier, title, url))
            elif "review" in state_name:
                review_issues.append((identifier, title, url))
            elif is_blocked:
                blocked_issues.append((identifier, title, url))
            elif state_group == "started":
                plane_active[identifier] = {"title": title, "url": url}

    # GitHub commits grouped by ticket
    print("Fetching GitHub commits...", file=sys.stderr)
    commits_by_ticket, orphan_commits = get_github_commits(date_from, date_to)

    worked_on: dict[str, dict] = {}
    done_ids = {i for i, _, _ in done_issues} | {i for i, _, _ in review_issues}
    # Commits for done tickets stored separately (shown only if 'done' in show_commits)
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

    # Print plain text to terminal
    report = build_report(
        done_issues, review_issues, worked_on, blocked_issues, workday,
        add_links=args.add_links, show_commits=show_commits,
        done_commits=done_commits, orphan_commits=orphan_commits,
    )
    print()
    print(report)

    # Copy to clipboard without the header line
    body = "\n".join(report.splitlines()[2:])  # skip "Daily standup —..." and blank line
    subprocess.run("pbcopy", input=body.encode(), check=False)
    print("✓ Copied to clipboard", file=sys.stderr)

    # Send to Slack DM if requested
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
            backlog_issues, commits_by_ticket, orphan_commits, workday,
            show_commits=show_commits, done_commits=done_commits,
        )
        try:
            send_to_slack(slack_text, token, user_id)
            print("✓ Sent to Slack", file=sys.stderr)
        except Exception as exc:
            print(f"[ERROR] Failed to send to Slack: {exc}", file=sys.stderr)
            sys.exit(1)

    # Print backlog to terminal only (not copied)
    if backlog_issues:
        print("\n--- Backlog (assigned, not started) ---")
        for ident, title, url in sorted(backlog_issues):
            link = f" {url}" if args.add_links else ""
            print(f"• {ident} — {title}{link}")


if __name__ == "__main__":
    main()
