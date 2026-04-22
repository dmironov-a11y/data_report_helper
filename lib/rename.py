import re
import subprocess
import sys
from datetime import datetime, timezone

import requests

from lib.config import PLANE_WORKSPACE_SLUG
from lib.plane import get_cycles, get_cycle_issues, get_states, plane_patch

VALID_TAGS = {"Chart", "Feature", "Fix", "BE", "FE", "Research", "Doc", "QA", "Infra"}
VALID_AREAS = {
    # UI tabs
    "Payments Tab", "Declines Tab", "3DS Tab", "BIN Tab", "Tax Tab", "Billing Tab",
    # Pipelines
    "Payments Pipeline", "Subscriptions Pipeline", "Refunds Pipeline",
    # Infra / general / cross-cutting
    "Tinybird", "Metrics", "General", "Paynext UI",
}
# For [Infra] tasks, area is a specific tool/system (Tinybird, Slack, GitHub, MCP, etc.) — never "Infra"

_TAG_RE = re.compile(r'^\[([A-Za-z]+)\]')
# Matches fully correct format: [ValidTag] ValidArea(, ValidArea)*: Description (min 3 chars)
_TAGS_PATTERN = '|'.join(re.escape(t) for t in VALID_TAGS)
_AREAS_PATTERN = '|'.join(re.escape(a) for a in sorted(VALID_AREAS, key=len, reverse=True))
_GOOD_FORMAT_RE = re.compile(
    rf'^\[(?:{_TAGS_PATTERN})\] '
    rf'(?:{_AREAS_PATTERN})(?:, (?:{_AREAS_PATTERN}))*: .{{3,}}$'
)

RENAME_SYSTEM_PROMPT = """You rename tasks for a payment analytics dashboard called Paynext.

Naming convention (ALWAYS follow this exactly):
  [Type] Area: Short Description

Type tags (always present, Title Case):
  [Chart]    — new or updated chart/dashboard UI
  [Feature]  — business feature (filters, drill-down, new tab, new capability)
  [Fix]      — bug or incorrect logic/calculation
  [BE]       — Tinybird endpoint, pipeline, data transformation, refactor
  [FE]       — frontend implementation (React components, UI wiring, prod deploy)
  [Research] — investigation, benchmark, validation with BQ/stakeholders
  [Doc]      — documentation, tooltips, descriptions, specs
  [QA]       — testing, comparison with BigQuery, load testing
  [Infra]    — infrastructure (workspace setup, alerting, MCP, monitoring)

Area — ALWAYS required, pick from these canonical names EXACTLY as written:
  UI tabs:   Payments Tab, Declines Tab, 3DS Tab, BIN Tab, Tax Tab, Billing Tab
  Pipelines: Payments Pipeline, Subscriptions Pipeline, Refunds Pipeline
  Other:     Tinybird, Metrics, General, Paynext UI
  For [Infra] tasks: use the specific tool/system as area (e.g. Tinybird, Slack, GitHub, MCP) — never use "Infra" as area.
  Multi-area example: [Chart] Payments Tab, Declines Tab: Add Failed Charts

Rules:
- Type tag always present, Title Case: [Chart] not [chart] or [CHART]
- Area is ALWAYS present — every title must have an area between the tag and the colon
- Use General for top-level tasks that span both frontend (Paynext UI) and backend (Tinybird) — e.g. full features, new dimensions, new tabs that require both BE and FE work
- Use Paynext UI for tasks that are purely frontend (React, layout, interactions, rendering)
- Use Tinybird for tasks that are purely backend (endpoints, pipelines, data transformation)
- Strong verb + object after colon, concise, Title Case, no filler words
- No arrows (=>), no dashes as separators, no all-caps except acronyms (BIN, MCP, SQL, DRY, BQ)
- Max ~80 chars total
- [Chart] PRIORITY: if the task is explicitly about creating, adding, or editing a specific chart or dashboard — always use [Chart], never [Feature] or [FE]. Area must be the specific tab where the chart lives (e.g. Payments Tab, Declines Tab)
- [FE] vs [Chart]: use [FE] for implementing/wiring UI code; use [Chart] for the chart spec/metric/design work
- Return ONLY the renamed title, nothing else."""


def _extract_existing_tag(title: str) -> str | None:
    """Return the canonical tag name if title starts with a valid [Type] tag, else None."""
    m = _TAG_RE.match(title)
    if m:
        raw = m.group(1)
        for tag in VALID_TAGS:
            if tag.lower() == raw.lower():
                return tag
    return None


def _is_good_format(title: str) -> bool:
    """Return True if title already matches [ValidTag] ValidArea(s): Description perfectly."""
    return bool(_GOOD_FORMAT_RE.match(title))


