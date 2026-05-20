# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the scripts

### Notion snapshot pipeline — `/snapshot [standup|review]`

The primary workflow. Run via the `/snapshot` skill inside Claude Code. Steps in order:

```bash
uv run notion/sprints.py --dir snapshots/<dir>                 # fetch sprint metadata (last/current/next)
uv run notion/tasks.py --dir snapshots/<dir> --sprint current  # fetch current sprint tasks
uv run notion/prs.py --dir snapshots/<dir>                     # fetch PR pages linked to tasks → notion_prs.json
uv run notion/comments.py --dir snapshots/<dir>                # enrich tasks with recent comments
uv run notion/synthesize.py --dir snapshots/<dir>              # AI summaries per ALL parent tasks → snapshot.json
```

Invocation variants:
- `/snapshot` — build snapshot only (no render)
- `/snapshot standup` — build + personal daily standup → Slack (requires `NOTION_USER_ID`)
- `/snapshot review` — build + sprint review → Slack (team-wide)

### notion/sprint_review.py — sprint review (detailed, with AI summaries + thread)

```bash
uv run notion/sprint_review.py --dir snapshots/2026-05-20_084636          # write standup_main.txt + standup_thread.txt
uv run notion/sprint_review.py --dir snapshots/2026-05-20_084636 --slack  # also post to Slack DM
```

### standup.py — daily async standup (Data Async Daily Status format)

Reads tasks from the latest `snapshot.json`, filters by assignee, merges with GitHub commits.
**Requires** a user ID — either `NOTION_USER_ID` in `.env` or `--user` flag.

```bash
uv run standup.py                                    # latest snapshot + commits for previous working day
uv run standup.py --user "user://2e8d..."            # override user (run standup for another person)
uv run standup.py --standup-date 2026-03-25          # override commit date range
uv run standup.py --snapshot-dir snapshots/2026-05-20_084636  # use specific snapshot
uv run standup.py --add-links                        # include Notion and GitHub URLs in output
uv run standup.py --slack                            # send report to Slack DM
uv run standup.py --commits in_progress              # show commits in in-progress section
uv run standup.py --commits all                      # show commits for all groups + orphans
uv run standup.py --standup-date 2026-03-25 --slack --commits all --add-links
```

## Environment setup

Copy `.env.example` to `.env` and fill in credentials. Required variables:
- `GITHUB_TOKEN` (needs `repo` + `read:org` scopes; authorize SSO for the org at github.com/settings/tokens)
- `GITHUB_ORG`, `GITHUB_USERNAME`
- `SLACK_BOT_TOKEN` (optional, needed for `--slack`; needs `chat:write` scope)
- `SLACK_USER_ID` (optional, your Slack member ID e.g. `U0123456789`)

## Architecture

Entry points and shared `lib/` package:

```
notion/
  sprints.py       # fetch sprint metadata from Notion DB
  tasks.py         # fetch current sprint tasks from Notion DB
  comments.py      # enrich tasks with comments (parallel, 5 workers)
  synthesize.py    # build AI snapshot: parent tasks + subtask metadata + per-task Claude summary
  render_standup.py # render standup_main.txt + standup_thread.txt from snapshot.json
standup.py         # old-format standup (Done/In Progress/Blocked) from snapshot + GitHub commits

lib/
  config.py   # env constants, parse_date_arg
  github.py   # GitHub helpers (get_github_commits, TICKET_RE, ...)
  slack.py    # send_to_slack
  report.py   # build_report, build_slack_report, prev_workday, workday_range
```

### Notion snapshot pipeline

Data flow: `notion/sprints.py` → `notion/tasks.py` → `notion/comments.py` → `notion/synthesize.py` → `snapshot.json`

1. **notion/sprints.py** — queries the Sprints DB (`collection://35750979-…`) via `notion-query-data-sources` MCP, saves `notion_sprints.json` with last/current/next sprint URLs and date ranges.

2. **notion/tasks.py** — queries the Tasks DB (`collection://35650979-…`), filters by current sprint URL, saves `notion_tasks.json`.

3. **notion/comments.py** — calls `notion-get-comments` MCP once per task (5 parallel workers), adds `recent_comments` to each task, saves `notion_tasks_with_comments.json`.

4. **notion/synthesize.py** — builds skeleton over parent-level tasks only (subtasks folded into `parent.subtasks`), classifies each as active/stale/dormant, calls Claude CLI once per parent (5 parallel workers, haiku model). Output: `snapshot.json` with per-task `status_summary`, `action_items`, `blocker`, `release_status`.

### standup.py — task classification from snapshot

```
group == "done"                          → done list
group == "review"                        → review list (moved to review)
blocked_by or action_required_from       → blocked list
group in ("started","skipped"),
  state not in (todo, inbox)             → active (worked_on)
state in (todo, inbox) or group==backlog → backlog list (terminal only)
group == "cancelled"                     → skipped
```

GitHub commits cannot be matched to Notion task IDs. All ticketed commits appear as standalone entries in the In Progress section.

#### Commit groups (`--commits`)

| Group        | Description                                      |
|--------------|--------------------------------------------------|
| `done`       | Commits linked to done/review tasks              |
| `in_progress`| Commits linked to in-progress tasks              |
| `orphan`     | Commits with no ticket ID in message             |
| `all`        | Shorthand for all three groups above             |

#### Output

- Full report printed to stdout
- Report body (without header line) copied to macOS clipboard via `pbcopy`
- Backlog printed to terminal after the report (not copied)
- If `--slack` is set: separate mrkdwn-formatted message sent to your Slack DM

# important-instruction-reminders
Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.