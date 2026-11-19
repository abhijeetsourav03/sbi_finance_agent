"""OCR-first SBI Relationship Summary extractor.

Works on image-only SBI PDFs with known six-column table layout.
Flags uncertain/invalid rows rather than silently accepting them.
"""
from __future__ import annotations
import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import cv2
import pymupdf
import numpy as np
import pandas as pd
import pytesseract
from PIL import Image
from .config import SBI_PDF_PASSWORD

# Column boundaries measured as fractions of the transaction table width.
# Date | Transaction Reference | Ref.No | Credit | Debit | Balance
COL_FRACTIONS = (0, .087, .49, .635, .746, .875, 1)
DATE_RE = re.compile(r"\b(\d{2})[-/.](\d{2})[-/.](\d{2,4})\b")


def amount(s):
    s = re.sub(r"[^0-9.,-]", "", s).replace(",", "")
    if not s or s == "-": return None
    try: return Decimal(s)
    except InvalidOperation: return None


def ocr(cell, mode=7):
    if cell.size == 0: return ""
    gray = cv2.cvtColor(cell, cv2.COLOR_RGB2GRAY) if cell.ndim == 3 else cell
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return pytesseract.image_to_string(gray, config=f"--psm {mode}").strip().replace("\n", " ")


def horizontal_grid_lines(img, x0, x1, y0, y1):
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    # Extract long horizontal grid lines from the table region.
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, (x1-x0)//4), 1))
    lines = cv2.morphologyEx(inv, cv2.MORPH_OPEN, kernel)
    ys = []
    for y in range(y0, y1):
        if np.count_nonzero(lines[y, x0:x1]) > (x1-x0) * .55:
            ys.append(y)
    groups=[]
    for y in ys:
        if not groups or y > groups[-1][-1] + 2: groups.append([y])
        else: groups[-1].append(y)
    return [int(np.mean(g)) for g in groups]


def extract(pdf_path: Path, output: Path, dpi: int = 220):
    # doc=pymupdf.open(pdf_path)
    with pymupdf.open(pdf_path) as doc:

        # Authenticate password-protected SBI statements
        if doc.needs_pass:

            if not SBI_PDF_PASSWORD:
                raise ValueError(
                    "SBI_PDF_PASSWORD is missing from .env"
                )

            if not doc.authenticate(SBI_PDF_PASSWORD):
                raise ValueError(
                    f"Incorrect PDF password for: {pdf_path}"
                )

            print(f"PDF unlocked successfully: {pdf_path}")

        print(f"Total pages: {len(doc)}")
        records=[]
        for page_index in range(len(doc)):
            # Pages 3-6 = account 1; page 7 = account 2.
            if page_index not in (2,3,4,5,6): continue
            page=doc[page_index]
            pix=page.get_pixmap(dpi=dpi, alpha=False)
            img=np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height,pix.width,3)
            h,w=img.shape[:2]
            x0,x1=int(.033*w),int(.97*w)
            # Relative table regions for the supplied 7-page SBI layout.
            y0=int((.55 if page_index in (2,6) else .175)*h)
            y1=int((.98 if page_index in (2,3,4) else (.34 if page_index==5 else .85))*h)
            boundaries=horizontal_grid_lines(img,x0,x1,y0,y1)
            if len(boundaries)<3:
                print(f"WARNING: page {page_index+1}: grid not detected; review manually")
                continue
            for top,bottom in zip(boundaries,boundaries[1:]):
                if bottom-top < 8: continue
                cells=[]
                for col in range(6):
                    xa=x0+int((x1-x0)*COL_FRACTIONS[col])+2
                    xb=x0+int((x1-x0)*COL_FRACTIONS[col+1])-2
                    crop=img[top+2:bottom-2,xa:xb]
                    cells.append(ocr(crop, 6 if col==1 else 7))
                m=DATE_RE.search(cells[0])
                if not m: continue  # Header, opening balance and closing balance lines
                dd,mm,yy=m.groups()
                yy=yy if len(yy)==4 else '20'+yy
                try: date=datetime(int(yy),int(mm),int(dd)).date().isoformat()
                except ValueError: date=None
                credit,debit,balance=[amount(s) for s in cells[3:6]]
                flags=[]
                if date is None: flags.append('invalid_date')
                if credit is None or debit is None or balance is None: flags.append('unreadable_amount')
                if credit is not None and debit is not None and credit>0 and debit>0: flags.append('both_credit_and_debit')
                if not cells[1]: flags.append('missing_reference')
                records.append(dict(account_label='account_1' if page_index<6 else 'account_2',page=page_index+1,date=date,reference=cells[1],ref_number=cells[2],credit=str(credit) if credit is not None else '',debit=str(debit) if debit is not None else '',balance=str(balance) if balance is not None else '',review_flags=';'.join(flags)))
    df=pd.DataFrame(records)
    if df.empty: raise RuntimeError('No transactions extracted. OCR/grid detection needs tuning for this statement.')
    # Validate each consecutive ledger row independently for each account.
    for account, indexes in df.groupby('account_label', sort=False).groups.items():
        prior=None
        for idx in indexes:
            row=df.loc[idx]
            try:
                bal=Decimal(row.balance); credit=Decimal(row.credit); debit=Decimal(row.debit)
                if prior is not None and abs((prior+credit-debit)-bal)>Decimal('.01'):
                    df.at[idx,'review_flags'] += ';balance_mismatch'
                prior=bal
            except (InvalidOperation,TypeError):
                prior=None
    output.parent.mkdir(parents=True,exist_ok=True)
    df.to_csv(output,index=False)
    flagged=int(df.review_flags.astype(bool).sum())
    print(f'Extracted {len(df)} candidate rows, {flagged} flagged for review. Saved {output}')
    print('IMPORTANT: Reconcile row counts and opening/closing balances manually before using for budgeting.')

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('pdf',type=Path)
    p.add_argument('--output',type=Path,default=Path('data/processed/sbi_transactions_review.csv'))
    p.add_argument('--tesseract',default=None,help='Windows path to tesseract.exe')
    a=p.parse_args()
    if a.tesseract: pytesseract.pytesseract.tesseract_cmd=a.tesseract
    extract(a.pdf,a.output)
