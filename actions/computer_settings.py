#computer_settings.py
import json
import re
import sys
import time
import subprocess
import platform
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    from actions.typing_utils import smart_write, smart_hotkey, smart_press
except ImportError:
    from typing_utils import smart_write, smart_hotkey, smart_press

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

def _get_macos_wifi_interface() -> str:
    try:
        result = subprocess.run(
            ["networksetup", "-listallhardwareports"],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if "Wi-Fi" in line or "AirPort" in line:
                for j in range(i, min(i + 4, len(lines))):
                    if lines[j].startswith("Device:"):
                        return lines[j].split(":", 1)[1].strip()
    except Exception:
        pass
    return "en0" 

def volume_up():
    if _OS == "Windows":
        for _ in range(5): smart_press("volumeup")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) + 10)"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
            capture_output=True)

def volume_down():
    if _OS == "Windows":
        for _ in range(5): smart_press("volumedown")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            "set volume output volume (output volume of (get volume settings) - 10)"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
            capture_output=True)

def volume_mute():
    if _OS == "Windows":
        smart_press("volumemute")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", "set volume with output muted"],
            capture_output=True)
    else:
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
            capture_output=True)

def volume_set(value: int):
    value = max(0, min(100, int(value)))
    if _OS == "Windows":
        try:
            import math
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices   = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol       = cast(interface, POINTER(IAudioEndpointVolume))
            vol_db    = -65.25 if value == 0 else max(-65.25, 20 * math.log10(value / 100))
            vol.SetMasterVolumeLevel(vol_db, None)
            return
        except Exception as e:
            print(f"[Settings] pycaw failed, using keypress fallback: {e}")
            smart_press("volumemute")
            smart_press("volumemute")
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e", f"set volume output volume {value}"],
            capture_output=True)
        return
    else:
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"],
            capture_output=True)
        return

def brightness_up():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 144'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "+10%"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(min(1.0,b+0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Min(100, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness + 10))"],
                capture_output=True, timeout=5
            )
        except Exception as e:
            print(f"[Settings] Brightness up failed on Windows: {e}")

def brightness_down():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to key code 145'],
            capture_output=True)
    elif _OS == "Linux":
        if subprocess.run(["which", "brightnessctl"],
                capture_output=True).returncode == 0:
            subprocess.run(["brightnessctl", "set", "10%-"], capture_output=True)
        else:
            subprocess.run(
                'xrandr --output $(xrandr | grep " connected" | head -1 | cut -d " " -f1)'
                ' --brightness $(python3 -c "import subprocess; '
                'b=float(subprocess.check_output([\"xrandr\",\"--verbose\"]).decode()'
                '.split(\"Brightness:\")[1].split()[0]); print(max(0.1,b-0.1))")',
                shell=True, capture_output=True
            )
    else:
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods)"
                 ".WmiSetBrightness(1, [math]::Max(0, "
                 "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness - 10))"],
                capture_output=True, timeout=5
            )
        except Exception as e:
            print(f"[Settings] Brightness down failed on Windows: {e}")

