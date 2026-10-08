import asyncio
import threading
import json
import sys
import time
import traceback
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.explain_file      import explain_file
from actions.send_email        import send_email
from actions.call_me           import call_me


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
# Live models in preference order. If a model keeps killing sessions
# instantly (like gemini-3.8-live's server-side 1011 errors), JARVIS
# automatically rotates to the next one.
LIVE_MODELS = [
    "models/gemini-3.1-flash-live-preview",        # verified working 2026-10-05
    "models/gemini-2.5-flash-native-audio-latest", # verified working 2026-10-05
    "models/gemini-3.8-live",                      # broken server-side (1011) 2026-10-05
]
LIVE_MODEL          = LIVE_MODELS[0]
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )
    
_last_memory_input = ""

_CONNECTION_ERROR_NAMES = {
    "ConnectionClosed", "ConnectionClosedError", "ConnectionClosedOK",
    "APIError", "ServerError", "ClientError", "WebSocketException",
}

def _is_connection_error(exc: BaseException) -> bool:
    """Transient network / Gemini Live session drop — expected, auto-reconnected."""
    if isinstance(exc, (ConnectionError, TimeoutError, OSError, asyncio.CancelledError)):
        return True
    for cls in type(exc).__mro__:
        if cls.__name__ in _CONNECTION_ERROR_NAMES:
            return True
    return False

def _flatten_exceptions(exc: BaseException) -> list:
    """Unwrap (nested) ExceptionGroups into a flat list of leaf exceptions."""
    if isinstance(exc, BaseExceptionGroup):
        leaves = []
        for sub in exc.exceptions:
            leaves.extend(_flatten_exceptions(sub))
        return leaves
    return [exc]


def _update_memory_async(user_text: str, jarvis_text: str) -> None:
    global _last_memory_input

    user_text   = (user_text   or "").strip()
    jarvis_text = (jarvis_text or "").strip()

    if len(user_text) < 5 or user_text == _last_memory_input:
        return
    _last_memory_input = user_text

    try:
        api_key = _get_api_key()
        if not should_extract_memory(user_text, jarvis_text, api_key):
            return
        data = extract_memory(user_text, jarvis_text, api_key)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the Windows computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "call_me",
        "description": (
            "Rings the owner's phone through their private Telegram bot: "
            "sends loud repeated alert notifications plus a voice message "
            "that speaks the text aloud. Use when the user asks to be "
            "called, phoned, or alerted on their phone about something."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message":  {"type": "STRING", "description": "What the voice should say on the call, max 250 characters"},
                "language": {"type": "STRING", "description": "Voice language: english, french, arabic, german, spanish, italian, turkish. Default english"},
                "repeat":   {"type": "NUMBER", "description": "How many times to repeat the message, 1-5. Default 2"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "send_email",
        "description": (
            "Sends an email. Use when the user asks to email them something: "
            "a note, reminder, summary, search result, or any information. "
            "If no recipient is given, it goes to the owner's own address."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "subject": {"type": "STRING", "description": "Email subject line"},
                "body":    {"type": "STRING", "description": "Full email message text. Write it out completely."},
                "to":      {"type": "STRING", "description": "Optional recipient email address. Omit to send to the owner."}
            },
            "required": ["subject", "body"]
        }
    },
    {
        "name": "explain_file",
        "description": (
            "Writes an explanation, lesson, summary, or notes about any topic "
            "into a document file, saves it to the Desktop and opens it on screen. "
            "Use when the user asks to explain something in a file, write notes or "
            "a summary to a file, or create a document about a topic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic":     {"type": "STRING", "description": "What to explain, including any details the user gave"},
                "file_name": {"type": "STRING", "description": "Optional file name without extension"},
                "format":    {"type": "STRING", "description": "txt (default), md, or html"},
                "detail":    {"type": "STRING", "description": "short or detailed (default: detailed)"},
                "language":  {"type": "STRING", "description": "Language to write in, e.g. English, French, Arabic. Default: language the user spoke"}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": (
            "Sets a timed reminder for a future time: at that moment it shows "
            "a notification on the computer AND rings the owner's phone. "
            "Use for 'remind me at/in X' and also 'call me in N minutes' or "
            "'call me at X time' — compute the exact date and time from the "
            "current time context."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Opens a NEW YouTube video. Use ONLY when the user names new content "
            "to search and play. NEVER use this to pause/resume/continue media "
            "that is already open — that is computer_settings action 'play_pause'. "
            "Also: summarizing a video's content, video info, trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "To close a SPECIFIC app, use action 'close_app' and ALWAYS pass its name in app_name "
            "(e.g. app_name='Teams') — never rely on the focused window when the user named an app. "
            "Use action 'list_apps' to see which applications are currently open. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform (e.g. close_app, list_apps, volume_set)"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."},
                "app_name":    {"type": "STRING", "description": "Target application name for close_app (e.g. 'Teams', 'Chrome'). REQUIRED when the user names an app to close."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls the web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, any web-based task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | press | close"},
                "url":         {"type": "STRING", "description": "URL for go_to action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "key":         {"type": "STRING", "description": "Key name for press action"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
    "name": "shutdown_jarvis",
    "description": (
        "Shuts down the assistant completely. "
        "Call this when the user expresses intent to end the conversation, "
        "close the assistant, say goodbye, or stop Jarvis. "
        "The user can say this in ANY language."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Amen, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "save_routine",
        "description": (
            "Teach yourself a custom command/habit the user wants automated. "
            "Call this whenever the user says things like: 'when I say X, do Y', "
            "'every time I ask for X you should...', 'always open X fullscreen', "
            "'from now on...', or teaches you a shortcut phrase. "
            "From then on, when the user says the trigger phrase, you execute the "
            "saved actions immediately with your tools, without asking. "
            "Store actions concretely (which tools, which apps, what order). "
            "Values in English regardless of conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "trigger": {"type": "STRING", "description": "The phrase that activates it, snake_case (e.g. start_work, movie_mode)"},
                "actions": {"type": "STRING", "description": "Exactly what to do, step by step (e.g. 'open_app Google Chrome, then open_app Microsoft Teams, then open_app Visual Studio Code')"},
            },
            "required": ["trigger", "actions"]
        }
    },
    {
        "name": "forget_routine",
        "description": "Delete a saved routine/custom command when the user asks to remove or change it.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "trigger": {"type": "STRING", "description": "The trigger phrase of the routine to delete"},
            },
            "required": ["trigger"]
        }
    },
]


