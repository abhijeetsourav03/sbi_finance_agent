"""Content-based transaction-table discovery.

Primary path: PyMuPDF `page.find_tables()` (pages with a text layer).
Fallback path: word geometry (OCR pages, or when find_tables finds nothing).
Both map physical columns onto the logical schema by *header position*:

    date | description | reference | credit | debit | balance

Tables are accepted only if normalised header cells match the known
synonyms exactly, so customer-info / account-metadata tables are rejected.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from decimal import Decimal

import pymupdf

from .pdf_reader import PageWords, Word
from .transaction_normalizer import looks_like_date, parse_amount

FIELDS = ("date", "description", "reference", "credit", "debit", "balance")
REQUIRED = {"date", "description", "credit", "debit", "balance"}

_SYN = {
    "date": {"date", "txn date", "transaction date", "tran date", "value date"},
    "description": {"transaction reference", "description", "narration", "particulars",
                    "transaction description", "transaction details"},
    "reference": {"ref no chq no", "ref no", "chq no", "cheque no", "reference no",
                  "chq ref no", "ref no cheque no"},
    "credit": {"credit", "credit amount", "deposit", "deposits", "cr amount"},
    "debit": {"debit", "debit amount", "withdrawal", "withdrawals", "dr amount"},
    "balance": {"balance", "closing balance", "running balance"},
}


def norm(s: str | None) -> str:
    """Lower-case, strip punctuation, collapse whitespace/line breaks."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def match_field(text: str | None) -> str | None:
    n = norm(text)
    if not n:
        return None
    for f, syn in _SYN.items():
        if n in syn:
            return f
    return None


# ---------------------------------------------------------------------------- types
@dataclass(frozen=True)
class Layout:
    spans: tuple[tuple[str, float, float], ...]       # (field, x0, x1) tiling left→right
    x_extent: tuple[float, float]
    duplicate_fields: tuple[str, ...] = ()

    def assign(self, x: float) -> str | None:
        for f, a, b in self.spans:
            if a <= x < b:
                return f
        return None

    def span(self, f: str) -> tuple[float, float] | None:
        for g, a, b in self.spans:
            if g == f:
                return a, b
        return None


@dataclass
class RawRow:
    page: int
    index: int                    # 1-based position within the detected table
    y: float
    cells: dict[str, str]
    kind: str                     # transaction | meta | header_repeat | other
    flags: set[str] = field(default_factory=set)
    min_desc_conf: float = 100.0


@dataclass
class TableCandidate:
    page: int
    source: str                   # find_tables | words
    y_top: float
    layout: Layout
    rows: list[RawRow]
    header_found: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class Marker:
    page: int
    y: float
    kind: str                     # account | opening | closing
    token: str | None = None
    amount: Decimal | None = None
    date: str | None = None


# --------------------------------------------------------------------------- helpers
def group_lines(words: list[Word]) -> list[list[Word]]:
    """Cluster words into visual lines by vertical centre."""
    if not words:
        return []
    tol = 0.6 * statistics.median(w.h for w in words)
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.cy, w.x0)):
        if lines and abs(w.cy - statistics.fmean(x.cy for x in lines[-1])) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(l, key=lambda w: w.x0) for l in lines]


def _match_phrases(seq: list[Word]) -> list[tuple[str, float, float]]:
    items, i = [], 0
    while i < len(seq):
        hit = None
        for n in (4, 3, 2, 1):
            if i + n <= len(seq):
                f = match_field(" ".join(w.text for w in seq[i:i + n]))
                if f:
                    hit = (f, n)
                    break
        if hit:
            f, n = hit
            items.append((f, seq[i].x0, seq[i + n - 1].x1))
            i += n
        else:
            i += 1
    return items


def _header_ok(items) -> bool:
    return REQUIRED <= {i[0] for i in items}


def build_layout(items, seps=(), extent=(0.0, 1e6)) -> Layout:
    seen, uniq, dups = set(), [], []
    for it in sorted(items, key=lambda i: i[1]):
        if it[0] in seen:
            dups.append(it[0])
            continue
        seen.add(it[0])
        uniq.append(it)
    bounds = [extent[0]]
    for a, b in zip(uniq, uniq[1:]):
        lo, hi = a[2], b[1]
        inside = [s for s in seps if min(lo, hi) <= s <= max(lo, hi)]
        bounds.append(inside[0] if inside else (lo + hi) / 2)
    bounds.append(extent[1])
    spans = tuple((it[0], bounds[i], bounds[i + 1]) for i, it in enumerate(uniq))
    return Layout(spans, extent, tuple(dups))


def _row_kind(cells: dict[str, str], full_text: str) -> str:
    n = norm(full_text)
    if "opening balance" in n or "closing balance" in n:
        return "meta"
    if looks_like_date(cells.get("date", "")):
        return "transaction"
    if sum(1 for f in ("date", "description", "credit", "debit", "balance")
           if match_field(cells.get(f)) == f) >= 3:
        return "header_repeat"
    return "other"


