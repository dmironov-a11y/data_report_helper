# fetch-tasks

Rules for Step 3 of the snapshot pipeline: query the Tasks DB for the current sprint and write `notion_tasks.json`.

## Input

Read `{dir}/notion_sprints.json`. Extract `current.url` — the Notion URL of the current sprint.

## MCP call

Call `mcp__claude_ai_Notion__notion-query-data-sources` with:
- mode: `"sql"`
- data_source_urls: `["collection://35650979-0d9a-80f6-92ed-000b93238f83"]`
- query:
  ```sql
  SELECT * FROM "collection://35650979-0d9a-80f6-92ed-000b93238f83"
  WHERE "Sprint" LIKE '%{sprint_url}%'
  ```
  Replace `{sprint_url}` with the actual `current.url` value from notion_sprints.json.

## Output

The MCP tool returns a JSON array (or `{"results": [...]}` wrapper). Write the **raw JSON array** to `{dir}/notion_tasks.json`. Strip any code fences or wrapper object so the file contains only a valid JSON array.
