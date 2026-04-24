# standup + sprints

CLI tools for daily standup reports and sprint cycle management, using Plane.so and GitHub.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- macOS (uses `pbcopy` for clipboard)

## Installation

```bash
git clone <repo>
cd data-report

uv sync

cp .env.example .env
```

## Configuration

Edit `.env`:

```env
# Plane.so
PLANE_API_KEY=your_personal_access_token
PLANE_WORKSPACE_SLUG=your-workspace
PLANE_PROJECT_ID=your-project-uuid

# GitHub
GITHUB_TOKEN=ghp_your_token        # needs: repo, read:org — authorize SSO if required
GITHUB_ORG=your-org-login
GITHUB_USERNAME=your-github-login

# Slack (optional)
SLACK_BOT_TOKEN=xoxb-your-bot-token   # needs: chat:write
SLACK_USER_ID=U0123456789             # your Slack member ID (Profile → ··· → Copy member ID)
```

### GitHub token scopes

Go to [github.com/settings/tokens](https://github.com/settings/tokens), create a token with:
- `repo` — read commits from private repos
- `read:org` — list org repos

If your org uses SSO, click **Authorize** next to the org after creating the token.

### Slack bot setup

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create App
2. **OAuth & Permissions** → Bot Token Scopes → add `chat:write`
3. Install app to workspace → copy **Bot User OAuth Token** → set as `SLACK_BOT_TOKEN`
4. Find your user ID in Slack: click your name → **···** → **Copy member ID**

## standup.py — daily standup report

```bash
uv run standup.py                                    # report for previous working day
uv run standup.py --standup-date 2026-03-25          # report on a specific date
uv run standup.py --add-links                        # include Plane and GitHub URLs in output
uv run standup.py --slack                            # send report to Slack DM
uv run standup.py --commits in_progress              # show commits under in-progress tasks
uv run standup.py --commits done in_progress         # show commits under done + in-progress
uv run standup.py --commits all                      # show commits for all groups + orphans

# Most useful
uv run standup.py --slack --add-links --commits all
```

### Issue classification

| State | Behaviour |
|---|---|
| `backlog` / `unstarted` | Always shown in backlog section (no date filter) |
| `started` | Always shown in In Progress (no date filter) |
| `completed` | Shown in Done only if `completed_at` falls on the report date |
| `review` in state name | Shown in Done (as "moved to review") only if `completed_at` on report date |
| `blocked` label | Shown in Blocked section |

### Commit groups (`--commits`)

| Group | Description |
|---|---|
| `done` | Commits linked to done/review tasks |
| `in_progress` | Commits linked to in-progress tasks |
| `orphan` | Commits with no DATA-XXXX ticket in message |
| `all` | Shorthand for all three groups above |

### Output

- **Terminal** — full plain-text report + backlog section
- **Clipboard** — report body (without header), ready to paste
- **Slack DM** — mrkdwn-formatted message with clickable links (requires `--slack`)

## sprints.py — sprint cycles view + rename

```bash
uv run sprints.py                                    # show current + next cycle in terminal
uv run sprints.py --slack                            # send cycles report to Slack DM

# Rename entire cycle
uv run sprints.py --rename-tasks --dry-run           # proposals for next cycle (no changes)
uv run sprints.py --rename-tasks --dry-run --cycle current
uv run sprints.py --rename-tasks --dry-run --cycle both
uv run sprints.py --rename-tasks                     # propose + confirm + apply to Plane
uv run sprints.py --rename-tasks --cycle current

# Rename single issue
uv run sprints.py --rename-tasks DATA-123            # rename one issue
uv run sprints.py --rename-tasks DATA-123 --dry-run  # preview rename only
```

### Naming convention

Format: `[Type] Area: Short Description`

| Tag | When to use |
|---|---|
| `[Chart]` | New or updated chart/dashboard UI |
| `[Feature]` | Business feature (filters, drill-down, new tab) |
| `[Fix]` | Bug or incorrect logic/calculation |
| `[BE]` | Tinybird endpoint, pipeline, data transformation |
| `[FE]` | Frontend implementation (React, UI wiring, prod deploy) |
| `[Research]` | Investigation, benchmark, validation with BQ/stakeholders |
| `[Doc]` | Documentation, tooltips, descriptions |
| `[QA]` | Testing, comparison with BigQuery |
| `[Infra]` | Infrastructure (workspace, alerting, MCP setup) |

Areas: `Payments Tab`, `Declines Tab`, `3DS Tab`, `BIN Tab`, `Tax Tab`, `Billing Tab`, `Payments Pipeline`, `Subscriptions Pipeline`, `Refunds Pipeline`, `Tinybird`, `Metrics`, `General`, `Paynext UI`