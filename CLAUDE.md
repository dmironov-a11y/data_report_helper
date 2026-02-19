# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the script

```bash
uv run standup.py                                    # report for previous working day
uv run standup.py --date 2026-02-14                  # report for a specific date
uv run standup.py --add-links                        # include Plane and GitHub URLs in output
uv run standup.py --slack                            # send report to Slack DM
uv run standup.py --commits in_progress              # show commits under in-progress tasks
uv run standup.py --commits done in_progress         # show commits under done + in-progress
uv run standup.py --commits all                      # show commits for all groups + orphans
uv run standup.py --date 2026-02-18 --slack --commits all --add-links              # full featured run
```

## Environment setup

Copy `.env.example` to `.env` and fill in credentials. Required variables:
- `PLANE_API_KEY`, `PLANE_WORKSPACE_SLUG`, `PLANE_PROJECT_ID`
- `GITHUB_TOKEN` (needs `repo` + `read:org` scopes; authorize SSO for the org at github.com/settings/tokens)
- `GITHUB_ORG`, `GITHUB_USERNAME`
- `SLACK_BOT_TOKEN` (optional, needed for `--slack`; needs `chat:write` scope)
- `SLACK_USER_ID` (optional, your Slack member ID e.g. `U0123456789`)

## Architecture

Single-file script (`standup.py`) with three data sources:

1. **Plane.so API** — fetches authenticated user via `/users/me/`, then paginates all issues for the configured project and filters client-side by assignee (the API ignores the `assignees` query param). Issues are classified by `state.group` and `updated_at` date.

2. **GitHub API** (PyGithub) — fetches commits by `GITHUB_USERNAME` for the target day by scanning the default branch of every repo in the org.

3. **Report builder** — merges Plane active issues with GitHub commits into a `worked_on` dict, builds plain-text standup output. Commits for done tasks go into a separate `done_commits` dict.

4. **Slack sender** — builds a separate mrkdwn-formatted message with clickable `<url|DATA-XXX>` links and linked commit SHAs, sends via `chat.postMessage` to the user's DM.

### Issue classification order (order matters)

```
backlog/unstarted  → backlog list (shown in terminal only, regardless of updated_at)
not updated today  → skipped
completed          → done list
"review" in name   → review list
blocked label      → blocked list
started            → plane_active (merged with GitHub commits into worked_on)
```

### Commit groups (`--commits`)

| Group        | Description                                      |
|--------------|--------------------------------------------------|
| `done`       | Commits linked to done/review tasks              |
| `in_progress`| Commits linked to in-progress tasks              |
| `orphan`     | Commits with no DATA-XXXX ticket in message      |
| `all`        | Shorthand for all three groups above             |

### Output

- Full report printed to stdout
- Report body (without header line) copied to macOS clipboard via `pbcopy`
- Backlog printed to terminal after the report (not copied)
- If `--slack` is set: separate mrkdwn-formatted message sent to your Slack DM

# important-instruction-reminders
Do what has been asked; nothing more, nothing less.
NEVER create files unless they're absolutely necessary for achieving your goal.
ALWAYS prefer editing an existing file to creating a new one.
NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.