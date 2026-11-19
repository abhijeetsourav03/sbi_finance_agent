import csv
import json
import logging

import pytest

from src import config
from src.statement_parser import CSV_COLUMNS, main, parse_statement, run
from tests.conftest import D, make_statement, txs

PW = "S3cret-test-pw"


def acct(token="XXXXXXX1111", n=6, opening="5000.00", rows=None):
    return {"token": token, "opening": D(opening), "rows": rows or txs(n)}


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def report(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_env_password(monkeypatch):
    monkeypatch.setattr(config, "SBI_PDF_PASSWORDS", [])


def test_clean_statement_passes_and_values_are_exact(tmp_path):
    pdf = tmp_path / "ok.pdf"
    exp = make_statement(pdf, [acct()])
    res = parse_statement(pdf, tmp_path / "out")
    assert res.status == "PASSED" and res.flagged == 0
    rows = read_csv(res.csv_path)
    assert list(rows[0].keys()) == CSV_COLUMNS
    assert len(rows) == 6
    for r, (d, desc, cr, db, bal) in zip(rows, exp[0]):
        assert (r["transaction_date"], r["description"]) == (d, desc)
        assert (D(r["credit"]), D(r["debit"]), D(r["balance"])) == (cr, db, bal)
        assert r["credit"].endswith(tuple(f".{i:02d}" for i in range(100)))          # 2dp preserved
        assert r["account_id"] == "XXXXXXX1111" and r["review_flags"] == ""
        assert len(r["transaction_id"]) == 16
    rep = report(res.report_path)
    assert rep["overall_status"] == "PASSED" and rep["accounts_detected"][0]["reconciled"]
    assert rep["accounts_detected"][0]["identity_status"] == "verified"


@pytest.mark.parametrize("zero_style", ["0", "-", "blank"])            # 10
@pytest.mark.parametrize("displaced", [False, True])                   # 9
def test_blank_zero_cells_and_displaced_decimals(tmp_path, zero_style, displaced):
    pdf = tmp_path / "s.pdf"
    exp = make_statement(pdf, [acct()], zero_style=zero_style, displaced=displaced)
    res = parse_statement(pdf, tmp_path / "o")
    assert res.status == "PASSED"
    for r, (d, desc, cr, db, bal) in zip(read_csv(res.csv_path), exp[0]):
        assert (D(r["credit"]), D(r["debit"]), D(r["balance"])) == (cr, db, bal)


@pytest.mark.parametrize("style", ["dd-mm-yy", "dd/mm/yyyy", "ddmmyy", "dd mm yy"])   # 8
def test_date_formats_end_to_end(tmp_path, style):
    pdf = tmp_path / "d.pdf"
    exp = make_statement(pdf, [acct()], date_style=style)
    res = parse_statement(pdf, tmp_path / "o")
    assert [r["transaction_date"] for r in read_csv(res.csv_path)] == [e[0] for e in exp[0]]
    assert res.status == "PASSED"


@pytest.mark.parametrize("layout", [6, 7, 8])                           # 5, 6
def test_layout_variants_end_to_end(tmp_path, layout):
    pdf = tmp_path / "l.pdf"
    make_statement(pdf, [acct()], layout=layout)
    assert parse_statement(pdf, tmp_path / "o").status == "PASSED"


def test_multiple_accounts_stay_separate_on_any_pages(tmp_path):         # 11
    pdf = tmp_path / "m.pdf"
    exp = make_statement(pdf, [acct("XXXXXXX1111", 4), acct("XXXXXXX2222", 3, "10.00")], filler=2)
    res = parse_statement(pdf, tmp_path / "o")
    rows = read_csv(res.csv_path)
    assert [r["account_id"] for r in rows] == ["XXXXXXX1111"] * 4 + ["XXXXXXX2222"] * 3
    assert res.status == "PASSED"
    accs = report(res.report_path)["accounts_detected"]
    assert [a["account_id"] for a in accs] == ["XXXXXXX1111", "XXXXXXX2222"]
    assert all(a["reconciled"] for a in accs)
    assert [D(r["balance"]) for r in rows[4:]] == [e[4] for e in exp[1]]


@pytest.mark.parametrize("repeat_header", [True, False])                 # 12
def test_multi_page_table_no_rows_lost_or_duplicated(tmp_path, repeat_header):
    pdf = tmp_path / "p.pdf"
    exp = make_statement(pdf, [acct(n=11)], rows_per_page=4, repeat_header=repeat_header)
    res = parse_statement(pdf, tmp_path / "o")
    rows = read_csv(res.csv_path)
    assert len(rows) == 11 and res.status == "PASSED"
    assert [r["source_page"] for r in rows] == ["1"] * 4 + ["2"] * 4 + ["3"] * 3
    assert [D(r["balance"]) for r in rows] == [e[4] for e in exp[0]]
    assert len({r["transaction_id"] for r in rows}) == 11
    codes = [w["code"] for w in report(res.report_path)["warnings"]]
    assert ("headerless_continuation" in codes) == (not repeat_header)       # reported, not hidden


def test_missing_row_is_detected_not_hidden(tmp_path):                   # 14
    pdf = tmp_path / "x.pdf"
    make_statement(pdf, [acct()], drop=(2,))
    res = parse_statement(pdf, tmp_path / "o")
    rows = read_csv(res.csv_path)
    assert res.status == "REVIEW_REQUIRED" and len(rows) == 5
    assert any("possible_missing_transaction" in r["review_flags"] for r in rows)
    assert report(res.report_path)["accounts_detected"][0]["running_balance_mismatches"]


def test_wrong_closing_balance_blocks_passed(tmp_path):
    pdf = tmp_path / "c.pdf"
    make_statement(pdf, [acct()], closing_override=D("1.00"))
    res = parse_statement(pdf, tmp_path / "o")
    rep = report(res.report_path)
    assert res.status == "REVIEW_REQUIRED" and rep["accounts_detected"][0]["closing_balance_check"] == "mismatch"


def test_duplicate_candidates_are_flagged_but_kept(tmp_path):            # 15
    rows = txs(2)
    dup = (rows[0][0], "FEE/ZERO/TEST", D(0), D(0))
    pdf = tmp_path / "dup.pdf"
    make_statement(pdf, [acct(rows=[rows[0], dup, dup, rows[1]])])
    res = parse_statement(pdf, tmp_path / "o")
    out = read_csv(res.csv_path)
    assert len(out) == 4
    assert [("duplicate_candidate" in r["review_flags"]) for r in out] == [False, True, True, False]


def test_statement_without_transaction_tables(tmp_path):                  # 16
    pdf = tmp_path / "n.pdf"
    make_statement(pdf, [], filler=2)
    res = parse_statement(pdf, tmp_path / "o")
    rep = report(res.report_path)
    assert res.status == "REVIEW_REQUIRED" and res.csv_path is None      # no CSV pretending to be complete
    assert rep["transactions_extracted"] == 0 and "no_transactions_extracted" in rep["status_reasons"]
    assert all(p["status"] == "no_transaction_content" for p in rep["page_status"])


def test_pages_with_signals_but_no_table_need_review(tmp_path):
    import pymupdf
    pdf = tmp_path / "s.pdf"
    doc = pymupdf.open()
    pg = doc.new_page()
    for i, w in enumerate(["Date", "Credit", "Debit", "Balance", "Narration", "Totals", "for", "the", "period"]):
        pg.insert_text((30 + 50 * i, 100), w, fontsize=8)
    doc.save(str(pdf))
    rep = report(parse_statement(pdf, tmp_path / "o").report_path)
    assert rep["page_status"][0]["status"] == "review_required" and rep["pages_requiring_manual_review"] == [1]


def test_encrypted_end_to_end_and_no_secret_leak(tmp_path, caplog):      # 1
    pdf = tmp_path / "e.pdf"
    make_statement(pdf, [acct()], password=PW)
    with caplog.at_level(logging.DEBUG):
        res = parse_statement(pdf, tmp_path / "o", passwords=[PW])
    assert res.status == "PASSED"
    blob = caplog.text + res.report_path.read_text() + res.csv_path.read_text()
    assert PW not in blob
    assert list((tmp_path / "o").glob("*.pdf")) == []                    # no decrypted copy written


@pytest.mark.parametrize("passwords", [["wrong"], []])                    # 2
def test_wrong_or_missing_password_fails_gracefully(tmp_path, passwords):
    pdf = tmp_path / "e.pdf"
    make_statement(pdf, [acct()], password=PW)
    res = parse_statement(pdf, tmp_path / "o", passwords=passwords)
    assert res.status == "FAILED" and res.csv_path is None
    assert PW not in (res.error or "") and "wrong" not in (res.error or "")
    assert report(res.report_path)["overall_status"] == "FAILED"


def test_batch_continues_after_a_failed_pdf(tmp_path, capsys):            # 17
    src = tmp_path / "raw"
    src.mkdir()
    make_statement(src / "a_good.pdf", [acct()])
    (src / "b_corrupt.pdf").write_bytes(b"%PDF-1.4 this is not a real pdf")
    make_statement(src / "c_locked.pdf", [acct()], password=PW)
    make_statement(src / "d_good.pdf", [acct("XXXXXXX9999")])
    results = run(src, tmp_path / "o")
    assert [r.status for r in results] == ["PASSED", "FAILED", "FAILED", "PASSED"]
    assert (tmp_path / "o" / "a_good_transactions.csv").exists() and (tmp_path / "o" / "d_good_transactions.csv").exists()
    assert main([str(src), "--output-dir", str(tmp_path / "o2"), "--no-ocr"]) == 1     # non-zero if any failed
    assert "PASSED=2" in capsys.readouterr().out


def test_output_is_deterministic(tmp_path):                               # 18
    pdf = tmp_path / "det.pdf"
    make_statement(pdf, [acct(), acct("XXXXXXX2222", 3)])
    parse_statement(pdf, tmp_path / "o1")
    parse_statement(pdf, tmp_path / "o2")
    for name in ("det_transactions.csv", "det_validation.json"):
        assert (tmp_path / "o1" / name).read_bytes() == (tmp_path / "o2" / name).read_bytes()
    assert b"\r" not in (tmp_path / "o1" / "det_transactions.csv").read_bytes()


def test_regeneration_overwrites_identically_and_leaves_no_tmp_files(tmp_path):
    pdf = tmp_path / "r.pdf"
    make_statement(pdf, [acct()])
    parse_statement(pdf, tmp_path / "o")
    first = (tmp_path / "o" / "r_transactions.csv").read_bytes()
    parse_statement(pdf, tmp_path / "o")
    assert (tmp_path / "o" / "r_transactions.csv").read_bytes() == first
    assert not list((tmp_path / "o").glob("*.tmp"))


def test_validated_output_is_never_replaced_by_a_worse_run(tmp_path):
    pdf = tmp_path / "v.pdf"
    make_statement(pdf, [acct()])
    parse_statement(pdf, tmp_path / "o")
    good = (tmp_path / "o" / "v_transactions.csv").read_bytes()
    make_statement(pdf, [acct()], drop=(3,))                               # same name, now incomplete
    res = parse_statement(pdf, tmp_path / "o")
    assert res.status == "REVIEW_REQUIRED" and res.csv_path.name == "v_transactions.candidate.csv"
    assert (tmp_path / "o" / "v_transactions.csv").read_bytes() == good
    assert report(tmp_path / "o" / "v_validation.json")["overall_status"] == "PASSED"


def test_report_contains_required_fields(tmp_path):
    pdf = tmp_path / "f.pdf"
    make_statement(pdf, [acct()], filler=1)
    rep = report(parse_statement(pdf, tmp_path / "o").report_path)
    for key in ["pdf_filename", "total_pages", "pages_scanned", "pages_with_transaction_tables", "tables_detected",
                "transactions_extracted", "transactions_flagged", "accounts_detected", "pages_requiring_manual_review",
                "warnings", "overall_status", "page_status"]:
        assert key in rep
    a = rep["accounts_detected"][0]
    for key in ["identity_status", "opening_balance_check", "closing_balance_check", "running_balance_mismatches"]:
        assert key in a
    assert rep["total_pages"] == rep["pages_scanned"] == 2
