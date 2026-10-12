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
    # 1) PyMuPDF — best text extraction; 2) pypdf as backup;
    # 3) OCR fallback when the PDF is a scan (no embedded text).
    text = ""
    try:
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
        doc = pymupdf.open(stream=data, filetype="pdf")
        pages = []
        for i, page in enumerate(doc):
            if i >= 60:
                break
            pages.append(page.get_text())
        doc.close()
        text = "\n".join(pages)
    except Exception:
        pass

    if not text.strip():
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            pages = []
            for page in reader.pages[:60]:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:
                    continue
            text = "\n".join(pages)
        except Exception:
            pass

    # Scanned / image-only PDF → OCR the rendered pages
    if len(text.strip()) < 100:
        print("[ReadDoc] 🔍 No embedded text — running OCR…")
        try:
            from actions.ocr_utils import ocr_pdf_bytes
        except ImportError:
            from ocr_utils import ocr_pdf_bytes
        ocr_text = ocr_pdf_bytes(data)
        if len(ocr_text.strip()) > len(text.strip()):
            return ocr_text
    return text


def _image_text(data: bytes) -> str:
    try:
        from actions.ocr_utils import ocr_image_bytes
    except ImportError:
        from ocr_utils import ocr_image_bytes
    return ocr_image_bytes(data)


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
    if ctype.startswith("image/"):
        return _image_text(r.content)
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


def _find_candidates(name: str) -> list[Path]:
    """All distinct-name matches, newest first (for 'which one?' flows)."""
    name_low = name.lower().strip()
    home = Path.home()
    seen: dict[str, Path] = {}
    hits: list[Path] = []
    for d in SEARCH_DIRS:
        base = home / d if d else home
        if not base.exists():
            continue
        try:
            pools = [base] + ([s for s in base.iterdir()
                               if s.is_dir() and not s.name.startswith(".")]
                              if d else [])
            for pool in pools:
                for p in pool.iterdir():
                    if p.is_file() and name_low in p.name.lower():
                        key = p.name.lower()
                        if key not in seen or \
                           p.stat().st_mtime > seen[key].stat().st_mtime:
                            seen[key] = p
        except PermissionError:
            continue
    hits = sorted(seen.values(), key=lambda p: -p.stat().st_mtime)
    return hits[:6]


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif",
              ".bmp", ".heic", ".gif"}


def _read_file(path: Path) -> str:
    ext = path.suffix.lower()
    data = path.read_bytes()
    if ext == ".pdf":
        return _pdf_text(data)
    if ext == ".docx":
        return _docx_text(data)
    if ext in (".html", ".htm"):
        return _html_text(data)
    if ext in IMAGE_EXTS:
        return _image_text(data)
    if ext in TEXT_EXTS or not ext:
        return data.decode("utf-8", errors="replace")
    return (f"[error] I can read pdf, docx, html, text and image files — "
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
            # full path given (drag-dropped / dictated) → use it directly
            direct = Path(file_name).expanduser()
            if ("/" in file_name or "\\" in file_name) and direct.is_file():
                print(f"[ReadDoc] 📄 {direct}")
                text   = _read_file(direct)
                origin = direct.name
                matches = None
            else:
                matches = _find_candidates(file_name)
            if matches is not None:
                if not matches:
                    return (f"I couldn't find a file matching '{file_name}' in "
                            f"Desktop, Downloads or Documents, sir.")
                if len(matches) > 1:
                    names = " | ".join(m.name for m in matches)
                    return (f"MULTIPLE MATCHES for '{file_name}': {names}. "
                            f"Call show_option_picker with these file names "
                            f"(on_select 'say'); when the user picks one, call "
                            f"read_document again with that exact file_name.")
                found = matches[0]
                print(f"[ReadDoc] 📄 {found}")
                text = _read_file(found)
                origin = found.name

        if text.startswith("[error]"):
            return text
        if not text.strip():
            return (f"'{origin}' appears to be empty — I even tried OCR but "
                    f"found no readable text, sir.")
        if player:
            player.write_log(f"[ReadDoc] {origin} — {len(text)} chars")
        return (f"CONTENT OF {origin}:\n\n{_clip(text)}\n\n"
                f"(Answer the user's request about this document concisely.)")
    except Exception as e:
        print(f"[ReadDoc] ❌ {e}")
        return f"I couldn't read that document, sir: {str(e)[:120]}"
