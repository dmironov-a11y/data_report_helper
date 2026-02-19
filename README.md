# standup

CLI tool that generates a daily standup report from Plane.so issues and GitHub commits, copies it to clipboard, and optionally sends it to Slack.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- macOS (uses `pbcopy` for clipboard)

## Installation

```bash
git clone <repo>
cd data-report

# Install dependencies
uv sync

# Copy and fill in credentials
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

## Usage

```bash
# Basic — report for previous working day (Mon → Fri, otherwise yesterday)
uv run standup.py

# Specific date
uv run standup.py --date 2026-02-14

# Include raw URLs in terminal output (Plane issues + commit links)
uv run standup.py --add-links

# Send to Slack DM
uv run standup.py --slack

# Show commits in report
uv run standup.py --commits in_progress        # commits under in-progress tasks
uv run standup.py --commits done               # commits under done/review tasks
uv run standup.py --commits orphan             # commits with no DATA-XXXX ticket
uv run standup.py --commits done in_progress   # combine groups
uv run standup.py --commits all                # all groups

# Combine flags
uv run standup.py --slack --commits all
uv run standup.py --date 2026-02-17 --commits in_progress --add-links
```

## Output

- **Terminal** — full plain-text report + backlog section
- **Clipboard** — report body (without header), ready to paste into Slack
- **Slack DM** — mrkdwn-formatted message with clickable issue and commit links (requires `--slack`)
