# actions/ocr_utils.py
# JARVIS ARVION — OCR for scanned PDFs and images
#
# Tier 1: Apple Vision framework (macOS) — best quality, auto language
#         detection, no external binaries.
# Tier 2: Tesseract via pytesseract — cross-platform fallback
#         (needs the tesseract binary: brew install tesseract).
#
# Public API:
#   ocr_image_bytes(data)              → text from one image (png/jpg/heic/…)
#   ocr_pdf_bytes(data, max_pages=15)  → text from a scanned PDF

import io
import platform

_TESS_LANGS = "eng+fra+ara"   # tried first; falls back to eng-only, then default


def _ocr_macos(image_bytes: bytes) -> str:
    """Apple Vision OCR — accepts raw image data (png, jpg, heic, tiff…)."""
    try:
        import Vision
        from Foundation import NSData
    except ImportError:
        return ""
    try:
        data    = NSData.dataWithBytes_length_(image_bytes, len(image_bytes))
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(True)
        try:
            request.setAutomaticallyDetectsLanguage_(True)   # macOS 13+
        except Exception:
            pass
        ok = handler.performRequests_error_([request], None)
        if isinstance(ok, tuple):          # pyobjc returns (bool, error)
            ok = ok[0]
        if not ok:
            return ""
        lines = []
        for obs in request.results() or []:
            cands = obs.topCandidates_(1)
            if cands and len(cands):
                lines.append(str(cands[0].string()))
        return "\n".join(lines)
    except Exception as e:
        print(f"[OCR] ⚠️ Vision failed: {e}")
        return ""


def _ocr_tesseract(image_bytes: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        for lang in (_TESS_LANGS, "eng", None):
            try:
                if lang:
                    return pytesseract.image_to_string(img, lang=lang)
                return pytesseract.image_to_string(img)
            except pytesseract.TesseractError:
                continue
        return ""
    except Exception as e:
        print(f"[OCR] ⚠️ Tesseract failed: {e}")
        return ""


def ocr_image_bytes(image_bytes: bytes) -> str:
    """OCR one image. Apple Vision first on macOS, then tesseract."""
    if platform.system() == "Darwin":
        text = _ocr_macos(image_bytes)
        if text.strip():
            return text
    return _ocr_tesseract(image_bytes)


def ocr_pdf_bytes(data: bytes, max_pages: int = 15) -> str:
    """Render each PDF page to an image and OCR it (for scanned PDFs)."""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            print("[OCR] ⚠️ pymupdf not installed — cannot OCR PDF")
            return ""

    pages = []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        total = doc.page_count
        for i, page in enumerate(doc):
            if i >= max_pages:
                pages.append(f"[... OCR stopped at page {max_pages} of {total} ...]")
                break
            pix = page.get_pixmap(dpi=200)
            text = ocr_image_bytes(pix.tobytes("png"))
            if text.strip():
                pages.append(text.strip())
            print(f"[OCR] 📄 page {i + 1}/{min(total, max_pages)} — "
                  f"{len(text)} chars")
        doc.close()
    except Exception as e:
        print(f"[OCR] ❌ PDF OCR failed: {e}")
    return "\n\n".join(pages)
