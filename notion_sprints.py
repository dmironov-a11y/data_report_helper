#!/usr/bin/env python3
"""Fetch Notion sprint info (last/current/next) via Claude + MCP, save to JSON.

Usage:
    uv run notion_sprints.py                                    # save to snapshots/YYYY-MM-DD_HHMMSS/
    uv run notion_sprints.py --dir snapshots/2026-05-07_demo   # specific snapshot dir
    uv run notion_sprints.py --model sonnet
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

SPRINTS_DB = "collection://35750979-0d9a-80a1-8b0f-000bf8fd37f8"


def run_claude(prompt: str, model: str) -> str:
    cmd = ["claude", "--print", "--model", model, prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"Claude error:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def extract_file_if_saved(text: str) -> str:
    m = re.search(r'(/Users/.+?/tool-results/.+?\.txt)', text)
    if m:
        file_path = m.group(1)
        try:
            with open(file_path) as f:
                return f.read().strip()
        except FileNotFoundError:
            print(f"Could not read file: {file_path}", file=sys.stderr)
            sys.exit(1)
    return text


def fetch_sprints(model: str) -> str:
    query = (
        f'SELECT url, "Sprint name", "Sprint status", "Sprint ID", '
        f'"date:Dates:start", "date:Dates:end" '
        f'FROM "{SPRINTS_DB}" '
        f"WHERE \"Sprint status\" IN ('Last', 'Current', 'Next') "
        f'ORDER BY "Sprint ID"'
    )
    prompt = (
        'Use the notion-query-data-sources MCP tool with these exact parameters:\n'
        f'- mode: "sql"\n'
        f'- data_source_urls: ["{SPRINTS_DB}"]\n'
        f'- query: {query}\n\n'
        'Return the COMPLETE raw output from the MCP tool with ALL fields and data unchanged.\n'
        'Do not filter, summarize, or modify the response.'
    )
    text = run_claude(prompt, model)
    return extract_file_if_saved(text)


def strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```[^\n]*\n', '', text)
        text = re.sub(r'\n```$', '', text.rstrip())
    return text.strip()


def parse_sprints(text: str) -> dict:
    text = strip_code_fence(text)
    try:
        rows = json.loads(text)
        if isinstance(rows, dict) and "results" in rows:
            rows = rows["results"]
    except json.JSONDecodeError:
        print(f"Failed to parse sprints JSON:\n{text[:500]}", file=sys.stderr)
        sys.exit(1)

    result = {}
    for row in rows:
        status = (row.get("Sprint status") or "").lower()
        if status in ("last", "current", "next"):
            result[status] = {
                "url": row.get("url"),
                "sprint_name": row.get("Sprint name"),
                "sprint_id": row.get("Sprint ID"),
                "start": row.get("date:Dates:start"),
                "end": row.get("date:Dates:end"),
            }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="snapshot directory (default: snapshots/YYYY-MM-DD_HHMMSS)")
    ap.add_argument("--out", default=None, help="output file (overrides --dir)")
    ap.add_argument("--model", default="haiku", choices=["haiku", "sonnet", "opus"])
    args = ap.parse_args()

    if args.out:
        out_path = args.out
    elif args.dir:
        out_path = os.path.join(args.dir, "notion_sprints.json")
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_path = f"snapshots/{timestamp}/notion_sprints.json"

    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    raw = fetch_sprints(args.model)
    sprints = parse_sprints(raw)

    with open(out_path, "w") as f:
        json.dump(sprints, f, indent=2)

    print(f"✓ {len(sprints)} sprints → {out_path}", file=sys.stderr)
    for key in ("last", "current", "next"):
        if key in sprints:
            s = sprints[key]
            print(f"  {key}: {s['sprint_name']} (ID {s['sprint_id']})", file=sys.stderr)


if __name__ == "__main__":
    main()
