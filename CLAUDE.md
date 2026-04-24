# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the scripts

### standup.py — daily standup report

```bash
uv run standup.py                                    # report for previous working day
uv run standup.py --standup-date 2026-03-25          # report on March 25
uv run standup.py --add-links                        # include Plane and GitHub URLs in output
uv run standup.py --slack                            # send report to Slack DM
uv run standup.py --commits in_progress              # show commits under in-progress tasks
uv run standup.py --commits done in_progress         # show commits under done + in-progress
uv run standup.py --commits all                      # show commits for all groups + orphans
uv run standup.py --standup-date 2026-03-25 --slack --commits all --add-links      # full featured run
```

### sprints.py — sprint cycles view + rename

```bash
uv run sprints.py                                    # show current + next cycle issues in terminal
uv run sprints.py --slack                            # send cycles report to Slack DM
uv run sprints.py --rename-tasks --dry-run           # AI rename proposals for next cycle (no changes applied)
uv run sprints.py --rename-tasks --dry-run --cycle current   # same for current cycle
uv run sprints.py --rename-tasks --dry-run --cycle both      # both cycles
uv run sprints.py --rename-tasks                     # propose + confirm + apply renames to Plane
uv run sprints.py --rename-tasks DATA-123            # rename single issue
uv run sprints.py --rename-tasks DATA-123 --dry-run  # preview rename for single issue
```

## Environment setup

Copy `.env.example` to `.env` and fill in credentials. Required variables:
- `PLANE_API_KEY`, `PLANE_WORKSPACE_SLUG`, `PLANE_PROJECT_ID`
- `GITHUB_TOKEN` (needs `repo` + `read:org` scopes; authorize SSO for the org at github.com/settings/tokens)
- `GITHUB_ORG`, `GITHUB_USERNAME`
- `SLACK_BOT_TOKEN` (optional, needed for `--slack`; needs `chat:write` scope)
- `SLACK_USER_ID` (optional, your Slack member ID e.g. `U0123456789`)

## Architecture

Two entry points with shared `lib/` package:
- `standup.py` — standup mode only
- `sprints.py` — cycles view (default) + rename mode (`--rename-tasks`)

```
lib/
  config.py   # env constants, validate_config, parse_date_arg
  plane.py    # Plane API helpers (plane_get, plane_patch, get_*, build_issue_url...)
  github.py   # GitHub helpers (get_github_commits, TICKET_RE, ...)
  slack.py    # send_to_slack
  report.py   # build_report, build_slack_report, prev_workday, workday_range
  cycles.py   # _build_cycle_message, build_cycle_messages
  rename.py   # RENAME_SYSTEM_PROMPT, ai_rename, run_rename_mode
```

### Standup mode (default)

Data sources:

1. **Plane.so API** — fetches authenticated user via `/users/me/`, then paginates all issues for the configured project and filters client-side by assignee (the API ignores the `assignees` query param). Issues are classified by `state.group` and `updated_at` date.

2. **GitHub API** (PyGithub) — fetches commits by `GITHUB_USERNAME` for the target day by scanning the default branch of every repo in the org.

3. **Report builder** — merges Plane active issues with GitHub commits into a `worked_on` dict, builds plain-text standup output. Commits for done tasks go into a separate `done_commits` dict.

4. **Slack sender** — builds a separate mrkdwn-formatted message with clickable `<url|DATA-XXX>` links and linked commit SHAs, sends via `chat.postMessage` to the user's DM.

#### Issue classification order (order matters)

```
backlog/unstarted  → backlog list (shown in terminal only, no date filter)
completed          → done list (only if completed_at on report date)
"review" in name   → review list (only if completed_at on report date)
blocked label      → blocked list
started            → plane_active (merged with GitHub commits into worked_on, no date filter)
```

#### Commit groups (`--commits`)

| Group        | Description                                      |
|--------------|--------------------------------------------------|
| `done`       | Commits linked to done/review tasks              |
| `in_progress`| Commits linked to in-progress tasks              |
| `orphan`     | Commits with no DATA-XXXX ticket in message      |
| `all`        | Shorthand for all three groups above             |

#### Output

- Full report printed to stdout
- Report body (without header line) copied to macOS clipboard via `pbcopy`
- Backlog printed to terminal after the report (not copied)
- If `--slack` is set: separate mrkdwn-formatted message sent to your Slack DM

### Cycles mode (sprints.py default)

Fetches current and next sprint cycles from Plane and sends them as **two separate Slack messages**.

1. **Cycle detection** — calls `/cycles/` for the project, determines current (`start_date ≤ now ≤ end_date`) and next (nearest upcoming by `start_date`) cycles. `status` field from API may be `null`, so dates are used as fallback.

2. **Issues** — fetches all issues in each cycle via `/cycles/{id}/cycle-issues/`. One request per cycle (no pagination needed for typical cycle sizes).

3. **Tree rendering** — issues are displayed as a parent→children tree:
   - If a child's parent is in the same cycle, it's nested under the parent with `↳`
   - If a parent is referenced but not in the cycle, it's fetched individually and used as a synthetic root node
   - Issues that are children of a known parent are excluded from the flat top-level list (no duplicates)

4. **Members** — workspace members are fetched once via `/workspaces/{slug}/members/` to resolve assignee UUIDs to `display_name`.

5. **Output** — two mrkdwn messages (current cycle, next cycle), each grouped by state group (completed / started / unstarted / backlog / cancelled) with progress counter.

### Rename mode (sprints.py --rename-tasks)

Uses local `claude` CLI (no API key needed, uses subscription) to propose AI-generated renames for issues in the selected cycle, then optionally applies them to Plane via PATCH.

#### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--cycle next` | ✓ | Rename issues in next cycle only |
| `--cycle current` | | Rename issues in current cycle only |
| `--cycle both` | | Rename issues in current + next cycles |
| `--dry-run` | | Show proposals, do not apply or prompt |

Without `--dry-run`: shows proposal table, then asks `[y/N]` confirmation before writing to Plane.

#### Naming manifest

Format: `[Type] Area: Short Description`

| Tag | When to use |
|-----|-------------|
| `[Chart]` | New or updated chart/dashboard UI |
| `[Feature]` | Business feature (filters, drill-down, new tab) |
| `[Fix]` | Bug or incorrect logic/calculation |
| `[BE]` | Tinybird endpoint, pipeline, data transformation |
| `[FE]` | Frontend implementation (React, UI wiring, prod deploy) |
| `[Research]` | Investigation, benchmark, validation with BQ/stakeholders |
| `[Doc]` | Documentation, tooltips, descriptions |
| `[QA]` | Testing, comparison with BigQuery |
| `[Infra]` | Infrastructure (workspace, alerting, MCP setup) |

Rules:
- Type tag always present, Title Case (`[Chart]` not `[chart]`)
- Area = short tab name: Payments, Declines, 3DS, BIN, Filters, Subscriptions, Tinybird, Metrics, Infra
- Top-level tasks (no parent) → lean toward `[Feature]` / `[Research]`
- Sub-tasks (has parent) → lean toward `[BE]` / `[FE]` / `[Chart]` / `[Fix]`

# important-instruction-reminders
Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.