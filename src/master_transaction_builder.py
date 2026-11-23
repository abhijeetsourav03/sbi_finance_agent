
# src/master_transaction_builder.py

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re

import pandas as pd

from .excel_reader import (
    PROJECT_ROOT,
    get_excel_passwords,
    read_encrypted_excel,
)
from .sbi_transaction_extractor import (
    extract_sheet,
    validate_statement,
)


INPUT_DIR = PROJECT_ROOT / "data" / "raw_statements"
OUTPUT_DIR = PROJECT_ROOT / "data" / "processed"

MASTER_PATH = OUTPUT_DIR / "sbi_master_transactions.xlsx"

KEY_FIELDS = (
    "date",
    "details",
    "reference",
    "debit",
    "credit",
    "balance",
)


def normalize_text(value):
    """Normalize formatting, not transaction meaning."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def transaction_signature(record):
    """
    Same signature means a possible occurrence of
    the same bank transaction.
    """
    values = []

    for field in KEY_FIELDS:
        value = record[field]

        if field in ("details", "reference"):
            value = normalize_text(value)

        values.append(str(value))

    return tuple(values)


def make_transaction_id(signature, occurrence):
    payload = json.dumps(
        [*signature, occurrence],
        ensure_ascii=False,
    )
    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:24]


def load_validated_statements():
    files = sorted(INPUT_DIR.glob("*Email_Statement*.xlsx"))

    if not files:
        raise RuntimeError("No SBI Excel statements found")

    passwords = get_excel_passwords()

    statements = []
    errors = []

    for path in files:
        print(f"\nReading: {path.name}")

        try:
            sheets, password_index = read_encrypted_excel(
                path, passwords
            )

            if len(sheets) != 1:
                raise ValueError("Expected one worksheet")

            df = next(iter(sheets.values()))

            records, summary = extract_sheet(
                df, path.name
            )

            checks, running_errors = validate_statement(
                records, summary
            )

            if not all(checks.values()):
                raise ValueError(
                    f"Reconciliation failed: {checks}; "
                    f"running-balance rows: {running_errors[:10]}"
                )

            statements.append({
                "filename": path.name,
                "records": records,
                "first_date": min(r["date"] for r in records),
                "last_date": max(r["date"] for r in records),
                "row_count": len(records),
            })

            print(f"Validated {len(records)} transactions")

        except Exception as exc:
            errors.append((path.name, str(exc)))
            print(f"FAILED: {type(exc).__name__}: {exc}")

    # Never publish an incomplete master as if all
    # available source statements were processed.
    if errors:
        print("\nMaster not updated. Failed statements:")
        for filename, reason in errors:
            print(f"- {filename}: {reason}")

        raise RuntimeError(
            f"{len(errors)} statement(s) failed validation"
        )

    return statements



def build_master(statements):
    all_rows = []

    for statement in statements:
        filename = statement["filename"]

        # Preserve original order within each statement.
        df = pd.DataFrame(statement["records"])

        if df.empty:
            continue

        # Number transactions independently for each date.
        df["daily_txn_no"] = (
            df.groupby("date", sort=False).cumcount() + 1
        )

        df["source_file"] = filename

        all_rows.append(df)

    if not all_rows:
        raise RuntimeError("No transactions found")

    combined = pd.concat(all_rows, ignore_index=True)

    key = ["date", "daily_txn_no"]

    # Check whether the same key has conflicting financial data.
    # conflicts = []

    for (date, number), group in combined.groupby(key):
        signatures = {
            transaction_signature(row)
            for row in group.to_dict("records")
        }

        if len(signatures) > 1:
            conflicts.append({
                "date": date,
                "daily_txn_no": number,
                "reason": "Different transactions share the same daily key",
                "source_files": " | ".join(
                    sorted(group["source_file"].unique())
                ),
            })

    # Prefer the most complete statement for each date.
    # Count transactions per date per source.
    combined["daily_source_count"] = (
        combined.groupby(["source_file", "date"])["date"]
        .transform("size")
    )

    combined = combined.sort_values(
        ["daily_source_count", "source_file"],
        ascending=[False, False],
        kind="stable",
    )

    master = combined.drop_duplicates(
        subset=key,
        keep="first",
    )

    master = master.sort_values(
        key,
        kind="stable",
    ).reset_index(drop=True)

    master.insert(
        0, "sequence",
        range(1, len(master) + 1),
    )

    master["transaction_id"] = master.apply(
        lambda row: make_transaction_id(
            (row["date"],),
            int(row["daily_txn_no"]),
        ),
        axis=1,
    )

    master["debit"] = master["debit"].astype(float)
    master["credit"] = master["credit"].astype(float)
    master["balance"] = master["balance"].astype(float)

    review = pd.DataFrame(
        conflicts,
        columns=[
            "date",
            "daily_txn_no",
            "reason",
            "source_files",
        ],
    )

    return master, review


def save_master(master, review, statements):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    coverage = pd.DataFrame([
        {
            "source_file": s["filename"],
            "first_date": s["first_date"],
            "last_date": s["last_date"],
            "transaction_rows": s["row_count"],
            "status": "VALIDATED",
        }
        for s in statements
    ])

    # Write a temporary file and replace the old master
    # only after the new workbook is successfully created.
    temporary_path = OUTPUT_DIR / "sbi_master_transactions.tmp.xlsx"

    try:
        with pd.ExcelWriter(
            temporary_path,
            engine="openpyxl",
        ) as writer:
            master.to_excel(
                writer,
                sheet_name="Master Transactions",
                index=False,
            )

            coverage.to_excel(
                writer,
                sheet_name="Source Coverage",
                index=False,
            )

            review.to_excel(
                writer,
                sheet_name="Duplicate Review",
                index=False,
            )

        temporary_path.replace(MASTER_PATH)

    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main():
    statements = load_validated_statements()

    master, review = build_master(statements)

    save_master(master, review, statements)

    print("\n========== MASTER SUMMARY ==========")
    print(f"Statements processed: {len(statements)}")
    print(f"Master transactions: {len(master)}")
    print(f"Earliest date: {master['date'].min()}")
    print(f"Latest date: {master['date'].max()}")
    print(f"Transactions flagged for review: {len(review)}")
    print(f"Master saved: {MASTER_PATH}")


if __name__ == "__main__":
    main()
