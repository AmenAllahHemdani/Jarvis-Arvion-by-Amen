# memory/media_history.py
# JARVIS ARVION — listening/watching history
#
# The HUD's now-playing watcher reports every YouTube video the user plays;
# this module remembers them (title, url, play count), lets JARVIS replay
# or search them by voice, and once a day offers a favourite.

import json
import re
import time
from datetime import datetime
from pathlib import Path
from threading import Lock

PATH        = Path(__file__).resolve().parent / "media_history.json"
MAX_ENTRIES = 200
_lock       = Lock()

_QURAN_HINTS = ("quran", "qur'an", "surah", "sura", "recit", "tilawa",
                "قرآن", "القرآن", "سورة", "تلاوة", "الشيخ")


def _load() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"items": {}}


def _save(d: dict) -> None:
    PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def _video_id(url: str) -> str:
    m = re.search(r"[?&]v=([\w-]{6,})", url or "")
    return m.group(1) if m else (url or "")[:40]


def _category(title: str) -> str:
    low = (title or "").lower()
    return "quran" if any(h in low for h in _QURAN_HINTS) else "music"


def log_entry(title: str, url: str) -> None:
    """Called by the now-playing watcher whenever a new video starts."""
    title = (title or "").strip()
    if not title or "youtube.com/watch" not in (url or ""):
        return
    vid = _video_id(url)
    with _lock:
        d = _load()
        items = d["items"]
        e = items.get(vid)
        if e:
            e["count"]    += 1
            e["last_seen"] = time.time()
            e["title"]     = title            # titles get cleaner over time
        else:
            items[vid] = {"title": title, "url": url.split("&")[0],
                          "count": 1, "last_seen": time.time(),
                          "category": _category(title)}
        if len(items) > MAX_ENTRIES:          # drop oldest, least played
            victims = sorted(items.items(),
                             key=lambda kv: (kv[1]["count"], kv[1]["last_seen"]))
            for k, _ in victims[:len(items) - MAX_ENTRIES]:
                del items[k]
        _save(d)
    print(f"[MediaHistory] 🎵 {title[:60]} (#{_load()['items'][vid]['count']})")


def recent(limit: int = 10, category: str = "") -> list[dict]:
    items = _load()["items"].values()
    if category:
        items = [e for e in items if e.get("category") == category]
    return sorted(items, key=lambda e: -e["last_seen"])[:limit]


def favorites(limit: int = 5, category: str = "") -> list[dict]:
    items = _load()["items"].values()
    if category:
        items = [e for e in items if e.get("category") == category]
    return sorted(items, key=lambda e: (-e["count"], -e["last_seen"]))[:limit]


def find(query: str) -> dict | None:
    """Best history match for a spoken query ('the quran I always play')."""
    q = (query or "").lower().strip()
    if not q:
        return None
    items = list(_load()["items"].values())
    if not items:
        return None
    if q in ("quran", "القرآن", "my quran"):
        fav = favorites(1, "quran")
        return fav[0] if fav else None
    words = [w for w in re.split(r"\W+", q) if len(w) >= 3]
    def score(e):
        t = e["title"].lower()
        hits = sum(1 for w in words if w in t)
        return (hits, e["count"], e["last_seen"])
    best = max(items, key=score)
    return best if score(best)[0] > 0 else None


def most_recent() -> dict | None:
    r = recent(1)
    return r[0] if r else None


# ───── daily "shall I play it?" offer ─────

def get_daily_offer() -> dict | None:
    with _lock:
        d = _load()
        today = datetime.now().strftime("%Y-%m-%d")
        if d.get("_last_offer_day") == today:
            return None
        dismissed = set(d.get("_dismissed", []))
        for e in favorites(5):
            vid = _video_id(e["url"])
            if e["count"] >= 3 and vid not in dismissed:
                d["_last_offer_day"] = today
                _save(d)
                return e
    return None


def dismiss(query: str) -> str:
    e = find(query) or most_recent()
    if not e:
        return "Nothing to dismiss."
    with _lock:
        d = _load()
        d.setdefault("_dismissed", []).append(_video_id(e["url"]))
        _save(d)
    return f"Understood — I won't offer '{e['title'][:50]}' again."
