#!/usr/bin/env python3
"""Build the final snapshot.json: skeleton + per-task AI synthesis (parallel).

Reads `$DIR/notion_tasks_with_comments.json` (produced by notion_comments.py),
optionally loads a prior `snapshot.json` to anchor summaries against yesterday,
then calls Claude CLI once per task in N parallel workers (haiku model) to
produce a structured one-line JSON per task: status_summary, action_items,
blocker, release_status. Pre-filters comments to the last few days before the
prompt to economize tokens.

Usage:
    uv run synthesize_snapshot.py --dir snapshots/2026-05-08_morning
    uv run synthesize_snapshot.py --dir snapshots/... --prev-dir snapshots/...
    uv run synthesize_snapshot.py --dir snapshots/... --workers 5 --model haiku
    uv run synthesize_snapshot.py --dir snapshots/... --comment-window-days 4 --stale-days 2
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

RELEASE_STATUSES = ("none", "ready_to_release", "sent_to_release", "released")


def run_claude(prompt: str, model: str, timeout: int = 120) -> str:
    cmd = ["claude", "--print", "--model", model, prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "claude exited non-zero")
    return result.stdout.strip()


def extract_json_obj(text: str) -> dict | None:
    """Pull the first JSON object out of Claude's output (strips fences/wrappers)."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", t, re.DOTALL)
    if m:
        t = m.group(1).strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def parse_url_id(url: str) -> str | None:
    """Notion URL → 32-char UUID without dashes."""
    if not url:
        return None
    m = re.search(r"notion\.so/([a-f0-9]{32})", url.lower())
    return m.group(1) if m else None


def get_state_group(state: str) -> str:
    s = (state or "").lower().strip()
    if s in ("done", "completed"):
        return "done"
    if "review" in s:
        return "review"
    if s == "backlog":
        return "backlog"
    if s == "cancelled":
        return "cancelled"
    if s.startswith("in"):
        return "started"
    return "skipped"


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def find_prior_snapshot_dir(current_dir: str) -> str | None:
    """Find the most recent snapshot dir older than current_dir that has snapshot.json."""
    current_dir = os.path.normpath(current_dir)
    candidates = sorted(glob.glob("snapshots/????-??-??_??????"))
    current_base = os.path.basename(current_dir)
    older = [c for c in candidates if os.path.basename(c) < current_base]
    for c in reversed(older):
        if os.path.exists(os.path.join(c, "snapshot.json")):
            return c
    return None


