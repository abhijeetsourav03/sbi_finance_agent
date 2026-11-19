"""Synthetic (non-sensitive) statement PDFs for the test-suite."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

D = Decimal
L6 = [("Date", 60, "date"), ("Transaction Reference", 200, "description"), ("Ref.No./Chq.No.", 60, "reference"),
      ("Credit", 60, "credit"), ("Debit", 60, "debit"), ("Balance", 70, "balance")]
L7 = L6[:2] + [("", 20, None)] + L6[2:]
L8 = L6[:1] + [("", 20, None)] + L6[1:2] + [("", 20, None)] + L6[2:]
LAYOUTS = {6: L6, 7: L7, 8: L8}


def fmt_date(d: date, style: str) -> str:
    return {"dd-mm-yy": d.strftime("%d-%m-%y"), "dd/mm/yyyy": d.strftime("%d/%m/%Y"),
            "ddmmyy": d.strftime("%d%m%y"), "dd mm yy": d.strftime("%d %m %y")}[style]


def _cell_text(v: Decimal, zero_style: str, displaced: bool) -> str:
    if v == 0:
        return {"0": "0", "-": "-", "blank": ""}[zero_style]
    s = f"{v:.2f}"
    return s.replace(".", "") + "\n." if displaced else s


def _draw_table(page, x0, y0, layout, rows, heights):
    xs = [x0]
    for _, w, _ in layout:
        xs.append(xs[-1] + w)
    y = y0
    ys = [y0]
    for h in heights:
        y += h
        ys.append(y)
    for yy in ys:
        page.draw_line((xs[0], yy), (xs[-1], yy), width=0.6)
    for xx in xs:
        page.draw_line((xx, ys[0]), (xx, ys[-1]), width=0.6)
    for r, row in enumerate(rows):
        for c, txt in enumerate(row):
            for k, line in enumerate(str(txt).split("\n")):
                if line:
                    page.insert_text((xs[c] + 3, ys[r] + 10 + 8 * k), line, fontsize=7)
    return ys[-1]


def _heights(rows):
    return [max(14, 8 * max(len(str(c).split("\n")) for c in r) + 6) for r in rows]


def make_statement(path: Path, accounts, layout: int = 6, rows_per_page: int = 40, repeat_header: bool = True,
                   filler: int = 0, password: str | None = None, zero_style: str = "0", displaced: bool = False,
                   date_style: str = "dd-mm-yy", closing_override=None, drop=(), footer: bool = True):
    """accounts: [{token, opening, rows:[(date, desc, credit, debit)]}]. Returns expected data per account.

    `drop` removes rows from the PDF *after* balances are computed (simulating a missing transaction).
    """
    lay = LAYOUTS[layout]
    header = [h for h, _, _ in lay]
    doc = pymupdf.open()
    for _ in range(filler):                                   # non-transaction pages (customer info table)
        pg = doc.new_page()
        pg.insert_text((30, 40), "Relationship Summary", fontsize=12)
        _draw_table(pg, 30, 80, [("Field", 100, "x"), ("Value", 200, "y")],
                    [["Name", "Test User"], ["Branch", "Main Road"], ["IFSC", "TEST0000001"]], [14, 14, 14])
        pg.insert_text((30, 200), "Customer care number and details for the account holder shown above", fontsize=7)
    expected = []
    for acct in accounts:
        bal = acct["opening"]
        out_rows = []
        for i, (d, desc, cr, db) in enumerate(acct["rows"]):
            bal = bal + cr - db
            out_rows.append((d, desc, cr, db, bal, i))
        expected.append([(d.isoformat(), desc, cr, db, b) for d, desc, cr, db, b, _ in out_rows])
        shown = [r for r in out_rows if r[5] not in drop]
        closing = closing_override if closing_override is not None else bal
        chunks = [shown[i:i + rows_per_page] for i in range(0, len(shown), rows_per_page)] or [[]]
        for n, chunk in enumerate(chunks):
            pg = doc.new_page()
            y = 40
            if n == 0:
                pg.insert_text((30, 50), "SAVING ACCOUNT", fontsize=12)
                pg.insert_text((30, 68), acct["token"], fontsize=12)
                pg.insert_text((30, 95), f"Your Opening Balance on 01-10-26:  Rs. {acct['opening']:.2f}", fontsize=8)
                y = 110
            if n == 0 or repeat_header:
                rows = [header]
            else:
                rows = []
            for d, desc, cr, db, b, _ in chunk:
                cells = {"date": fmt_date(d, date_style), "description": desc, "reference": "-",
                         "credit": _cell_text(cr, zero_style, displaced), "debit": _cell_text(db, zero_style, displaced),
                         "balance": _cell_text(b, zero_style, displaced)}
                rows.append([cells.get(f, "") if f else "" for _, _, f in lay])
            bottom = _draw_table(pg, 30, y, lay, rows, _heights(rows))
            if n == len(chunks) - 1:
                pg.insert_text((30, bottom + 14), f"Your Closing Balance on 31-10-26:  Rs. {closing:.2f}", fontsize=8)
                if footer:
                    pg.insert_text((30, bottom + 28), "*All dates are in DD-MM-YY format", fontsize=7)
    if password:
        doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "-owner")
    else:
        doc.save(str(path))
    doc.close()
    return expected


def txs(n=6, start=date(2026, 10, 1)):
    """Deterministic synthetic rows: alternating debits and credits."""
    rows = []
    for i in range(n):
        d = date(start.year, start.month, 1 + i)
        if i % 3 == 2:
            rows.append((d, f"NEFT*TEST0000001*REF{i:04d}*EMPLOYER PAYMENT", D("1500.50"), D(0)))
        else:
            rows.append((d, f"UPI/DR/6000000000{i:02d}/SHOP {i}/TEST/user{i}@bank/Sent", D(0), D("170.00") + i))
    return rows


@pytest.fixture
def tmp_pdf(tmp_path):
    return tmp_path / "stmt.pdf"