def ai_rename(title: str, is_subtask: bool = False, description: str = "") -> str:
    """Call local claude CLI to rename a task title."""
    task_hint = (
        "This is a SUB-TASK (has a parent task). Pick the most specific type: [BE], [FE], [Chart], [Fix], [QA], [Doc]."
        if is_subtask else
        "This is a TOP-LEVEL task (no parent). Lean toward [Feature] or [Research] unless it is clearly a bug ([Fix]), infra ([Infra]), or purely technical ([BE])."
    )

    existing_tag = _extract_existing_tag(title)
    tag_hint = (
        f'\nIMPORTANT: The original title already has a valid type tag [{existing_tag}]. '
        f'You MUST keep [{existing_tag}] as the type tag. Only fix the area and description.'
        if existing_tag else ""
    )

    desc_section = f'\n\nDescription (first 300 chars):\n{description[:300]}' if description else ""
    prompt = f'Rename this task title following the convention.\n\n{task_hint}{tag_hint}{desc_section}\n\nOriginal: "{title}"\n\nRenamed:'
    for attempt in range(3):
        try:
            result = subprocess.run(
                ["claude", "--print", "--system-prompt", RENAME_SYSTEM_PROMPT, prompt],
                capture_output=True, text=True, timeout=60,
            )
            renamed = result.stdout.strip().strip("`\"'")
            return renamed if renamed else title
        except subprocess.TimeoutExpired:
            if attempt < 2:
                print(f"  [warn] claude timeout, retrying ({attempt + 2}/3)...", file=sys.stderr)
            else:
                print(f"  [warn] claude timed out 3 times, keeping original", file=sys.stderr)
    return title


def run_rename_mode(projects: list[dict], dry_run: bool, cycle_filter: str = "next") -> None:
    """Fetch issues from the selected cycle(s) and propose AI renames.

    cycle_filter: "next" (default) | "current" | "both"
    """
    label_map = {"next": "next cycle", "current": "current cycle", "both": "current + next cycles"}
    print(f"\nFetching issues from {label_map.get(cycle_filter, cycle_filter)} for rename...", file=sys.stderr)

    for project in projects:
        project_id = project["id"]
        cycles = get_cycles(project_id)
        states = get_states(project_id)

        now_iso = datetime.now(timezone.utc).isoformat()

        def cycle_status(c: dict) -> str:
            s = c.get("start_date") or ""
            e = c.get("end_date") or ""
            st = c.get("status")
            if st:
                return st
            if s and e:
                if s <= now_iso <= e:
                    return "CURRENT"
                elif s > now_iso:
                    return "UPCOMING"
                else:
                    return "COMPLETED"
            return "DRAFT"

        current = next((c for c in cycles if cycle_status(c) == "CURRENT"), None)
        upcoming = [c for c in cycles if cycle_status(c) == "UPCOMING"]
        nxt = min(upcoming, key=lambda c: c.get("start_date") or "", default=None)

        selected_cycles: list[dict] = []
        if cycle_filter == "current":
            selected_cycles = [c for c in [current] if c]
        elif cycle_filter == "next":
            selected_cycles = [c for c in [nxt] if c]
        else:  # both
            selected_cycles = [c for c in [current, nxt] if c]

        all_issues: dict[str, dict] = {}
        for cycle in selected_cycles:
            cycle_name = cycle.get("name", cycle["id"])
            print(f"  Cycle: {cycle_name}", file=sys.stderr)
            for issue in get_cycle_issues(project_id, cycle["id"]):
                all_issues[issue["id"]] = issue

        if not all_issues:
            print(f"No issues found in {label_map.get(cycle_filter, cycle_filter)}.", file=sys.stderr)
            return

        identifier = project.get("identifier", "??")
        proposals: list[tuple[dict, str]] = []

        sorted_issues = sorted(all_issues.values(), key=lambda i: i.get("sequence_id", 0))
        needs_rename = [i for i in sorted_issues if not _is_good_format(i.get("name", ""))]
        skipped = len(sorted_issues) - len(needs_rename)
        print(f"\nGenerating rename proposals for {len(needs_rename)} issues ({skipped} already correct, skipped)...\n")
        for issue in sorted_issues:
            original = issue.get("name", "")
            seq = issue.get("sequence_id", "?")
            if _is_good_format(original):
                proposals.append((issue, original))
                continue
            print(f"  {identifier}-{seq}  {original}", file=sys.stderr)
            is_subtask = bool(issue.get("parent"))
            description = issue.get("description_stripped", "") or ""
            renamed = ai_rename(original, is_subtask=is_subtask, description=description)
            proposals.append((issue, renamed))

        changed = [(i, r) for i, r in proposals if r != i.get("name", "")]

        if changed:
            print("\n" + "─" * 72)
            print(f"{'TICKET':<12} {'ORIGINAL':<40} {'PROPOSED'}")
            print("─" * 72)
            for issue, renamed in changed:
                seq = issue.get("sequence_id", "?")
                original = issue.get("name", "")
                orig_short = original[:38] + ".." if len(original) > 40 else original
                print(f"{identifier}-{seq:<8} {orig_short:<40} {renamed}")
            print("─" * 72)

        print(f"\n{len(changed)} of {len(proposals)} titles would change.")

        if dry_run:
            print("\n[dry-run] No changes applied. Remove --dry-run to apply.")
            return

        if not changed:
            print("Nothing to update.")
            return

        confirm = input(f"\nApply {len(changed)} renames to Plane? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

        print("\nApplying renames...")
        for issue, renamed in changed:
            seq = issue.get("sequence_id", "?")
            try:
                plane_patch(
                    f"/workspaces/{PLANE_WORKSPACE_SLUG}/projects/{project_id}/work-items/{issue['id']}/",
                    {"name": renamed},
                )
                print(f"  ✓ {identifier}-{seq}: {renamed}")
            except requests.HTTPError as exc:
                print(f"  ✗ {identifier}-{seq}: {exc}", file=sys.stderr)

        print(f"\nDone. {len(changed)} issues renamed.")
