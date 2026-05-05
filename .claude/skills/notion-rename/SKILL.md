---
name: notion-rename
description: >
  Rename a single Paynext task in Notion following the project naming convention [Type] Area: Description.
  Use this skill when the user mentions renaming, formatting, or fixing a task name — by ID number
  (e.g. "переименуй задачу 42", "rename task 38") or by name fragment (e.g. "rename the unlimit bug task",
  "fix the name of the payments filter task"). Also triggers when the user asks to fix or improve
  a task title, or says a task name looks wrong.
---

Find a single task in the Notion "Paynext Data Tasks" database by ID or name, propose an AI rename following the naming convention, and apply it after confirmation.

## Input

`$ARGUMENTS` is either:
- A numeric task ID (e.g. `42`) — match against `"userDefined:ID"`
- A text fragment (e.g. `"payments filter"`) — match against `"Name"` with LIKE

## Step 1 — Find the task

Query `collection://35650979-0d9a-80f6-92ed-000b93238f83` using `notion-query-data-sources`.

If numeric:
```sql
SELECT url, "Name", "State", "Parent task", "Short Description", "userDefined:ID"
FROM "collection://35650979-0d9a-80f6-92ed-000b93238f83"
WHERE "userDefined:ID" = <number>
LIMIT 1
```

If text:
```sql
SELECT url, "Name", "State", "Parent task", "Short Description", "userDefined:ID"
FROM "collection://35650979-0d9a-80f6-92ed-000b93238f83"
WHERE "Name" LIKE '%<text>%'
LIMIT 5
```

Multiple results → show numbered list, ask user to pick one. Nothing found → stop.

## Step 2 — Propose rename

Generate the renamed title yourself following the convention below. A task is a **subtask** if `"Parent task"` is NOT NULL.

### Naming convention: `[Type] Area: Short Description`

**Type tags** (Title Case, always required):
- `[Chart]` — new or updated chart/dashboard UI
- `[Feature]` — business feature (filters, drill-down, new tab, new capability)
- `[Fix]` — bug or incorrect logic/calculation
- `[BE]` — Tinybird endpoint, pipeline, data transformation, refactor
- `[FE]` — frontend implementation (React components, UI wiring, prod deploy)
- `[Research]` — investigation, benchmark, validation with BQ/stakeholders
- `[Doc]` — documentation, tooltips, descriptions, specs
- `[QA]` — testing, comparison with BigQuery, load testing
- `[Infra]` — infrastructure (workspace setup, alerting, MCP, monitoring)

**Area** (always required):
- UI tabs: `Payments Tab`, `Declines Tab`, `3DS Tab`, `BIN Tab`, `Tax Tab`, `Billing Tab`
- Pipelines: `Payments Pipeline`, `Subscriptions Pipeline`, `Refunds Pipeline`
- Other: `Tinybird`, `Metrics`, `General`, `Paynext UI`
- For `[Infra]`: use the specific tool/system as area (never "Infra")

**Rules:**
- Top-level task → lean toward `[Feature]` or `[Research]`
- Subtask → lean toward `[BE]`, `[FE]`, `[Chart]`, `[Fix]`
- `[Chart]` takes priority if explicitly about a specific chart/dashboard
- `General` = spans FE+BE; `Paynext UI` = purely FE; `Tinybird` = purely BE
- Strong verb + object after colon, Title Case, max ~80 chars
- No arrows, no dashes as separators, no all-caps except acronyms (BIN, MCP, SQL, BQ)
- If title already has a valid `[Type]` tag — keep it, only fix area and description

If already perfectly formatted → say so and stop.

## Step 3 — Confirm

```
Task #<ID>
Current:  <original name>
Proposed: <new name>

Apply rename? [y/N]
```

## Step 4 — Apply

Call `notion-update-page`:
```json
{
  "pageId": "<page url>",
  "properties": {
    "Name": { "title": [{ "text": { "content": "<new name>" } }] }
  }
}
```

Confirm: `✓ Renamed to: <new name>`
