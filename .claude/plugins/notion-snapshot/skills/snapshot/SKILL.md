---
description: This skill should be used when the user invokes "/notion-snapshot:snapshot", asks to "build a sprint snapshot", "fetch notion tasks", "run the snapshot pipeline", or "update snapshot". Runs the full Notion sprint snapshot pipeline using MCP directly in-session — fetches sprints, tasks, PRs, and comments via Notion MCP, then runs AI synthesis.
argument-hint: "[standup|review] [en]"
allowed-tools: ["Bash", "Read", "Write", "mcp__claude_ai_Notion__notion-query-data-sources", "mcp__claude_ai_Notion__notion-get-comments"]
---

Build a full Notion sprint snapshot. All Notion data fetching happens in the current session via MCP tools directly — no subprocess spawning, no agents.

> **Detailed rules** for each step live in:
> `.claude/plugins/notion-snapshot/skills/snapshot/rules/`
> Read the relevant file if you need the exact field mappings, output format, or parsing logic for a given step.

## Step 1 — Create snapshot directory

```bash
DIR="snapshots/$(date +%Y-%m-%d_%H%M%S)"
mkdir -p "$DIR"
echo "$DIR"
```

Remember `$DIR` for all subsequent steps.

## Step 2 — Fetch sprint IDs

> Detailed rules: `.claude/plugins/notion-snapshot/skills/snapshot/rules/fetch-sprints.md`

Call `mcp__claude_ai_Notion__notion-query-data-sources` with:
- mode: `"sql"`
- data_source_urls: `["collection://35750979-0d9a-80a1-8b0f-000bf8fd37f8"]`
- query:
  ```sql
  SELECT url, "Sprint name", "Sprint status", "Sprint ID", "date:Dates:start", "date:Dates:end"
  FROM "collection://35750979-0d9a-80a1-8b0f-000bf8fd37f8"
  WHERE "Sprint status" IN ('Last', 'Current', 'Next')
  ORDER BY "Sprint ID"
  ```

Parse into a JSON object keyed by lowercase sprint status (`last`, `current`, `next`). Write to `$DIR/notion_sprints.json`.

## Step 3 — Fetch current-sprint tasks

> Detailed rules: `.claude/plugins/notion-snapshot/skills/snapshot/rules/fetch-tasks.md`

Read `$DIR/notion_sprints.json`, extract `current.url`.

Call `mcp__claude_ai_Notion__notion-query-data-sources` with:
- mode: `"sql"`
- data_source_urls: `["collection://35650979-0d9a-80f6-92ed-000b93238f83"]`
- query:
  ```sql
  SELECT * FROM "collection://35650979-0d9a-80f6-92ed-000b93238f83"
  WHERE "Sprint" LIKE '%{SPRINT_URL}%'
  ```
  Replace `{SPRINT_URL}` with the actual `current.url` value.

Write the raw JSON array to `$DIR/notion_tasks.json`.

## Step 4 — Fetch PR data

> Detailed rules: `.claude/plugins/notion-snapshot/skills/snapshot/rules/fetch-prs.md`

Read `$DIR/notion_tasks.json`. Parse `GitHub Pull Requests` field per task to collect unique PR page URLs.

If none found: write `{}` to `$DIR/notion_prs.json` and skip.

Otherwise call `mcp__claude_ai_Notion__notion-query-data-sources` with:
- mode: `"sql"`
- data_source_urls: `["collection://36650979-0d9a-805f-80b4-000ba2669c0d"]`
- query:
  ```sql
  SELECT url, "Title", "PR Number", "date:Merged At:start", "date:Closed At:start",
         "Related to Paynext Data Tasks (GitHub Pull Requests)"
  FROM "collection://36650979-0d9a-805f-80b4-000ba2669c0d"
  WHERE url IN ('url1', 'url2', ...)
  ```

Build a dict keyed by task Notion URL → sorted list of PR objects (`number`, `merged`, `title`, `env`, `url`). Write to `$DIR/notion_prs.json`.

## Step 5 — Enrich tasks with comments

> Detailed rules: `.claude/plugins/notion-snapshot/skills/snapshot/rules/fetch-comments.md`

Read `$DIR/notion_tasks.json`. For each task with a non-empty `id`, call `mcp__claude_ai_Notion__notion-get-comments` with:
- page_id: the task `id` (UUID format) — NOT the full `url`
- include_all_blocks: true
- include_resolved: false

Parse `<comment datetime="...">TEXT</comment>`, strip HTML tags, sort newest-first. Add `recent_comments: [{text, datetime}]` to each task row.

Write the enriched array to `$DIR/notion_tasks_with_comments.json`.

## Step 6 — Build AI snapshot

```bash
# argument contains "en":
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

## Error handling

- If any step fails to produce its output file, stop and report which step failed.
- `notion_prs.json` may be `{}` — valid when no tasks have PRs.
- Empty `recent_comments: []` on tasks is normal.
- synthesize.py errors are logged to stderr and in `snapshot.metadata.errors`.
