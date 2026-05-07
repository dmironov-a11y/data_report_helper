#!/usr/bin/env python3
"""Fetch current sprint tasks from Notion — minimal Claude invocation, no synthesis.

Usage:
    python3 get_tasks.py                  # JSON to stdout
    python3 get_tasks.py --out tasks.json # save to file
    python3 get_tasks.py --sprint <uuid>  # skip sprint lookup (use known UUID)
"""
import argparse
import json
import re
import subprocess
import sys

TASKS_DB = "collection://35650979-0d9a-80f6-92ed-000b93238f83"
SPRINTS_DB = "collection://35750979-0d9a-80a1-8b0f-000bf8fd37f8"


def claude_sql(sql: str) -> list[dict]:
    out = subprocess.run(
        ["claude", "--print", sql],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        sys.exit(1)
    text = out.stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\[.*\]', text, re.DOTALL)
        if m:
            return json.loads(m.group())
        print(f"Could not parse JSON:\n{text}", file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--sprint", default=None, help="sprint UUID (skip auto-detect)")
    args = ap.parse_args()

    if args.sprint:
        sprint_uuid = args.sprint
    else:
        rows = claude_sql(
            f'SELECT url FROM "{SPRINTS_DB}" WHERE "Sprint status" = \'Current\' LIMIT 1\n\n'
            f'Output ONLY a JSON array, no explanation.'
        )
        if not rows:
            print("No current sprint", file=sys.stderr)
            sys.exit(1)
        sprint_uuid = rows[0]["url"].rstrip("/").split("/")[-1]

    tasks = claude_sql(
        f'SELECT * FROM "{TASKS_DB}" '
        f'WHERE "Sprint" LIKE \'%{sprint_uuid}%\' AND "State" != \'Cancelled\' '
        f'ORDER BY "userDefined:ID"\n\n'
        f'Output ONLY a JSON array, no explanation.'
    )

    out_text = json.dumps(tasks, indent=2, ensure_ascii=False)

    if args.out:
        with open(args.out, "w") as f:
            f.write(out_text)
        print(f"✓ {len(tasks)} tasks → {args.out}", file=sys.stderr)
    else:
        print(out_text)


if __name__ == "__main__":
    main()
