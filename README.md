# data-report

CLI tools for daily standup reports, powered by Notion and GitHub.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- macOS (uses `pbcopy` for clipboard)
- Claude Code with Notion MCP configured (for `/snapshot`)

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

## Workflow

### 1. Build a snapshot

Run `/snapshot [standup|review]` inside Claude Code. Triggers the full pipeline and optionally renders output:

```
notion/sprints.py → notion/tasks.py → notion/prs.py → notion/comments.py → notion/synthesize.py
```

- `/snapshot` — build only, prints snapshot path
- `/snapshot standup` — build + send personal daily standup to Slack
- `/snapshot review` — build + send sprint review to Slack

Produces `snapshots/YYYY-MM-DD_HHMMSS/snapshot.json` — universal (all sprint tasks, no user filter).

### 2a. Daily async standup (recommended)

Requires a user ID: set `NOTION_USER_ID` in `.env` or pass `--user`.

```bash
uv run standup.py                                    # latest snapshot + commits for previous working day
uv run standup.py --user "user://2e8d..."            # run for a different user
uv run standup.py --standup-date 2026-03-25          # override commit date range
uv run standup.py --snapshot-dir snapshots/2026-05-20_084636  # use specific snapshot
uv run standup.py --slack                            # send to Slack DM
uv run standup.py --add-links                        # include Notion and GitHub URLs
uv run standup.py --commits all                      # show commits for all groups + orphans
uv run standup.py --slack --add-links --commits all  # full featured
```

Format: ✅ Done → 🔄 In progress / planned → ⛔ Blocked → 🧩 Need tasks. Filters to tasks assigned to the specified user. Report body auto-copied to clipboard.

### 2b. Sprint review (detailed, with AI summaries + thread)

```bash
uv run notion/sprint_review.py --dir snapshots/2026-05-20_084636
# → writes standup_main.txt + standup_thread.txt

uv run notion/sprint_review.py --dir snapshots/2026-05-20_084636 --slack
# → also posts main message + thread to your Slack DM
```

Output sections:
- **Main:** 🎯 Фокус дня → 🔄 В работе → ⛔ Заблокированы → 📦 Готово к релизу
- **Thread:** ✅ Закрыто сегодня → ⚠️ Висит без апдейтов → 📥 Не запущено → 🗄 Закрыто ранее

### Commit groups (`--commits`)

| Group | Description |
|---|---|
| `done` | Commits linked to done/review tasks |
| `in_progress` | Commits linked to in-progress tasks |
| `orphan` | Commits with no ticket ID in message |
| `all` | Shorthand for all three groups above |
