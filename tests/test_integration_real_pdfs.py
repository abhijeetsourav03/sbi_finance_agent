"""Integration test against the real local statements in data/raw_statements/.

No personal data is copied into fixtures: the test only asserts structural
properties and that the outputs contain nothing but masked account ids.
Skipped automatically when no PDFs (or no OCR for image-only PDFs) are available.
"""
import json
import re
from pathlib import Path

import pytest

from src.statement_parser import parse_statement

RAW = Path("data/raw_statements")
PDFS = sorted(RAW.glob("*.pdf")) if RAW.exists() else []
pytestmark = pytest.mark.skipif(not PDFS, reason="no local statements present")


@pytest.mark.parametrize("pdf", PDFS, ids=[p.name for p in PDFS])
def test_real_statement_is_processed_honestly(pdf, tmp_path):
    res = parse_statement(pdf, tmp_path)
    if res.status == "FAILED" and "Tesseract" in (res.error or ""):
        pytest.skip("image-only PDF and Tesseract is not installed")
    assert res.status in {"PASSED", "REVIEW_REQUIRED", "FAILED"}
    rep = json.loads(res.report_path.read_text(encoding="utf-8"))
    assert rep["pages_scanned"] == rep["total_pages"]                  # nothing silently skipped
    assert len(rep["page_status"]) == rep["total_pages"]
    if res.status == "PASSED":                                         # PASSED must mean fully reconciled
        assert rep["transactions_flagged"] == 0
        assert all(a["reconciled"] for a in rep["accounts_detected"])
    for a in rep["accounts_detected"]:
        assert a["account_id"] == "unverified" or re.fullmatch(r"X{3,}\d{3,6}", a["account_id"])
    blob = res.report_path.read_text(encoding="utf-8") + (res.csv_path.read_text(encoding="utf-8") if res.csv_path else "")
    assert "address" not in blob.lower() and "password" not in blob.lower()
