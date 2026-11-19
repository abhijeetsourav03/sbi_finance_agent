"""Date / amount / description normalisation. All money is Decimal, never float."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")


def money(d: Decimal) -> str:
    """Two-decimal string for CSV/JSON."""
    return str(d.quantize(CENT))


# ------------------------------------------------------------------------------ dates
@dataclass(frozen=True)
class DateParse:
    iso: str | None
    flags: tuple[str, ...] = ()


_DATE_SHAPE = re.compile(r"^\d{1,2}[-/. ]?\d{1,2}[-/. ]?\d{2,4}$")


def looks_like_date(text: str) -> bool:
    s = re.sub(r"\s*([-/.])\s*", r"\1", (text or "").strip())
    return bool(_DATE_SHAPE.match(s))


def parse_date(text: str, *, fmt_known: bool = True,
               period: tuple[date, date] | None = None) -> DateParse:
    """Parse DD-MM-YY / DD/MM/YYYY / DDMMYY / 'DD MM YY' into ISO.

    Day-first is the Indian bank convention. When the statement does not state
    its format and the date could be read either way, the row is flagged
    `ambiguous_date_format` unless the statement period disambiguates.
    """
    s = re.sub(r"\s*([-/.])\s*", r"\1", (text or "").strip())
    s = re.sub(r"\s+", " ", s)
    m = re.fullmatch(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{2}|\d{4})", s)
    if m:
        d, mo, y = m.groups()
    elif re.fullmatch(r"\d{6}|\d{8}", s):
        d, mo, y = s[:2], s[2:4], s[4:]
    else:
        return DateParse(None, ("invalid_date",))
    year = int(y) + (2000 if len(y) == 2 else 0)
    try:
        primary = date(year, int(mo), int(d))
    except ValueError:
        return DateParse(None, ("invalid_date",))
    flags: list[str] = []
    if not fmt_known and int(d) <= 12 and int(mo) <= 12 and d != mo:
        try:
            alt = date(year, int(d), int(mo))
        except ValueError:
            alt = None
        resolved = bool(period and alt and period[0] <= primary <= period[1]
                        and not (period[0] <= alt <= period[1]))
        if not resolved:
            flags.append("ambiguous_date_format")
    return DateParse(primary.isoformat(), tuple(flags))


# ---------------------------------------------------------------------------- amounts
@dataclass(frozen=True)
class AmountParse:
    """Candidate readings for one amount cell, most-likely first."""
    raw: str
    status: str                      # exact | displaced | no_decimal | blank | dash | invalid
    candidates: tuple[Decimal, ...] = ()

    @property
    def is_blank(self) -> bool:
        return self.status in ("blank", "dash")


_CUR = re.compile(r"(₹|rs\.?|inr)", re.I)
_PLAIN = re.compile(r"\d{1,3}(,\d{2,3})*(\.\d{1,2})?|\d+(\.\d{1,2})?")


def parse_amount(raw: str | None) -> AmountParse:
    """Normalise an amount cell.

    Handles '1,250.00', '1250.00', '0', '-', blank, and displaced decimals such
    as '17000\\n.' (PyMuPDF splitting '170.00'). Ambiguous readings return BOTH
    candidates; the validator picks using the running-balance equation, so no
    amount is blindly divided by 100.
    """
    raw = raw or ""
    s = re.sub(r"\s+", "", _CUR.sub("", raw))
    if s == "":
        return AmountParse(raw, "blank", ())
    if s in {"-", "–", "—"}:
        return AmountParse(raw, "dash", (ZERO,))
    neg = len(s) > 1 and s[0] == "-"          # overdrawn balances, e.g. "-160.00"
    if neg:
        s = s[1:]
    sign = Decimal(-1) if neg else Decimal(1)
    try:
        if _PLAIN.fullmatch(s):
            v = Decimal(s.replace(",", "")) * sign
            digits = s.replace(",", "")
            if "." not in digits and v != 0 and len(digits) >= 3:
                # decimal point lost by extraction: 2-decimal reading is the default
                return AmountParse(raw, "no_decimal", ((v / 100).quantize(CENT), v.quantize(CENT)))
            return AmountParse(raw, "exact", (v.quantize(CENT),))
        m = re.fullmatch(r"(\d+)\.", s)
        if m:
            v = Decimal(m.group(1)) * sign
            return AmountParse(raw, "displaced", ((v / 100).quantize(CENT), v.quantize(CENT)))
    except InvalidOperation:  # pragma: no cover
        pass
    return AmountParse(raw, "invalid", ())


# ----------------------------------------------------------------------- descriptions
def clean_description(text: str | None) -> str:
    """Collapse whitespace/line breaks only. Identifiers are never removed or truncated."""
    return re.sub(r"\s+", " ", text or "").strip()


def clean_reference(text: str | None) -> str:
    t = clean_description(text)
    return "" if t in {"-", "–", "—", ".", ","} else t


# -------------------------------------------------------------------------- records
@dataclass
class Txn:
    """One extracted row after normalisation, before balance validation."""
    source_page: int
    source_row: int
    section: int                         # account-section index
    date_iso: str | None
    description: str
    reference: str
    credit_c: tuple[Decimal, ...]
    debit_c: tuple[Decimal, ...]
    balance_c: tuple[Decimal, ...]
    flags: set[str] = field(default_factory=set)
    credit: Decimal | None = None
    debit: Decimal | None = None
    balance: Decimal | None = None
    account_id: str = "unverified"
    transaction_id: str = ""


def fingerprint(pdf_hash: str, account_id: str, t: Txn) -> str:
    parts = [pdf_hash, account_id, t.date_iso or "", t.description,
             money(t.credit) if t.credit is not None else "",
             money(t.debit) if t.debit is not None else "",
             money(t.balance) if t.balance is not None else ""]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
