from datetime import date
from decimal import Decimal as D

import pytest

from src.transaction_normalizer import (Txn, clean_description, clean_reference, fingerprint, parse_amount,
                                        parse_date)


@pytest.mark.parametrize("raw,iso", [("01-10-26", "2026-10-01"), ("01/10/2026", "2026-10-01"),
                                      ("011026", "2026-10-01"), ("01 10 26", "2026-10-01"),
                                      ("01-10-\n26", "2026-10-01"), (" 01 - 10 - 26 ", "2026-10-01")])
def test_date_formats(raw, iso):                                      # 6, 8
    assert parse_date(raw).iso == iso


@pytest.mark.parametrize("raw", ["32-10-26", "31-02-26", "00-10-26", "abc", "", "1234567"])
def test_impossible_dates_rejected(raw):
    r = parse_date(raw)
    assert r.iso is None and "invalid_date" in r.flags


def test_ambiguous_date_flagged_unless_statement_states_format_or_period_decides():
    assert "ambiguous_date_format" in parse_date("03-04-26", fmt_known=False).flags
    assert parse_date("03-04-26", fmt_known=True).flags == ()
    assert parse_date("13-04-26", fmt_known=False).flags == ()              # only one valid reading
    period = (date(2026, 4, 1), date(2026, 4, 30))
    assert parse_date("03-04-26", fmt_known=False, period=period).flags == ()   # MDY (Mar 4) is outside


@pytest.mark.parametrize("raw,val", [("1,250.00", "1250.00"), ("1250.00", "1250.00"), ("0", "0.00"),
                                      ("1,25,000.50", "125000.50"), ("Rs. 99.5", "99.50"), ("₹ 88401.73", "88401.73")])
def test_normal_amounts(raw, val):                                    # 7
    a = parse_amount(raw)
    assert a.status == "exact" and a.candidates == (D(val),)


@pytest.mark.parametrize("raw", ["", "  ", None])
def test_blank_cell(raw):                                             # 10
    assert parse_amount(raw).is_blank and parse_amount(raw).candidates == ()


def test_dash_is_zero():
    a = parse_amount("-")
    assert a.is_blank and a.candidates == (D(0),)


@pytest.mark.parametrize("raw,default,alt", [("17000\n.", "170.00", "17000.00"), ("300000\n.", "3000.00", "300000.00"),
                                              ("4284864\n.", "42848.64", "4284864.00")])
def test_displaced_decimal_returns_both_readings(raw, default, alt):  # 9  – never blindly /100
    a = parse_amount(raw)
    assert a.status == "displaced" and a.candidates == (D(default), D(alt))


def test_missing_decimal_point_is_ambiguous_not_silently_fixed():
    a = parse_amount("1594144")
    assert a.status == "no_decimal" and a.candidates[0] == D("15941.44") and len(a.candidates) == 2


@pytest.mark.parametrize("raw", ["12a.00", "1.2.3", "--", "abc"])
def test_malformed_amounts_flagged(raw):
    assert parse_amount(raw).status == "invalid"


def test_negative_amounts_supported():
    assert parse_amount("-160.00").candidates == (D("-160.00"),)
    assert parse_amount("-17000\n.").candidates == (D("-170.00"), D("-17000.00"))
    assert parse_amount("-").candidates == (D(0),)          # lone dash is still "nil"


def test_decimal_not_float():
    assert isinstance(parse_amount("0.10").candidates[0], D)
    assert parse_amount("0.10").candidates[0] + parse_amount("0.20").candidates[0] == D("0.30")


def test_description_whitespace_only_normalised():                     # 7 (multiline)
    raw = "NEFT*YESB0000001*YESF362767014097*ZERODHA\n  BROKING"
    assert clean_description(raw) == "NEFT*YESB0000001*YESF362767014097*ZERODHA BROKING"
    assert clean_reference("-") == "" and clean_reference(" 123 ") == "123"


def _t(**kw):
    base = dict(source_page=1, source_row=1, section=0, date_iso="2026-10-01", description="X",
                reference="", credit_c=(), debit_c=(), balance_c=())
    base.update(kw)
    t = Txn(**base)
    t.credit, t.debit, t.balance = D(0), D("10.00"), D("90.00")
    return t


def test_fingerprint_deterministic_and_field_sensitive():
    a, b = _t(), _t()
    assert fingerprint("h", "A", a) == fingerprint("h", "A", b)
    assert fingerprint("h", "A", a) != fingerprint("h2", "A", a)
    assert fingerprint("h", "A", a) != fingerprint("h", "B", a)
    b.balance = D("80.00")
    assert fingerprint("h", "A", a) != fingerprint("h", "A", b)
