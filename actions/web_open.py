# actions/web_open.py
# JARVIS ARVION — open URLs in the USER'S own browser, never a fresh
# automation/testing instance.
#
# Picks the browser the user is actually in:
#   1. the frontmost app, if it's a browser
#   2. otherwise the first known browser that is currently running
#   3. otherwise the system default browser
#
# Playwright is deliberately NOT used here — this is for plain "open a site"
# requests where the user expects their real browser (profile, logins, tabs).

import platform
import shutil
import subprocess
from pathlib import Path

# Priority order when several are running (most likely daily drivers first).
_MAC_BROWSERS = ["Brave Browser", "Google Chrome", "Arc", "Microsoft Edge",
                 "Safari", "Firefox", "Opera", "Vivaldi"]
_WIN_BROWSERS = ["brave.exe", "chrome.exe", "msedge.exe", "firefox.exe",
                 "opera.exe", "vivaldi.exe"]
_LINUX_BROWSERS = ["brave-browser", "brave", "google-chrome",
                   "google-chrome-stable", "chromium-browser", "chromium",
                   "firefox", "opera", "vivaldi"]


def _mac_running_browser() -> str:
    """Browser the user is actually using: frontmost wins, else first running."""
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of every '
             'application process whose background only is false'],
            capture_output=True, text=True, timeout=5)
        procs = [p.strip() for p in r.stdout.split(",")]
    except Exception:
        return ""

    try:
        front = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first '
             'application process whose frontmost is true'],
            capture_output=True, text=True, timeout=5).stdout.strip()
        if front in _MAC_BROWSERS:
            return front
    except Exception:
        pass

    for browser in _MAC_BROWSERS:
        if browser in procs:
            return browser
    return ""


def _running_process_exes() -> dict:
    """{lowercase process name: exe path} for all running processes."""
    try:
        import psutil
    except ImportError:
        return {}
    found = {}
    for proc in psutil.process_iter(["name", "exe"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name and name not in found:
                found[name] = proc.info["exe"] or ""
        except Exception:
            continue
    return found


def _win_running_browser_exe() -> str:
    procs = _running_process_exes()
    for name in _WIN_BROWSERS:
        exe = procs.get(name)
        if exe:
            return exe
    return ""


def _linux_running_browser() -> str:
    procs = _running_process_exes()
    for name in _LINUX_BROWSERS:
        if name in procs:
            binary = shutil.which(name)
            if binary:
                return binary
    return ""


def open_url(url: str, background: bool = False) -> str:
    """
    Open a URL in the user's own browser (running one preferred, else the
    system default). Returns the name of the browser used, for the reply.
    """
    if not url.startswith("http"):
        url = "https://" + url

    system = platform.system()

    try:
        if system == "Darwin":
            browser = _mac_running_browser()
            cmd = ["open"]
            if background:
                cmd.append("-g")
            if browser:
                cmd += ["-a", browser]
            cmd.append(url)
            subprocess.Popen(cmd)
            used = browser or "your default browser"

        elif system == "Windows":
            exe = _win_running_browser_exe()
            if exe:
                subprocess.Popen([exe, url])
                used = Path(exe).stem.capitalize()
            else:
                import webbrowser
                webbrowser.open(url)
                used = "your default browser"

        else:  # Linux
            binary = _linux_running_browser()
            if binary:
                subprocess.Popen([binary, url],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                used = Path(binary).stem
            else:
                subprocess.Popen(["xdg-open", url],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                used = "your default browser"

        print(f"[web_open] 🌐 {url} → {used}")
        return used

    except Exception as e:
        print(f"[web_open] ⚠️ {e} — falling back to webbrowser module")
        import webbrowser
        webbrowser.open(url)
        return "your default browser"
