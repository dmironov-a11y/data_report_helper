#!/usr/bin/env python3
"""Fetch all Notion tasks via Claude + MCP, save raw output.

Two modes:
  --sql (default):   Use notion-query-data-sources MCP tool with explicit SQL
  --prompt:          Use natural language prompt for MCP tool

Usage:
    uv run notion_tasks.py                                   # SQL mode → snapshots/YYYY-MM-DD_HHMMSS/
    uv run notion_tasks.py --prompt                          # Prompt mode
    uv run notion_tasks.py --dir snapshots/2026-05-07_demo   # save to specific snapshot dir
    uv run notion_tasks.py --model sonnet                    # use Sonnet instead of Haiku
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
    cmd = ["claude", "--print", "--model", model, prompt]
    result = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=120,
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


def sql_mode(model: str) -> str:
    """Use notion-query-data-sources MCP tool with explicit SQL query."""
    prompt = (
        'Use the notion-query-data-sources MCP tool with these exact parameters:\n'
        f'- mode: "sql"\n'
        f'- data_source_urls: ["{TASKS_DB}"]\n'
        f'- query: SELECT * FROM "{TASKS_DB}"\n\n'
        'Return the COMPLETE raw output from the MCP tool with ALL fields and data unchanged.\n'
        'Do not filter, summarize, or modify the response.'
    )
    text = run_claude(prompt, model)
    return extract_file_if_saved(text)


def prompt_mode(model: str) -> str:
    """Use natural language prompt for MCP tool."""
    prompt = (
        'Use the notion-query-data-sources MCP tool to query all tasks:\n'
        f'SELECT * FROM "{TASKS_DB}"\n\n'
        'Return the COMPLETE raw output from the MCP tool with ALL fields and data unchanged.\n'
        'Do not filter, summarize, or modify the response.'
    )
    text = run_claude(prompt, model)
    return extract_file_if_saved(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="snapshot directory (default: snapshots/YYYY-MM-DD_HHMMSS)")
    ap.add_argument("--out", default=None, help="output file (overrides --dir)")
    ap.add_argument("--model", default="haiku", choices=["haiku", "sonnet", "opus"],
                    help="Claude model (default: haiku)")

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

    # Fetch data based on mode
    if args.prompt_mode:
        text = prompt_mode(args.model)
        mode_label = "prompt"
    else:  # SQL mode (default)
        text = sql_mode(args.model)
        mode_label = "sql"

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
