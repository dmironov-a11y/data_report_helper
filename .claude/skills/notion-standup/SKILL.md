---
name: notion-standup
description: >
  Daily standup summary for the Paynext project. Use this skill whenever the user asks for a
  standup, daily summary, sprint status, or wants to know what was discussed or updated on tasks
  since the last standup call. Triggers on: "стендап", "standup", "что делали", "статус спринта",
  "покажи задачи", "что обновилось", "daily summary", or any request about today's/yesterday's
  task updates. The skill reads the current Notion sprint, fetches comments since the previous
  standup, synthesizes per-task status with action items and blockers (de-duplicated against
  yesterday), saves a JSON snapshot, and posts a tight 5–10 line summary to Slack with the full
  per-task report and follow-up diff attached as thread replies.
---

# Notion Daily Standup

Pipeline keeps the model on the narrow band of work it actually has to do: SQL via MCP,
parallel comment fetches, and per-task synthesis. Everything else — date math, JSON
construction, comment filtering, snapshot diffing, Slack threading — lives in scripts.

## Layout
- Snapshots: `standups/standup_YYYY-MM-DD.json`
- Scripts: `.claude/skills/notion-standup/scripts/` — referenced as `$S` in the steps below.
  In bash, set `S=.claude/skills/notion-standup/scripts` once, then reuse.

## Data sources
- Tasks DB: `collection://35650979-0d9a-80f6-92ed-000b93238f83`
- Sprints DB: `collection://35750979-0d9a-80a1-8b0f-000bf8fd37f8`
- MCP tools: `notion-query-data-sources`, `notion-get-comments`
- Slack creds (`SLACK_BOT_TOKEN`, `SLACK_USER_ID`) come from `.env` via the slack script

---

## Step 1 — compute window
```bash
python3 $S/window.py [--date YYYY-MM-DD]
```
Returns JSON. Capture: `report_date`, `prev_date`, `window_start`, `window_end`,
`snapshot_path`, `prev_snapshot_path`, `prev_snapshot_exists`.

`window_start` is the mtime of the previous snapshot (UTC) when it exists, otherwise the
start of the previous working day. This narrows the comment window to "since the last
standup" so yesterday's chatter doesn't bleed into today's actions.

## Step 2 — current sprint (MCP)
```sql
SELECT url, "Sprint name", "date:Dates:start", "date:Dates:end"
FROM "collection://35750979-0d9a-80a1-8b0f-000bf8fd37f8"
WHERE "Sprint status" = 'Current' LIMIT 1
```
Sprint UUID = last URL path segment, no dashes. If no current sprint, stop.

## Step 3 — sprint tasks (MCP)
```sql
SELECT *
FROM "collection://35650979-0d9a-80f6-92ed-000b93238f83"
WHERE "Sprint" LIKE '%<sprint_uuid>%' AND "State" != 'Cancelled'
ORDER BY "userDefined:ID"
```
Use `SELECT *` — the schema has many columns and `build_snapshot.py` only uses the ones it
needs. Don't transform the rows in-context.

## Step 4 — initial snapshot (script)
Write the rows array to `/tmp/standup_rows.json` (a normal Write tool call), then:
```bash
python3 $S/build_snapshot.py \
  --report-date "$REPORT_DATE" \
  --sprint '{"name":"...","start":"...","end":"..."}' \
  --out "$SNAPSHOT_PATH" \
  < /tmp/standup_rows.json
```
The script resolves `parent_id` from the `Parent task` URL field and classifies `group`
from `State`. Synthesis fields are written empty.

## Step 5 — fetch comments (MCP, in parallel)
Read `$SNAPSHOT_PATH`. For every task with `group != "done"`, call `notion-get-comments`
in a single message with multiple parallel calls.

UUID format for the tool: take the URL's last path segment (32 hex chars) and reformat
as 8-4-4-4-12, e.g. `35250979...81568801d49cb224f55d` → `35250979-0d9a-8156-8801-d49cb224f55d`.

Empty `{}` responses → silently skip.

