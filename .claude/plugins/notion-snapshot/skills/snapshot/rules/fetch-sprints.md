# fetch-sprints

Rules for Step 2 of the snapshot pipeline: query the Sprints DB and write `notion_sprints.json`.

## MCP call

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

## Parsing

The MCP tool returns a JSON array of rows (or `{"results": [...]}` wrapper). For each row, create an entry keyed by the **lowercase** sprint status:

```json
{
  "last":    { "url": "...", "sprint_name": "...", "sprint_id": ..., "start": "...", "end": "..." },
  "current": { "url": "...", "sprint_name": "...", "sprint_id": ..., "start": "...", "end": "..." },
  "next":    { "url": "...", "sprint_name": "...", "sprint_id": ..., "start": "...", "end": "..." }
}
```

Field mapping:
- `url` → `url`
- `Sprint name` → `sprint_name`
- `Sprint ID` → `sprint_id`
- `date:Dates:start` → `start`
- `date:Dates:end` → `end`

Only include keys (`last`, `current`, `next`) that are present in the results.

## Output

Write the parsed object to `{dir}/notion_sprints.json`.
