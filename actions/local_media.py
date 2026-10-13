# actions/local_media.py
# JARVIS ARVION — local video library (Downloads/Movies/Desktop)
#
# Knows which episodes/films are saved locally AND which were watched:
#   • VLC's resume positions (~/Library/Preferences/org.videolan.vlc) —
#     position ≥ 85% of duration = fully watched, middle = in progress
#   • Spotlight kMDItemLastUsedDate — file was opened at least once
#   • Jarvis's own play/mark records (memory/local_media.json)
#
# Actions:
#   overview      — everything grouped by series/film with watch status
#   cleanup       — watched files + sizes, ready to confirm deletion
#   delete_watched— move fully-watched files to Trash (confirm=true required)
#   next_episode  — play (or name) the next unwatched episode of a series
#   play          — play a specific episode/film
#   suggest       — watch-history summary so the model can recommend titles

import json
import plistlib
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}
SCAN_DIRS  = ["Downloads", "Movies", "Desktop"]
RECORDS    = Path(__file__).resolve().parent.parent / "memory" / "local_media.json"

_QUALITY_TOKENS = re.compile(
    r"\b(1080p|720p|480p|2160p|4k|web[-.]?dl|webrip|bluray|blu[-.]?ray|brrip|"
    r"hdrip|dvdrip|nf|amzn|remastered|proper|repack|x264|x265|h264|h265|hevc|"
    r"aac|ac3|yify|rarbg|extended|unrated|part[-.]?\d)\b", re.I)
_EP_PATTERNS = [re.compile(r"[. _-]S(\d{1,2})[. _-]?E(\d{1,2})", re.I),
                re.compile(r"[. _-](\d{1,2})x(\d{2})[. _-]", re.I)]


# ───────────── filename parsing ─────────────

def _clean_title(raw: str) -> str:
    t = re.sub(r"[._]", " ", raw)
    t = _QUALITY_TOKENS.sub("", t)
    t = re.sub(r"\b(19|20)\d{2}\b", "", t)          # years
    t = re.sub(r"[-–]\s*$", "", t.strip())
    t = re.sub(r"\s{2,}", " ", t).strip(" -_")
    return t.title() if t else raw


def _parse(path: Path) -> dict:
    stem = path.stem
    for pat in _EP_PATTERNS:
        m = pat.search(stem)
        if m:
            return {"kind": "episode",
                    "show": _clean_title(stem[:m.start()]),
                    "season": int(m.group(1)), "episode": int(m.group(2))}
    return {"kind": "film", "show": _clean_title(stem),
            "season": None, "episode": None}


# ───────────── watch signals ─────────────

def _vlc_positions() -> dict:
    """{absolute path: seconds position} from VLC; -1 = opened, pos unknown."""
    try:
        out = subprocess.run(["defaults", "export", "org.videolan.vlc", "-"],
                             capture_output=True, timeout=5).stdout
        d = plistlib.loads(out)
    except Exception:
        return {}
    pos = {}
    for url, p in (d.get("recentlyPlayedMedia") or {}).items():
        if url.startswith("file://"):
            pos[urllib.parse.unquote(url[7:])] = float(p)
    for url in (d.get("recentlyPlayedMediaList") or []):
        if url.startswith("file://"):
            pos.setdefault(urllib.parse.unquote(url[7:]), -1.0)
    return pos


def _mdls(path: Path) -> tuple:
    """(duration_seconds | None, was_opened: bool) via Spotlight."""
    try:
        r = subprocess.run(
            ["mdls", "-name", "kMDItemDurationSeconds",
             "-name", "kMDItemLastUsedDate", str(path)],
            capture_output=True, text=True, timeout=5)
        dur, used = None, False
        for line in r.stdout.splitlines():
            if "kMDItemDurationSeconds" in line:
                m = re.search(r"= ([\d.]+)", line)
                if m:
                    dur = float(m.group(1))
            elif "kMDItemLastUsedDate" in line and "(null)" not in line:
                used = True
        return dur, used
    except Exception:
        return None, False


def _records() -> dict:
    try:
        return json.loads(RECORDS.read_text(encoding="utf-8"))
    except Exception:
        return {"watched": {}}


def _mark_watched(path: Path) -> None:
    d = _records()
    d.setdefault("watched", {})[str(path)] = time.time()
    RECORDS.write_text(json.dumps(d, indent=2), encoding="utf-8")


