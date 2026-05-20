---
description: This skill should be used when the user invokes "/notion-snapshot:snapshot", asks to "build a sprint snapshot", "fetch notion tasks", "run the snapshot pipeline", or "update snapshot". Runs the full Notion sprint snapshot pipeline using MCP sub-agents (no subprocess Claude CLI) — fetches sprints, tasks, PRs, and comments directly via Notion MCP, then runs AI synthesis.
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

Spawn the `notion-snapshot:fetch-sprints` sub-agent with the task:

> Fetch sprint metadata and write to `{DIR}/notion_sprints.json`

The agent calls `mcp__claude_ai_Notion__notion-query-data-sources` with a SQL query against the Sprints DB and writes the parsed sprint data. Wait for completion before proceeding.

Expected output: `$DIR/notion_sprints.json` — object keyed by `last`/`current`/`next`.

## Step 3 — Fetch current-sprint tasks

Spawn the `notion-snapshot:fetch-tasks` sub-agent with the task:

> Read `{DIR}/notion_sprints.json`, fetch tasks for the current sprint, and write to `{DIR}/notion_tasks.json`

The agent reads the sprint URL from notion_sprints.json, queries the Tasks DB via `mcp__claude_ai_Notion__notion-query-data-sources`, and writes the raw task array. Wait for completion.

Expected output: `$DIR/notion_tasks.json` — JSON array of task rows.

## Step 4 — Fetch PR data

Spawn the `notion-snapshot:fetch-prs` sub-agent with the task:

> Read `{DIR}/notion_tasks.json`, fetch PR pages for tasks that have GitHub Pull Requests, and write to `{DIR}/notion_prs.json`

The agent reads PR URLs from task rows, queries the PR DB via `mcp__claude_ai_Notion__notion-query-data-sources`, and builds a task-keyed index of PR metadata. If no tasks have PRs, it writes `{}`. Wait for completion.

Expected output: `$DIR/notion_prs.json` — dict keyed by task Notion URL.

## Step 5 — Enrich tasks with comments

Spawn the `notion-snapshot:fetch-comments` sub-agent with the task:

> Read `{DIR}/notion_tasks.json`, call notion-get-comments for each task, and write the enriched array to `{DIR}/notion_tasks_with_comments.json`

The agent calls `mcp__claude_ai_Notion__notion-get-comments` per task (sequentially), parses the XML response, and writes the full task array with `recent_comments` added. Wait for completion.

Expected output: `$DIR/notion_tasks_with_comments.json` — same structure as notion_tasks.json with `recent_comments: [{text, datetime}]` on each row.

## Step 6 — Build AI snapshot

Check if the argument contains `en`. Run via Bash:

```bash
# argument contains "en":
uv run notion/synthesize.py --dir "$DIR" --lang en

# otherwise (default):
uv run notion/synthesize.py --dir "$DIR"
```

This calls `claude --print` per parent task for text-only synthesis (no MCP needed — works fine). Produces `$DIR/snapshot.json`.

## Step 7 — Render output

Check the argument passed when `/notion-snapshot:snapshot` was invoked:

**`standup`** or **`standup en`** — personal daily standup → Slack:
```bash
uv run standup.py --snapshot-dir "$DIR" --slack
```
Requires `NOTION_USER_ID` in `.env`.

**`review`** — full sprint review → Slack:
```bash
uv run notion/sprint_review.py --dir "$DIR" --slack
```

**No argument** — print path only:
```
Snapshot ready: $DIR/snapshot.json
```

## Error handling

- If any agent fails (file not written), stop and report which step failed.
- `notion_prs.json` may be `{}` — that is valid (no tasks with PRs).
- If `notion_tasks_with_comments.json` has tasks with empty `recent_comments` — that is normal.
- synthesize.py errors are logged to stderr and reflected in `snapshot.metadata.errors`.
