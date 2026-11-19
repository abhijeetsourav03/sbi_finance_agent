"""SBI statement parser (Phase 2): native PDF text first, OCR only as fallback.

Usage:
    python -m src.statement_parser "data/raw_statements/statement.pdf"
    python -m src.statement_parser "data/raw_statements" --output-dir data/processed

Outputs per PDF:  <stem>_transactions.csv  and  <stem>_validation.json
Everything runs locally. Passwords, tokens, addresses and full account
numbers are never logged or written.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from .pdf_reader import open_pdf, read_page_words, sha256_file
from .table_detector import (Marker, date_format_stated, detect_tables,
                             looks_like_date, match_field, scan_markers)
from .transaction_normalizer import (ZERO, Txn, clean_description, clean_reference,
                                     fingerprint, money, parse_amount, parse_date)
from .transaction_validator import AccountResult, flag_duplicates, validate_account

PARSER_VERSION = "2.0"
DEFAULT_OUTPUT_DIR = Path("data/processed")
CSV_COLUMNS = ["source_pdf", "source_page", "source_row", "account_id", "transaction_date",
               "description", "reference_number", "credit", "debit", "balance",
               "transaction_id", "review_flags"]
_REVIEW_WARNINGS = ("ocr_unavailable", "no_text_layer_and_ocr_disabled",
                    "transaction_like_table_without_header_unmapped",
                    "transaction_like_rows_without_header_unmapped")

log = logging.getLogger(__name__)


@dataclass
class ParseResult:
    pdf: Path
    status: str                      # PASSED | REVIEW_REQUIRED | FAILED
    transactions: int = 0
    flagged: int = 0
    csv_path: Path | None = None
    report_path: Path | None = None
    error: str | None = None


@dataclass
class _Section:
    token: str | None
    identity: str
    opening: Decimal | None = None
    closing: Decimal | None = None
    open_date: str | None = None
    close_date: str | None = None
    rows: list = None                # [(page, RawRow)]

    def __post_init__(self):
        self.rows = []


# --------------------------------------------------------------------------- helpers
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _page_has_signals(pw, markers: Sequence[Marker]) -> bool:
    fields = {match_field(w.text) for w in pw.words}
    if {"credit", "debit", "balance"} <= fields:
        return True
    left_dates = sum(1 for w in pw.words if w.cx < 0.25 * pw.width and looks_like_date(w.text))
    return left_dates >= 3 or any(m.kind in ("opening", "closing") for m in markers)


def _period(sec: _Section):
    if sec.open_date and sec.close_date:
        a, b = parse_date(sec.open_date).iso, parse_date(sec.close_date).iso
        if a and b:
            return date.fromisoformat(a), date.fromisoformat(b)
    return None


def _to_txn(page: int, row, sec_idx: int, fmt_known: bool, period) -> Txn:
    dp = parse_date(row.cells.get("date", ""), fmt_known=fmt_known, period=period)
    cr, db, bal = (parse_amount(row.cells.get(k)) for k in ("credit", "debit", "balance"))
    flags = set(row.flags) | set(dp.flags)
    if cr.status == "blank" and db.status == "blank":
        flags.add("no_amount")
    if row.min_desc_conf < 60:
        flags.add("low_confidence_ocr_text")
    side = lambda ap: (ZERO,) if ap.is_blank else ap.candidates       # blank cell == 0 (table structure)
    return Txn(page, row.index, sec_idx, dp.iso, clean_description(row.cells.get("description")),
               clean_reference(row.cells.get("reference")), side(cr), side(db),
               () if bal.is_blank else bal.candidates, flags)


# ------------------------------------------------------------------------- main API
def parse_statement(pdf_path: Path | str, output_dir: Path | str = DEFAULT_OUTPUT_DIR,
                    passwords: Sequence[str] | None = None, allow_ocr: bool = True) -> ParseResult:
    pdf_path, out_dir = Path(pdf_path), Path(output_dir)
    stem = pdf_path.stem
    report: dict = {"parser_version": PARSER_VERSION, "pdf_filename": pdf_path.name}
    try:
        pdf_hash = sha256_file(pdf_path)
        report["pdf_sha256"] = pdf_hash
        txns, accounts, report_core = _extract(pdf_path, pdf_hash, passwords, allow_ocr)
        report.update(report_core)
    except Exception as exc:  # noqa: BLE001 - one bad PDF must not stop a batch
        log.error("%s: failed (%s)", pdf_path.name, type(exc).__name__)
        report.update({"overall_status": "FAILED", "error": f"{type(exc).__name__}: {exc}"})
        report_path = _write_report(out_dir, stem, report)
        return ParseResult(pdf_path, "FAILED", report_path=report_path, error=report["error"])

    status = report["overall_status"]
    csv_text = _render_csv(pdf_path.name, txns)
    csv_path, report_path = _write_outputs(out_dir, stem, status, csv_text, report)
    flagged = sum(1 for t in txns if t.flags)
    return ParseResult(pdf_path, status, len(txns), flagged, csv_path, report_path)


def _extract(pdf_path: Path, pdf_hash: str, passwords, allow_ocr: bool):
    events: list[tuple[int, float, int, object]] = []
    page_status, warnings, tables_total = [], [], 0
    fmt_stated = False
    prev_layout = None
    with open_pdf(pdf_path, passwords) as doc:            # stays open for the whole extraction
        total = len(doc)
        for i, page in enumerate(doc):
            pw = read_page_words(page, i + 1, allow_ocr)
            fmt_stated |= date_format_stated(pw)
            markers = scan_markers(pw)
            tables, warns = detect_tables(page, pw, prev_layout)
            for w in pw.warnings + warns:
                sev = "review" if w.startswith(_REVIEW_WARNINGS) else "info"
                warnings.append({"page": i + 1, "code": w, "severity": sev})
            if pw.source == "ocr":
                warnings.append({"page": i + 1, "code": "ocr_fallback_used", "severity": "info"})
            for m in markers:
                if m.kind == "opening":      # opening-balance row sits inside/just under the header: process before rows
                    for t in tables:
                        first = next((r.y for r in t.rows), None)
                        if first is not None and t.y_top <= m.y <= first:
                            m.y = t.y_top - 0.01
                events.append((i + 1, m.y, 0, m))
            n_tx = 0
            for t in tables:
                tables_total += 1
                events.append((i + 1, t.y_top, 1, t))
                n_tx += sum(1 for r in t.rows if r.kind == "transaction")
                if n_tx:
                    prev_layout = t.layout
                for note in t.notes:
                    warnings.append({"page": i + 1, "code": note, "severity": "info"})
            if n_tx:
                status = "transaction_table"
            elif pw.source == "none" and pw.has_graphics:
                status = "extraction_failed"
            elif tables or _page_has_signals(pw, markers):
                status = "review_required"
            else:
                status = "no_transaction_content"
            page_status.append({"page": i + 1, "status": status, "text_source": pw.source,
                                "transaction_rows": n_tx})

    # ---- build account sections in reading order
    sections: list[_Section] = []
    cur: _Section | None = None
    for page, _y, _o, obj in sorted(events, key=lambda e: (e[0], e[1], e[2])):
        if isinstance(obj, Marker):
            if obj.kind == "account" and obj.token and (cur is None or obj.token != cur.token):
                cur = _Section(obj.token, "verified")
                sections.append(cur)
            elif obj.kind == "opening":
                if cur is None or cur.rows:
                    cur = _Section(None, "unverified")
                    sections.append(cur)
                cur.opening, cur.open_date = obj.amount, obj.date
            elif obj.kind == "closing" and cur is not None:
                cur.closing, cur.close_date = obj.amount, obj.date
        else:
            if cur is None:
                cur = _Section(None, "unverified")
                sections.append(cur)
            for r in obj.rows:
                if r.kind == "transaction":
                    cur.rows.append((page, r))
                elif r.kind == "other" and any(r.cells.get(k) for k in ("credit", "debit", "balance")):
                    warnings.append({"page": page, "code": "row_without_date_ignored", "severity": "review"})

    # ---- normalise + validate each account independently
    txns: list[Txn] = []
    accounts: list[AccountResult] = []
    for idx, sec in enumerate(sections):
        period = _period(sec)
        sec_txns = [_to_txn(p, r, idx, fmt_stated, period) for p, r in sec.rows]
        acct_id = sec.token or "unverified"
        res = validate_account(sec_txns, sec.opening, sec.closing, acct_id, sec.identity, period)
        for t in sec_txns:
            t.account_id = acct_id
            t.transaction_id = fingerprint(pdf_hash, acct_id, t)
        accounts.append(res)
        txns.extend(sec_txns)
    flag_duplicates(txns)

    # ---- report
    flagged = [t for t in txns if t.flags]
    review_pages = [p["page"] for p in page_status if p["status"] in ("review_required", "extraction_failed")]
    review_pages += [w["page"] for w in warnings if w["severity"] == "review"]
    review_pages = sorted(set(review_pages))
    reasons: list[str] = []
    if not txns:
        reasons.append("no_transactions_extracted")
    if flagged:
        reasons.append("flagged_transactions")
    if review_pages:
        reasons.append("pages_require_review")
    for a in accounts:
        if not a.reconciled:
            reasons.append(f"account_not_reconciled:{a.account_id}")
    if any(p["status"] == "extraction_failed" for p in page_status) and not txns:
        overall = "FAILED"
    else:
        overall = "PASSED" if not reasons else "REVIEW_REQUIRED"
    report = {
        "total_pages": total,
        "pages_scanned": len(page_status),
        "pages_with_transaction_tables": [p["page"] for p in page_status if p["status"] == "transaction_table"],
        "page_status": page_status,
        "tables_detected": tables_total,
        "transactions_extracted": len(txns),
        "transactions_flagged": len(flagged),
        "flag_summary": dict(sorted(Counter(f for t in txns for f in t.flags).items())),
        "accounts_detected": [a.as_dict() for a in accounts],
        "pages_requiring_manual_review": review_pages,
        "warnings": warnings,
        "date_format_stated_in_statement": fmt_stated,
        "overall_status": overall,
        "status_reasons": reasons,
    }
    return txns, accounts, report


# ------------------------------------------------------------------------- outputs
def _render_csv(pdf_name: str, txns: Sequence[Txn]) -> str:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for t in txns:
        w.writerow([pdf_name, t.source_page, t.source_row, t.account_id, t.date_iso or "",
                    t.description, t.reference,
                    money(t.credit) if t.credit is not None else "",
                    money(t.debit) if t.debit is not None else "",
                    money(t.balance) if t.balance is not None else "",
                    t.transaction_id, ";".join(sorted(t.flags))])
    return buf.getvalue()


def _write_report(out_dir: Path, stem: str, report: dict, suffix: str = "") -> Path:
    path = out_dir / f"{stem}_validation{suffix}.json"
    _atomic_write(path, json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def _write_outputs(out_dir: Path, stem: str, status: str, csv_text: str, report: dict):
    """Never replace previously PASSED output with a worse result."""
    prior = out_dir / f"{stem}_validation.json"
    suffix = ""
    if status != "PASSED" and prior.exists():
        try:
            if json.loads(prior.read_text(encoding="utf-8")).get("overall_status") == "PASSED":
                suffix = ".candidate"
                report["note"] = "Earlier PASSED output preserved; this run written as .candidate"
        except (OSError, ValueError):
            pass
    csv_path = None
    if status != "FAILED" and report.get("transactions_extracted", 0) > 0:
        csv_path = out_dir / f"{stem}_transactions{suffix}.csv"
        _atomic_write(csv_path, csv_text)
    return csv_path, _write_report(out_dir, stem, report, suffix)


# ----------------------------------------------------------------------------- CLI
def find_pdfs(target: Path) -> list[Path]:
    if target.is_dir():
        return sorted({p for p in target.iterdir() if p.suffix.lower() == ".pdf"})
    return [target]


def run(target: Path, output_dir: Path, allow_ocr: bool = True,
        passwords: Sequence[str] | None = None) -> list[ParseResult]:
    pdfs = find_pdfs(target)
    if not pdfs:
        raise FileNotFoundError(f"No PDF files found at {target}")
    results = []
    for pdf in pdfs:
        log.info("Processing %s", pdf.name)
        results.append(parse_statement(pdf, output_dir, passwords, allow_ocr))
    return results


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SBI statement parser (native text first, OCR fallback)")
    ap.add_argument("path", type=Path, help="PDF file or directory of PDFs")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    ap.add_argument("--no-ocr", action="store_true", help="never fall back to OCR")
    ap.add_argument("--tesseract", default=None, help="path to tesseract.exe (Windows)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(message)s")
    if a.tesseract:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = a.tesseract
    results = run(a.path, a.output_dir, allow_ocr=not a.no_ocr)
    print("\nSummary")
    for r in results:
        extra = f" ({r.error})" if r.error else f"  rows={r.transactions} flagged={r.flagged}"
        print(f"  {r.status:16} {r.pdf.name}{extra}")
    counts = Counter(r.status for r in results)
    print("  " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 1 if counts.get("FAILED") else 0


if __name__ == "__main__":
    sys.exit(main())
