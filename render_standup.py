#!/usr/bin/env python3
"""Рендерит standup-отчёт (main + thread) из snapshot.json — на русском.

Создаёт два файла в формате Slack mrkdwn:
- standup_main.txt    — короткий фоллоуап с группами и фокусом дня
- standup_thread.txt  — детали по каждой задаче

Usage:
    uv run render_standup.py --dir snapshots/2026-05-08_morning
    uv run render_standup.py --dir ... --prev-dir ...
    uv run render_standup.py --dir ... --slack
"""
import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Полный порядок групп. Все категории — каждая задача попадает РОВНО в одну.
ORDER = (
    "in_progress",
    "blocked",
    "released",
    "ready_to_release",
    "sent_to_release",
    "done_today",
    "stale",
    "to_refine",
    "archived",
)

# В main выводятся только эти группы (всё, что требует внимания сегодня).
MAIN_GROUPS = ("in_progress", "blocked",
               "ready_to_release", "sent_to_release", "released")
# Остальное уходит в thread.
THREAD_GROUPS = tuple(g for g in ORDER if g not in MAIN_GROUPS)

LABEL = {
    "in_progress": "🔄 *В работе*",
    "blocked": "⛔ *Заблокированы / ожидают*",
    "released": "🚀 *Зарелижено*",
    "ready_to_release": "📦 *Готово к релизу*",
    "sent_to_release": "📤 *Отдано в релиз* (ждём релиз-команду)",
    "done_today": "✅ *Закрыто сегодня*",
    "stale": "⚠️ *Висит без апдейтов*",
    "to_refine": "📥 *Не запущено / на ревизию*",
    "archived": "🗄 *Закрыто ранее*",
}

# Состояния, которые считаются «активной фазой» (не cancelled / done / inbox / backlog).
ACTIVE_STATES = {"todo", "in progress", "in review", "paused", "in integration"}
NOT_STARTED_STATES = {"inbox", "backlog"}
DONE_STATES = {"done", "completed", "cancelled"}

WEEKDAY_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTH_RU = ["янв", "фев", "мар", "апр", "мая", "июн",
            "июл", "авг", "сен", "окт", "ноя", "дек"]


