from collections import OrderedDict
from datetime import date, timedelta

from lib.github import COMMIT_RE, TICKET_RE

# ---------------------------------------------------------------------------
# Section headers
# ---------------------------------------------------------------------------

SEC_DONE = ":white_check_mark: Done:"
SEC_REVIEW = ":eyes: Moved to review:"
SEC_IN_PROGRESS = ":arrows_counterclockwise: In progress / planned (with ETA):"
SEC_ORPHAN_COMMITS = ":ghost: Commits without ticket:"
SEC_BLOCKED = ":no_entry: Blocked:"
SEC_NEED_TASKS = ":jigsaw: Need tasks (Optional):"
SEC_BACKLOG = ":card_index: Backlog (assigned, not started):"
SEC_UNKNOWN_TICKETS = ":spiral_note_pad: Commits linked to unknown ticket:"


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


# ---------------------------------------------------------------------------
# Commit rendering
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


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------

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

    report_date = workday.strftime("%B %d, %Y")
    lines = [f"*Data Async Daily Status: {report_date}*\n"]

    def issue_line(i: str, t: str, u: str, suffix: str = "") -> str:
        linked = f"<{u}|{i}>" if u else i
        label = f" — {t}" if t else ""
        return f"• {linked}{label}{suffix}"

    def bold(s: str) -> str:
        parts = s.split(" ", 1)
        return f"{parts[0]} *{parts[1]}*" if len(parts) == 2 else f"*{s}*"

    lines.append(bold(SEC_DONE))
    all_done = list(done_issues) + [(i, t, u) for i, t, u in review_issues]
    review_ids = {i for i, _, _ in review_issues}
    if all_done:
        for iss_id, iss_title, iss_url in all_done:
            suffix = " _(moved to review)_" if iss_id in review_ids else ""
            lines.append(issue_line(iss_id, iss_title, iss_url, suffix=suffix))
            if "done" in show_commits:
                lines.extend(_render_commits_slack(done_commits.get(iss_id, [])))
    else:
        lines.append("• —")

    lines.append(f"\n{bold(SEC_IN_PROGRESS)}")
    if worked_on:
        for ticket, info in sorted(worked_on.items()):
            lines.append(issue_line(ticket, info.get("title", ""), info.get("url", "")))
            if "in_progress" in show_commits:
                lines.extend(_render_commits_slack(info.get("commits", [])))
    else:
        lines.append("• —")

    if "in_progress" in show_commits:
        done_ids = {iss_id for iss_id, _, _ in done_issues} | {iss_id for iss_id, _, _ in review_issues}
        orphan_tickets = {t for t in commits_by_ticket if t not in worked_on and t not in done_ids}
        if orphan_tickets:
            lines.append(f"\n{bold(SEC_UNKNOWN_TICKETS)}")
            for ticket in sorted(orphan_tickets):
                for line in _render_commits_slack(commits_by_ticket[ticket]):
                    lines.append(f"  {ticket}:{line.lstrip()}")

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

    report_date = workday.strftime("%B %d, %Y")
    lines = [f"Data Async Daily Status: {report_date}\n"]

    def issue_line(ident: str, title: str, url: str, suffix: str = "") -> str:
        link = f" {url}" if add_links and url else ""
        return f"• {ident}{' — ' + title if title else ''}{suffix}{link}"

    lines.append(SEC_DONE)
    all_done = list(done_issues) + [(i, t, u) for i, t, u in review_issues]
    review_ids = {i for i, _, _ in review_issues}
    if all_done:
        for iss_id, iss_title, iss_url in all_done:
            suffix = " (moved to review)" if iss_id in review_ids else ""
            lines.append(issue_line(iss_id, iss_title, iss_url, suffix=suffix))
            if "done" in show_commits:
                lines.extend(_render_commits_plain(done_commits.get(iss_id, []), add_links))
    else:
        lines.append("• —")

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