def list_open_apps() -> list:
    """Return names of applications with visible windows / UI."""
    apps = []
    try:
        if _OS == "Darwin":
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of every '
                 'application process whose background only is false'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                apps = [a.strip() for a in result.stdout.strip().split(",") if a.strip()]
        elif _OS == "Windows":
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process | Where-Object {$_.MainWindowTitle} | "
                 "Select-Object -ExpandProperty ProcessName -Unique"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                apps = [a.strip() for a in result.stdout.splitlines() if a.strip()]
        else:
            result = subprocess.run(["wmctrl", "-lx"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                seen = set()
                for line in result.stdout.splitlines():
                    parts = line.split(None, 4)
                    if len(parts) >= 3 and "." in parts[2]:
                        name = parts[2].split(".")[-1]
                        if name.lower() not in seen:
                            seen.add(name.lower())
                            apps.append(name)
    except Exception as e:
        print(f"[Settings] list_open_apps failed: {e}")
    return apps


def _match_open_app(requested: str, open_apps: list):
    """Fuzzy-match a user-spoken app name against actually open apps."""
    req = requested.lower().strip()
    if not req:
        return None
    for app in open_apps:
        if app.lower() == req:
            return app
    for app in open_apps:
        if req in app.lower() or app.lower() in req:
            return app
    req_compact = req.replace(" ", "")
    for app in open_apps:
        app_compact = app.lower().replace(" ", "")
        if req_compact in app_compact or app_compact in req_compact:
            return app
    return None


def close_app(app_name: str = ""):
    """Quit a specific app by name; fall back to the focused app only if no name given."""
    app_name = (app_name or "").strip()

    if not app_name:
        if _OS == "Darwin": smart_hotkey("command", "q")
        else:               smart_hotkey("alt", "f4")
        return "Closed the focused application."

    open_apps = list_open_apps()
    target = _match_open_app(app_name, open_apps)

    if target is None:
        running = ", ".join(open_apps) if open_apps else "none detected"
        return (f"{app_name} doesn't seem to be open, so I didn't close anything. "
                f"Currently open: {running}.")

    try:
        if _OS == "Darwin":
            safe = target.replace('"', '\\"')
            # Quit via bundle id: process name (e.g. "MSTeams") often differs
            # from the app name ("Microsoft Teams"), which breaks `tell application "X"`.
            script = (
                f'tell application "System Events" to set bid to '
                f'bundle identifier of application process "{safe}"\n'
                f'tell application id bid to quit'
            )
            result = subprocess.run(["osascript", "-e", script],
                                    capture_output=True, timeout=10)
            if result.returncode != 0:
                subprocess.run(["osascript", "-e", f'tell application "{safe}" to quit'],
                               capture_output=True, timeout=10)
        elif _OS == "Windows":
            result = subprocess.run(["taskkill", "/IM", f"{target}.exe"],
                                    capture_output=True, timeout=10)
            if result.returncode != 0:
                subprocess.run(["taskkill", "/F", "/IM", f"{target}.exe"],
                               capture_output=True, timeout=10)
        else:
            result = subprocess.run(["wmctrl", "-c", target],
                                    capture_output=True, timeout=10)
            if result.returncode != 0:
                subprocess.run(["pkill", "-f", "-i", target],
                               capture_output=True, timeout=10)
        return f"Closed {target}."
    except Exception as e:
        return f"Failed to close {target}: {e}"

def close_window():
    if _OS == "Darwin": smart_hotkey("command", "w")
    else:               smart_hotkey("ctrl", "w")

def full_screen():
    if _OS == "Darwin": smart_hotkey("ctrl", "command", "f")
    else:               smart_press("f11")

def minimize_window():
    if _OS == "Darwin": smart_hotkey("command", "m")
    else:               smart_hotkey("win", "down")

def maximize_window():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to keystroke "f" '
            'using {control down, command down}'],
            capture_output=True)
    elif _OS == "Windows":
        smart_hotkey("win", "up")
    else:
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"],
                capture_output=True)
        except Exception:
            smart_hotkey("super", "up")

def snap_left():
    if _OS == "Windows":
        smart_hotkey("win", "left")
    elif _OS == "Linux":
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,0,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def snap_right():
    if _OS == "Windows":
        smart_hotkey("win", "right")
    elif _OS == "Linux":
        try:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-e", "0,960,0,960,1080"],
                capture_output=True)
        except Exception:
            pass

def switch_window():
    if _OS == "Darwin": smart_hotkey("command", "tab")
    else:               smart_hotkey("alt", "tab")

def show_desktop():
    if _OS == "Darwin":   smart_hotkey("fn", "f11")
    elif _OS == "Windows": smart_hotkey("win", "d")
    else:                  smart_hotkey("super", "d")

def open_task_manager():
    if _OS == "Windows":
        smart_hotkey("ctrl", "shift", "esc")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "Activity Monitor"])
    else:
        for cmd in [["gnome-system-monitor"], ["xfce4-taskmanager"], ["htop"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                break


def focus_search():
    if _OS == "Darwin": smart_hotkey("command", "l")
    else:               smart_hotkey("ctrl", "l")

def _macos_media_key(key_type: int) -> bool:
    """Post a hardware media-key event (NX_KEYTYPE_*). macOS routes it to the
    most recent media session — the last video/audio the user had playing,
    even in a background tab. 16=play/pause 17=next 18=previous"""
    try:
        from AppKit import NSEvent
        import Quartz

        def post(down: bool):
            flags = 0xA00 if down else 0xB00
            data1 = (key_type << 16) | ((0xA if down else 0xB) << 8)
            ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                14, (0, 0), flags, 0, 0, None, 8, data1, -1)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev.CGEvent())

        post(True)
        post(False)
        return True
    except Exception as e:
        print(f"[Settings] media key failed: {e}")
        return False


