"""Secure PDF opening and word extraction (native text first, OCR fallback).

Nothing here ever logs or returns the password, and decrypted copies are
never written to disk: the document is authenticated in memory only.
"""
from __future__ import annotations

import hashlib
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import pymupdf

from . import config


log = logging.getLogger(__name__)

NATIVE_MIN_WORDS = 8          # fewer native words than this => page treated as "no text layer"


class PdfError(Exception):
    """Base class for PDF reading problems."""


class PdfPasswordMissing(PdfError):
    """The PDF is encrypted but no password is configured."""


class PdfPasswordIncorrect(PdfError):
    """None of the configured passwords opened the PDF."""


class OcrUnavailable(PdfError):
    """OCR was needed but pytesseract/OpenCV/Tesseract is not usable."""


@dataclass(frozen=True)
class Word:
    """A word with its bounding box in PDF points (origin top-left)."""
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    conf: float = 100.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class PageWords:
    page_no: int                 # 1-based
    width: float
    height: float
    words: list[Word]
    source: str                  # "native" | "ocr" | "none"
    has_graphics: bool           # images/vector drawings present (possible text as pixels/paths)
    warnings: list[str]


def default_passwords() -> list[str]:
    """Read all configured passwords without exposing them."""
    return [
        p for p in config.SBI_PDF_PASSWORDS
        if p
    ]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()



@contextmanager
def open_pdf(
    path: Path | str,
    passwords: Sequence[str] | None = None
) -> Iterator[pymupdf.Document]:

    path = Path(path)
    doc = pymupdf.open(path)

    try:
        if doc.needs_pass:

            candidates = (
                passwords
                if passwords is not None
                else default_passwords()
            )

            candidates = [p for p in candidates if p]

            if not candidates:
                raise PdfPasswordMissing(
                    f"{path.name}: No PDF password configured"
                )

            authenticated = False

            for password in candidates:
                if doc.authenticate(password):
                    authenticated = True
                    break

            if not authenticated:
                raise PdfPasswordIncorrect(
                    f"{path.name}: None of the configured passwords worked"
                )

            log.info("PDF authenticated: %s", path.name)

        yield doc

    finally:
        doc.close()



def native_words(page: pymupdf.Page) -> list[Word]:
    return [Word(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words") if w[4].strip()]


# ----------------------------------------------------------------------------- OCR fallback
_DATE_LIKE = re.compile(r"^\d{2}[-/.]\d{2}[-/.]\d{2,4}$")
_NUMERIC = re.compile(r"^[^\w]*[\d.,\-]+[^\w]*$")   # digits, tolerating stray punctuation like "0)"


def _reocr(cv2, np, pytesseract, img, x0, y0, x1, y1, whitelist: str, pad: int = 6) -> str:
    crop = img[max(0, y0 - pad): y1 + pad, max(0, x0 - pad): x1 + pad]
    if crop.size == 0:
        return ""
    g = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    g = cv2.resize(g, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    g = cv2.copyMakeBorder(g, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
    return pytesseract.image_to_string(g, config=f"--psm 7 -c tessedit_char_whitelist={whitelist}").strip()


def ocr_words(page: pymupdf.Page, dpi: int = 300) -> list[Word]:
    """OCR a page into Words (PDF points). Local only; nothing leaves the machine.

    Two passes: sparse-text OCR for the body, plus a dedicated pass over any
    coloured header bar (white-on-teal text is invisible to normal OCR).
    Low-confidence numeric tokens are re-read digits-only.
    """
    try:
        import cv2
        import numpy as np
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:  # pragma: no cover
        raise OcrUnavailable("OCR dependencies (opencv-python, pytesseract) are not installed") from exc

    pix = page.get_pixmap(dpi=dpi, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    scale = 72.0 / dpi
    out: list[Word] = []
    try:
        d = pytesseract.image_to_data(img, config="--psm 11", output_type=Output.DATAFRAME)
    except pytesseract.TesseractNotFoundError as exc:  # pragma: no cover
        raise OcrUnavailable("Tesseract executable not found") from exc

    d = d.dropna(subset=["text"])
    d = d[d.conf > 0]
    for r in d.itertuples():
        text = str(r.text).strip()
        if not text:
            continue
        left, top, width, height, conf = int(r.left), int(r.top), int(r.width), int(r.height), float(r.conf)
        if conf < 85:
            if _DATE_LIKE.match(text) or (_NUMERIC.match(text) and any(c.isdigit() for c in text)):
                wl = "0123456789-/." if _DATE_LIKE.match(text) else "0123456789.,-"
                fix = _reocr(cv2, np, pytesseract, img, left, top, left + width, top + height, wl)
                if fix and any(c.isdigit() for c in fix):
                    text = fix
        out.append(Word(left * scale, top * scale, (left + width) * scale, (top + height) * scale, text, conf))

    # Header-bar pass (its words replace any partial body-OCR words inside the bar)
    band_words: list[Word] = []
    bands: list[tuple[float, float]] = []
    mx, mn = img.max(2).astype(int), img.min(2).astype(int)
    g_, b_, r_ = img[..., 1].astype(int), img[..., 2].astype(int), img[..., 0].astype(int)
    mask = ((mx - mn) > 60) & (g_ > b_ - 20) & (r_ < 140)
    x_lo, x_hi = int(.12 * pix.width), int(.90 * pix.width)
    rows = np.where(mask[:, x_lo:x_hi].mean(1) > .6)[0]
    if len(rows):
        for grp in np.split(rows, np.where(np.diff(rows) > 5)[0] + 1):
            if len(grp) < 25:
                continue
            y0, y1 = int(grp.min()), int(grp.max())
            bands.append((y0 * scale - 1, (y1 + 1) * scale + 1))
            gray = cv2.cvtColor(img[y0:y1 + 1], cv2.COLOR_RGB2GRAY)
            bw = np.where(gray > 200, 0, 255).astype(np.uint8)
            bw = cv2.copyMakeBorder(bw, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
            hd = pytesseract.image_to_data(bw, config="--psm 6", output_type=Output.DATAFRAME)
            hd = hd.dropna(subset=["text"])
            hd = hd[hd.conf > 0]
            for r in hd.itertuples():
                t = str(r.text).strip()
                if t:
                    l, tp = int(r.left) - 20, int(r.top) - 20 + y0
                    band_words.append(Word(l * scale, tp * scale, (l + int(r.width)) * scale,
                                           (tp + int(r.height)) * scale, t, float(r.conf)))
    if bands:
        out = [w for w in out if not any(a <= w.cy <= b for a, b in bands)]
    return out + band_words


def read_page_words(page: pymupdf.Page, page_no: int, allow_ocr: bool = True, dpi: int = 300) -> PageWords:
    """Native words if the page has a usable text layer, else OCR (if allowed)."""
    rect = page.rect
    warnings: list[str] = []
    words = native_words(page)
    has_graphics = bool(page.get_images()) or len(page.get_drawings()) > 0
    source = "native"
    if len(words) < NATIVE_MIN_WORDS and has_graphics:
        if allow_ocr:
            try:
                words = ocr_words(page, dpi)
                source = "ocr"
            except OcrUnavailable as exc:
                warnings.append(f"ocr_unavailable: {exc}")
                source = "none"
        else:
            warnings.append("no_text_layer_and_ocr_disabled")
            source = "none"
    return PageWords(page_no, rect.width, rect.height, words, source, has_graphics, warnings)
