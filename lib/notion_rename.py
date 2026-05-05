import sys

import requests

from lib.config import NOTION_DATABASE_ID
from lib.notion import (
    get_page_relation_ids,
    get_page_title,
    get_title_prop_name,
    query_database,
    update_page_title,
)
from lib.rename import _is_good_format, ai_rename

# Default Notion property names — adjust if your database uses different names.
# Sprint:      Select property that holds the sprint/cycle value (e.g. "Sprint 12")
# Parent item: Relation property that links a sub-task to its parent task
_DEFAULT_SPRINT_PROP = "Sprint"
_DEFAULT_PARENT_PROP = "Parent item"


def run_notion_rename_mode(
    sprint_value: str = "",
    sprint_prop: str = _DEFAULT_SPRINT_PROP,
    parent_prop: str = _DEFAULT_PARENT_PROP,
    dry_run: bool = False,
    database_id: str = "",
) -> None:
    """Fetch pages from Notion and propose AI renames.

    sprint_value: Filter to pages where sprint_prop equals this value.
                  Pass empty string to process all pages in the database.
    sprint_prop:  Name of the Select property that holds the sprint value.
    parent_prop:  Name of the Relation property linking a sub-task to its parent.
    dry_run:      Show proposals without writing to Notion.
    """
    db_id = database_id or NOTION_DATABASE_ID

    filter_body = None
    if sprint_value:
        filter_body = {
            "property": sprint_prop,
            "select": {"equals": sprint_value},
        }

    label = f"sprint '{sprint_value}'" if sprint_value else "all pages"
    print(f"\nFetching pages ({label}) from Notion...", file=sys.stderr)

    title_prop = get_title_prop_name(db_id)
    pages = query_database(filter_body=filter_body, database_id=db_id)

    if not pages:
        print("No pages found.", file=sys.stderr)
        return

    sorted_pages = sorted(pages, key=lambda p: p.get("created_time", ""))
    needs_rename = [p for p in sorted_pages if not _is_good_format(get_page_title(p, title_prop))]
    skipped = len(sorted_pages) - len(needs_rename)
    print(
        f"\nGenerating rename proposals for {len(needs_rename)} pages "
        f"({skipped} already correct, skipped)...\n"
    )

    proposals: list[tuple[dict, str]] = []
    for page in sorted_pages:
        original = get_page_title(page, title_prop)
        if _is_good_format(original):
            proposals.append((page, original))
            continue
        print(f"  {original}", file=sys.stderr)
        subtask = bool(get_page_relation_ids(page, parent_prop))
        renamed = ai_rename(original, is_subtask=subtask)
        proposals.append((page, renamed))

    changed = [(p, r) for p, r in proposals if r != get_page_title(p, title_prop)]

    if changed:
        print("\n" + "─" * 80)
        print(f"{'ORIGINAL':<45} {'PROPOSED'}")
        print("─" * 80)
        for page, renamed in changed:
            original = get_page_title(page, title_prop)
            orig_short = original[:43] + ".." if len(original) > 45 else original
            print(f"{orig_short:<45} {renamed}")
        print("─" * 80)

    print(f"\n{len(changed)} of {len(proposals)} titles would change.")

    if dry_run:
        print("\n[dry-run] No changes applied. Remove --dry-run to apply.")
        return

    if not changed:
        print("Nothing to update.")
        return

    confirm = input(f"\nApply {len(changed)} renames to Notion? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    print("\nApplying renames...")
    for page, renamed in changed:
        page_id = page["id"]
        original = get_page_title(page, title_prop)
        try:
            update_page_title(page_id, title_prop, renamed)
            print(f"  ✓ {renamed}")
        except requests.HTTPError as exc:
            print(f"  ✗ {original}: {exc}", file=sys.stderr)

    print(f"\nDone. {len(changed)} pages renamed.")