def _macos_media_remote(command: int) -> bool:
    """Drive the system Now Playing session (the menu-bar media widget)
    via Apple's MediaRemote framework — the same engine the widget's own
    buttons use. Needs no Accessibility permission and controls whatever
    played last (YouTube tab, Spotify, VLC...), even in the background.
    0=play 1=pause 2=toggle 4=next 5=previous"""
    try:
        import ctypes
        mr = ctypes.CDLL(
            "/System/Library/PrivateFrameworks/MediaRemote.framework/MediaRemote")
        mr.MRMediaRemoteSendCommand.restype = ctypes.c_bool
        return bool(mr.MRMediaRemoteSendCommand(command, None))
    except Exception as e:
        print(f"[Settings] MediaRemote failed: {e}")
        return False


_MAC_BROWSERS = ["Brave Browser", "Google Chrome", "Microsoft Edge", "Chromium"]


def _macos_youtube_toggle() -> bool:
    """Find an open youtube.com/watch tab in a running browser, bring it to
    front and press 'k' (YouTube's play/pause key). Works even when the tab
    was paused long ago and macOS dropped its media session — the case where
    the hardware media key does nothing."""
    for browser in _MAC_BROWSERS:
        script = f'''
        if application "{browser}" is running then
            tell application "{browser}"
                set winIdx to 0
                repeat with w in windows
                    set winIdx to winIdx + 1
                    set tabIdx to 0
                    repeat with t in tabs of w
                        set tabIdx to tabIdx + 1
                        if URL of t contains "youtube.com/watch" then
                            set active tab index of w to tabIdx
                            set index of w to 1
                            activate
                            return "FOUND"
                        end if
                    end repeat
                end repeat
            end tell
        end if
        return "NONE"
        '''
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=10)
            if r.stdout.strip() == "FOUND":
                time.sleep(0.6)   # let the tab take focus
                subprocess.run(["osascript", "-e",
                                'tell application "System Events" to keystroke "k"'],
                               capture_output=True, timeout=5)
                print(f"[Settings] ▶⏸ Toggled YouTube tab in {browser}")
                return True
        except Exception as e:
            print(f"[Settings] youtube toggle ({browser}) failed: {e}")
    return False


