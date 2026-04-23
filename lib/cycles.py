from lib.plane import get_issue_by_id, get_all_project_issues, get_issue_identifier, build_issue_url


def _build_cycle_message(
    cycle: dict | None,
    issues: list[dict],
    label: str,
    project: dict,
    states: dict[str, dict],
    members: dict[str, str],
) -> str:
    """Build a single Slack message for one cycle: Epics block + flat state groups."""
    lines = []

    if not cycle:
        return f"*{label}*\nNo cycle found."

    def fmt_date(d: str | None) -> str:
        return d[:10] if d else "?"

    def render_issue(issue: dict, indent: str = "  ") -> str:
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
        return f"{indent}• <{url}|{identifier}> — {title} `{state_name}`{assignees_str}"

    # Build lookup by id from cycle issues
    issues_by_id: dict[str, dict] = {i["id"]: i for i in issues}

    # Fetch all project issues once — used to build full children map for epics
    all_project_issues = get_all_project_issues(project["id"])
    all_by_id: dict[str, dict] = {i["id"]: i for i in all_project_issues}

    # Add missing parents (external) to issues_by_id
    parent_ids = {i["parent"] for i in issues if i.get("parent") and i["parent"] not in issues_by_id}
    for pid in parent_ids:
        if pid in all_by_id:
            issues_by_id[pid] = all_by_id[pid]
        else:
            try:
                issues_by_id[pid] = get_issue_by_id(project["id"], pid)
            except Exception:
                pass

    # Build full children map from all project issues (not just cycle issues)
    all_children: dict[str, list[dict]] = {}
    for issue in all_project_issues:
        p = issue.get("parent")
        if p:
            all_children.setdefault(p, []).append(issue)

    # Build cycle-only children map (for flat list rendering reference)
    children: dict[str, list[dict]] = {}
    for issue in issues:
        p = issue.get("parent")
        if p:
            children.setdefault(p, []).append(issue)

    cycle_ids = {i["id"] for i in issues}

    # In-cycle parents: cycle issues that have children also in cycle
    in_cycle_parent_ids = {i["parent"] for i in issues if i.get("parent") and i["parent"] in cycle_ids}

    # External parents: fetched from API, not in cycle themselves
    external_parent_ids = {pid for pid in issues_by_id if pid not in cycle_ids}

    # All epics = union
    epic_ids = in_cycle_parent_ids | external_parent_ids

    # Epic issue objects sorted by sequence_id
    epics = [issues_by_id[eid] for eid in epic_ids if eid in issues_by_id]
    epics.sort(key=lambda i: i.get("sequence_id", 0))

    def subtask_summary(parent_id: str) -> str:
        subs = all_children.get(parent_id, [])
        done = sum(1 for s in subs if states.get(s.get("state"), {}).get("group") == "completed")
        started = sum(1 for s in subs if states.get(s.get("state"), {}).get("group") == "started")
        unstarted = sum(1 for s in subs if states.get(s.get("state"), {}).get("group") in ("unstarted", "backlog"))
        n = len(subs)
        word = "subtask" if n == 1 else "subtasks"
        return f"    ↳ {done}/{n} {word}: {done} done, {started} started, {unstarted} unstarted"

    name = cycle.get("name", "Unnamed")
    start = fmt_date(cycle.get("start_date"))
    end = fmt_date(cycle.get("end_date"))
    total = cycle.get("total_issues", len(issues))
    completed = cycle.get("completed_issues", 0)
    lines.append(f"*{label}: {name}* ({start} → {end})")
    lines.append(f"Progress: {completed}/{total} issues completed\n")

    # Epics block
    if epics:
        lines.append(f":dart: *Epics* ({len(epics)})")
        for epic in epics:
            lines.append(render_issue(epic, indent="  "))
            lines.append(subtask_summary(epic["id"]))
        lines.append("")

    # Flat state groups — only non-epic cycle issues
    flat_issues = [i for i in issues if i["id"] not in epic_ids]

    by_state: dict[str, list[dict]] = {}
    for issue in flat_issues:
        group = states.get(issue.get("state"), {}).get("group", "other")
        by_state.setdefault(group, []).append(issue)

    order = ["completed", "started", "unstarted", "backlog", "cancelled"]
    group_emoji = {
        "completed": ":white_check_mark:",
        "started": ":arrows_counterclockwise:",
        "unstarted": ":white_circle:",
        "backlog": ":black_circle:",
        "cancelled": ":x:",
    }
    for group in order:
        group_issues = sorted(by_state.get(group, []), key=lambda i: i.get("sequence_id", 0))
        if not group_issues:
            continue
        emoji = group_emoji.get(group, ":small_blue_diamond:")
        lines.append(f"{emoji} *{group.capitalize()}* ({len(group_issues)})")
        for issue in group_issues:
            lines.append(render_issue(issue, indent="  "))
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