class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self._resume_handle = None
        self.ui.on_text_command = self._on_text_command

        # Wake-word standby: when enabled, mic audio is analysed on-device
        # and nothing streams to Gemini until "hey Jarvis" is heard.
        self._wake_detector = None
        self._awake_until   = 0.0      # awake while time.time() < this
        self._was_awake     = True
        try:
            cfg = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
            if cfg.get("wake_word", False):
                from actions.wake_word import WakeWordDetector
                det = WakeWordDetector()
                if det.available:
                    self._wake_detector = det
                    self._was_awake     = False
        except Exception as e:
            print(f"[WakeWord] init failed: {e}")

    def _is_awake(self) -> bool:
        return self._wake_detector is None or time.time() < self._awake_until

    def _extend_awake(self, seconds: float = 45.0):
        if self._wake_detector is not None:
            self._awake_until = max(self._awake_until, time.time() + seconds)

    def _wake_up(self):
        print("[WakeWord] 🟢 'hey Jarvis' — listening")
        self._extend_awake(45)
        self._was_awake = True
        self.ui.write_log("SYS: Wake word — at your service.")
        self._badge("core", "AI CORE\nACTIVE", "#00ff88")
        self.ui.set_state("LISTENING")
        if self.session and self._loop:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"role": "user",
                           "parts": [{"text": "[WAKE] The user just said the wake "
                                              "word. Reply with only: 'Yes, sir?'"}]},
                    turn_complete=True),
                self._loop)

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def _badge(self, which: str, text: str, color: str):
        try:
            self.ui.set_badge(which, text, color)
        except Exception:
            pass

    @staticmethod
    def _model_short(model: str) -> str:
        s = model.split("/")[-1].replace("gemini-", "")
        return "-".join(s.split("-")[:2]).upper()

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")
        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        if name == "save_routine":
            trigger = args.get("trigger", "").strip().lower().replace(" ", "_")
            actions = args.get("actions", "").strip()
            if trigger and actions:
                update_memory({"routines": {trigger: {"value": actions}}})
                print(f"[Memory] 🤖 save_routine: '{trigger}' → {actions[:80]}")
                self.ui.write_log(f"SYS: Learned command '{trigger.replace('_', ' ')}'")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": f"Routine '{trigger}' saved. Execute it whenever the user says it."}
            )

        if name == "forget_routine":
            from memory.memory_manager import forget
            trigger = args.get("trigger", "").strip().lower().replace(" ", "_")
            result  = forget(trigger, "routines") if trigger else "No trigger given."
            print(f"[Memory] 🗑️ forget_routine: {trigger}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": result}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "explain_file":
                r = await loop.run_in_executor(None, lambda: explain_file(parameters=args, player=self.ui))
                result = r or "Explanation file created and opened."

            elif name == "send_email":
                r = await loop.run_in_executor(None, lambda: send_email(parameters=args, player=self.ui))
                result = r or "Email sent."

            elif name == "call_me":
                r = await loop.run_in_executor(None, lambda: call_me(parameters=args, player=self.ui))
                result = r or "Call placed."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."


            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=self.speak)
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")

                def _shutdown():
                    import time, sys, os
                    time.sleep(1)
                    os._exit(0)

                threading.Thread(target=_shutdown, daemon=True).start()
            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    def _enqueue_mic_audio(self, item: dict):
        """Runs on the event loop. Live audio must never error on overflow:
        if the uploader falls behind, drop the oldest frame and move on."""
        q = self.out_queue
        if q is None:
            return
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(
                audio=types.Blob(data=msg["data"], mime_type=msg["mime_type"])
            )

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        # While JARVIS speaks the mic stays live so the user can barge in
        # ("shut up", "stop", or just talking over him). A loudness gate
        # keeps his own voice echoing from the speakers from interrupting
        # him: echo arrives much quieter than direct speech into the mic.
        BARGE_IN_RMS = 900   # int16 RMS; raise if he interrupts himself

        def callback(indata, frames, time_info, status):
            # standby: analyse locally for "hey Jarvis", stream nothing
            if not self._is_awake():
                if self._wake_detector.feed(indata.copy()):
                    loop.call_soon_threadsafe(self._wake_up)
                return
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if jarvis_speaking:
                rms = float((indata.astype("float32") ** 2).mean()) ** 0.5
                if rms < BARGE_IN_RMS:
                    return
            data = indata.tobytes()
            loop.call_soon_threadsafe(
                self._enqueue_mic_audio,
                {"data": data, "mime_type": "audio/pcm;rate=16000"}
            )

        # While muted the input device is fully RELEASED (not just ignored):
        # holding a mic open forces Bluetooth headphones into low-quality
        # call mode (HFP) — releasing it lets music return to hi-fi A2DP.
        stream = None
        try:
            while True:
                if self.ui.muted:
                    if stream is not None:
                        stream.stop(); stream.close(); stream = None
                        print("[JARVIS] 🎤 Mic released (muted) — audio back to hi-fi")
                else:
                    if stream is None:
                        stream = sd.InputStream(
                            samplerate=SEND_SAMPLE_RATE,
                            channels=CHANNELS,
                            dtype="int16",
                            blocksize=CHUNK_SIZE,
                            callback=callback,
                        )
                        stream.start()
                        print("[JARVIS] 🎤 Mic stream open")

                # awake window expired → announce standby once
                if self._wake_detector is not None:
                    awake = self._is_awake()
                    if self._was_awake and not awake:
                        self._was_awake = False
                        self._wake_detector.reset()
                        print("[WakeWord] 💤 Standby — say 'hey Jarvis' to wake me")
                        self.ui.write_log("SYS: Standby — say 'hey Jarvis'.")
                        self._badge("core", "AI CORE\nSTANDBY", "#5ab8cc")
                await asyncio.sleep(0.3)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise
        finally:
            if stream is not None:
                stream.stop(); stream.close()

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    sru = getattr(response, "session_resumption_update", None)
                    if sru and sru.resumable and sru.new_handle:
                        self._resume_handle = sru.new_handle

                    go_away = getattr(response, "go_away", None)
                    if go_away:
                        print(f"[JARVIS] 🔌 Server closing session soon "
                              f"(time left: {go_away.time_left})")

                    if response.data:
                        self.audio_in_queue.put_nowait(response.data)
                        self._extend_awake(30)   # he's mid-answer — stay awake

                    if response.server_content:
                        sc = response.server_content

                        if sc.input_transcription and sc.input_transcription.text:
                            self._extend_awake(45)   # user is talking

                        if sc.interrupted:
                            # user talked over JARVIS — dump every chunk of
                            # buffered speech so he goes quiet immediately
                            flushed = 0
                            while not self.audio_in_queue.empty():
                                try:
                                    self.audio_in_queue.get_nowait()
                                    flushed += 1
                                except asyncio.QueueEmpty:
                                    break
                            self.set_speaking(False)
                            out_buf = []
                            print(f"[JARVIS] 🤫 Interrupted — flushed {flushed} audio chunks")

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            self.set_speaking(False)

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if self._wake_detector is not None and any(
                                        p in full_in.lower() for p in
                                        ("go to sleep", "standby", "stand by",
                                         "va dormir", "mets-toi en veille")):
                                    self._awake_until = 0.0
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                                try:
                                    from actions import telegram_bridge
                                    telegram_bridge.on_turn_output(full_out)
                                except Exception:
                                    pass
                                try:
                                    from actions import app_bridge
                                    app_bridge.on_turn_output(full_out)
                                except Exception:
                                    pass
                            out_buf = []

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True
                                ).start()

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            if _is_connection_error(e):
                print(f"[JARVIS] 🔌 Live connection dropped (will reconnect): {e}")
            else:
                print(f"[JARVIS] ❌ Recv: {e}")
                traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")
        loop = asyncio.get_event_loop()

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                # single-voice rule: while the vision module is speaking,
                # the main session's audio is dropped so the two Gemini
                # voices never overlap on the speakers
                try:
                    from actions import screen_processor
                    if screen_processor.vision_is_speaking():
                        self.set_speaking(False)
                        continue
                except Exception:
                    pass
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        model_idx   = 0
        fail_streak = 0

        while True:
            model = LIVE_MODELS[model_idx % len(LIVE_MODELS)]
            session_start = None
            try:
                print(f"[JARVIS] 🔌 Connecting ({model.split('/')[-1]})...")
                self.ui.set_state("THINKING")
                self._badge("core", "AI CORE\nBOOTING", "#ffcc00")
                config    = self._build_config()
                resuming  = bool(self._resume_handle)
                connected = False

                async with (
                    client.aio.live.connect(model=model, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    connected           = True
                    session_start       = time.time()
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=32)

                    if resuming:
                        print("[JARVIS] ✅ Connected (session resumed).")
                        self.ui.write_log("SYS: JARVIS back online — conversation resumed.")
                    else:
                        print("[JARVIS] ✅ Connected.")
                        self.ui.write_log("SYS: JARVIS online.")
                    self.ui.set_state("LISTENING")

                    self._badge("core", "AI CORE\nACTIVE", "#00ff88")
                    self._badge("model", f"MODEL\n{self._model_short(model)}", "#00d4ff")
                    try:
                        cfg = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
                        if str(cfg.get("telegram_bot_token", "")).strip():
                            self._badge("bridge", "BRIDGE\nLOCAL", "#00ff88")
                        else:
                            self._badge("bridge", "BRIDGE\nOFF", "#3a8a9a")
                    except Exception:
                        pass

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except Exception as e:
                leaves = _flatten_exceptions(e)
                if leaves and all(_is_connection_error(x) for x in leaves):
                    print(f"[JARVIS] 🔌 Session ended ({leaves[0]})")
                    self.ui.write_log("SYS: Connection lost — reconnecting...")
                else:
                    print(f"[JARVIS] ⚠️ {e}")
                    traceback.print_exc()

                if not connected and self._resume_handle:
                    print("[JARVIS] ⚠️ Resume handle rejected — starting a fresh session.")
                    self._resume_handle = None

            # Model failover: two sessions in a row dying within 30s means
            # the model itself is broken (e.g. server-side 1011) — rotate.
            alive = (time.time() - session_start) if session_start else 0
            if alive < 30:
                fail_streak += 1
                if fail_streak >= 2:
                    model_idx  += 1
                    fail_streak = 0
                    self._resume_handle = None  # handles don't transfer between models
                    nxt = LIVE_MODELS[model_idx % len(LIVE_MODELS)].split('/')[-1]
                    print(f"[JARVIS] 🔁 {model.split('/')[-1]} keeps failing — switching to {nxt}")
                    self.ui.write_log(f"SYS: Model unstable — switching to {nxt}")
            else:
                fail_streak = 0

            self.set_speaking(False)
            self.ui.set_state("THINKING")
            self._badge("core", "AI CORE\nLINK LOST", "#ff3355")
            print("[JARVIS] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)

def main():
    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            from actions import telegram_bridge
            telegram_bridge.set_app(jarvis)
        except Exception:
            pass
        try:
            from actions import app_bridge
            app_bridge.set_app(jarvis)
        except Exception:
            pass
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    from actions.typing_utils import detect_layout
    detect_layout()
    from actions.telegram_bridge import start_telegram_bridge
    start_telegram_bridge()
    from actions.app_bridge import start_app_bridge
    start_app_bridge()
    main()