---
description: Build a full Notion sprint snapshot — fetches sprints, tasks, PRs, and comments directly in-session via Notion MCP (no subprocess Claude CLI), then runs AI synthesis. Use /snapshot any time a fresh snapshot of current sprint tasks is needed.
argument-hint: "[standup|review] [en]"
allowed-tools: ["Bash", "Read", "Write", "mcp__claude_ai_Notion__notion-query-data-sources", "mcp__claude_ai_Notion__notion-get-comments"]
---

Build a full Notion sprint snapshot. All Notion data fetching happens in the current session via MCP tools directly — no subprocess spawning, no agents.

## Step 1 — Create snapshot directory

```bash
DIR="snapshots/$(date +%Y-%m-%d_%H%M%S)"
mkdir -p "$DIR"
```

Remember `$DIR` for all subsequent steps.

## Step 2 — Fetch sprint IDs

Call the Notion MCP query tool with:
- mode: `"sql"`
- data_source_urls: `["collection://35750979-0d9a-80a1-8b0f-000bf8fd37f8"]`
- query:
  ```sql
  SELECT url, "Sprint name", "Sprint status", "Sprint ID", "date:Dates:start", "date:Dates:end"
  FROM "collection://35750979-0d9a-80a1-8b0f-000bf8fd37f8"
  WHERE "Sprint status" IN ('Last', 'Current', 'Next')
  ORDER BY "Sprint ID"
  ```

Parse the result. Build a JSON object keyed by lowercase sprint status (`last`, `current`, `next`):
```json
{
  "current": { "url": "...", "sprint_name": "...", "sprint_id": ..., "start": "...", "end": "..." }
}
```
Field mapping: `Sprint name`→`sprint_name`, `Sprint ID`→`sprint_id`, `date:Dates:start`→`start`, `date:Dates:end`→`end`.

Write to `$DIR/notion_sprints.json`.

## Step 3 — Fetch current-sprint tasks

Read `$DIR/notion_sprints.json`, extract `current.url`.

Call the Notion MCP query tool with:
- mode: `"sql"`
- data_source_urls: `["collection://35650979-0d9a-80f6-92ed-000b93238f83"]`
- query:
  ```sql
  SELECT * FROM "collection://35650979-0d9a-80f6-92ed-000b93238f83"
  WHERE "Sprint" LIKE '%{SPRINT_URL}%'
  ```

Write the raw JSON array to `$DIR/notion_tasks.json`. Strip any code fences or wrapper objects — the file must contain only a valid JSON array.

## Step 4 — Fetch PR data

Read `$DIR/notion_tasks.json`. For each task, parse the `GitHub Pull Requests` field (JSON-encoded list). Collect all unique PR page URLs.

If no PR URLs found: write `{}` to `$DIR/notion_prs.json` and skip.

Otherwise, call the Notion MCP query tool with:
- mode: `"sql"`
- data_source_urls: `["collection://36650979-0d9a-805f-80b4-000ba2669c0d"]`
- query:
  ```sql
  SELECT url, "Title", "PR Number", "date:Merged At:start", "date:Closed At:start",
         "Related to Paynext Data Tasks (GitHub Pull Requests)"
  FROM "collection://36650979-0d9a-805f-80b4-000ba2669c0d"
  WHERE url IN ('url1', 'url2', ...)
  ```

Build the index — dict keyed by task URL:
- `merged`: true if `date:Merged At:start` or `date:Closed At:start` is non-null
- `env`: first `[TAG]` from title via `^\[([^\]]+)\]`, else `""`
- Sort each task's list by `number`

Write to `$DIR/notion_prs.json`.

## Step 5 — Enrich tasks with comments

Read all tasks from `$DIR/notion_tasks.json`.

For each task with a non-empty `url`, call the Notion MCP get-comments tool with:
- page_id: the task url
- include_all_blocks: true
- include_resolved: false

Parse `<comment datetime="...">TEXT</comment>` from the XML response. Strip HTML tags; skip empty. Add `recent_comments: [{text, datetime}]` (newest-first) to each task row.

Write the enriched array to `$DIR/notion_tasks_with_comments.json`.

## Step 6 — Build AI snapshot

Check if the argument contains `en`:

```bash
# "en" in argument:
uv run notion/synthesize.py --dir "$DIR" --lang en

# otherwise:
uv run notion/synthesize.py --dir "$DIR"
```

Produces `$DIR/snapshot.json`.

## Step 7 — Render output

- **`standup`** or **`standup en`**:
  ```bash
  uv run standup.py --snapshot-dir "$DIR" --slack
  ```
- **`review`**:
  ```bash
  uv run notion/sprint_review.py --dir "$DIR" --slack
  ```
- **No argument**: print `Snapshot ready: $DIR/snapshot.json`
