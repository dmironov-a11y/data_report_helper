---
description: Build a full Notion snapshot for the current sprint — runs notion_sprints → notion_tasks → notion_comments (one Claude call per task) → create_snapshot and produces snapshots/YYYY-MM-DD_HHMMSS/snapshot.json. Use /snapshot any time you need a fresh snapshot of current sprint tasks with all comments.
---

Build a full Notion snapshot for the current sprint. Follow steps in order.

## Step 1 — Create snapshot directory

```bash
DIR="snapshots/$(date +%Y-%m-%d_%H%M%S)"
mkdir -p "$DIR"
```

## Step 2 — Fetch sprint IDs

```bash
uv run notion_sprints.py --dir "$DIR"
```

Produces `$DIR/notion_sprints.json` with last/current/next sprint data.

## Step 3 — Fetch current-sprint tasks

```bash
uv run notion_tasks.py --dir "$DIR" --sprint current
```

Produces `$DIR/notion_tasks.json` — clean JSON with task rows.

## Step 4 — Enrich tasks with comments

```bash
uv run notion_comments.py --dir "$DIR"
```

Calls Claude CLI once per task (5 parallel workers). Fetches comments via `notion-get-comments` MCP and saves `$DIR/notion_tasks_with_comments.json` — full task array with `recent_comments` added to each task. `notion_tasks.json` stays unchanged.

## Step 5 — Build snapshot.json

```bash
uv run create_snapshot.py --dir "$DIR"
```

Reads enriched `notion_tasks.json` and sprint info from `notion_sprints.json`, produces `$DIR/snapshot.json`.

## Done

Print path to `$DIR/snapshot.json`, task count, and total comment count.
