import pytest

from src import config
from src.pdf_reader import (PdfPasswordIncorrect, PdfPasswordMissing, open_pdf, read_page_words)
from tests.conftest import D, make_statement, txs

PW = "S3cret-test-pw"


def _acct():
    return [{"token": "XXXXXXX1111", "opening": D("1000.00"), "rows": txs(4)}]


def test_encrypted_pdf_authenticates(tmp_pdf):                       # 1
    make_statement(tmp_pdf, _acct(), password=PW)
    with open_pdf(tmp_pdf, [PW]) as doc:
        assert len(doc) == 1
        assert "Opening Balance" in doc[0].get_text()


@pytest.mark.parametrize("pw", ["wrong-pw", ""])
def test_wrong_or_missing_password(tmp_pdf, pw, monkeypatch):        # 2
    make_statement(tmp_pdf, _acct(), password=PW)
    monkeypatch.setattr(config, "SBI_PDF_PASSWORDS", [])
    if pw:
        with pytest.raises(PdfPasswordIncorrect) as e:
            with open_pdf(tmp_pdf, [pw]):
                pass
        assert pw not in str(e.value) and PW not in str(e.value)    # never leak passwords
    else:
        with pytest.raises(PdfPasswordMissing):
            with open_pdf(tmp_pdf):                                  # nothing configured
                pass


def test_default_password_comes_from_config(tmp_pdf, monkeypatch):
    make_statement(tmp_pdf, _acct(), password=PW)
    monkeypatch.setattr(config, "SBI_PDF_PASSWORDS", [PW])
    with open_pdf(tmp_pdf) as doc:
        assert len(doc) == 1


def test_multiple_passwords_are_tried(tmp_pdf):
    make_statement(tmp_pdf, _acct(), password=PW)
    with open_pdf(tmp_pdf, ["nope", PW]) as doc:
        assert len(doc) == 1


def test_unencrypted_pdf_needs_no_password(tmp_pdf, monkeypatch):    # 3
    monkeypatch.setattr(config, "SBI_PDF_PASSWORDS", [])
    make_statement(tmp_pdf, _acct())
    with open_pdf(tmp_pdf) as doc:
        assert len(doc) == 1


def test_document_closed_after_context(tmp_pdf):
    make_statement(tmp_pdf, _acct())
    with open_pdf(tmp_pdf) as doc:
        pass
    assert doc.is_closed


def test_native_words_used_when_text_layer_exists(tmp_pdf):
    make_statement(tmp_pdf, _acct())
    with open_pdf(tmp_pdf) as doc:
        pw = read_page_words(doc[0], 1, allow_ocr=False)
    assert pw.source == "native" and len(pw.words) > 20
