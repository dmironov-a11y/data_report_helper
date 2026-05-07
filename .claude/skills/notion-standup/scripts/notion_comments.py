#!/usr/bin/env python3
"""Fetch Notion task comments via Claude + MCP, save raw output.

Usage:
    uv run notion_comments.py page_id1 page_id2                           # → snapshots/YYYY-MM-DD_HHMMSS/
    uv run notion_comments.py --dir snapshots/2026-05-07_demo page_id1    # save to specific snapshot dir
    uv run notion_comments.py --model sonnet page_id1                      # use Sonnet instead of Haiku
    uv run notion_comments.py --include-all-blocks page_id1                # include child block comments
    uv run notion_comments.py --include-resolved page_id1                  # include resolved discussions
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime


def run_claude(prompt: str, model: str) -> str:
    """Run Claude with --print and return stdout."""
    cmd = ["claude", "--print", "--model", model, prompt]
    result = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=180,
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


def fetch_comments(page_ids: list, model: str, include_all_blocks: bool, include_resolved: bool) -> str:
    """Use notion-get-comments MCP tool to fetch comments."""
    page_ids_list = "\n".join(page_ids)
    prompt = (
        'Use the notion-get-comments MCP tool to fetch comments for each of these page IDs.\n'
        'Fetch in parallel.\n\n'
        f'Page IDs:\n{page_ids_list}\n\n'
        f'Options: include_all_blocks={str(include_all_blocks).lower()}, include_resolved={str(include_resolved).lower()}\n\n'
        'Return the COMPLETE raw output from the MCP tool with ALL comment data and fields unchanged.\n'
        'Do not filter, summarize, or modify the response.'
    )
    text = run_claude(prompt, model)
    return extract_file_if_saved(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page_ids", nargs="+", help="page IDs to fetch comments for (required)")
    ap.add_argument("--dir", default=None, help="snapshot directory (default: snapshots/YYYY-MM-DD_HHMMSS)")
    ap.add_argument("--out", default=None, help="output file (overrides --dir)")
    ap.add_argument("--model", default="haiku", choices=["haiku", "sonnet", "opus"],
                    help="Claude model (default: haiku)")
    ap.add_argument("--include-all-blocks", action="store_true",
                    help="include discussions on child blocks (default: page-level only)")
    ap.add_argument("--include-resolved", action="store_true",
                    help="include resolved discussions (default: unresolved only)")

    args = ap.parse_args()

    # Prepare output path
    if args.out:
        out_path = args.out
    elif args.dir:
        out_path = os.path.join(args.dir, "notion_comments.json")
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_path = f"snapshots/{timestamp}/notion_comments.json"

    # Create output dir
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir)

    print(f"Fetching comments for {len(args.page_ids)} task(s)...", file=sys.stderr)

    # Fetch comments
    text = fetch_comments(args.page_ids, args.model, args.include_all_blocks, args.include_resolved)

    # Save raw output
    with open(out_path, "w") as f:
        f.write(text)

    # Count comment objects (works for both JSON and XML formats)
    count = 0
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for page_id, comments in data.items():
                if isinstance(comments, list):
                    count += len(comments)
    except:
        # Try XML format
        count = text.count('<comment')

    print(f"✓ {count} comments → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
