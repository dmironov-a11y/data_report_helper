---
description: Build a full Notion sprint snapshot via MCP sub-agents — fetches sprints, tasks, PRs, and comments directly in-session via Notion MCP (no subprocess Claude CLI), then runs AI synthesis. Use /snapshot any time a fresh snapshot of current sprint tasks is needed.
argument-hint: "[standup|review] [en]"
allowed-tools: ["Bash", "Read", "Write", "Agent"]
---

Build a full Notion sprint snapshot for the current sprint. Steps 2–5 are delegated to sub-agents — each agent calls MCP directly within the current session.

## Step 1 — Create snapshot directory

Run via Bash:

```bash
DIR="snapshots/$(date +%Y-%m-%d_%H%M%S)"
mkdir -p "$DIR"
echo "$DIR"
```

Remember `$DIR` for all subsequent steps.

## Step 2 — Fetch sprint IDs

Spawn the `fetch-sprints` agent with this task:

> Fetch sprint metadata (last/current/next) and write to `{DIR}/notion_sprints.json`

Pass the snapshot directory path as context. Wait for the agent to complete and confirm `notion_sprints.json` was written before proceeding.

## Step 3 — Fetch current-sprint tasks

Spawn the `fetch-tasks` agent with this task:

> Read `{DIR}/notion_sprints.json`, fetch all tasks for the current sprint, and write to `{DIR}/notion_tasks.json`

Pass the snapshot directory path as context. Wait for completion.

## Step 4 — Fetch PR data

Spawn the `fetch-prs` agent with this task:

> Read `{DIR}/notion_tasks.json`, fetch Notion PR pages for all tasks that have GitHub Pull Requests, and write to `{DIR}/notion_prs.json`

Pass the snapshot directory path as context. Wait for completion.

## Step 5 — Enrich tasks with comments

Spawn the `fetch-comments` agent with this task:

> Read `{DIR}/notion_tasks.json`, call notion-get-comments for each task URL, and write the enriched array to `{DIR}/notion_tasks_with_comments.json`

Pass the snapshot directory path as context. Wait for completion.

## Step 6 — Build AI snapshot

Check if the argument contains `en`. Run via Bash:

```bash
# argument contains "en":
uv run notion/synthesize.py --dir "$DIR" --lang en

# otherwise (default):
uv run notion/synthesize.py --dir "$DIR"
```

Produces `$DIR/snapshot.json`.

## Step 7 — Render output

Check the argument:

- **`standup`** or **`standup en`** — personal standup → Slack:
  ```bash
  uv run standup.py --snapshot-dir "$DIR" --slack
  ```
- **`review`** — sprint review → Slack:
  ```bash
  uv run notion/sprint_review.py --dir "$DIR" --slack
  ```
- **No argument** — print path only:
  ```
  Snapshot ready: $DIR/snapshot.json
  ```

## Error handling

- If an agent fails to write its output file, stop and report which step failed.
- `notion_prs.json` may be `{}` — valid when no tasks have PRs.
- Empty `recent_comments: []` on tasks is normal.
- synthesize.py errors are logged to stderr and in `snapshot.metadata.errors`.
