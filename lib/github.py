import re
import sys
from datetime import date, datetime, timezone

from lib.config import GITHUB_TOKEN, GITHUB_ORG, GITHUB_USERNAME, GITHUB_EXTRA_REPOS

TICKET_RE = re.compile(r'\b(DATA-\d+)\b', re.IGNORECASE)
MERGE_RE = re.compile(r'^Merge ', re.IGNORECASE)
COMMIT_RE = re.compile(r'^(`[0-9a-f]+`) (.+) \((https?://\S+)\)$')


def _date_window(start: date, end: date) -> tuple[datetime, datetime]:
    """Return UTC datetime range from start 00:00 to end 23:59:59."""
    since = datetime(start.year, start.month, start.day, 0, 0, 0, tzinfo=timezone.utc)
    until = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    return since, until


def title_from_commits(ticket: str, commit_lines: list[str]) -> str:
    """Extract a human-readable title from commit messages for tickets not found in Plane.

    Strips the ticket prefix (e.g. 'DATA-1567 - ') from the first commit message.
    """
    prefix_re = re.compile(rf'^\s*{re.escape(ticket)}\s*[-–—:]\s*', re.IGNORECASE)
    for line in commit_lines:
        m = COMMIT_RE.match(line)
        msg = m.group(2) if m else line
        cleaned = prefix_re.sub("", msg).strip()
        if cleaned:
            return cleaned
    return ""


def get_github_commits(
    date_from: date,
    date_to: date,
) -> tuple[dict[str, list[str]], list[str]]:
    """Return ({ticket_id: [commit_msg, ...]}, [orphan_commit, ...]) for non-merge commits in [date_from, date_to].

    Scans the default branch of every repo in the org.
    """
    if not GITHUB_TOKEN:
        return {}, []

    from github import Auth, Github
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

    if GITHUB_EXTRA_REPOS:
        extra = [r.strip() for r in GITHUB_EXTRA_REPOS.split(",") if r.strip()]
        print(f"  GitHub: scanning {len(extra)} extra repo(s)...", file=sys.stderr)
        for full_name in extra:
            try:
                repo = gh.get_repo(full_name)
                for commit in repo.get_commits(author=GITHUB_USERNAME, since=since, until=until):
                    process(commit)
            except Exception as exc:
                print(f"  [WARN] Could not scan {full_name}: {exc}", file=sys.stderr)

    return {t: sorted(msgs) for t, msgs in sorted(by_ticket.items())}, sorted(orphans)
