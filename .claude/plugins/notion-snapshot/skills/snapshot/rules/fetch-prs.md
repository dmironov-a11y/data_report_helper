# fetch-prs

Rules for Step 4 of the snapshot pipeline: query the PR DB and write `notion_prs.json`.

## Input

Read `{dir}/notion_tasks.json`. For each task row, parse the `GitHub Pull Requests` field (a JSON-encoded list of Notion page URLs). Build a mapping:

```
{ task_url: [pr_page_url, ...] }
```

If no tasks have PR URLs (mapping is empty), write `{}` to `{dir}/notion_prs.json` and stop.

## MCP call

Collect all unique PR page URLs across all tasks. Build a comma-separated list quoted with single quotes: `'url1', 'url2', ...`.

Call `mcp__claude_ai_Notion__notion-query-data-sources` with:
- mode: `"sql"`
- data_source_urls: `["collection://36650979-0d9a-805f-80b4-000ba2669c0d"]`
- query:
  ```sql
  SELECT url, "Title", "PR Number",
         "date:Merged At:start", "date:Closed At:start",
         "Related to Paynext Data Tasks (GitHub Pull Requests)"
  FROM "collection://36650979-0d9a-805f-80b4-000ba2669c0d"
  WHERE url IN ({comma_separated_quoted_urls})
  ```

## Building the PR index

Parse the result rows. For each row:
- `number`: integer value of `PR Number` (or null)
- `merged`: true if `date:Merged At:start` or `date:Closed At:start` is non-null/non-empty
- `title`: value of `Title`
- `env`: first `[TAG]` captured by regex `^\[([^\]]+)\]` from the title, empty string if no match
- `url`: value of `url`

Build a dict keyed by **task Notion URL**, where each value is a list of PR objects sorted by `number` ascending. Only include task URLs that have at least one matched PR:

```json
{
  "https://www.notion.so/<task-uuid>": [
    { "number": 154, "merged": true, "title": "[STAGE] Fix auth", "env": "STAGE", "url": "https://www.notion.so/..." }
  ]
}
```

## Output

Write the index to `{dir}/notion_prs.json`.
