# SBI Finance Agent — Phase 1 & Phase 2 v2

Automatically fetch SBI bank statements from Gmail, decrypt and validate Excel files, consolidate transactions, and generate a categorized transaction workbook.

## 1. Setup

Activate the virtual environment:

```powershell
.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
pip install -r requirements-v2.txt
```

Configure `.env`:

```env
GMAIL_QUERY="from:yonobysbi@alerts.sbi.bank.in has:attachment {filename:xlsx filename:xls filename:csv}"

SBI_EXCEL_PASSWORD_1="your_password_1"
SBI_EXCEL_PASSWORD_2="your_password_2"
```

Gmail API credentials must already be configured for the Gmail fetcher. On first use, complete the Google OAuth authorization if prompted.

## 2. Processing Flow

```text
Gmail Inbox
    |
    v
Fetch SBI Statement Attachments
(src/main.py)
    |
    v
Encrypted SBI Excel Statements
(data/raw/)
    |
    v
Excel Decryption
(src/excel_reader.py)
    |
    v
Transaction Extraction & Validation
(src/sbi_transaction_extractor.py)
    |
    v
Master Transaction Consolidation
(src/master_transaction_builder.py)
    |
    v
Transaction Categorization
(src/transaction_categorizer.py)
    |
    v
Categorized Transactions
(data/processed/sbi_categorized_transactions.xlsx)
```

## 3. Run the Pipeline

Run these commands from the project root, in order.

**Step 1 — Fetch SBI statements from Gmail**

```powershell
python -m src.main
```

Downloads matching SBI Excel attachments from Gmail.

**Step 2 — Generate master transactions**

```powershell
python -m src.master_transaction_builder
```

Decrypts statements, extracts and validates transactions, and consolidates overlapping statements.

Output: `data/processed/sbi_master_transactions.xlsx`

**Step 3 — Categorize transactions**

```powershell
python -m src.transaction_categorizer
```

Applies the custom categorization rules.

Output: `data/processed/sbi_categorized_transactions.xlsx`


## 4. Final Output

`data/processed/sbi_categorized_transactions.xlsx`

The **Categorized Transactions** worksheet contains seven columns:

| Column | Description |
|---|---|
| date | Transaction date |
| daily_txn | Daily transaction sequence |
| transaction_type | Expense, Trading, Loan, Transfer, etc. |
| category | Assigned category |
| credit | Amount received |
| debit | Amount sent |
| balance | Account balance |

Additional worksheets provide monthly category totals, transactions needing review, and an overview.

## 5. Notes

- Run the three commands in sequence whenever you want to refresh the transaction data.
- Overlapping statements are consolidated to avoid duplicate transactions.
- Unclassified transactions remain available for review.
- Never commit `.env`, Gmail credentials, bank statements, or generated financial data to Git.