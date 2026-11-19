"""Running-balance reconciliation and per-row validation flags.

Principles:
* Amounts are never edited to force a reconciliation.
* Where an extracted cell has several possible readings (e.g. displaced
  decimal), the reading that satisfies the balance equation is chosen and the
  row is flagged `amount_resolved_by_balance` when it was not the default.
* A mismatch may originate in a *previous* (missing/misread) row, so the first
  break after a good chain also carries `possible_missing_transaction`.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import product
from typing import Sequence

from .transaction_normalizer import Txn, money

TOL = Decimal("0.01")


@dataclass
class AccountResult:
    account_id: str
    identity: str                         # verified | verified_by_continuity | unverified
    opening: Decimal | None
    closing: Decimal | None
    period: tuple[date, date] | None = None
    transactions: int = 0
    pages: list[int] = field(default_factory=list)
    first_balance_check: str = "missing"  # ok | mismatch | missing   (opening vs first row)
    closing_check: str = "missing"        # ok | mismatch | missing
    last_balance: Decimal | None = None
    mismatches: list[dict] = field(default_factory=list)

    @property
    def reconciled(self) -> bool:
        return (self.identity != "unverified" and self.first_balance_check == "ok"
                and self.closing_check == "ok" and not self.mismatches)

    def as_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "identity_status": self.identity,
            "opening_balance": money(self.opening) if self.opening is not None else None,
            "closing_balance": money(self.closing) if self.closing is not None else None,
            "last_extracted_balance": money(self.last_balance) if self.last_balance is not None else None,
            "opening_balance_check": self.first_balance_check,
            "closing_balance_check": self.closing_check,
            "transactions": self.transactions,
            "pages": sorted(set(self.pages)),
            "running_balance_mismatches": self.mismatches,
            "reconciled": self.reconciled,
        }


def validate_account(txns: Sequence[Txn], opening: Decimal | None, closing: Decimal | None,
                     account_id: str, identity: str,
                     period: tuple[date, date] | None = None) -> AccountResult:
    res = AccountResult(account_id, identity, opening, closing, period, transactions=len(txns))
    prev: Decimal | None = opening
    chain_good = opening is not None
    first = True
    for t in txns:
        res.pages.append(t.source_page)
        if identity == "unverified":
            t.flags.add("account_unverified")
        if t.date_iso is None:
            t.flags.add("invalid_date")
        elif period and not (period[0] <= date.fromisoformat(t.date_iso) <= period[1]):
            t.flags.add("date_outside_statement_period")
        if not t.description:
            t.flags.add("missing_description")

        if not (t.credit_c and t.debit_c and t.balance_c):
            t.flags.add("unreadable_amount")
            t.credit = t.debit = t.balance = None
            prev = t.balance_c[0] if t.balance_c else None
            chain_good = False
            first = False
            continue

        combos = list(product(t.credit_c, t.debit_c, t.balance_c))      # default reading first
        default = combos[0]
        pick = default
        if prev is None:
            if any(len(c) > 1 for c in (t.credit_c, t.debit_c, t.balance_c)):
                t.flags.add("ambiguous_amount")
        else:
            sat = [c for c in combos if abs(prev + c[0] - c[1] - c[2]) <= TOL]
            if sat:
                pick = sat[0]
                if pick != default:
                    t.flags.add("amount_resolved_by_balance")
                if len({s for s in sat}) > 1:
                    t.flags.add("ambiguous_amount")
                if first:
                    res.first_balance_check = "ok"
            else:
                t.flags.add("balance_mismatch")
                gap = default[2] - (prev + default[0] - default[1])
                if chain_good:
                    t.flags.add("possible_missing_transaction")
                res.mismatches.append({"page": t.source_page, "row": t.source_row,
                                       "unexplained_balance_difference": money(gap)})
                if first:
                    res.first_balance_check = "mismatch"
        t.credit, t.debit, t.balance = pick
        if t.credit > 0 and t.debit > 0:
            t.flags.add("both_credit_and_debit")
        chain_good = "balance_mismatch" not in t.flags
        prev = t.balance
        first = False
        res.last_balance = t.balance

    if closing is not None and res.last_balance is not None:
        res.closing_check = "ok" if abs(res.last_balance - closing) <= TOL else "mismatch"
    return res


def flag_duplicates(txns: Sequence[Txn]) -> None:
    """Flag identical fingerprints. Rows are kept: repeats can be legitimate."""
    counts = Counter(t.transaction_id for t in txns)
    for t in txns:
        if counts[t.transaction_id] > 1:
            t.flags.add("duplicate_candidate")
