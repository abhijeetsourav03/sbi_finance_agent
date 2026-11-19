from datetime import date
from decimal import Decimal as D

from src.transaction_normalizer import Txn, fingerprint
from src.transaction_validator import flag_duplicates, validate_account

Z = D(0)


def row(i, cr, db, bal, *, desc="X", d="2026-10-01", cr_c=None, db_c=None, bal_c=None):
    return Txn(1, i, 0, d, desc, "", (D(cr),) if cr_c is None else cr_c, (D(db),) if db_c is None else db_c,
               (D(bal),) if bal_c is None else bal_c)


def chain():
    return [row(1, 0, "100.00", "900.00"), row(2, "50.50", 0, "950.50"), row(3, 0, "0.50", "950.00")]


def test_clean_chain_reconciles():
    r = validate_account(chain(), D("1000.00"), D("950.00"), "XXXXXXX1", "verified")
    assert r.reconciled and r.first_balance_check == "ok" and r.closing_check == "ok"


def test_no_flags_on_clean_rows():
    t = chain()
    validate_account(t, D("1000.00"), D("950.00"), "A", "verified")
    assert all(not x.flags for x in t)


def test_balance_mismatch_flagged_and_values_never_modified():     # 13
    t = chain()
    t[1].balance_c = (D("999.99"),)                                  # wrong extracted balance
    r = validate_account(t, D("1000.00"), D("950.00"), "A", "verified")
    assert "balance_mismatch" in t[1].flags
    assert t[1].balance == D("999.99")                               # NOT forced to 950.50
    assert not r.reconciled and r.mismatches[0]["row"] == 2


def test_missing_transaction_detected_by_balance_gap():             # 14
    t = [chain()[0], chain()[2]]                                     # middle row missing
    r = validate_account(t, D("1000.00"), D("950.00"), "A", "verified")
    assert {"balance_mismatch", "possible_missing_transaction"} <= t[1].flags
    assert r.mismatches[0]["unexplained_balance_difference"] == "50.50"
    assert r.mismatches[0]["page"] == 1 and not r.reconciled


def test_mismatch_not_assumed_to_be_current_row_only():
    t = chain()
    t[0].balance_c = (D("800.00"),)                                  # error in row 1 → rows 1 and 2 break
    r = validate_account(t, D("1000.00"), D("950.00"), "A", "verified")
    assert len(r.mismatches) == 2


def test_displaced_decimal_resolved_by_balance_not_blind_division():
    t = [row(1, 0, 0, 0, db_c=(D("170.00"), D("17000.00")), bal_c=(D("830.00"), D("83000.00")))]
    validate_account(t, D("1000.00"), D("830.00"), "A", "verified")
    assert t[0].debit == D("170.00") and t[0].balance == D("830.00") and not t[0].flags
    # when the alternative reading is the one that balances, it is chosen and flagged
    t = [row(1, 0, 0, 0, db_c=(D("1.70"), D("170.00")), bal_c=(D("8.30"), D("830.00")))]
    validate_account(t, D("1000.00"), D("830.00"), "A", "verified")
    assert t[0].debit == D("170.00") and "amount_resolved_by_balance" in t[0].flags


def test_ambiguous_amount_without_opening_is_flagged():
    t = [row(1, 0, 0, 0, db_c=(D("1.70"), D("170.00")), bal_c=(D("8.30"),))]
    validate_account(t, None, None, "A", "verified")
    assert "ambiguous_amount" in t[0].flags


def test_unreadable_amount_flagged():
    t = [row(1, 0, 0, 0, db_c=(), bal_c=(D("5.00"),))]
    validate_account(t, D("5.00"), None, "A", "verified")
    assert "unreadable_amount" in t[0].flags and t[0].balance is None


def test_both_credit_and_debit_flagged():
    t = [row(1, "10.00", "5.00", "1005.00")]
    validate_account(t, D("1000.00"), None, "A", "verified")
    assert "both_credit_and_debit" in t[0].flags


def test_opening_and_closing_checks():
    r = validate_account(chain(), D("1.00"), D("950.00"), "A", "verified")
    assert r.first_balance_check == "mismatch" and not r.reconciled
    r = validate_account(chain(), D("1000.00"), D("951.00"), "A", "verified")
    assert r.closing_check == "mismatch"
    r = validate_account(chain(), None, None, "A", "verified")
    assert r.first_balance_check == "missing" and r.closing_check == "missing" and not r.reconciled


def test_tolerance_is_one_paisa():
    t = [row(1, 0, "100.00", "899.99")]
    validate_account(t, D("1000.00"), None, "A", "verified")
    assert not t[0].flags
    t = [row(1, 0, "100.00", "899.98")]
    validate_account(t, D("1000.00"), None, "A", "verified")
    assert "balance_mismatch" in t[0].flags


def test_invalid_date_missing_description_unverified_account_and_period():
    t = [row(1, 0, "1.00", "999.00", desc="", d=None), row(2, 0, "1.00", "998.00", d="2026-11-05")]
    validate_account(t, D("1000.00"), None, "?", "unverified", (date(2026, 10, 1), date(2026, 10, 31)))
    assert {"invalid_date", "missing_description", "account_unverified"} <= t[0].flags
    assert "date_outside_statement_period" in t[1].flags


def test_duplicate_candidates_flagged_not_removed():               # 15
    a, b, c = row(1, 0, "5.00", "95.00"), row(2, 0, "5.00", "95.00"), row(3, 0, "5.00", "90.00")
    for t in (a, b, c):
        t.credit, t.debit, t.balance = t.credit_c[0], t.debit_c[0], t.balance_c[0]
        t.transaction_id = fingerprint("h", "A", t)
    flag_duplicates([a, b, c])
    assert "duplicate_candidate" in a.flags and "duplicate_candidate" in b.flags
    assert not c.flags                                              # same amount, different balance = legitimate repeat