def load_prior_index(prev_dir: str | None) -> dict[int, dict]:
    if not prev_dir:
        return {}
    path = os.path.join(prev_dir, "snapshot.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    idx = {}
    for t in data.get("tasks", []):
        if t.get("id") is not None:
            idx[t["id"]] = {
                "status_summary": t.get("status_summary"),
                "action_items": t.get("action_items") or [],
                "blocker": t.get("blocker") or {"is_blocker": False, "description": None},
                "release_status": t.get("release_status")
                    or ("released" if t.get("released") else "none"),
            }
    return idx


def filter_recent_comments(comments: list[dict], days: int, now: datetime) -> list[dict]:
    out = []
    cutoff = now.timestamp() - days * 86400
    for c in comments or []:
        dt = parse_dt(c.get("datetime"))
        if dt is None:
            continue
        if dt.timestamp() >= cutoff:
            out.append(c)
    return out


def days_since_newest(comments: list[dict], now: datetime) -> int | None:
    newest = None
    for c in comments or []:
        dt = parse_dt(c.get("datetime"))
        if dt is None:
            continue
        if newest is None or dt > newest:
            newest = dt
    if newest is None:
        return None
    return int((now.timestamp() - newest.timestamp()) // 86400)


def classify_staleness(group: str, days_since: int | None, stale_days: int) -> str:
    if group != "started":
        return "n/a"
    if days_since is None:
        return "dormant"
    if days_since <= stale_days:
        return "active"
    if days_since <= 5:
        return "stale"
    return "dormant"


def strip_html(text: str) -> str:
    """Lightweight clean of HTML/markup found in Notion comment text."""
    if not text:
        return ""
    t = re.sub(r"<mention-user[^/]*/>", "@user", text)
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


def render_comments_block(comments: list[dict], max_chars: int = 1200) -> str:
    if not comments:
        return "none"
    lines = []
    used = 0
    for c in comments:
        dt = c.get("datetime", "")[:10]
        body = strip_html(c.get("text", ""))
        if not body:
            continue
        line = f"[{dt}] {body}"
        if used + len(line) > max_chars:
            line = line[: max(0, max_chars - used)] + "…"
            lines.append(line)
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines) if lines else "none"


def has_value(v) -> bool:
    """True если поле Notion содержит реальное значение (учитывает JSON-encoded массивы)."""
    if v is None or v == "None":
        return False
    if isinstance(v, str):
        s = v.strip()
        if not s or s == "None":
            return False
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return len(parsed) > 0
        except (json.JSONDecodeError, ValueError):
            pass
        return True
    if isinstance(v, list):
        return len(v) > 0
    return bool(v)


def classify_subtask(state: str, blocked_by: bool, action_required_from: bool,
                     has_recent_comments: bool) -> str:
    """4-bucket классификация для агрегации сабтасков под parent'ом."""
    s = (state or "").lower().strip()
    if s in {"done", "completed", "cancelled"}:
        return "done"
    if blocked_by or action_required_from:
        return "blocked"
    if s in {"inbox", "backlog"} and not has_recent_comments:
        return "not_started"
    return "in_progress"


def aggregate_subtasks(subs: list[dict]) -> dict | None:
    """Свернуть список сабтасков в metadata-объект. None если subs пуст."""
    if not subs:
        return None
    counts = {"done": 0, "in_progress": 0, "blocked": 0, "not_started": 0}
    items = []
    for s in subs:
        bucket = classify_subtask(
            s["state"], s["blocked_by"], s["action_required_from"],
            bool(s["recent_comments"]),
        )
        counts[bucket] += 1
        items.append({
            "id": s["id"],
            "name": s["name"],
            "state": s["state"],
            "url": s["url"],
            "bucket": bucket,
        })
    total = sum(counts.values())
    if total == 0:
        return None
    # progress = (done + max(in_progress - blocked, 0) / 2) / total
    nonblocked_in_progress = max(counts["in_progress"] - counts["blocked"], 0)
    percent = round(100 * (counts["done"] + nonblocked_in_progress / 2) / total)
    return {
        "total": total,
        "done": counts["done"],
        "in_progress": counts["in_progress"],
        "blocked": counts["blocked"],
        "not_started": counts["not_started"],
        "percent": percent,
        "items": items,
    }


def _row_to_task(r: dict, url_to_id: dict[str, int]) -> dict:
    """Сырая Notion-строка → плоский task-объект (без skeleton-defaults пока)."""
    url = r.get("url") or ""
    state = r.get("State") or ""

    parent_id = None
    parent_raw = r.get("Parent task")
    if parent_raw and parent_raw != "None":
        try:
            urls = json.loads(parent_raw) if isinstance(parent_raw, str) else parent_raw
            if isinstance(urls, list) and urls:
                p_uuid = parse_url_id(urls[0])
                parent_id = url_to_id.get(p_uuid)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    return {
        "id": r.get("userDefined:ID"),
        "name": r.get("Task name", "") or "",
        "url": url,
        "state": state,
        "group": get_state_group(state),
        "parent_id": parent_id,
        "description": (r.get("Short Description") or "").strip() or None,
        "blocked_by": has_value(r.get("Blocked by")),
        "action_required_from": has_value(r.get("Action required from")),
        "recent_comments": r.get("recent_comments") or [],
    }


def build_skeleton(rows: list[dict]) -> list[dict]:
    """Возвращает только parent-задачи (top-level) с агрегатом по сабтаскам.

    Top-level == `parent_id is None` или родитель отсутствует в текущем датасете
    (orphan-сабы лечатся как parent). Сабтаски не попадают в `tasks[]`,
    но их данные сворачиваются в parent.subtasks.
    """
    url_to_id: dict[str, int] = {}
    for r in rows:
        uid = r.get("userDefined:ID")
        url = r.get("url")
        if uid is not None and url:
            page_uuid = parse_url_id(url)
            if page_uuid:
                url_to_id[page_uuid] = uid

    all_tasks = [_row_to_task(r, url_to_id) for r in rows]
    by_id = {t["id"]: t for t in all_tasks if t["id"] is not None}
    valid_parent_ids = set(by_id.keys())

    # Сабы группируются по parent (только если parent в датасете).
    subs_by_parent: dict[int, list[dict]] = {}
    parents: list[dict] = []
    for t in all_tasks:
        pid = t["parent_id"]
        if pid is not None and pid in valid_parent_ids:
            subs_by_parent.setdefault(pid, []).append(t)
        else:
            parents.append(t)

    # Каждому parent добавляем skeleton-defaults и агрегат сабов.
    out = []
    for p in parents:
        p["status_summary"] = None
        p["action_items"] = []
        p["blocker"] = {"is_blocker": False, "description": None}
        p["release_status"] = "none"
        p["staleness"] = "n/a"
        p["days_since_last_comment"] = None
        p["subtasks"] = aggregate_subtasks(subs_by_parent.get(p["id"], []))
        out.append(p)
    return out


def render_subtasks_block(sub: dict | None) -> str:
    if not sub:
        return ""
    lines = [
        f"Подзадачи (всего {sub['total']}, прогресс {sub['percent']}%): "
        f"{sub['done']} закрыто, {sub['in_progress']} в работе, "
        f"{sub['blocked']} блокер, {sub['not_started']} не начата.",
        "Список:",
    ]
    bucket_ru = {
        "done": "✓",
        "in_progress": "→",
        "blocked": "⛔",
        "not_started": "·",
    }
    for it in sub["items"][:15]:
        marker = bucket_ru.get(it["bucket"], "·")
        lines.append(f"  {marker} [{it['state']}] [#{it['id']}] {it['name']}")
    if len(sub["items"]) > 15:
        lines.append(f"  … и ещё {len(sub['items']) - 15}")
    return "\n".join(lines)


def build_prompt(task: dict, recent_window: list[dict], prior: dict | None,
                 window_days: int) -> str:
    name = task["name"]
    state = task["state"] or "(no state)"
    if prior and prior.get("status_summary"):
        prior_summary = prior["status_summary"]
    else:
        prior_summary = "—"
    if prior and prior.get("action_items"):
        prior_actions = "; ".join(
            f"{ai.get('owner','')}: {ai.get('action','')}".strip(": ")
            for ai in prior["action_items"][:5]
        ) or "—"
    else:
        prior_actions = "—"
    comments_block = render_comments_block(recent_window)
    subtasks_block = render_subtasks_block(task.get("subtasks"))
    subtasks_section = f"\n{subtasks_block}\n" if subtasks_block else ""

    return (
        "Сводка задачи для дейли-стендапа. Верни ТОЛЬКО однострочный JSON, без комментариев и без обрамления.\n\n"
        f"Задача: {name}\n"
        f"Статус: {state}\n"
        f"Вчерашняя сводка: {prior_summary}\n"
        f"Открытые акшен-айтемы со вчера: {prior_actions}\n"
        f"{subtasks_section}\n"
        f"Свежие комментарии (последние {window_days} дн., сначала новые):\n"
        f"{comments_block}\n\n"
        'Верни JSON: {"status_summary": "ОДНО короткое предложение по-русски — фокус на том, что НОВОГО относительно вчера и на прогрессе подзадач (если они есть); если ничего нового — кратко напиши об этом", '
        '"action_items": [{"owner": "имя из комментариев / @user / ?", "action": "коротко по-русски"}], '
        '"blocker": {"is_blocker": bool, "description": "коротко по-русски или null"}, '
        '"release_status": "none | ready_to_release | sent_to_release | released"}\n\n'
        "ПРАВИЛА для owner в action_items: бери ИМЯ из комментариев (если есть: «Лиза», «Гриша», «V. Budruev» и т.п.) ИЛИ роль из комментариев («frontend», «BE»). "
        "Если в комментариях упомянут @user но имя не понятно — пиши `@user` или `?`. "
        "НЕ ВЫДУМЫВАЙ имена/роли — никаких 'автор', 'разработчик', 'исполнитель', 'команда', 'team', 'task owner', 'PM/требования', 'rear it', 'unassigned' и т.п. "
        "Если совсем непонятно кто исполнитель — ставь `?`.\n\n"
        "Релиз-статус (релизами занимается другая команда — мы передаём и ждём):\n"
        '- "ready_to_release": работа закончена, готова к передаче (напр. "готово к релизу", "PR смержен, ждём деплой")\n'
        '- "sent_to_release": уже отдано релиз-команде, ждём (напр. "отдали в релиз", "в очереди на релиз", "ждём infra/devops")\n'
        '- "released": подтверждённый прод (напр. "зарелижено", "задеплоено", "shipped to prod", "в проде")\n'
        '- "none": не подходит ни под что выше\n'
        "ВСЕ текстовые поля (status_summary, action_items.action, blocker.description) пиши по-русски, даже если комментарии на английском."
    )


def synthesize_one(task: dict, recent_window: list[dict], prior: dict | None,
                   model: str, window_days: int) -> dict:
    prompt = build_prompt(task, recent_window, prior, window_days)
    try:
        text = run_claude(prompt, model)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        return {"_error": f"claude: {e}"}
    obj = extract_json_obj(text)
    if not obj:
        return {"_error": f"unparseable: {text[:160]}"}
    out = {}
    if isinstance(obj.get("status_summary"), str):
        out["status_summary"] = obj["status_summary"].strip() or None
    if isinstance(obj.get("action_items"), list):
        clean = []
        for ai in obj["action_items"]:
            if isinstance(ai, dict):
                owner = (ai.get("owner") or "").strip()
                action = (ai.get("action") or "").strip()
                if action:
                    clean.append({"owner": owner, "action": action})
        out["action_items"] = clean
    blk = obj.get("blocker")
    if isinstance(blk, dict):
        out["blocker"] = {
            "is_blocker": bool(blk.get("is_blocker")),
            "description": (blk.get("description") or None),
        }
    rs = obj.get("release_status")
    if isinstance(rs, str) and rs in RELEASE_STATUSES:
        out["release_status"] = rs
    return out


def merge_synthesis(task: dict, syn: dict) -> None:
    """In-place merge — only overwrite fields actually present in synthesis."""
    if "status_summary" in syn:
        task["status_summary"] = syn["status_summary"]
    if "action_items" in syn:
        task["action_items"] = syn["action_items"]
    if "blocker" in syn:
        task["blocker"] = syn["blocker"]
    if "release_status" in syn:
        task["release_status"] = syn["release_status"]


def should_call_ai(task: dict, recent_window: list[dict], prior: dict | None) -> bool:
    """Skip AI for low-signal tasks to economize tokens."""
    if recent_window:
        return True
    if task["group"] == "started":
        return True
    if prior and prior.get("status_summary"):
        return True
    sub = task.get("subtasks")
    if sub and (sub.get("in_progress") or sub.get("blocked")):
        # парент сам тихий, но в сабах есть движение — суммируем через них
        return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="current snapshot dir")
    ap.add_argument("--prev-dir", default=None,
                    help="prior snapshot dir (default: auto-find latest older one)")
    ap.add_argument("--no-prev", action="store_true",
                    help="ignore any prior snapshot")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--comment-window-days", type=int, default=4)
    ap.add_argument("--stale-days", type=int, default=2)
    ap.add_argument("--model", default="haiku", choices=["haiku", "sonnet", "opus"])
    args = ap.parse_args()

    snap_dir = args.dir
    tasks_path = os.path.join(snap_dir, "notion_tasks_with_comments.json")
    if not os.path.exists(tasks_path):
        print(f"Not found: {tasks_path}", file=sys.stderr)
        sys.exit(1)

    with open(tasks_path) as f:
        data = json.load(f)
    rows = data.get("results", data) if isinstance(data, dict) else data

    # Sprint info
    sprint = {"name": "Unknown", "start": None, "end": None}
    sprints_path = os.path.join(snap_dir, "notion_sprints.json")
    if os.path.exists(sprints_path):
        try:
            with open(sprints_path) as f:
                sd = json.load(f)
            cur = sd.get("current") or {}
            sprint = {
                "name": cur.get("sprint_name") or "Unknown",
                "start": cur.get("start"),
                "end": cur.get("end"),
            }
        except (json.JSONDecodeError, OSError) as e:
            print(f"warn: could not read {sprints_path}: {e}", file=sys.stderr)

    # Resolve prior dir
    prev_dir = None if args.no_prev else (args.prev_dir or find_prior_snapshot_dir(snap_dir))
    prior_index = load_prior_index(prev_dir)
    if prev_dir:
        print(f"prior: {prev_dir} ({len(prior_index)} task summaries)", file=sys.stderr)
    else:
        print("prior: (none — first run or --no-prev)", file=sys.stderr)

    # Skeleton — только parent-задачи; subtask'и — в metadata.
    tasks = build_skeleton(rows)
    now = datetime.now(timezone.utc)
    for t in tasks:
        ds = days_since_newest(t["recent_comments"], now)
        t["days_since_last_comment"] = ds
        t["staleness"] = classify_staleness(t["group"], ds, args.stale_days)

    # Decide which tasks need AI
    windows = {
        t["id"]: filter_recent_comments(t["recent_comments"], args.comment_window_days, now)
        for t in tasks
    }
    targets = [t for t in tasks if t["id"] is not None
               and should_call_ai(t, windows[t["id"]], prior_index.get(t["id"]))]

    print(f"tasks: {len(tasks)} total, {len(targets)} → AI synthesis "
          f"(workers={args.workers}, model={args.model})", file=sys.stderr)

    ai_calls = 0
    errors: list[str] = []
    if targets:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    synthesize_one,
                    t,
                    windows[t["id"]],
                    prior_index.get(t["id"]),
                    args.model,
                    args.comment_window_days,
                ): t for t in targets
            }
            done = 0
            for fut in as_completed(futures):
                t = futures[fut]
                done += 1
                try:
                    syn = fut.result()
                except Exception as e:
                    print(f"  [{done}/{len(targets)}] #{t['id']} — exception: {e}",
                          file=sys.stderr)
                    errors.append(f"#{t['id']}: {e}")
                    continue
                if "_error" in syn:
                    print(f"  [{done}/{len(targets)}] #{t['id']} — {syn['_error']}",
                          file=sys.stderr)
                    errors.append(f"#{t['id']}: {syn['_error']}")
                else:
                    merge_synthesis(t, syn)
                    ai_calls += 1
                    summary = (t.get("status_summary") or "")[:60]
                    print(f"  [{done}/{len(targets)}] #{t['id']} — {summary}",
                          file=sys.stderr)

    snapshot = {
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "snapshot_dir": snap_dir,
            "prior_snapshot_dir": prev_dir,
            "model": args.model,
            "ai_calls": ai_calls,
            "comment_window_days": args.comment_window_days,
            "stale_days": args.stale_days,
            "errors": errors,
        },
        "date": date.today().isoformat(),
        "sprint": sprint,
        "tasks": tasks,
    }

    out_path = os.path.join(snap_dir, "snapshot.json")
    with open(out_path, "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    print(f"✓ {len(tasks)} tasks, {ai_calls} AI synthesised → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