# ───────────── scanning ─────────────

def _scan() -> list:
    vlc  = _vlc_positions()
    own  = _records().get("watched", {})
    home = Path.home()
    items = []
    for d in SCAN_DIRS:
        base = home / d
        if not base.exists():
            continue
        for p in sorted(base.iterdir()):
            if p.suffix.lower() not in VIDEO_EXTS or p.name.startswith("."):
                continue
            if re.search(r"screen[ _.-]?record|screenshot|simulator",
                         p.name, re.I):
                continue          # screen captures aren't movies
            dur, opened = _mdls(p)
            pos = vlc.get(str(p))
            if str(p) in own:
                status = "watched"
            elif pos is not None and pos > 0 and dur:
                ratio = pos / dur
                if ratio >= 0.85:
                    status = "watched"
                elif ratio >= 0.05:
                    status = f"in progress ({int(pos // 60)} of {int(dur // 60)} min)"
                else:
                    status = "barely started"
            elif (pos is not None) or opened:
                status = "opened (unknown progress)"
            else:
                status = "unwatched"
            info = _parse(p)
            items.append({**info, "path": p, "status": status,
                          "size": p.stat().st_size, "duration": dur})
    return items


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _ep_tag(it: dict) -> str:
    return f"S{it['season']:02d}E{it['episode']:02d}"


def _group(items: list) -> dict:
    groups = {}
    for it in items:
        groups.setdefault(it["show"].lower(), []).append(it)
    return groups


def _fuzzy_show(items: list, query: str) -> list:
    q = re.sub(r"\W+", " ", (query or "").lower()).strip()
    if not q:
        return []
    hits = [it for it in items
            if q in re.sub(r"\W+", " ", it["show"].lower())]
    if not hits:
        words = [w for w in q.split() if len(w) >= 3]
        hits = [it for it in items
                if sum(w in it["show"].lower() for w in words) >= max(1, len(words) - 1)]
    return hits


def _play_file(path: Path) -> None:
    subprocess.Popen(["open", str(path)])


# ───────────── the tool ─────────────

