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
uv run notion/sprints.py --dir "$DIR"
```

Produces `$DIR/notion_sprints.json` with last/current/next sprint data.

## Step 3 — Fetch current-sprint tasks

```bash
uv run notion/tasks.py --dir "$DIR" --sprint current
```

Produces `$DIR/notion_tasks.json` — clean JSON with task rows.

## Step 4 — Fetch PR data

```bash
uv run notion/prs.py --dir "$DIR"
```

Queries the PR DB for all PR pages linked to current-sprint tasks. Produces `$DIR/notion_prs.json` — dict keyed by task Notion URL, value is list of `{number, merged, title, env, url}`. Runs in one SQL query (no per-PR round trips).

## Step 5 — Enrich tasks with comments

```bash
uv run notion/comments.py --dir "$DIR"
```

Calls Claude CLI once per task (5 parallel workers). Fetches comments via `notion-get-comments` MCP and saves `$DIR/notion_tasks_with_comments.json` — full task array with `recent_comments` added to each task. `notion_tasks.json` stays unchanged.

## Step 6 — Build AI snapshot

```bash
uv run notion/synthesize.py --dir "$DIR"
```

Builds skeleton **only over parent-level tasks** (subtasks fold into `parent.subtasks` metadata: `{total, done, in_progress, blocked, not_started, percent}` with progress = `(done + max(in_progress - blocked, 0) / 2) / total`). Classifies each task as `active`/`stale`/`dormant` scriptably (no AI), then calls Claude CLI once per parent in 5 parallel workers (`haiku` model). Pre-filters comments to last 4 days and passes prior snapshot's summary + open actions + subtask names/states so Claude can focus on what's NEW vs yesterday and reflect subtask progress. Returns per task: `status_summary`, `action_items`, `blocker`, `release_status` (`none|ready_to_release|sent_to_release|released` — releases are owned by another team, we hand off and wait). Auto-finds the latest older snapshot in `snapshots/` as the prior. Produces `$DIR/snapshot.json` — the canonical input for standup rendering.

## Step 7 — Render output

Check the argument passed when `/snapshot` was invoked:

- **`/snapshot standup`** — personal daily standup → Slack:
  ```bash
  uv run standup.py --snapshot-dir "$DIR" --slack
  ```
  Filters to tasks assigned to `NOTION_USER_ID`. Requires `NOTION_USER_ID` in `.env`.

- **`/snapshot review`** — full sprint review → Slack:
  ```bash
  uv run notion/sprint_review.py --dir "$DIR" --slack
  ```
  Team-wide, no user filter.

- **`/snapshot`** (no argument) — skip render, print snapshot path only.