from lib.plane import get_issue_by_id, get_issue_identifier, build_issue_url


def _build_cycle_message(
    cycle: dict | None,
    issues: list[dict],
    label: str,
    project: dict,
    states: dict[str, dict],
    members: dict[str, str],
) -> str:
    """Build a single Slack message for one cycle, showing issues as a parent→children tree."""
    lines = []

    if not cycle:
        return f"*{label}*\nNo cycle found."

    def fmt_date(d: str | None) -> str:
        return d[:10] if d else "?"

    def render_issue(issue: dict, indent: str = "  ", prefix: str = "") -> str:
        identifier = get_issue_identifier(project, issue)
        title = issue.get("name", issue.get("title", "Untitled"))
        issue_id = issue.get("id", "")
        url = build_issue_url(project["id"], issue_id)
        state_id = issue.get("state")
        state = states.get(state_id, {})
        state_name = state.get("name", "?")
        assignee_ids = issue.get("assignees", [])
        assignee_names = [
            members.get(a.get("id") if isinstance(a, dict) else a, "?")
            for a in assignee_ids
        ]
        assignees_str = f" — _{', '.join(assignee_names)}_" if assignee_names else ""
        return f"{indent}• {prefix}<{url}|{identifier}> — {title} `{state_name}`{assignees_str}"

    # Build lookup by id
    issues_by_id: dict[str, dict] = {i["id"]: i for i in issues}

    # Fetch missing parents (those referenced but not in cycle)
    parent_ids = {i["parent"] for i in issues if i.get("parent") and i["parent"] not in issues_by_id}
    for pid in parent_ids:
        try:
            parent_issue = get_issue_by_id(project["id"], pid)
            issues_by_id[pid] = parent_issue
        except Exception:
            pass

    # Build children map
    children: dict[str, list[dict]] = {}
    for issue in issues:
        p = issue.get("parent")
        if p:
            children.setdefault(p, []).append(issue)

    cycle_ids = {i["id"] for i in issues}
    external_parent_ids = {pid for pid in issues_by_id if pid not in cycle_ids}

    # Count ALL cycle issues by their own state group (for accurate headers)
    group_counts: dict[str, int] = {}
    for issue in issues:
        state_id = issue.get("state")
        group = states.get(state_id, {}).get("group", "other")
        group_counts[group] = group_counts.get(group, 0) + 1

    # Build by_state grouped by each cycle issue's OWN state.
    # Each entry is a list of render nodes:
    #   ["issue", issue_dict]                        — plain cycle issue (no external parent)
    #   ["wrapper", parent_dict, [child_dicts]]       — external parent + its cycle children in this group
    by_state: dict[str, list[list]] = {}
    for issue in issues:
        parent_id = issue.get("parent")
        state_id = issue.get("state")
        group = states.get(state_id, {}).get("group", "other")

        if parent_id and parent_id in cycle_ids:
            # Parent is in cycle → rendered as child under parent, skip here
            continue
        elif parent_id and parent_id in external_parent_ids:
            # External parent → add to a wrapper node in this group
            entries = by_state.setdefault(group, [])
            found = next((e for e in entries if e[0] == "wrapper" and e[1]["id"] == parent_id), None)
            if found:
                found[2].append(issue)
            else:
                entries.append(["wrapper", issues_by_id[parent_id], [issue]])
        else:
            # No parent or unknown parent → plain entry
            by_state.setdefault(group, []).append(["issue", issue])

    name = cycle.get("name", "Unnamed")
    start = fmt_date(cycle.get("start_date"))
    end = fmt_date(cycle.get("end_date"))
    total = cycle.get("total_issues", len(issues))
    completed = cycle.get("completed_issues", 0)
    lines.append(f"*{label}: {name}* ({start} → {end})")
    lines.append(f"Progress: {completed}/{total} issues completed\n")

    order = ["completed", "started", "unstarted", "backlog", "cancelled"]
    group_emoji = {
        "completed": ":white_check_mark:",
        "started": ":arrows_counterclockwise:",
        "unstarted": ":white_circle:",
        "backlog": ":black_circle:",
        "cancelled": ":x:",
    }
    for group in order:
        entries = by_state.get(group, [])
        count = group_counts.get(group, 0)
        if not entries and count == 0:
            continue
        emoji = group_emoji.get(group, ":small_blue_diamond:")
        lines.append(f"{emoji} *{group.capitalize()}* ({count})")

        def entry_sort_key(e: list) -> int:
            if e[0] == "issue":
                return e[1].get("sequence_id", 0)
            else:
                return min(c.get("sequence_id", 0) for c in e[2]) if e[2] else 0

        for entry in sorted(entries, key=entry_sort_key):
            if entry[0] == "issue":
                issue = entry[1]
                lines.append(render_issue(issue, indent="  "))
                for child in sorted(children.get(issue["id"], []), key=lambda i: i.get("sequence_id", 0)):
                    lines.append(render_issue(child, indent="      ↳ "))
            else:
                _, parent, group_children = entry
                lines.append(render_issue(parent, indent="  ", prefix="🏷️ "))
                for child in sorted(group_children, key=lambda i: i.get("sequence_id", 0)):
                    lines.append(render_issue(child, indent="      ↳ "))
        lines.append("")

    return "\n".join(lines)


def build_cycle_messages(
    current_cycle: dict | None,
    current_issues: list[dict],
    next_cycle: dict | None,
    next_issues: list[dict],
    project: dict,
    states: dict[str, dict],
    members: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Return (current_msg, next_msg) as two separate Slack messages."""
    members = members or {}
    current_msg = _build_cycle_message(
        current_cycle, current_issues, ":large_green_circle: Current Cycle",
        project, states, members,
    )
    next_msg = _build_cycle_message(
        next_cycle, next_issues, ":large_yellow_circle: Next Cycle",
        project, states, members,
    )
    return current_msg, next_msg
