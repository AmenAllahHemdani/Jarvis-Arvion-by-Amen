# memory/behavior.py
# JARVIS ARVION — behaviour learning
#
# Logs what the user actually DOES (which apps, when), mines the log for
# habits — fully locally, no LLM calls — and turns confirmed habits into
# saved routines. Privacy: everything stays in memory/*.jsonl on disk.

import json
import time
import statistics
from datetime import datetime
from itertools import combinations
from pathlib import Path
from threading import Lock

BASE_DIR  = Path(__file__).resolve().parent.parent
LOG_PATH  = BASE_DIR / "memory" / "behavior_log.jsonl"
SUGG_PATH = BASE_DIR / "memory" / "behavior_suggestions.json"

MAX_LOG_LINES   = 4000
MAX_AGE_DAYS    = 45
SESSION_GAP_MIN = 10     # app opens within 10 min = one "session"
MIN_DAYS        = 3      # habit = seen on at least this many distinct days

_lock = Lock()

# which tool calls are worth remembering, and what their "label" is
_TRACKED = {
    "open_app":          lambda a: f"open_app:{(a.get('app_name') or '').strip().lower()}",
    "youtube_video":     lambda a: "youtube:play" if (a.get("action", "play") == "play") else None,
    "computer_settings": lambda a: (f"settings:{a.get('action')}"
                                    if a.get("action") in ("play_pause", "dark_mode") else None),
}


def log_event(tool: str, args: dict) -> None:
    """Append one behaviour event. Cheap, silent, never raises."""
    try:
        fn = _TRACKED.get(tool)
        if not fn:
            return
        label = fn(args or {})
        if not label or label.endswith(":"):
            return
        now = datetime.now()
        rec = {"ts": time.time(), "hour": now.hour + now.minute / 60.0,
               "day": now.strftime("%Y-%m-%d"), "label": label}
        with _lock:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _load_events() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    events = []
    with _lock:
        lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LOG_LINES:          # prune on read
            lines = lines[-MAX_LOG_LINES:]
            LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        try:
            rec = json.loads(line)
            if rec.get("ts", 0) >= cutoff:
                events.append(rec)
        except Exception:
            continue
    return events


def _app_sessions(events: list[dict]) -> list[dict]:
    """Group open_app events into per-day sessions of apps opened together."""
    opens = sorted((e for e in events if e["label"].startswith("open_app:")),
                   key=lambda e: e["ts"])
    sessions = []
    for e in opens:
        app = e["label"].split(":", 1)[1]
        if (sessions and e["day"] == sessions[-1]["day"]
                and e["ts"] - sessions[-1]["last_ts"] <= SESSION_GAP_MIN * 60):
            sessions[-1]["apps"].add(app)
            sessions[-1]["last_ts"] = e["ts"]
        else:
            sessions.append({"day": e["day"], "hour": e["hour"],
                             "apps": {app}, "last_ts": e["ts"]})
    return sessions


def mine_patterns() -> list[dict]:
    """Find app groups opened together on several distinct days.
    Returns suggestions sorted by strength (days, group size)."""
    sessions = _app_sessions(_load_events())

    group_days:  dict[frozenset, set]  = {}
    group_hours: dict[frozenset, list] = {}
    for s in sessions:
        apps = sorted(s["apps"])[:5]
        for size in (3, 2):
            for combo in combinations(apps, min(size, len(apps))):
                if len(combo) < 2:
                    continue
                key = frozenset(combo)
                group_days.setdefault(key, set()).add(s["day"])
                group_hours.setdefault(key, []).append(s["hour"])

    out = []
    for key, days in group_days.items():
        if len(days) < MIN_DAYS:
            continue
        hours  = group_hours[key]
        median = statistics.median(hours)
        # habit must be time-consistent: most sessions within ±2h of median
        tight  = sum(1 for h in hours if abs(h - median) <= 2.0) / len(hours)
        if tight < 0.6:
            continue
        out.append({
            "apps":   sorted(key),
            "days":   len(days),
            "hour":   round(median),
            "period": ("morning" if median < 12 else
                       "afternoon" if median < 18 else "evening"),
        })
    # bigger groups and more days first; drop subsets of a kept group
    out.sort(key=lambda g: (len(g["apps"]), g["days"]), reverse=True)
    kept = []
    for g in out:
        if not any(set(g["apps"]) <= set(k["apps"]) for k in kept):
            kept.append(g)
    return kept


# ───────────── suggestions lifecycle ─────────────

def _load_sugg() -> dict:
    try:
        return json.loads(SUGG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_sugg(d: dict) -> None:
    SUGG_PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False),
                         encoding="utf-8")


def _covered_by_routine(apps: list[str]) -> bool:
    try:
        from memory.memory_manager import load_memory
        routines = load_memory().get("routines", {})
        for entry in routines.values():
            val = (entry.get("value") if isinstance(entry, dict) else str(entry)) or ""
            if all(a.split()[0] in val.lower() for a in apps):
                return True
    except Exception:
        pass
    return False


def get_startup_suggestion() -> dict | None:
    """One new habit suggestion, max once per day. None if nothing new."""
    sugg = _load_sugg()
    today = datetime.now().strftime("%Y-%m-%d")
    if sugg.get("_last_prompt_day") == today:
        return None

    for g in mine_patterns():
        sid = "+".join(g["apps"])
        state = sugg.get(sid, {}).get("status")
        if state in ("accepted", "dismissed"):
            continue
        if _covered_by_routine(g["apps"]):
            continue
        trigger = f"{g['period']} setup"
        actions = ", then ".join(f"open_app {a}" for a in g["apps"])
        entry = {"status": "pending", "apps": g["apps"], "trigger": trigger,
                 "actions": actions, "days": g["days"], "hour": g["hour"]}
        sugg[sid] = entry
        sugg["_last_prompt_day"] = today
        _save_sugg(sugg)
        return {"id": sid, **entry}
    return None


def respond_suggestion(sid: str, accepted: bool) -> str:
    sugg = _load_sugg()
    entry = sugg.get(sid)
    if not entry:
        return "Unknown suggestion."
    entry["status"] = "accepted" if accepted else "dismissed"
    _save_sugg(sugg)
    if not accepted:
        return "Understood — I won't suggest that again."
    try:
        from memory.memory_manager import update_memory
        trigger = entry["trigger"].replace(" ", "_")
        update_memory({"routines": {trigger: {"value": entry["actions"]}}})
        return (f"Routine '{entry['trigger']}' saved — say it anytime to run: "
                f"{entry['actions']}")
    except Exception as e:
        return f"Could not save the routine: {e}"


def habits_summary() -> str:
    """Human-readable analysis for 'what are my habits?'."""
    events   = _load_events()
    if not events:
        return "No behaviour data collected yet, sir."
    patterns = mine_patterns()

    counts: dict[str, int] = {}
    for e in events:
        if e["label"].startswith("open_app:"):
            app = e["label"].split(":", 1)[1]
            counts[app] = counts.get(app, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]

    lines = [f"Observed {len(events)} actions over the last weeks."]
    if top:
        lines.append("Most opened apps: "
                     + ", ".join(f"{a} ({n}×)" for a, n in top) + ".")
    for g in patterns[:3]:
        lines.append(f"Habit: you open {', '.join(g['apps'])} together in the "
                     f"{g['period']} (~{g['hour']:02d}:00) — seen on {g['days']} days.")
    if not patterns:
        lines.append("No strong time-based habits detected yet.")
    return " ".join(lines)
