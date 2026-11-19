import pytest

from src.pdf_reader import open_pdf, read_page_words
from src.table_detector import detect_tables, match_field, norm, scan_markers
from tests.conftest import D, make_statement, txs


def _tables(pdf, page_no, allow_ocr=False):
    with open_pdf(pdf, []) as doc:
        pw = read_page_words(doc[page_no], page_no + 1, allow_ocr)
        return detect_tables(doc[page_no], pw, None)[0], pw


def _acct(n=5, desc_rows=None):
    return [{"token": "XXXXXXX2222", "opening": D("5000.00"), "rows": desc_rows or txs(n)}]


@pytest.mark.parametrize("raw", ["Date", " DATE ", "Transaction\nReference", "TRANSACTION   REFERENCE", "Ref.No./\nChq.No.",
                                  "ref no / chq no", "Credit", "Debit", "Balance", "Narration"])
def test_header_normalisation_matches(raw):
    assert match_field(raw) is not None


@pytest.mark.parametrize("raw", ["Name", "Branch Code", "Account Number", "Available Balance", "Credit Card", "", None])
def test_unrelated_headers_rejected(raw):                            # no over-broad keyword matching
    assert match_field(raw) is None


def test_norm():
    assert norm(" Ref.No./\nChq.No. ") == "ref no chq no"


@pytest.mark.parametrize("layout", [6, 7, 8])
def test_six_seven_eight_columns_map_to_same_logical_schema(tmp_pdf, layout):      # 5, 6
    rows = txs(5)
    make_statement(tmp_pdf, _acct(desc_rows=rows), layout=layout)
    tables, _ = _tables(tmp_pdf, 0)
    assert len(tables) == 1 and tables[0].header_found
    tx = [r for r in tables[0].rows if r.kind == "transaction"]
    assert len(tx) == 5
    for r, (d, desc, cr, db) in zip(tx, rows):
        assert r.cells["description"] == desc
        assert not r.flags
        got_cr = r.cells["credit"]; got_db = r.cells["debit"]
        assert (got_cr == "0" and got_db == f"{db:.2f}") or (got_db == "0" and got_cr == f"{cr:.2f}")
    assert tx[-1].cells["balance"]


def test_transaction_table_found_on_any_page(tmp_pdf):                # 4
    make_statement(tmp_pdf, _acct(), filler=3)                          # transaction page is page 4
    with open_pdf(tmp_pdf, []) as doc:
        assert len(doc) == 4
    for p in range(3):
        assert _tables(tmp_pdf, p)[0] == []                             # customer-info tables rejected
    assert len(_tables(tmp_pdf, 3)[0]) == 1


def test_multiline_description_preserved(tmp_pdf):                    # 7
    long = "NEFT*YESB0000001*YESF362767014097*ZERODHA\nBROKING"
    rows = [(txs(1)[0][0], long, D("100.00"), D(0))]
    make_statement(tmp_pdf, _acct(desc_rows=rows))
    t, _ = _tables(tmp_pdf, 0)
    assert t[0].rows[0].cells["description"] == "NEFT*YESB0000001*YESF362767014097*ZERODHA BROKING"


def test_no_hardcoded_pages_markers_and_account_token(tmp_pdf):
    make_statement(tmp_pdf, _acct(), filler=2)
    _, pw = _tables(tmp_pdf, 2)
    kinds = [(m.kind, m.token, m.amount) for m in scan_markers(pw)]
    assert ("account", "XXXXXXX2222", None) in kinds
    assert ("opening", None, D("5000.00")) in kinds
    assert any(k == "closing" for k, _, _ in kinds)


def test_page_without_tables(tmp_pdf):                                # 16
    make_statement(tmp_pdf, [], filler=2)
    for p in range(2):
        assert _tables(tmp_pdf, p)[0] == []