# ------------------------------------------------------------------- find_tables path
def _cell_text(words: list[Word], bbox, fallback: str | None) -> str:
    """Text of one table cell, rebuilt from page words inside its box.

    `Table.extract()` drops inter-word spaces ("EMPLOYERPAYMENT") and can split
    decimals ("17000\\n."), so it is only used when no words fall in the cell.
    """
    if bbox is None:
        return ""
    x0, y0, x1, y1 = bbox
    inside = [w for w in words if x0 <= w.cx < x1 and y0 <= w.cy < y1]
    if not inside:
        return re.sub(r"\s+", " ", fallback or "").strip()
    return " ".join(w.text for line in group_lines(inside) for w in line)


def _from_find_tables(page: pymupdf.Page, pw: PageWords, prev: Layout | None):
    page_no = pw.page_no
    tables: list[TableCandidate] = []
    warns: list[str] = []
    try:
        finder = page.find_tables()
    except Exception as exc:  # noqa: BLE001 - PyMuPDF raises varied errors
        return [], [f"find_tables_failed:{type(exc).__name__}"]
    for t in finder.tables:
        extracted = t.extract()
        geo = t.rows
        grid: list[list[str]] = []
        for i, row in enumerate(extracted):
            cells = geo[i].cells if i < len(geo) else []
            grid.append([_cell_text(pw.words, cells[j] if j < len(cells) else None, row[j])
                         for j in range(len(row))])
        hdr_i, items = None, []
        for i, row in enumerate(grid):
            cand = []
            for j, txt in enumerate(row):
                f = match_field(txt) if txt else None
                bbox = geo[i].cells[j] if i < len(geo) and j < len(geo[i].cells) else None
                if f and bbox:
                    cand.append((f, bbox[0], bbox[2]))
            if _header_ok(cand):
                hdr_i, items = i, cand
                break
        notes: list[str] = []
        if hdr_i is not None:
            layout = build_layout(items, extent=(t.bbox[0] - 1, t.bbox[2] + 1))
            start = hdr_i + 1
        else:
            date_rows = sum(1 for r in grid if r and r[0] and looks_like_date(r[0]))
            if date_rows < 2:
                continue                       # not a transaction-like table
            if (prev and abs(t.bbox[0] - (prev.x_extent[0] + 1)) < 8 and abs(t.bbox[2] - (prev.x_extent[1] - 1)) < 8):
                layout, start = prev, 0
                notes.append("headerless_continuation")
            else:
                warns.append("transaction_like_table_without_header_unmapped")
                continue
        rows: list[RawRow] = []
        for i in range(start, len(grid)):
            cells: dict[str, list[str]] = {f: [] for f in FIELDS}
            flags: set[str] = set()
            for j, txt in enumerate(grid[i]):
                if not txt.strip():
                    continue
                bbox = geo[i].cells[j] if i < len(geo) and j < len(geo[i].cells) else None
                f = layout.assign((bbox[0] + bbox[2]) / 2) if bbox else None
                if f is None:
                    flags.add("ambiguous_column_mapping")
                    continue
                cells[f].append(txt)
            joined = {f: " ".join(v) for f, v in cells.items()}
            full = " ".join(joined.values())
            if not full.strip() and not flags:
                continue
            rows.append(RawRow(page_no, i - start + 1, geo[i].bbox[1] if i < len(geo) else 0.0,
                               joined, _row_kind(joined, full), flags))
        if layout.duplicate_fields:
            for r in rows:
                r.flags.add("ambiguous_column_mapping")
        tables.append(TableCandidate(page_no, "find_tables", t.bbox[1], layout, rows, hdr_i is not None, notes))
    return tables, warns


# ---------------------------------------------------------------------- words path
def _words_rows(pw: PageWords, layout: Layout, y_start: float, y_end: float,
                words: list[Word]) -> list[RawRow]:
    d0, d1 = layout.span("date") or (0, 0)
    body = [w for w in words if y_start < w.cy < y_end]
    anchors = []
    for line in group_lines([w for w in body if d0 <= w.cx < d1]):
        if looks_like_date("".join(w.text for w in line)):
            anchors.append(line)
    if not anchors:
        return []
    med_h = statistics.median(statistics.fmean(w.h for w in a) for a in anchors)
    tops = [min(w.y0 for w in a) for a in anchors]
    stops = [w.y0 for w in body if w.y0 > tops[0] and norm(w.text) in ("closing", "visit")]
    table_end = min(stops) - 1 if stops else y_end
    bounds = [t - 0.4 * med_h for t in tops] + [min(tops[-1] + 2.4 * med_h, table_end)]
    rows = []
    for i, a in enumerate(anchors):
        band = [w for w in body if bounds[i] <= w.cy < bounds[i + 1]]
        cells: dict[str, list[Word]] = {f: [] for f in FIELDS}
        for w in band:
            f = layout.assign(w.cx)
            if f is None or (f == "date" and w not in a):
                continue
            cells[f].append(w)
        joined = {}
        for f, ws in cells.items():
            joined[f] = " ".join(w.text for line in group_lines(ws) for w in line)
        conf = min((w.conf for w in cells["description"]), default=100.0)
        full = " ".join(joined.values())
        rows.append(RawRow(pw.page_no, i + 1, tops[i], joined, _row_kind(joined, full), set(), conf))
    return rows


