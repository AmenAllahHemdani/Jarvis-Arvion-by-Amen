# actions/youtube_tab.py
# JARVIS ARVION — control the already-open YouTube tab WITHOUT focusing it
#
# Tier 1: AppleScript "execute javascript" in the tab — exact control
#         (restart, play, pause) with zero focus change. Needs the one-time
#         browser setting View → Developer → "Allow JavaScript from Apple
#         Events" (Brave/Chrome).
# Tier 2: MediaRemote 'play' — an ENDED html5 video restarts from the
#         beginning when played, so the replay-button case works silently.
# Tier 3: open -g (background open) — never steals focus.

import re
import subprocess

BROWSERS = ["Brave Browser", "Google Chrome", "Microsoft Edge"]

_js_blocked_logged = False


def _osa(script: str, timeout: int = 10):
    return subprocess.run(["osascript", "-e", script],
                          capture_output=True, text=True, timeout=timeout)


def find_watch_tab(video_id: str = ""):
    """Locate a youtube.com/watch tab → (browser, win_idx, tab_idx, url)."""
    needle = f"v={video_id}" if video_id else "youtube.com/watch"
    for browser in BROWSERS:
        script = f'''
        if application "{browser}" is running then
            tell application "{browser}"
                set winIdx to 0
                repeat with w in windows
                    set winIdx to winIdx + 1
                    set tabIdx to 0
                    repeat with t in tabs of w
                        set tabIdx to tabIdx + 1
                        set u to URL of t
                        if u contains "youtube.com/watch" and u contains "{needle.replace('"', '')}" then
                            return (winIdx as text) & "|" & (tabIdx as text) & "|" & u
                        end if
                    end repeat
                end repeat
            end tell
        end if
        return ""'''
        try:
            out = _osa(script).stdout.strip()
            if out.count("|") >= 2:
                w, t, url = out.split("|", 2)
                return browser, int(w), int(t), url
        except Exception:
            continue
    return None


def _js(browser: str, win: int, tab: int, code: str):
    """Run JS in a background tab. Returns (ok, output)."""
    global _js_blocked_logged
    safe = code.replace("\\", "\\\\").replace('"', '\\"')
    r = _osa(f'tell application "{browser}" to tell window {win} '
             f'to execute tab {tab} javascript "{safe}"', timeout=8)
    err = (r.stderr or "").lower()
    if r.returncode != 0:
        if ("javascript" in err or "turned off" in err) and not _js_blocked_logged:
            _js_blocked_logged = True
            print(f"[YouTubeTab] ℹ️ Silent control needs a one-time setting in "
                  f"{browser}: View → Developer → Allow JavaScript from Apple Events")
        return False, r.stderr.strip()
    return True, r.stdout.strip()


def replay_current() -> str:
    """Restart the video in the open YouTube tab — no focus change."""
    tab = find_watch_tab()

    if tab:
        browser, w, t, url = tab
        ok, _ = _js(browser, w, t,
                    "var v=document.querySelector('video');"
                    "if(v){v.currentTime=0;v.play();'ok'}")
        if ok:
            print(f"[YouTubeTab] 🔁 Restarted in background ({browser})")
            return ("Replaying from the start in the background tab — "
                    "your screen stays where it is, sir.")

    # Tier 2: ended videos restart from 0 on a 'play' media command
    try:
        from actions.computer_settings import _macos_media_remote
        if _macos_media_remote(0):          # kMRPlay
            print("[YouTubeTab] 🔁 Replay via MediaRemote play")
            return ("Playing again, sir. (If it resumed mid-video instead of "
                    "restarting, enable 'Allow JavaScript from Apple Events' "
                    "in the browser's View → Developer menu for exact replay.)")
    except Exception:
        pass

    if tab:
        # Tier 3: reload the background tab at t=0 (doesn't steal focus)
        browser, w, t, url = tab
        base = url.split("&")[0]
        _osa(f'tell application "{browser}" to set URL of tab {t} '
             f'of window {w} to "{base}&t=0"')
        return "Restarted the video in its tab, sir."

    return "I can't find an open YouTube video to replay, sir."


def play_in_background(url: str, title: str = "") -> str:
    """Play a history entry without yanking the user to the browser."""
    vid = ""
    m = re.search(r"[?&]v=([\w-]{6,})", url)
    if m:
        vid = m.group(1)

    tab = find_watch_tab(vid) if vid else None
    if tab:
        browser, w, t, _ = tab
        ok, _ = _js(browser, w, t,
                    "var v=document.querySelector('video');"
                    "if(v){v.play();'ok'}")
        if ok:
            return f"Resumed in its background tab: {title[:60]}"
        from actions.computer_settings import _macos_media_remote
        if _macos_media_remote(0):
            return f"Playing: {title[:60]}"

    # no tab with it → open a NEW tab in the background (-g = no focus steal)
    subprocess.Popen(["open", "-g", url])
    return (f"Opened in a background tab: {title[:60]}. "
            f"It may wait for a click to start if the browser blocks autoplay.")