def _macos_spotify_toggle() -> bool:
    try:
        r = subprocess.run(["osascript", "-e",
            'if application "Spotify" is running then\n'
            '  tell application "Spotify" to playpause\n'
            '  return "OK"\n'
            'end if\n'
            'return "NONE"'],
            capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "OK"
    except Exception:
        return False


def play_pause_media():
    """Play/pause the CURRENT media (last opened video/audio), system-wide."""
    if _OS == "Darwin":
        # MediaRemote drives the menu-bar Now Playing session directly —
        # verified to pause a backgrounded YouTube tab with no permissions.
        if _macos_media_remote(2):
            return
        # Session gone from the widget (video paused long ago): find the
        # YouTube tab itself, focus it, press 'k'.
        if _macos_youtube_toggle():
            return
        if _macos_spotify_toggle():
            return
        if _macos_media_key(16):
            return
    elif _OS == "Windows":
        smart_press("playpause")
        return
    else:
        for cmd in (["playerctl", "play-pause"],):
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.run(cmd, capture_output=True)
                return
    smart_press("space")   # last resort: needs the player focused


def next_track():
    if _OS == "Darwin" and _macos_media_key(17):
        return
    if _OS == "Windows":
        smart_press("nexttrack")
    else:
        subprocess.run(["playerctl", "next"], capture_output=True)


def prev_track():
    if _OS == "Darwin" and _macos_media_key(18):
        return
    if _OS == "Windows":
        smart_press("prevtrack")
    else:
        subprocess.run(["playerctl", "previous"], capture_output=True)


def pause_video():      play_pause_media()

def refresh_page():
    if _OS == "Darwin": smart_hotkey("command", "r")
    else:               smart_press("f5")

def close_tab():
    if _OS == "Darwin": smart_hotkey("command", "w")
    else:               smart_hotkey("ctrl", "w")

def new_tab():
    if _OS == "Darwin": smart_hotkey("command", "t")
    else:               smart_hotkey("ctrl", "t")

def next_tab():
    if _OS == "Darwin": smart_hotkey("command", "shift", "bracketright")
    else:               smart_hotkey("ctrl", "tab")

def prev_tab():
    if _OS == "Darwin": smart_hotkey("command", "shift", "bracketleft")
    else:               smart_hotkey("ctrl", "shift", "tab")

def go_back():
    if _OS == "Darwin": smart_hotkey("command", "left")
    else:               smart_hotkey("alt", "left")

def go_forward():
    if _OS == "Darwin": smart_hotkey("command", "right")
    else:               smart_hotkey("alt", "right")

def zoom_in():
    if _OS == "Darwin": smart_hotkey("command", "equal")
    else:               smart_hotkey("ctrl", "equal")

def zoom_out():
    if _OS == "Darwin": smart_hotkey("command", "minus")
    else:               smart_hotkey("ctrl", "minus")

def zoom_reset():
    if _OS == "Darwin": smart_hotkey("command", "0")
    else:               smart_hotkey("ctrl", "0")

def find_on_page():
    if _OS == "Darwin": smart_hotkey("command", "f")
    else:               smart_hotkey("ctrl", "f")

def reload_page_n(n: int):
    for _ in range(max(1, n)):
        refresh_page()
        time.sleep(0.8)


def scroll_up(amount: int = 500):    pyautogui.scroll(amount)
def scroll_down(amount: int = 500):  pyautogui.scroll(-amount)

def scroll_top():
    if _OS == "Darwin": smart_hotkey("command", "up")
    else:               smart_hotkey("ctrl", "home")

def scroll_bottom():
    if _OS == "Darwin": smart_hotkey("command", "down")
    else:               smart_hotkey("ctrl", "end")

def page_up():   smart_press("pageup")
def page_down(): smart_press("pagedown")


def copy():
    if _OS == "Darwin": smart_hotkey("command", "c")
    else:               smart_hotkey("ctrl", "c")

def paste():
    if _OS == "Darwin": smart_hotkey("command", "v")
    else:               smart_hotkey("ctrl", "v")

def cut():
    if _OS == "Darwin": smart_hotkey("command", "x")
    else:               smart_hotkey("ctrl", "x")

def undo():
    if _OS == "Darwin": smart_hotkey("command", "z")
    else:               smart_hotkey("ctrl", "z")

def redo():
    if _OS == "Darwin": smart_hotkey("command", "shift", "z")
    else:               smart_hotkey("ctrl", "y")

def select_all():
    if _OS == "Darwin": smart_hotkey("command", "a")
    else:               smart_hotkey("ctrl", "a")

def save_file():
    if _OS == "Darwin": smart_hotkey("command", "s")
    else:               smart_hotkey("ctrl", "s")

def press_enter():   smart_press("enter")
def press_escape():  smart_press("escape")
def press_key(key: str): smart_press(key)

def type_text(text: str, press_enter_after: bool = False):
    if not text:
        return
    if _PYPERCLIP:
        pyperclip.copy(str(text))
        time.sleep(0.15)
        paste()
    else:
        smart_write(str(text), interval=0.03)
    if press_enter_after:
        time.sleep(0.1)
        smart_press("enter")

def take_screenshot():
    if _OS == "Windows":
        smart_hotkey("win", "shift", "s")
    elif _OS == "Darwin":
        smart_hotkey("command", "shift", "3")
    else:
        for cmd in [["scrot"], ["gnome-screenshot"], ["import", "-window", "root", "screenshot.png"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        smart_hotkey("ctrl", "print_screen")

def lock_screen():
    if _OS == "Windows":
        smart_hotkey("win", "l")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        for cmd in [
            ["gnome-screensaver-command", "-l"],
            ["xdg-screensaver", "lock"],
            ["loginctl", "lock-session"],
        ]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.run(cmd, capture_output=True)
                return

def open_system_settings():
    if _OS == "Windows":
        smart_hotkey("win", "i")
    elif _OS == "Darwin":
        subprocess.Popen(["open", "-a", "System Preferences"])
    else:
        for cmd in [["gnome-control-center"], ["xfce4-settings-manager"], ["kcmshell5"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return

def open_file_explorer():
    if _OS == "Windows":
        smart_hotkey("win", "e")
    elif _OS == "Darwin":
        subprocess.Popen(["open", str(Path.home())])
    else:
        for cmd in [["nautilus"], ["thunar"], ["dolphin"], ["nemo"]]:
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.Popen(cmd)
                return
        subprocess.Popen(["xdg-open", str(Path.home())])

def sleep_display():
    if _OS == "Windows":
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)
        except Exception as e:
            print(f"[Settings] sleep_display failed: {e}")
    elif _OS == "Darwin":
        subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    else:
        subprocess.run(["xset", "dpms", "force", "off"], capture_output=True)

def open_run():
    if _OS == "Windows":
        smart_hotkey("win", "r")

def dark_mode():
    if _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell app "System Events" to tell appearance preferences '
            'to set dark mode to not dark mode'],
            capture_output=True)
    elif _OS == "Windows":
        try:
            import winreg
            key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            current, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, 1 - current)
            winreg.CloseKey(key)
        except Exception as e:
            print(f"[Settings] dark_mode registry failed: {e}")
    else:
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True, text=True
            )
            current = result.stdout.strip()
            new_scheme = "'default'" if "dark" in current else "'prefer-dark'"
            subprocess.run(
                ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", new_scheme],
                capture_output=True
            )
        except Exception as e:
            print(f"[Settings] dark_mode Linux failed: {e}")