def local_media(parameters=None, response=None, player=None,
                session_memory=None) -> str:
    p       = parameters or {}
    action  = (p.get("action") or "overview").lower().strip()
    series  = (p.get("series") or "").strip()
    confirm = str(p.get("confirm", "")).lower() in ("true", "1", "yes")

    items = _scan()
    if not items:
        return "No video files found in Downloads, Movies or Desktop, sir."

    if player:
        player.write_log(f"[LocalMedia] {action} — {len(items)} files")

    # ── overview ──
    if action in ("overview", "list", "scan"):
        lines = []
        for show_key, eps in sorted(_group(items).items()):
            if eps[0]["kind"] == "episode":
                eps.sort(key=lambda e: (e["season"], e["episode"]))
                watched   = [e for e in eps if e["status"] == "watched"]
                unwatched = [e for e in eps if e["status"] == "unwatched"]
                partial   = [e for e in eps if "progress" in e["status"]]
                line = (f"SERIES {eps[0]['show']}: {len(eps)} eps on disk "
                        f"({_gb(sum(e['size'] for e in eps))}) — "
                        f"watched: {', '.join(_ep_tag(e) for e in watched) or 'none'}")
                if partial:
                    line += f" | {', '.join(_ep_tag(e) + ' ' + e['status'] for e in partial)}"
                if unwatched:
                    line += f" | NOT watched: {', '.join(_ep_tag(e) for e in unwatched)}"
                lines.append(line)
            else:
                for e in eps:
                    lines.append(f"FILM {e['show']} ({_gb(e['size'])}) — {e['status']}")
        total = _gb(sum(it["size"] for it in items))
        return (f"LOCAL VIDEO LIBRARY ({len(items)} files, {total} total):\n"
                + "\n".join(lines)
                + "\n\n(Report this naturally. Offer to: delete watched ones, "
                  "play the next episode, or suggest similar titles.)")

    # ── cleanup: what can be deleted ──
    if action in ("cleanup", "watched"):
        done = [it for it in items if it["status"] == "watched"]
        if not done:
            return "Nothing is fully watched yet, sir — nothing to clean up."
        lines = [f"- {it['path'].name} ({_gb(it['size'])})" for it in done]
        total = _gb(sum(it["size"] for it in done))
        return (f"FULLY WATCHED ({len(done)} files, {total} reclaimable):\n"
                + "\n".join(lines)
                + "\n\n(Ask the user to confirm, then call local_media "
                  "action='delete_watched' confirm=true. Files go to Trash, "
                  "not permanently deleted.)")

    # ── delete watched ──
    if action == "delete_watched":
        if not confirm:
            return ("Deletion needs confirm=true — ask the user first, "
                    "then call again with confirm=true.")
        done = [it for it in items if it["status"] == "watched"]
        if not done:
            return "Nothing fully watched to delete, sir."
        from send2trash import send2trash
        freed, names = 0, []
        for it in done:
            try:
                send2trash(str(it["path"]))
                freed += it["size"]
                names.append(it["path"].name)
            except Exception as e:
                print(f"[LocalMedia] ⚠️ trash failed {it['path'].name}: {e}")
        return (f"Moved {len(names)} watched files to Trash, freeing {_gb(freed)}: "
                + ", ".join(n[:50] for n in names[:8])
                + (" …" if len(names) > 8 else ""))

    # ── next episode ──
    if action == "next_episode":
        eps = [it for it in (_fuzzy_show(items, series) if series else items)
               if it["kind"] == "episode"]
        if not eps:
            return (f"I don't have any episodes of '{series}' saved locally, sir."
                    if series else "No series episodes found locally, sir.")
        by_show = _group(eps)
        if len(by_show) > 1 and not series:
            names = ", ".join(v[0]["show"] for v in by_show.values())
            return f"Which series, sir? On disk: {names}"
        show_eps = sorted(next(iter(by_show.values())),
                          key=lambda e: (e["season"], e["episode"]))
        show = show_eps[0]["show"]
        partial = [e for e in show_eps if "progress" in e["status"]]
        if partial:
            e = partial[-1]
            _play_file(e["path"])
            return f"Resuming {show} {_ep_tag(e)}, sir — {e['status']}."
        watched = [e for e in show_eps if e["status"] == "watched"]
        last = max(((e["season"], e["episode"]) for e in watched), default=(0, 0))
        nxt = next((e for e in show_eps
                    if (e["season"], e["episode"]) > last
                    and e["status"] != "watched"), None)
        if nxt:
            _play_file(nxt["path"])
            _mark_watched(nxt["path"])
            return f"Playing {show} {_ep_tag(nxt)}, sir. Enjoy."
        nxt_num = f"S{last[0]:02d}E{last[1] + 1:02d}"
        return (f"You've watched everything of {show} on disk (up to "
                f"S{last[0]:02d}E{last[1]:02d}), sir. The next episode "
                f"{nxt_num} isn't downloaded — shall I search for it online?")

    # ── play a specific one ──
    if action == "play":
        hits = _fuzzy_show(items, series or p.get("file_name", ""))
        if not hits:
            return f"I couldn't find '{series}' in the local library, sir."
        ep_num = p.get("episode")
        if ep_num is not None:
            try:
                ep_num = int(ep_num)
                hits = [h for h in hits if h["episode"] == ep_num] or hits
            except (TypeError, ValueError):
                pass
        hits.sort(key=lambda e: (e["status"] == "watched",
                                 e["season"] or 0, e["episode"] or 0))
        target = hits[0]
        _play_file(target["path"])
        _mark_watched(target["path"])
        tag = f" {_ep_tag(target)}" if target["kind"] == "episode" else ""
        return f"Playing {target['show']}{tag}, sir."

    # ── suggestions based on history ──
    if action == "suggest":
        watched = [it for it in items if it["status"] != "unwatched"]
        shows   = sorted({it["show"] for it in watched if it["kind"] == "episode"})
        films   = sorted({it["show"] for it in watched if it["kind"] == "film"})
        yt = []
        try:
            from memory import media_history
            yt = [e["title"] for e in media_history.favorites(5)]
        except Exception:
            pass
        return ("WATCH HISTORY — series: " + (", ".join(shows) or "none")
                + " | films: " + (", ".join(films) or "none")
                + (" | often plays on YouTube: " + "; ".join(yt) if yt else "")
                + "\n\n(Based on these tastes, recommend 4-5 specific series or "
                  "films the user hasn't seen, with one short reason each. "
                  "Answer in the user's language.)")

    return (f"Unknown action '{action}'. Use: overview | cleanup | "
            f"delete_watched | next_episode | play | suggest")
