# actions/read_document.py
# JARVIS ARVION — universal document reader
#
# One tool, three sources:
#   url       — web page, online PDF, or a Google Drive share link
#   file_name — finds the file by (fuzzy) name in Desktop/Downloads/Documents/home
#   screen    — delegates to the vision module (what's visible right now)
#
# Returns the extracted TEXT to the live model, which then summarizes /
# answers / reads it aloud — so "read me the pdf report" just works.

import io
import re
from pathlib import Path

import requests

MAX_CHARS    = 9000          # keep tool responses within a sane prompt size
SEARCH_DIRS  = ["Desktop", "Downloads", "Documents", ""]
TEXT_EXTS    = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html",
                ".css", ".yaml", ".yml", ".toml", ".sh", ".log", ".tex"}

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh) JarvisArvion/1.0"}


def _clip(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n[... truncated — document continues ...]"
    return text


# ───────────── extraction by format ─────────────

def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages[:60]:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(pages)


def _docx_text(data: bytes) -> str:
    import docx
    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _html_text(data: bytes) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text("\n")
    return re.sub(r"[ \t]+", " ", text)


def _bytes_to_text(data: bytes, hint: str) -> str:
    h = hint.lower()
    if h.endswith(".pdf") or data[:5] == b"%PDF-":
        return _pdf_text(data)
    if h.endswith(".docx") or data[:2] == b"PK" and ".doc" in h:
        return _docx_text(data)
    if h.endswith(".docx"):
        return _docx_text(data)
    if b"<html" in data[:2000].lower() or h.endswith((".html", ".htm")):
        return _html_text(data)
    return data.decode("utf-8", errors="replace")


# ───────────── sources ─────────────

def _normalize_drive_url(url: str) -> str:
    """Google Drive / Docs share links → direct download links."""
    m = re.search(r"drive\.google\.com/file/d/([\w-]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    m = re.search(r"drive\.google\.com/open\?id=([\w-]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    m = re.search(r"docs\.google\.com/document/d/([\w-]+)", url)
    if m:
        return f"https://docs.google.com/document/d/{m.group(1)}/export?format=txt"
    m = re.search(r"docs\.google\.com/spreadsheets/d/([\w-]+)", url)
    if m:
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
    m = re.search(r"docs\.google\.com/presentation/d/([\w-]+)", url)
    if m:
        return f"https://docs.google.com/presentation/d/{m.group(1)}/export/txt"
    return url


def _read_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    url = _normalize_drive_url(url)
    r = requests.get(url, headers=_UA, timeout=30, allow_redirects=True)

    # Drive's "can't scan for viruses" interstitial for big files
    if b"confirm=" in r.content[:4000] and b"drive.google.com" in r.content[:4000]:
        m = re.search(rb'confirm=([\w-]+)', r.content)
        if m:
            r = requests.get(url + "&confirm=" + m.group(1).decode(),
                             headers=_UA, timeout=60)

    if r.status_code != 200:
        return f"[error] HTTP {r.status_code} fetching {url}"
    hint = url.split("?")[0]
    ctype = r.headers.get("Content-Type", "")
    if "pdf" in ctype:
        hint += ".pdf"
    return _bytes_to_text(r.content, hint)


def _find_file(name: str) -> Path | None:
    name_low = name.lower().strip()
    home = Path.home()
    candidates = []
    for d in SEARCH_DIRS:
        base = home / d if d else home
        if not base.exists():
            continue
        try:
            for p in base.iterdir():
                if p.is_file() and name_low in p.name.lower():
                    candidates.append(p)
            # one level deeper for Desktop/Documents folders
            if d:
                for sub in base.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        for p in sub.iterdir():
                            if p.is_file() and name_low in p.name.lower():
                                candidates.append(p)
        except PermissionError:
            continue
    if not candidates:
        return None
    # newest match wins
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _read_file(path: Path) -> str:
    ext = path.suffix.lower()
    data = path.read_bytes()
    if ext == ".pdf":
        return _pdf_text(data)
    if ext == ".docx":
        return _docx_text(data)
    if ext in (".html", ".htm"):
        return _html_text(data)
    if ext in TEXT_EXTS or not ext:
        return data.decode("utf-8", errors="replace")
    return (f"[error] I can read pdf, docx, html and text files — "
            f"'{path.name}' is {ext or 'unknown type'}.")


# ───────────── the tool ─────────────

def read_document(parameters=None, response=None, player=None,
                  session_memory=None) -> str:
    p         = parameters or {}
    url       = (p.get("url") or "").strip()
    file_name = (p.get("file_name") or "").strip()
    source    = (p.get("source") or "").strip().lower()

    try:
        if source == "screen" or (not url and not file_name):
            # delegate to the vision module — reads whatever is displayed
            from actions.screen_processor import screen_process
            q = p.get("question") or "Read the document on screen and summarize it."
            screen_process({"angle": "screen", "text": q}, player=player)
            return ("Reading the screen now — the vision module will answer "
                    "aloud. Stay silent.")

        if url:
            print(f"[ReadDoc] 🌐 {url[:80]}")
            text = _read_url(url)
            origin = url
        else:
            found = _find_file(file_name)
            if not found:
                return (f"I couldn't find a file matching '{file_name}' in "
                        f"Desktop, Downloads or Documents, sir.")
            print(f"[ReadDoc] 📄 {found}")
            text = _read_file(found)
            origin = found.name

        if text.startswith("[error]"):
            return text
        if not text.strip():
            return (f"'{origin}' appears to be empty or an image-only scan — "
                    f"no text I can extract, sir.")
        if player:
            player.write_log(f"[ReadDoc] {origin} — {len(text)} chars")
        return (f"CONTENT OF {origin}:\n\n{_clip(text)}\n\n"
                f"(Answer the user's request about this document concisely.)")
    except Exception as e:
        print(f"[ReadDoc] ❌ {e}")
        return f"I couldn't read that document, sir: {str(e)[:120]}"