def toggle_wifi():
    if _OS == "Darwin":
        iface = _get_macos_wifi_interface()
        result = subprocess.run(
            ["networksetup", "-getairportpower", iface],
            capture_output=True, text=True
        )
        state = "off" if "On" in result.stdout else "on"
        subprocess.run(["networksetup", "-setairportpower", iface, state],
            capture_output=True)
    elif _OS == "Windows":
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "$adapter = Get-NetAdapter | Where-Object {$_.PhysicalMediaType -eq 'Native 802.11'};"
                 "if ($adapter.Status -eq 'Up') { Disable-NetAdapter -Name $adapter.Name -Confirm:$false }"
                 "else { Enable-NetAdapter -Name $adapter.Name -Confirm:$false }"],
                capture_output=True, timeout=10
            )
        except Exception as e:
            print(f"[Settings] toggle_wifi Windows failed: {e}")
    else:
        try:
            result = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True)
            state  = "off" if "enabled" in result.stdout else "on"
            subprocess.run(["nmcli", "radio", "wifi", state], capture_output=True)
        except Exception as e:
            print(f"[Settings] toggle_wifi Linux failed: {e}")

def restart_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/r", "/t", "10"], capture_output=True)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to restart'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "reboot"], capture_output=True)

def shutdown_computer():
    if _OS == "Windows":
        subprocess.run(["shutdown", "/s", "/t", "10"], capture_output=True)
    elif _OS == "Darwin":
        subprocess.run(["osascript", "-e",
            'tell application "System Events" to shut down'],
            capture_output=True)
    else:
        subprocess.run(["systemctl", "poweroff"], capture_output=True)

ACTION_MAP: dict[str, callable] = {
    "volume_up":           volume_up,
    "volume_down":         volume_down,
    "mute":                volume_mute,
    "unmute":              volume_mute,
    "toggle_mute":         volume_mute,
    "brightness_up":       brightness_up,
    "brightness_down":     brightness_down,
    "sleep_display":       sleep_display,
    "screen_off":          sleep_display,
    "pause_video":         play_pause_media,
    "play_pause":          play_pause_media,
    "play":                play_pause_media,
    "pause":               play_pause_media,
    "resume":              play_pause_media,
    "next_track":          next_track,
    "prev_track":          prev_track,
    "previous_track":      prev_track,
    "close_app":           close_app,
    "close_window":        close_window,
    "full_screen":         full_screen,
    "fullscreen":          full_screen,
    "minimize":            minimize_window,
    "maximize":            maximize_window,
    "snap_left":           snap_left,
    "snap_right":          snap_right,
    "switch_window":       switch_window,
    "show_desktop":        show_desktop,
    "task_manager":        open_task_manager,
    "focus_search":        focus_search,
    "refresh_page":        refresh_page,
    "reload":              refresh_page,
    "close_tab":           close_tab,
    "new_tab":             new_tab,
    "next_tab":            next_tab,
    "prev_tab":            prev_tab,
    "go_back":             go_back,
    "go_forward":          go_forward,
    "zoom_in":             zoom_in,
    "zoom_out":            zoom_out,
    "zoom_reset":          zoom_reset,
    "find_on_page":        find_on_page,
    "scroll_up":           scroll_up,
    "scroll_down":         scroll_down,
    "scroll_top":          scroll_top,
    "scroll_bottom":       scroll_bottom,
    "page_up":             page_up,
    "page_down":           page_down,
    "copy":                copy,
    "paste":               paste,
    "cut":                 cut,
    "undo":                undo,
    "redo":                redo,
    "select_all":          select_all,
    "save":                save_file,
    "enter":               press_enter,
    "escape":              press_escape,
    "screenshot":          take_screenshot,
    "lock_screen":         lock_screen,
    "open_settings":       open_system_settings,
    "file_explorer":       open_file_explorer,
    "open_run":            open_run,
    "dark_mode":           dark_mode,
    "toggle_wifi":         toggle_wifi,
    "restart":             restart_computer,
    "shutdown":            shutdown_computer,
}

