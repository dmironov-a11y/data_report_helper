#!/usr/bin/env python3
"""Build the initial standup snapshot JSON from raw Notion task rows.

Usage:
    python3 build_snapshot.py \
        --report-date YYYY-MM-DD \
        --sprint '{"name":"...","start":"...","end":"..."}' \
        --out standups/standup_YYYY-MM-DD.json
    < raw_rows.json

stdin: a JSON list of task rows from notion-query-data-sources (SELECT *).
The script handles parent_id resolution from "Parent task" and group
classification from "State". All synthesis fields are written empty so that
later steps can fill them in place.
"""
import argparse
import json
import re
import sys
from pathlib import Path

URL_RE = re.compile(r"https://www\.notion\.so/[A-Za-z0-9-]+")

IN_PROGRESS_STATES = {
    "In Progress",
    "In Review",
    "Ready for Integration",
    "In Integration",
    "Paused",
}


def classify(state: str) -> str:
    if state == "Done":
        return "done"
    if state in IN_PROGRESS_STATES:
        return "in_progress"
    return "skipped"


def normalize_url(u: str) -> str:
    return u.replace("-", "").lower().strip()


def parent_url(field) -> str | None:
    if not field:
        return None
    if isinstance(field, list):
        return field[0] if field else None
    if isinstance(field, str):
        try:
            arr = json.loads(field)
            if isinstance(arr, list) and arr:
                return arr[0] if isinstance(arr[0], str) else None
            if isinstance(arr, str):
                return arr
        except Exception:
            m = URL_RE.search(field)
            if m:
                return m.group(0)
    return None


def task_name(row: dict) -> str | None:
    for key in ("Task name", "Your Request / Task name", "Name", "title"):
        if key in row and row[key]:
            return row[key]
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-date", required=True)
    ap.add_argument("--sprint", required=True, help='JSON: {"name","start","end"}')
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = json.load(sys.stdin)
    sprint = json.loads(args.sprint)

    url_to_id: dict[str, int] = {}
    for r in rows:
        u = r.get("url")
        tid = r.get("userDefined:ID")
        if u and tid is not None:
            url_to_id[normalize_url(u)] = tid

    tasks = []
    for r in rows:
        state = (r.get("State") or "").strip()
        if state == "Cancelled":
            continue
        purl = parent_url(r.get("Parent task"))
        pid = url_to_id.get(normalize_url(purl)) if purl else None
        tasks.append(
            {
                "id": r.get("userDefined:ID"),
                "name": task_name(r),
                "url": r.get("url"),
                "state": state,
                "group": classify(state),
                "parent_id": pid,
                "status_summary": None,
                "recent_comments": [],
                "action_items": [],
                "blocker": {"is_blocker": False, "description": None},
                "released": False,
            }
        )

    tasks.sort(key=lambda t: (t["id"] is None, t["id"] or 0))

    out = {"date": args.report_date, "sprint": sprint, "tasks": tasks}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(str(out_path))


if __name__ == "__main__":
    main()
