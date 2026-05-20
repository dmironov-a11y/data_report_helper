---
description: Build a full Notion sprint snapshot via MCP sub-agents — fetches sprints, tasks, PRs, and comments directly via Notion MCP (no subprocess Claude CLI), then runs AI synthesis. Produces snapshots/YYYY-MM-DD_HHMMSS/snapshot.json.
argument-hint: "[standup|review] [en]"
allowed-tools: ["Bash", "Read", "Write"]
---

Build a full Notion sprint snapshot for the current sprint. All Notion data fetching is done by sub-agents running in the current session (which has MCP access) — no subprocess `claude --print` calls needed.

The pipeline: **fetch-sprints** → **fetch-tasks** → **fetch-prs** → **fetch-comments** → **synthesize** → optional render.

## Step 1 — Create snapshot directory

Run via Bash:

```bash
DIR="snapshots/$(date +%Y-%m-%d_%H%M%S)"
mkdir -p "$DIR"
echo "$DIR"
```

Remember `$DIR` for all subsequent steps.

## Step 2 — Fetch sprint IDs

Spawn the `notion-snapshot:fetch-sprints` sub-agent with this exact task:

> Fetch sprint metadata and write to `{DIR}/notion_sprints.json`

The agent calls `mcp__claude_ai_Notion__notion-query-data-sources` with a SQL query against the Sprints DB and writes the parsed sprint data. Wait for the agent to complete before proceeding.

Expected output: `$DIR/notion_sprints.json` — object keyed by `last`/`current`/`next`.

## Step 3 — Fetch current-sprint tasks

Spawn the `notion-snapshot:fetch-tasks` sub-agent with this task:

> Read `{DIR}/notion_sprints.json`, fetch tasks for the current sprint, write to `{DIR}/notion_tasks.json`

The agent reads the sprint URL from notion_sprints.json, queries the Tasks DB via `mcp__claude_ai_Notion__notion-query-data-sources`, and writes the raw task array. Wait for completion.

Expected output: `$DIR/notion_tasks.json` — JSON array of task rows.

## Step 4 — Fetch PR data

Spawn the `notion-snapshot:fetch-prs` sub-agent with this task:

> Read `{DIR}/notion_tasks.json`, fetch PR pages for tasks with GitHub Pull Requests, write to `{DIR}/notion_prs.json`

The agent reads PR URLs from task rows, queries the PR DB via `mcp__claude_ai_Notion__notion-query-data-sources`, and builds a task-keyed index. If no tasks have PRs, it writes `{}`. Wait for completion.

Expected output: `$DIR/notion_prs.json` — dict keyed by task Notion URL.

## Step 5 — Enrich tasks with comments

Spawn the `notion-snapshot:fetch-comments` sub-agent with this task:

> Read `{DIR}/notion_tasks.json`, call notion-get-comments for each task URL, write enriched array to `{DIR}/notion_tasks_with_comments.json`

The agent calls `mcp__claude_ai_Notion__notion-get-comments` per task (sequentially), parses XML responses, and writes the full task array with `recent_comments` added. Wait for completion.

Expected output: `$DIR/notion_tasks_with_comments.json` — same as notion_tasks.json with `recent_comments: [{text, datetime}]` on each row.

## Step 6 — Build AI snapshot

Check if the argument contains `en`. Run via Bash:

```bash
# argument contains "en":
uv run notion/synthesize.py --dir "$DIR" --lang en

# otherwise (default):
uv run notion/synthesize.py --dir "$DIR"
```

This runs per-task Claude CLI text synthesis (no MCP needed — works fine as subprocess). Produces `$DIR/snapshot.json`.

## Step 7 — Render output

Check the argument passed when this command was invoked:

- **`standup`** or **`standup en`** — personal daily standup → Slack:
  ```bash
  uv run standup.py --snapshot-dir "$DIR" --slack
  ```
  Requires `NOTION_USER_ID` in `.env`.

- **`review`** — full sprint review → Slack:
  ```bash
  uv run notion/sprint_review.py --dir "$DIR" --slack
  ```

- **No argument** — print path only:
  ```
  Snapshot ready: $DIR/snapshot.json
  ```

## Error handling

- If any agent fails (file not written), stop and report which step failed.
- `notion_prs.json` may be `{}` — valid (no tasks with PRs).
- Empty `recent_comments: []` on tasks is normal.
- synthesize.py errors are logged to stderr and in `snapshot.metadata.errors`.