def _from_words(pw: PageWords, prev: Layout | None):
    words = [w for w in pw.words if w.text != "|"]
    bars = [w for w in pw.words if w.text == "|"]
    headers = []
    for line in group_lines(words):
        items = _match_phrases(line)
        if _header_ok(items):
            y0, y1 = min(w.y0 for w in line), max(w.y1 for w in line)
            seps = [b.cx for b in bars if y0 - 5 <= b.cy <= y1 + 5]
            headers.append((y0, y1, items, seps))
    tables: list[TableCandidate] = []
    warns: list[str] = []
    for k, (y0, y1, items, seps) in enumerate(headers):
        y_end = headers[k + 1][0] if k + 1 < len(headers) else pw.height
        layout = build_layout(items, seps, (0.0, pw.width))
        rows = _words_rows(pw, layout, y1, y_end, words)
        if layout.duplicate_fields:
            for r in rows:
                r.flags.add("ambiguous_column_mapping")
        tables.append(TableCandidate(pw.page_no, "words", y0, layout, rows, True))
    if not headers and prev is not None:
        rows = _words_rows(pw, prev, 0.0, pw.height, words)
        tx = [r for r in rows if r.kind == "transaction"]
        supported = len(tx) >= 2 and all(parse_amount(r.cells.get("balance")).status in
                                         ("exact", "displaced", "no_decimal") for r in tx)
        if supported:
            tables.append(TableCandidate(pw.page_no, "words", 0.0, prev, rows, False, ["headerless_continuation"]))
        elif tx:
            warns.append("transaction_like_rows_without_header_unmapped")
    return tables, warns


def detect_tables(page: pymupdf.Page, pw: PageWords, prev: Layout | None):
    """Return (tables, warnings) for one page."""
    tables: list[TableCandidate] = []
    warns: list[str] = []
    if pw.source == "native":
        tables, warns = _from_find_tables(page, pw, prev)
        if not tables:
            t2, w2 = _from_words(pw, prev)
            if t2:
                warns.append("find_tables_found_nothing_used_word_geometry")
            tables, warns = t2, warns + w2
    elif pw.source == "ocr":
        tables, warns = _from_words(pw, prev)
    return tables, warns


# --------------------------------------------------------------------- page context
_MASKED = re.compile(r"^[A-Za-z*]{4,}(\d{3,6})$")
_NOT_HEADING = {"number", "no", "status", "holder", "details", "type", "balance", "summary", "name"}


def _is_masked(text: str) -> bool:
    m = _MASKED.match(text)
    if not m:
        return False
    letters = re.sub(r"\d", "", text)
    return sum(c in "Xx*" for c in letters) >= 0.6 * len(letters)


def date_format_stated(pw: PageWords) -> bool:
    return any(re.search(r"\bdd mm yy(yy)?\b", norm(" ".join(w.text for w in line)))
               for line in group_lines(pw.words))


def scan_markers(pw: PageWords) -> list[Marker]:
    """Opening/closing balance lines and account headings, with y positions."""
    out: list[Marker] = []
    words = [w for w in pw.words if w.text != "|"]
    for line in group_lines(words):
        low = [norm(w.text) for w in line]
        for kind in ("opening", "closing"):
            if kind in low and "balance" in low[low.index(kind) + 1:low.index(kind) + 3]:
                i = low.index(kind)
                amount, date_txt = None, None
                for w in line[i + 1:]:
                    t = w.text.rstrip(":")
                    if date_txt is None and looks_like_date(t):
                        date_txt = t
                        continue
                    ap = parse_amount(w.text)
                    if ap.status in ("exact", "displaced", "no_decimal"):
                        amount = ap.candidates[0]
                out.append(Marker(pw.page_no, line[i].y0, kind, None, amount, date_txt))
    for w in words:
        if norm(w.text) != "account":
            continue
        right = [x for x in words if abs(x.cy - w.cy) < w.h * .6 and x.x0 > w.x1 - 1]
        right.sort(key=lambda x: x.x0)
        if right and norm(right[0].text) in _NOT_HEADING and right[0].x0 - w.x1 < 40:
            continue
        cands = [x for x in words if _is_masked(x.text) and w.y0 - 2 <= x.y0 <= w.y1 + 80
                 and w.x0 - 150 <= x.x0 <= w.x0 + 300]
        cands.sort(key=lambda x: (x.y0, x.x0))
        token = None
        if cands:
            token = "XXXXXXX" + _MASKED.match(cands[0].text).group(1)[-4:]
        out.append(Marker(pw.page_no, w.y0, "account", token))
    return sorted(out, key=lambda m: m.y)
