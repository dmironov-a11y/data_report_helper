# fetch-comments

Rules for Step 5 of the snapshot pipeline: enrich tasks with Notion comments and write `notion_tasks_with_comments.json`.

## Input

Read `{dir}/notion_tasks.json`. This is a JSON array of task rows.

## MCP calls

For each task row that has a non-empty `id` field, call `mcp__claude_ai_Notion__notion-get-comments` with:
- page_id: the task's `id` value (UUID format e.g. `34550979-0d9a-8193-9431-ef996782fa3a`) — NOT the full `url`
- include_all_blocks: true
- include_resolved: false

All tasks can be called in parallel.

## Parsing the response

The MCP tool returns an XML response. Parse `<comment>` elements:

```
<comment datetime="ISO_TIMESTAMP">COMMENT_TEXT</comment>
```

For each matched comment:
1. Extract the `datetime` attribute value
2. Extract the inner text, stripping HTML:
   - `<mention-user url="..."/>` → `@user`
   - `<br/>` or `<br>` → newline
   - all other tags → removed
3. Skip comments where the text is empty after stripping

Build `[{"text": "...", "datetime": "..."}]` sorted by `datetime` descending (newest first).

If the MCP call fails or returns no comments, use `recent_comments: []` for that task.

## Output

Add the `recent_comments` field to each task row (use `[]` for tasks with no `id`). Write the complete enriched array to `{dir}/notion_tasks_with_comments.json`. The structure is identical to `notion_tasks.json` plus `recent_comments` on each row.