_DANGEROUS_ACTIONS = {"restart", "shutdown"}


def _detect_action(description: str) -> dict:
    from or_client import client

    available = ", ".join(sorted(ACTION_MAP.keys())) + \
                ", volume_set, type_text, press_key, reload_n, list_apps"

    prompt = f"""You are an intent detector for a computer control assistant.
The user issued a command (possibly in any language): "{description}"
Available actions: {available}
Return ONLY a valid JSON object: {{"action": "action_name", "value": null_or_value}}
Rules:
- For volume_set: value is an integer 0-100.
- For type_text: value is the exact text to type.
- For press_key: value is the key name (e.g. "f5", "tab", "enter").
- For reload_n: value is an integer.
- For close_app: value is the NAME of the app the user wants to close
  (e.g. "close teams" -> {{"action": "close_app", "value": "teams"}}).
  Only use null if the user did not name any app ("close this").
- For list_apps: use when the user asks what apps/windows are open.
- Return ONLY the JSON, no explanation, no markdown."""

    try:
        raw  = client.chat_json(prompt, system="Return only valid JSON. No extra text.")
        return raw
    except Exception as e:
        print(f"[Settings] Intent detection failed: {e}")
        return {"action": description.lower().replace(" ", "_"), "value": None}
    
def computer_settings(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    if not _PYAUTOGUI:
        return "pyautogui is not installed. Run: pip install pyautogui"

    params      = parameters or {}
    raw_action  = params.get("action", "").strip()
    description = params.get("description", "").strip()
    value       = params.get("value", None)

    if not raw_action and description:
        detected   = _detect_action(description)
        raw_action = detected.get("action", "")
        if value is None:
            value = detected.get("value")

    action = raw_action.lower().strip().replace(" ", "_").replace("-", "_")

    if not action:
        return "No action could be determined."

    print(f"[Settings] Action: {action}  Value: {value}  OS: {_OS}")
    if player:
        player.write_log(f"[Settings] {action}")

    if action in _DANGEROUS_ACTIONS:
        confirmed = str(params.get("confirmed", "")).lower()
        if confirmed not in ("yes", "true", "1", "confirm"):
            return (
                f"This will {action} the computer. "
                f"Please confirm by calling again with confirmed=yes."
            )

    if action in ("close_app", "quit_app", "kill_app", "close_application"):
        app = str(params.get("app_name") or value or "").strip()
        return close_app(app)

    if action in ("list_apps", "open_apps", "running_apps", "list_open_apps"):
        apps = list_open_apps()
        if not apps:
            return "I couldn't detect any open applications."
        return "Currently open applications: " + ", ".join(apps) + "."

    if action == "volume_set":
        try:
            volume_set(int(value or 50))
            return f"Volume set to {value}%."
        except Exception as e:
            return f"Could not set volume: {e}"

    if action in ("type_text", "write_on_screen", "type", "write"):
        text = str(value or params.get("text", "")).strip()
        if not text:
            return "No text provided to type."
        enter_after = str(params.get("press_enter", "false")).lower() in ("true", "1", "yes")
        type_text(text, press_enter_after=enter_after)
        return f"Typed: {text[:80]}"

    if action == "press_key":
        key = str(value or params.get("key", "")).strip()
        if not key:
            return "No key specified."
        press_key(key)
        return f"Pressed: {key}"

    if action in ("reload_n", "refresh_n", "reload_page_n"):
        try:
            reload_page_n(int(value or 1))
            return f"Reloaded {value or 1} time(s)."
        except Exception as e:
            return f"Reload failed: {e}"

    if action == "scroll_up":
        scroll_up(int(value or 500))
        return "Scrolled up."

    if action == "scroll_down":
        scroll_down(int(value or 500))
        return "Scrolled down."

    func = ACTION_MAP.get(action)
    if not func:
        return f"Unknown action: '{raw_action}'."

    try:
        func()
        return f"Done: {action}."
    except Exception as e:
        print(f"[Settings] Action failed ({action}): {e}")
        return f"Action failed ({action}): {e}"