def fmt_date_ru(iso: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return f"{WEEKDAY_RU[d.weekday()]}, {d.day} {MONTH_RU[d.month - 1]} {d.year}"
    except (ValueError, TypeError):
        return iso or ""


def lbl(t: dict) -> str:
    """Slack mrkdwn-ссылка: <url|[#ID] short name>."""
    name = t.get("name") or "(без названия)"
    short = name if len(name) <= 70 else name[:67] + "…"
    tag = f"[#{t['id']}] {short}" if t.get("id") is not None else short
    url = t.get("url")
    return f"<{url}|{tag}>" if url else tag


def find_prior_snapshot_dir(current_dir: str) -> str | None:
    current_base = os.path.basename(os.path.normpath(current_dir))
    candidates = sorted(glob.glob("snapshots/????-??-??_??????"))
    older = [c for c in candidates if os.path.basename(c) < current_base]
    for c in reversed(older):
        if os.path.exists(os.path.join(c, "snapshot.json")):
            return c
    return None


def load_snapshot(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def state_bucket(state: str) -> str:
    s = (state or "").lower().strip()
    if s in DONE_STATES:
        return "done"
    if s in NOT_STARTED_STATES:
        return "not_started"
    if s in ACTIVE_STATES or s.startswith("in "):
        return "active"
    return "active"  # default: treat unknown states as active


def categorize(t: dict, prior_by_id: dict[int, dict], have_prior: bool,
               stale_days: int) -> str:
    """Один бакет на задачу — гарантированно non-None.
    Приоритет: blocked > release > done_today > done/cancelled (archived) >
    active/inbox-логика. Любая задача попадает РОВНО в один бакет.
    """
    # Заблокированы / ожидают: парсится только по структурным полям из Notion.
    if t.get("blocked_by") or t.get("action_required_from"):
        return "blocked"

    rs = t.get("release_status") or "none"
    pid = t.get("id")
    p = prior_by_id.get(pid) or {}
    p_rs = p.get("release_status") or ("released" if p.get("released") else "none")

    if rs == "released":
        # без prior считаем что это новость дня
        return "released" if (not have_prior or p_rs != "released") else "archived"
    if rs == "ready_to_release":
        return "ready_to_release"
    if rs == "sent_to_release":
        return "sent_to_release"

    bucket = state_bucket(t.get("state") or "")
    p_bucket = state_bucket(p.get("state") or "") if p else None

    if bucket == "done":
        if have_prior and p_bucket and p_bucket != "done":
            return "done_today"
        return "archived"  # давно закрытые / cancelled — отдельный бакет

    has_recent = bool(t.get("recent_comments"))
    has_desc = bool((t.get("description") or "").strip())
    days = t.get("days_since_last_comment")
    fresh = isinstance(days, int) and days <= stale_days

    if bucket == "active":
        return "in_progress" if fresh else "stale"

    # bucket == "not_started" (inbox / backlog)
    if has_recent:
        return "in_progress"  # обсуждается
    return "to_refine"  # без комментариев — нужно описать/запустить


def build_focus_line(grouped: dict[str, list[dict]]) -> str:
    """1 строка: топ-3 актуальных задачи (заблокированные → in_progress с акшенами → release)."""
    picked: list[dict] = []
    seen_ids: set[int] = set()

    def take(items: list[dict], limit: int = 3) -> None:
        for t in items:
            if len(picked) >= limit:
                return
            if t.get("id") in seen_ids:
                continue
            picked.append(t)
            seen_ids.add(t["id"])

    take(grouped.get("blocked", []))
    if len(picked) < 3:
        with_actions = [t for t in grouped.get("in_progress", []) if t.get("action_items")]
        take(with_actions)
    if len(picked) < 3:
        take(grouped.get("ready_to_release", []) + grouped.get("sent_to_release", []))
    if len(picked) < 3:
        take(grouped.get("in_progress", []))

    if not picked:
        return "🎯 *Фокус дня:* нечего выделить — день для разбора и планирования."
    return "🎯 *Фокус дня:* " + " · ".join(lbl(t) for t in picked[:3])


def render_action_items(t: dict, indent: str) -> list[str]:
    out = []
    for ai in t.get("action_items") or []:
        owner = (ai.get("owner") or "Team").strip() or "Team"
        action = (ai.get("action") or "").strip()
        if action:
            out.append(f"{indent}→ *{owner}*: {action}")
    return out


def render_subtask_progress(sub: dict | None) -> str:
    """Инлайновая строка прогресса по сабтаскам: '(63%, 4/7 закрыто, 2 в работе, 1 блокер, 0 не начата)'.
    Пустая строка если сабов нет.
    """
    if not sub or not sub.get("total"):
        return ""
    parts = [f"{sub['done']}/{sub['total']} закрыто"]
    if sub["in_progress"]:
        parts.append(f"{sub['in_progress']} в работе")
    if sub["blocked"]:
        parts.append(f"{sub['blocked']} блокер")
    if sub["not_started"]:
        parts.append(f"{sub['not_started']} не начата")
    return f"_({sub['percent']}%, {', '.join(parts)})_"


def sprint_progress_line(grouped: dict[str, list[dict]]) -> str:
    """Прогресс спринта по парент-задачам, та же формула что для сабтасков."""
    done = len(grouped.get("done_today") or []) + len(grouped.get("archived") or [])
    in_progress = (
        len(grouped.get("in_progress") or [])
        + len(grouped.get("ready_to_release") or [])
        + len(grouped.get("sent_to_release") or [])
        + len(grouped.get("released") or [])
        + len(grouped.get("stale") or [])
    )
    blocked = len(grouped.get("blocked") or [])
    not_started = len(grouped.get("to_refine") or [])
    total = done + in_progress + blocked + not_started
    if total == 0:
        return ""
    nonblocked_in_progress = max(in_progress - blocked, 0)
    percent = round(100 * (done + nonblocked_in_progress / 2) / total)
    parts = [f"{done}/{total} закрыто"]
    if in_progress:
        parts.append(f"{in_progress} в работе")
    if blocked:
        parts.append(f"{blocked} блокер")
    if not_started:
        parts.append(f"{not_started} не начата")
    return f"Прогресс: {percent}% ({', '.join(parts)})"


def categorize_all(snapshot: dict, prior: dict | None, stale_days: int
                   ) -> tuple[dict[str, list[dict]], list[int]]:
    """Распределить ВСЕ задачи. Каждая попадает ровно в один бакет.
    Возвращает (grouped, missing_ids) — missing_ids должен быть пустой.
    """
    tasks = snapshot.get("tasks") or []
    prior_by_id = {t["id"]: t for t in (prior or {}).get("tasks") or []
                   if t.get("id") is not None}
    have_prior = prior is not None

    grouped: dict[str, list[dict]] = {k: [] for k in ORDER}
    placed: set[int] = set()
    for t in tasks:
        tid = t.get("id")
        if tid is None:
            continue
        cat = categorize(t, prior_by_id, have_prior, stale_days)
        if cat not in grouped:
            grouped.setdefault(cat, []).append(t)
        else:
            grouped[cat].append(t)
        placed.add(tid)

    all_ids = [t["id"] for t in tasks if t.get("id") is not None]
    missing = [i for i in all_ids if i not in placed]
    return grouped, missing


def build_main(snapshot: dict, prior: dict | None, stale_days: int,
               grouped: dict[str, list[dict]]) -> str:
    sprint = snapshot.get("sprint") or {}
    date_iso = snapshot.get("date") or ""
    header_top = f"*Daily Standup — {fmt_date_ru(date_iso)}*"
    sprint_line = sprint.get("name") or "Спринт"
    if sprint.get("start") or sprint.get("end"):
        sprint_line = f"{sprint_line} ({sprint.get('start') or '?'} → {sprint.get('end') or '?'})"
    progress_line = sprint_progress_line(grouped)
    if progress_line:
        sprint_line = f"{sprint_line}  ·  {progress_line}"

    out = [header_top, sprint_line, "", build_focus_line(grouped), ""]

    any_section = False
    for cat in MAIN_GROUPS:
        items = grouped.get(cat) or []
        if not items:
            continue
        any_section = True
        out.append(LABEL[cat])
        for t in items:
            line = f"• {lbl(t)}"
            summary = (t.get("status_summary") or "").strip()
            if cat == "blocked":
                reasons = []
                if t.get("blocked_by"):
                    reasons.append("блок-задача")
                if t.get("action_required_from"):
                    reasons.append("ждём ответа")
                tail = " / ".join(reasons) if reasons else "ожидает"
                if summary:
                    line += f" — {summary} _({tail})_"
                else:
                    line += f" — _{tail}_"
            elif summary:
                line += f" — {summary}"
            sub_progress = render_subtask_progress(t.get("subtasks"))
            if sub_progress:
                line += f"  {sub_progress}"
            out.append(line)
            out.extend(render_action_items(t, indent="    "))
        out.append("")

    if not any_section:
        out.append("_Нет актуальных задач в работе, релизе или заблокированных._")

    return "\n".join(out).rstrip() + "\n"


def build_thread(snapshot: dict, grouped: dict[str, list[dict]]) -> str:
    """Per-task детали для треда: всё, что не вошло в main. Все задачи здесь parent-уровня."""

    def render_task(t: dict, compact: bool = False) -> list[str]:
        head = f"*{lbl(t)}*  _{t.get('state') or '?'}_"
        sub_progress = render_subtask_progress(t.get("subtasks"))
        if sub_progress:
            head += f"  {sub_progress}"
        lines = [head]
        if not compact:
            summary = (t.get("status_summary") or "").strip()
            if summary:
                lines.append(f"  · {summary}")
            for ai in t.get("action_items") or []:
                owner = (ai.get("owner") or "Team").strip() or "Team"
                action = (ai.get("action") or "").strip()
                if action:
                    lines.append(f"  → *{owner}*: {action}")
            ds = t.get("days_since_last_comment")
            if isinstance(ds, int):
                lines.append(f"  · последний коммент: {ds}д назад")
        return lines

    out: list[str] = []
    for cat in THREAD_GROUPS:
        items = grouped.get(cat) or []
        if not items:
            continue
        if out:
            out.append("")
        out.append(LABEL[cat])
        compact = cat in ("to_refine", "archived")
        for t in items:
            out += render_task(t, compact=compact)

    if not out:
        out.append("_Нет дополнительных деталей._")
    return "\n".join(out).rstrip() + "\n"


def post_slack(main_path: str, thread_path: str) -> None:
    repo_root = Path(__file__).resolve().parent
    script = repo_root / "slack_threaded.py"
    if not script.exists():
        print(f"slack_threaded.py не найден: {script}", file=sys.stderr)
        sys.exit(1)
    cmd = ["uv", "run", str(script), "--main", main_path, "--thread", thread_path]
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--prev-dir", default=None)
    ap.add_argument("--no-prev", action="store_true")
    ap.add_argument("--stale-days", type=int, default=2,
                    help="порог для in_progress vs stale (по умолчанию 2)")
    ap.add_argument("--out-main", default=None)
    ap.add_argument("--out-thread", default=None)
    ap.add_argument("--slack", action="store_true")
    args = ap.parse_args()

    snap_path = os.path.join(args.dir, "snapshot.json")
    snapshot = load_snapshot(snap_path)
    if snapshot is None:
        print(f"snapshot.json не найден: {snap_path}", file=sys.stderr)
        sys.exit(1)

    prev_dir = None if args.no_prev else (args.prev_dir or find_prior_snapshot_dir(args.dir))
    prior = load_snapshot(os.path.join(prev_dir, "snapshot.json")) if prev_dir else None

    grouped, missing = categorize_all(snapshot, prior, args.stale_days)
    total = sum(len(v) for v in grouped.values())
    counts = ", ".join(f"{k}={len(v)}" for k, v in grouped.items() if v)
    print(f"buckets: {counts} (total={total})", file=sys.stderr)
    if missing:
        print(f"⚠ unbucketed task ids: {missing}", file=sys.stderr)
        sys.exit(2)

    main_text = build_main(snapshot, prior, args.stale_days, grouped)
    thread_text = build_thread(snapshot, grouped)

    out_main = args.out_main or os.path.join(args.dir, "standup_main.txt")
    out_thread = args.out_thread or os.path.join(args.dir, "standup_thread.txt")
    Path(out_main).write_text(main_text)
    Path(out_thread).write_text(thread_text)

    print(f"✓ {out_main}", file=sys.stderr)
    print(f"✓ {out_thread}", file=sys.stderr)
    if prev_dir:
        print(f"prior: {prev_dir}", file=sys.stderr)

    if args.slack:
        post_slack(out_main, out_thread)


if __name__ == "__main__":
    main()
