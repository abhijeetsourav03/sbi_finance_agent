import re
from pathlib import Path

import pandas as pd

from .excel_reader import PROJECT_ROOT

MASTER_PATH = (
    PROJECT_ROOT / "data" / "processed"
    / "sbi_master_transactions.xlsx"
)

OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "processed"
    / "sbi_categorized_transactions.xlsx"
)



# User-defined SBI transaction categories

TRADING_KEYWORDS = (
    "ZERODHA",
    "GROWW",
    "GR0WW",
)

TRANSFER_KEYWORDS = {
    "MUSKAN": "Muskan (Transfer)",
    "DEBABRATA": "Debabrata (Transfer)",
    "ODUS": "Odus (Transfer)",
}

NETBANKING_KEYWORDS = (
    "NEFT",
    "IMPS",
    "CEMTEX",
    "ACHCR",
    "SBIY",
)

def normalize_description(value):
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).upper().strip()


def classify_transaction(row):
    """
    Classify transactions according to user-defined rules.

    Returns:
        transaction_type
        category
        classification_confidence
    """

    description = normalize_description(row["details"])

    debit = row["debit"]
    credit = row["credit"]

    if debit > 0 and credit > 0:
        return "Review", "Other", "Needs Review"

    if debit <= 0 and credit <= 0:
        return "Review", "Other", "Needs Review"

    # 1. Trading activity
    if any(
        keyword in description
        for keyword in TRADING_KEYWORDS
    ):
        return (
            "Trading",
            "Trading - Zerodha/Groww",
            "User Rule",
        )

    # 2. Nutan loan transactions
    if "NUTAN" in description:
        return (
            "Loan",
            "Nutan (Loan)",
            "User Rule",
        )

    # 3. Excluded personal transfers
    for keyword, category in TRANSFER_KEYWORDS.items():
        if keyword in description:
            return (
                "Transfer",
                category,
                "User Rule",
            )

    # 4. IRCTC transactions
    if "IRCTC" in description:
        if debit > 0:
            return (
                "Expense",
                "IRCTC",
                "User Rule",
            )

        return (
            "Credit - Unclassified",
            "IRCTC",
            "Needs Review",
        )

    # 5. Netbanking
    if any(
        keyword in description
        for keyword in NETBANKING_KEYWORDS
    ):
        return (
            "Netbanking",
            "Netbanking",
            "Needs Review",
        )

    # 6. All remaining UPI debits = daily expenses
    if "UPI" in description:
        if debit > 0:
            return (
                "Expense",
                "UPI - Daily Expenses",
                "User Rule",
            )

        return (
            "Credit - Unclassified",
            "UPI - Incoming",
            "Needs Review",
        )

    # 7. Remaining transactions
    if debit > 0:
        return (
            "Debit - Unclassified",
            "Other",
            "Needs Review",
        )

    return (
        "Credit - Unclassified",
        "Other",
        "Needs Review",
    )






def main():
    if not MASTER_PATH.exists():
        raise FileNotFoundError(
            f"Master workbook not found: {MASTER_PATH}"
        )

    df = pd.read_excel(
        MASTER_PATH,
        sheet_name="Master Transactions",
        engine="openpyxl",
    )

    required = {
        "date", "details", "debit", "credit",
        "balance", "daily_txn_no",
    }

    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing required master columns: {sorted(missing)}"
        )

    if df.empty:
        raise ValueError("Master workbook has no transactions")

    # Validate amounts before classification.
    for column in ["debit", "credit", "balance"]:
        df[column] = pd.to_numeric(
            df[column],
            errors="raise",
        )

        if df[column].isna().any():
            raise ValueError(
                f"Missing values in {column}"
            )

    if (df[["debit", "credit"]] < 0).any().any():
        raise ValueError(
            "Negative debit or credit values detected"
        )

    df["date"] = pd.to_datetime(
        df["date"], errors="raise"
    )

    if df["date"].isna().any():
        raise ValueError("Missing transaction dates")

    # Prevent silent duplicate daily keys.
    duplicate_keys = df.duplicated(
        subset=["date", "daily_txn_no"],
        keep=False,
    )

    if duplicate_keys.any():
        raise ValueError(
            "Duplicate date + daily_txn_no keys found"
        )

    results = df.apply(
        classify_transaction,
        axis=1,
        result_type="expand",
    )

    results.columns = [
        "transaction_type",
        "category",
        "classification_confidence",
    ]

    df = pd.concat([df, results], axis=1)

    df["month"] = df["date"].dt.strftime("%Y-%m")

    # Only rule-matched expenses enter the confirmed
    # spending summary.
    confirmed_expenses = df[
        df["transaction_type"] == "Expense"
    ].copy()

    monthly = (
        confirmed_expenses
        .groupby(["month", "category"], as_index=False)
        .agg(
            transaction_count=("debit", "size"),
            total_spent=("debit", "sum"),
        )
        .sort_values(["month", "category"])
    )

    review = df[
        df["classification_confidence"] == "Needs Review"
    ].copy()

    overview = pd.DataFrame([
        {
            "metric": "Total transactions",
            "value": len(df),
        },
        {
            "metric": "Rule-matched expenses",
            "value": len(confirmed_expenses),
        },
        {
            "metric": "Transactions needing review",
            "value": len(review),
        },
        {
            "metric": "Rule-matched expense amount",
            "value": round(
                confirmed_expenses["debit"].sum(), 2
            ),
        },
        {
            "metric": "Unclassified debit amount",
            "value": round(
                df.loc[
                    df["transaction_type"]
                    == "Debit - Unclassified",
                    "debit",
                ].sum(),
                2,
            ),
        },
    ])

    OUTPUT_PATH.parent.mkdir(
        parents=True, exist_ok=True
    )

    with pd.ExcelWriter(
        OUTPUT_PATH,
        engine="openpyxl",
    ) as writer:
        # df.to_excel(
        #     writer,
        #     sheet_name="Categorized Transactions",
        #     index=False,
        # )


        # Select only the required columns for the output sheet
        categorized_output = df[
            [
                "date",
                "daily_txn_no",
                "transaction_type",
                "category",
                "credit",
                "debit",
                "balance",
            ]
        ].copy()

        # Rename daily transaction number
        categorized_output = categorized_output.rename(
            columns={"daily_txn_no": "daily_txn"}
        )

        categorized_output.to_excel(
            writer,
            sheet_name="Categorized Transactions",
            index=False,
        )


        monthly.to_excel(
            writer,
            sheet_name="Monthly Categories",
            index=False,
        )

        review.to_excel(
            writer,
            sheet_name="Needs Review",
            index=False,
        )

        overview.to_excel(
            writer,
            sheet_name="Overview",
            index=False,
        )

    print("\n===== PHASE 3 CATEGORIZATION =====")
    print(f"Total transactions: {len(df)}")
    print(
        "Rule-matched expenses:",
        len(confirmed_expenses),
    )
    print(
        "Transactions needing review:",
        len(review),
    )
    print(f"Output: {OUTPUT_PATH}")

if __name__ == "__main__":
    main()