## Step 6 — apply comments (script)
Build one combined object: `{"<task_id>": [{"text": "...", "datetime": "<iso>"}, ...]}`
with **all** comments returned by the MCP calls (don't pre-filter by date — that's the
script's job). Write to `/tmp/standup_comments.json`, then:
```bash
python3 $S/apply_comments.py \
  --snapshot "$SNAPSHOT_PATH" \
  --window-start "$WINDOW_START" \
  --window-end "$WINDOW_END" \
  < /tmp/standup_comments.json
```
The script keeps only in-window comments, populates `recent_comments` (newest first),
and reclassifies any task with at least one in-window comment to `group="updated"`.

## Step 7 — synthesize per-task (model)
Re-read `$SNAPSHOT_PATH`. If `prev_snapshot_exists`, also read `$PREV_SNAPSHOT_PATH` and
build a quick `prev_by_id` map of yesterday's `action_items` and `blocker.description` —
you'll consult it in the next part.

For every task with `group == "updated"`, emit one entry into a JSON array:

```json
{
  "id": <int>,
  "status_summary": "<one sentence: what changed, current state, next step>",
  "action_items": [{"owner": "<name or Team>", "action": "<imperative>"}],
  "blocker": {"is_blocker": <bool>, "description": "<one sentence or null>"},
  "released": <bool>
}
```

### What to include
Today's `action_items` and `blocker` should reflect **everything today's comments
identify** — including items that were already true yesterday. Don't dedup at this step:
the snapshot is the source of truth, and the diff (Step 9) compares full state vs. full
state. Hiding repeats happens at render time (Step 8 with `--prev`), so you only need to
write what today's comments actually say. Rephrase comments — don't quote verbatim.

### Signals
Action items (look in `recent_comments` text):
- Russian: `надо / нужно / задеплоить / выкатить / ждём / уточнить / спросить / сделает / пингает`
- English: `todo / need to / will / should / waiting on`
- Comment says `done / готово` but `state != Done` → action: move task to Done

Blockers: comment says `ждём / blocked / waiting for`, or task is paused pending an
external response that hasn't arrived.

Released: comment says task is shipped / deployed / released / ready for release / in production
/ merged to main. If comment signals release and state ≠ Done, set `released=true` (this is a
release candidate).

`status_summary` rewrites the comment(s) into a tight sentence — don't quote verbatim.

Apply via:
```bash
python3 $S/apply_synthesis.py --snapshot "$SNAPSHOT_PATH" < /tmp/standup_synthesis.json
```

## Step 8 — render report (script)
```bash
python3 $S/render_report.py "$SNAPSHOT_PATH" \
  ${PREV_SNAPSHOT_EXISTS:+--prev "$PREV_SNAPSHOT_PATH"} \
  > /tmp/standup_report.txt
cat /tmp/standup_report.txt
```
The renderer emits sections: ✅ Done, 🚀 Released, 💬 Updated, 🔄 In Progress. Tasks with
`released=true` appear in the 🚀 Released section and are excluded from Updated/In Progress
(no duplicates). With `--prev`, action items and blockers already in yesterday's snapshot are
hidden — "don't repeat yesterday unless freshly re-stated today". The snapshot itself stays
complete so the diff still works.

## Step 9 — diff vs yesterday + sprint review (script)
If `prev_snapshot_exists`:
```bash
python3 $S/diff_snapshots.py \
  --today "$SNAPSHOT_PATH" \
  --prev  "$PREV_SNAPSHOT_PATH" \
  ${SPRINT_REVIEW:+--sprint-review} \
  > /tmp/standup_followup.txt
```
The script emits sections: ✅ closed, ⚠️ stale, 🔁 pending-from-yesterday, 🚨 new
blockers, ✅ blockers resolved, 🚀 newly released. If NOT `--sprint-review`, also shows
🚫 removed from focus and ✅ added to focus. Empty sections are skipped automatically.

If `--sprint-review` flag was passed and `prev_snapshot_exists`:
```bash
python3 $S/sprint_review.py \
  --today "$SNAPSHOT_PATH" \
  --prev  "$PREV_SNAPSHOT_PATH" \
  > /tmp/standup_sprint_review.txt
```
Emits sprint review block (shipped, carried over, newly added) when sprint name changes,
or nothing if sprint name unchanged. This block appears before the follow-up in Slack threading.

## Step 10 — write the short summary (model)
Based on the rendered report and snapshot, write a structured summary to `/tmp/standup_summary.txt`.

**Standard template** (when sprint did not change):
```
*Daily Standup — <Weekday, DD Mon YYYY>* · Sprint: <name> (day X/Y)
`<N closed> · <N released> · <N in flight> · <N blocked>`

🚀 *Released*              ← only if released > 0
• <url|[#NN]> <task name> — <one-liner from status_summary>

✅ *Closed today*          ← only if closed > 0 (detected in diff)
• <url|[#NN]> <task name>

🚨 *Blockers*              ← only if blockers > 0
• <url|[#NN]> <blocker.description>

⏳ *Waiting on*            ← only if action_items have external owner
• <owner> — <action> (<url|[#NN]>)

🎯 *Today's focus*         ← top-3 action_items of own team
• <owner>: <action> (<url|[#NN]>)

_Per-task detail + follow-up in thread._
```

**With sprint review** (when `--sprint-review` flag passed and sprint changed):
```
*Daily Standup — <Weekday, DD Mon YYYY>* · 📊 Sprint review
`Previous sprint: <N closed> · <N carried> · <completion>% · New sprint: <N closed> · <N released> · <N in flight> · <N blocked>`

🚀 *Released*              ← only if released > 0
• <url|[#NN]> <task name> — <one-liner from status_summary>

✅ *Closed today*          ← only if closed > 0 (detected in diff)
• <url|[#NN]> <task name>

🚨 *Blockers*              ← only if blockers > 0
• <url|[#NN]> <blocker.description>

⏳ *Waiting on*            ← only if action_items have external owner
• <owner> — <action> (<url|[#NN]>)

🎯 *Today's focus*         ← top-3 action_items of own team
• <owner>: <action> (<url|[#NN]>)

_Sprint review, per-task detail + follow-up in thread._
```

For sprint review metrics: read prev snapshot for completion calculation (closed / (closed + carried) * 100).
Counts: Read from snapshot.tasks, filter by group and released state. Released tasks = `task.released == true`.
Empty sections are skipped. Each line ≤ 120 chars. Reference tasks as `<url|[#NN]>` (Slack mrkdwn link).

## Step 11 — Slack (script, optional)
If the user asked for `--slack` or just for "стендап в слак", re-render the report and
follow-up in Slack mode (with clickable `<url|[#NN]>` links), then post:
```bash
python3 $S/render_report.py "$SNAPSHOT_PATH" \
  ${PREV_SNAPSHOT_EXISTS:+--prev "$PREV_SNAPSHOT_PATH"} --slack \
  > /tmp/standup_report_slack.txt
python3 $S/diff_snapshots.py \
  --today "$SNAPSHOT_PATH" --prev "$PREV_SNAPSHOT_PATH" \
  ${SPRINT_REVIEW:+--sprint-review} --slack \
  > /tmp/standup_followup_slack.txt
if [[ -n "$SPRINT_REVIEW" && -f /tmp/standup_sprint_review.txt ]]; then
  python3 $S/sprint_review.py \
    --today "$SNAPSHOT_PATH" --prev "$PREV_SNAPSHOT_PATH" --slack \
    > /tmp/standup_sprint_review_slack.txt
fi
uv run python $S/slack_threaded.py \
  --main   /tmp/standup_summary.txt \
  --thread /tmp/standup_sprint_review_slack.txt \
  --thread /tmp/standup_report_slack.txt \
  --thread /tmp/standup_followup_slack.txt
```
The summary becomes the parent message; sprint review (if applicable), per-task report,
and follow-up attach as thread replies in that order. Empty / missing files are skipped silently.

If the user did **not** ask for Slack, skip Step 11 — the report is already on screen and
the snapshot is on disk.
