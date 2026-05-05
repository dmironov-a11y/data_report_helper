---
name: notion-standup
description: >
  Daily standup summary for the Paynext project. Use this skill whenever the user asks for a
  standup, daily summary, sprint status, or wants to know what was discussed or updated on tasks
  since the last standup call. Triggers on: "стендап", "standup", "что делали", "статус спринта",
  "покажи задачи", "что обновилось", "daily summary", or any request about today's/yesterday's
  task updates. The skill reads the current Notion sprint, fetches comments written during or after
  the standup call, and produces a structured report with status synthesis and action items.
---

# Notion Daily Standup

Produces a daily standup report from the current Notion sprint. The workflow:
during the standup call the user writes comments on tasks; this skill reads those
comments and synthesises them into a structured status report with action items.

## Data sources

- **Tasks DB:** `collection://35650979-0d9a-80f6-92ed-000b93238f83`
- **Sprints DB:** `collection://35750979-0d9a-80a1-8b0f-000bf8fd37f8`
- MCP tools: `notion-query-data-sources`, `notion-get-comments`, `notion-update-page`

## Step 1 — Report date and comment window

Default date: today. If the user passes a date like `2026-05-04`, use that.
If the date is Saturday or Sunday, shift back to Friday.

**Previous working day:**
- Monday → Friday (skip weekend)
- Tuesday–Friday → day before

**Comment window:** from start of previous working day through end of today.
This captures comments written after yesterday's standup as well as during today's call.

Label comments as `today` or `yesterday` when displaying.

## Step 2 — Get current sprint

```sql
SELECT url, "Sprint name", "date:Dates:start", "date:Dates:end"
FROM "collection://35750979-0d9a-80a1-8b0f-000bf8fd37f8"
WHERE "Sprint status" = 'Current'
LIMIT 1
```

Extract the UUID from the sprint `url` (last path segment, no dashes). If no current sprint, stop.

## Step 3 — Get sprint tasks

```sql
SELECT url, "Name", "State", "Parent task", "Assignees", "Estimate", "userDefined:ID"
FROM "collection://35650979-0d9a-80f6-92ed-000b93238f83"
WHERE "Sprint" LIKE '%<sprint_uuid>%'
  AND "State" != 'Cancelled'
ORDER BY "userDefined:ID"
```

## Step 4 — Fetch comments for active tasks

Fetch comments only for tasks where `State` is **not** `Done` — skip Done tasks entirely.

**Critical — UUID format:** `notion-get-comments` requires a UUID with dashes, not a URL.
Convert the task `url` by extracting the last path segment and formatting as 8-4-4-4-12:

```
https://www.notion.so/35250979...81568801d49cb224f55d
→  35250979-0d9a-8156-8801-d49cb224f55d
```

If `notion-get-comments` returns `{}` (empty object) — no comments, skip silently.

Build: `task_url → comments[]` sorted newest first.

A **recent comment** = timestamp date falls within the comment window (today or previous working day).

## Step 5 — Classify tasks

| Group | Condition |
|---|---|
| **Done** | `State = Done` |
| **Updated** | ≥1 recent comment — tasks with standup activity |
| **In Progress** | State in (`In Progress`, `In Review`, `Ready for Integration`, `In Integration`, `Paused`) and no recent comment |
| **Todo / Backlog** | State in (`Todo`, `Backlog`) — show only if has a recent comment, otherwise skip |

**Subtask hierarchy:** a task is a subtask if `"Parent task"` is NOT NULL.
Show subtasks indented (`↳`) under their parent if the parent is also in the sprint task list.
If the parent is not in the list, show the subtask flat.
Build the tree after classification — parents appear before their children within each group.

## Step 6 — Build the report

```
Daily Standup — <Weekday, DD Mon YYYY>
Sprint: <Sprint name>  (<start> → <end>)

✅ Done
• [#ID] Task name

💬 Updated since last standup
• [#ID] Task name  [estimate]
  Status: <one sentence synthesising all recent comments — what changed, where it stands, what's next>
  ↳ • [#ID] Subtask name
      Status: <one sentence>

🔄 In Progress  (no recent update)
• [#ID] Task name  [estimate]
  ↳ • [#ID] Subtask name

──────────────────────────────
🎯 Action Items

• <person or "Team">: <concrete next action> — [#ID]

──────────────────────────────
⚠️ Blockers  (only if found)

• [#ID] Task name: <what is blocking it>
```

### Status line rules
Synthesise ALL recent comments for the task into one sentence. Do not copy comment text
verbatim — rewrite as a status update focused on current state and next step.

Good: `"Endpoint deployed to staging, FE wiring needed before merge"`
Bad: `"d.mironov said: endpoint is on staging"`

### Action item extraction
Scan comments for anything implying a next step. Signal patterns (English and Russian):
- `todo`, `need to`, `needs to`, `will`, `should`, `надо`, `нужно`, `сделать`
- `waiting for`, `ждём`, `жду`, `blocked on`
- `follow up`, `check with`, `уточнить`, `спросить`
- `deploy`, `задеплоить`, `выкатить`
- `done`/`готово` in comment but State is not Done → action: move task to Done

If owner is clear from the comment, use their name. Otherwise use `Team`.

### Empty state handling
- No recent comments at all → show `💬 Updated since last standup` with:
  `— no comments found since <prev_working_day>. Add comments during the call and re-run.`
- Omit empty sections entirely (no Done tasks → skip ✅ Done header)
- No blockers → skip ⚠️ Blockers entirely
