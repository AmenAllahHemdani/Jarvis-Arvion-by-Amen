# actions/explain_file.py

import os
import re
import platform
import subprocess
from datetime import datetime
from pathlib import Path

_OS = platform.system()


def _speak_and_log(message: str, player=None):
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass
    print(f"[ExplainFile] {message}")


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\s-]", "", name).strip()
    name = re.sub(r"[\s]+", "_", name)
    return name[:60] or "explanation"


def _output_dir() -> Path:
    desktop = Path.home() / "Desktop"
    if desktop.exists():
        return desktop
    return Path.home()


def _open_file(path: Path) -> bool:
    try:
        if _OS == "Darwin":
            subprocess.Popen(["open", str(path)])
        elif _OS == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception as e:
        print(f"[ExplainFile] ⚠️ Could not open file: {e}")
        return False


def explain_file(parameters: dict, player=None):
    """
    Generates an explanation of a topic, writes it to a file and opens it.

    parameters:
        topic     (required) — what to explain
        file_name (optional) — file name without extension
        format    (optional) — txt | md | html  (default: txt)
        detail    (optional) — short | detailed (default: detailed)
        language  (optional) — language of the explanation
    """
    topic = parameters.get("topic")
    if not topic or not isinstance(topic, str):
        msg = "Sir, I need a topic to explain."
        _speak_and_log(msg, player)
        return msg

    topic    = topic.strip()
    fmt      = str(parameters.get("format", "txt")).lower().strip(". ")
    if fmt not in ("txt", "md", "html"):
        fmt = "txt"
    detail   = str(parameters.get("detail", "detailed")).lower()
    language = str(parameters.get("language", "")).strip()

    length_rule = (
        "Keep it compact: roughly half a page."
        if detail == "short" else
        "Be thorough: cover the key ideas, give examples, and finish with a short summary."
    )
    lang_rule = f"Write the explanation in {language}." if language else \
                "Write the explanation in the same language as the topic."

    if fmt == "html":
        style_rule = (
            "Return a complete standalone HTML document (<!DOCTYPE html> ... </html>) "
            "with simple embedded CSS, clear headings and readable typography. "
            "Return ONLY the HTML, no markdown fences."
        )
    elif fmt == "md":
        style_rule = (
            "Format the explanation as clean Markdown with headings and bullet "
            "points. Return ONLY the Markdown content, no code fences around it."
        )
    else:
        style_rule = (
            "Format the explanation as plain text: clear title line, short "
            "paragraphs, simple dashes for lists. No markdown symbols like # or **."
        )

    prompt = (
        f"Write an explanation of the following topic for a document file.\n"
        f"Topic: {topic}\n\n{lang_rule}\n{length_rule}\n{style_rule}"
    )

    try:
        from or_client import client
        content = client.chat(
            prompt,
            system="You are a precise technical writer producing clear, well-structured documents.",
            max_tokens=4096,
        )
    except Exception as e:
        msg = f"Sir, I couldn't generate the explanation: {e}"
        _speak_and_log(msg, player)
        return msg

    if not content or not content.strip():
        msg = "Sir, the explanation came back empty. Please try again."
        _speak_and_log(msg, player)
        return msg

    content = content.strip()
    # strip accidental code fences around the whole document
    content = re.sub(r"^```[a-zA-Z]*\n", "", content)
    content = re.sub(r"\n```$", "", content).strip()

    file_name = parameters.get("file_name")
    base = _safe_filename(file_name if isinstance(file_name, str) and file_name.strip()
                          else topic)
    path = _output_dir() / f"{base}.{fmt}"
    if path.exists():
        stamp = datetime.now().strftime("%H%M%S")
        path  = _output_dir() / f"{base}_{stamp}.{fmt}"

    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        msg = f"Sir, I couldn't save the file: {e}"
        _speak_and_log(msg, player)
        return msg

    opened = _open_file(path)
    msg = (f"Explanation saved to {path.name} on the Desktop"
           f"{' and opened on screen' if opened else ', but I could not open it automatically'}.")
    _speak_and_log(msg, player)
    return msg
