#!/usr/bin/env python3
"""Fetch Notion tasks via Claude + MCP, save raw output.

Two modes:
  --sql (default):   Use notion-query-data-sources MCP tool with explicit SQL
  --prompt:          Use natural language prompt for MCP tool

Usage:
    uv run notion/tasks.py                                   # SQL mode → snapshots/YYYY-MM-DD_HHMMSS/
    uv run notion/tasks.py --prompt                          # Prompt mode
    uv run notion/tasks.py --dir snapshots/2026-05-07_demo   # save to specific snapshot dir
    uv run notion/tasks.py --model sonnet                    # use Sonnet instead of Haiku
    uv run notion/tasks.py --sprint current                  # filter by current sprint (default)
    uv run notion/tasks.py --sprint all                      # fetch all tasks (no sprint filter)

Sprint filtering requires notion_sprints.json in the snapshot dir (run notion/sprints.py first).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

TASKS_DB = "collection://35650979-0d9a-80f6-92ed-000b93238f83"


def run_claude(prompt: str, model: str) -> str:
    """Run Claude with --print and return stdout."""
    cmd = [
        "claude", "--print", "--model", model,
        "--allowedTools", "mcp__claude_ai_Notion__notion-query-data-sources,Read",
    ]
    result = subprocess.run(
        cmd,
        input=prompt, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        print(f"Claude error:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def extract_file_if_saved(text: str) -> str:
    """If Claude saved output to file, read and return it."""
    m = re.search(r'(/Users/.+?/tool-results/.+?\.txt)', text)
    if m:
        file_path = m.group(1)
        try:
            with open(file_path, "r") as f:
                return f.read().strip()
        except FileNotFoundError:
            print(f"Could not read file: {file_path}", file=sys.stderr)
            sys.exit(1)
    return text


def extract_json(text: str) -> str:
    """Strip Claude preamble and code fences, return raw JSON string."""
    m = re.search(r'```(?:json)?\n(.*?)\n```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    for char in ('{', '['):
        idx = text.find(char)
        if idx != -1:
            return text[idx:].strip()
    return text.strip()


def load_sprint_url(snapshot_dir: str, sprint_key: str) -> str | None:
    """Read sprint URL from notion_sprints.json in the snapshot dir."""
    if not snapshot_dir:
        return None
    sprints_path = os.path.join(snapshot_dir, "notion_sprints.json")
    if not os.path.exists(sprints_path):
        return None
    with open(sprints_path) as f:
        sprints = json.load(f)
    sprint = sprints.get(sprint_key)
    return sprint.get("url") if sprint else None


def sql_mode(model: str, sprint_url: str | None = None, extra_urls: list[str] | None = None) -> str:
    """Use notion-query-data-sources MCP tool with explicit SQL query.

    If extra_urls is provided, fetch done tasks from those sprint URLs too and merge results.
    """
    if sprint_url:
        query = f'SELECT * FROM "{TASKS_DB}" WHERE "Sprint" LIKE \'%{sprint_url}%\''
    else:
        query = f'SELECT * FROM "{TASKS_DB}"'

    extra_queries = []
    for eu in (extra_urls or []):
        extra_queries.append(
            f'SELECT * FROM "{TASKS_DB}" WHERE "Sprint" LIKE \'%{eu}%\''
            f" AND \"State\" IN ('Done', 'To Release', 'Cancelled', 'Monitoring')"
        )

    if extra_queries:
        all_queries = "\n".join(
            [f"Query 1: {query}"] + [f"Query {i+2}: {q}" for i, q in enumerate(extra_queries)]
        )
        prompt = (
            'Use the notion-query-data-sources MCP tool to run each query below in sequence.\n'
            'Merge all result arrays into a single JSON array (deduplicate by "id").\n'
            f'{all_queries}\n\n'
            'Output ONLY the merged raw JSON array, nothing else.\n'
            'No preamble, no explanation, no code fences, no markdown.'
        )
    else:
        prompt = (
            'Use the notion-query-data-sources MCP tool with these exact parameters:\n'
            f'- mode: "sql"\n'
            f'- data_source_urls: ["{TASKS_DB}"]\n'
            f'- query: {query}\n\n'
            'Output ONLY the raw JSON response from the MCP tool, nothing else.\n'
            'No preamble, no explanation, no code fences, no markdown.\n'
            'Every character must come from the MCP tool output.\n'
            'Do not truncate, abbreviate, or modify any data.'
        )
    text = run_claude(prompt, model)
    return extract_file_if_saved(text)


def prompt_mode(model: str) -> str:
    """Use natural language prompt for MCP tool."""
    prompt = (
        'Use the notion-query-data-sources MCP tool to query all tasks:\n'
        f'SELECT * FROM "{TASKS_DB}"\n\n'
        'Output ONLY the raw JSON response from the MCP tool, nothing else.\n'
        'No preamble, no explanation, no code fences, no markdown.\n'
        'Every character must come from the MCP tool output.\n'
        'Do not truncate, abbreviate, or modify any data.'
    )
    text = run_claude(prompt, model)
    return extract_file_if_saved(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="snapshot directory (default: snapshots/YYYY-MM-DD_HHMMSS)")
    ap.add_argument("--out", default=None, help="output file (overrides --dir)")
    ap.add_argument("--model", default="haiku", choices=["haiku", "sonnet", "opus"],
                    help="Claude model (default: haiku)")
    ap.add_argument("--sprint", default="current", choices=["current", "next", "last", "all"],
                    help="sprint filter: current (default), next, last, or all (no filter)")

    ap.add_argument("--include-last-sprint-done", action="store_true", default=False,
                    help="Also fetch done tasks from last sprint and merge (useful at sprint transitions)")

    mode_group = ap.add_mutually_exclusive_group()
    mode_group.add_argument("--sql", action="store_true", dest="sql_mode", default=True,
                           help="SQL mode: explicit notion-query-data-sources MCP (default)")
    mode_group.add_argument("--prompt", action="store_true", dest="prompt_mode",
                           help="Prompt mode: natural language MCP instruction")
    args = ap.parse_args()

    # Prepare output path
    if args.out:
        out_path = args.out
    elif args.dir:
        out_path = os.path.join(args.dir, "notion_tasks.json")
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_path = f"snapshots/{timestamp}/notion_tasks.json"

    # Create output dir
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    # Resolve sprint filter
    sprint_url = None
    snapshot_dir = args.dir or out_dir
    if args.sprint != "all":
        sprint_url = load_sprint_url(snapshot_dir, args.sprint)
        if sprint_url:
            sprint_name = args.sprint
            print(f"Sprint filter: {sprint_name} ({sprint_url})", file=sys.stderr)
        else:
            print(f"No notion_sprints.json found in {snapshot_dir}, fetching all tasks", file=sys.stderr)

    # Resolve extra sprint URLs for last-sprint done tasks
    extra_urls = None
    if args.include_last_sprint_done and not args.prompt_mode and args.sprint not in ("all", "last"):
        last_url = load_sprint_url(snapshot_dir, "last")
        if last_url:
            extra_urls = [last_url]
            print(f"Also fetching done tasks from last sprint ({last_url})", file=sys.stderr)

    # Fetch data based on mode
    if args.prompt_mode:
        text = prompt_mode(args.model)
        mode_label = "prompt"
    else:  # SQL mode (default)
        text = sql_mode(args.model, sprint_url=sprint_url, extra_urls=extra_urls)
        mode_label = "sql"

    # Strip preamble/code fences so the file is clean JSON
    text = extract_json(text)

    # Save raw output
    with open(out_path, "w") as f:
        f.write(text)

    # Count entries
    count = 0
    try:
        data = json.loads(text)
        if isinstance(data, list):
            count = len(data)
        elif isinstance(data, dict) and "results" in data:
            count = len(data.get("results", []))
    except:
        count = text.count('{"id"')

    print(f"✓ {count} tasks [{mode_label}] → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
