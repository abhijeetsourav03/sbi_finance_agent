
"""
Phase 3: SBI Financial Analytics Engine

Input:
    data/processed/sbi_categorized_transactions.xlsx

Output:
    data/processed/sbi_monthly_analytics.xlsx

Calculates:
    - Monthly cash flow
    - Recognized everyday spending
    - Category-wise transactions
    - Daily spending
    - Month-over-month spending changes
    - Classification coverage
"""

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent

INPUT_FILE = (
    ROOT / "data" / "processed"
    / "sbi_categorized_transactions.xlsx"
)

OUTPUT_FILE = (
    ROOT / "data" / "processed"
    / "sbi_monthly_analytics.xlsx"
)

REQUIRED_COLUMNS = [
    "date",
    "daily_txn",
    "transaction_type",
    "category",
    "credit",
    "debit",
    "balance",
]

SPENDING_CATEGORIES = {
    "UPI - Daily Expenses",
    "IRCTC",
    "UPI - Incoming",
    "ATM - Cash Withdrawal"
}

EXCLUDED_CATEGORIES = {
    "Trading - Zerodha/Groww",
    "Nutan (Loan)",
    "Muskan (Transfer)",
    "Debabrata (Transfer)",
    "Odus (Transfer)",
}


def load_transactions():
    """Read and validate Phase 2 categorized transactions."""

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Categorized workbook not found: {INPUT_FILE}\n"
            "Run python -m src.transaction_categorizer first."
        )

    df = pd.read_excel(
        INPUT_FILE,
        sheet_name="Categorized Transactions",
        engine="openpyxl",
    )

    missing = [
        col for col in REQUIRED_COLUMNS
        if col not in df.columns
    ]

    if missing:
        raise ValueError(
            f"Missing required columns: {missing}"
        )

    df["date"] = pd.to_datetime(
        df["date"], errors="coerce"
    )

    if df["date"].isna().any():
        raise ValueError(
            "Invalid or missing transaction dates found."
        )

    for col in ["credit", "debit", "balance"]:
        df[col] = pd.to_numeric(
            df[col], errors="coerce"
        )

    if df[["credit", "debit", "balance"]].isna().any().any():
        raise ValueError(
            "Missing or invalid monetary values found."
        )

    if (df[["credit", "debit"]] < 0).any().any():
        raise ValueError(
            "Negative credit or debit amounts found."
        )

    df["category"] = (
        df["category"]
        .fillna("Unclassified")
        .astype(str)
        .str.strip()
    )

    df["month"] = df["date"].dt.strftime("%Y-%m")

    df["spending_type"] = "Unclassified / Other"

    df.loc[
        df["category"].isin(SPENDING_CATEGORIES),
        "spending_type",
    ] = "Recognized Spending"

    df.loc[
        df["category"].isin(EXCLUDED_CATEGORIES),
        "spending_type",
    ] = "Excluded"

    return df.sort_values(
        ["date", "daily_txn"]
    ).reset_index(drop=True)


def calculate_monthly_summary(df):
    """Calculate monthly bank movements and spending."""

    monthly = (
        df.groupby("month", as_index=False)
        .agg(
            total_inflow=("credit", "sum"),
            total_outflow=("debit", "sum"),
            transaction_count=("date", "size"),
        )
    )

    spending = (
        df[
            df["spending_type"]
            == "Recognized Spending"
        ]
        .groupby("month")["debit"]
        .sum()
    )

    excluded = (
        df[
            df["spending_type"]
            == "Excluded"
        ]
        .groupby("month")["debit"]
        .sum()
    )

    monthly["recognized_spending"] = (
        monthly["month"].map(spending).fillna(0)
    )

    monthly["excluded_outflows"] = (
        monthly["month"].map(excluded).fillna(0)
    )

    monthly["unclassified_outflows"] = (
        monthly["total_outflow"]
        - monthly["recognized_spending"]
        - monthly["excluded_outflows"]
    )

    monthly["net_cash_flow"] = (
        monthly["total_inflow"]
        - monthly["total_outflow"]
    )

    # Reconcile all debit movements.
    difference = (
        monthly["total_outflow"]
        - (
            monthly["recognized_spending"]
            + monthly["excluded_outflows"]
            + monthly["unclassified_outflows"]
        )
    )

    if not (difference.abs() < 0.01).all():
        raise ValueError(
            "Monthly outflow reconciliation failed."
        )

    return monthly.round(2)


def calculate_category_breakdown(df):
    """Summarize debits and credits by month/category."""

    result = (
        df.groupby(
            ["month", "spending_type", "category"],
            as_index=False,
        )
        .agg(
            transactions=("date", "size"),
            debit=("debit", "sum"),
            credit=("credit", "sum"),
        )
    )

    return result.round(2)


def calculate_daily_spending(df):
    """Calculate recognized everyday spending by date."""

    spending_df = df[
        (df["spending_type"] == "Recognized Spending")
        & (df["debit"] > 0)
    ]

    result = (
        spending_df.groupby(
            ["date", "month"], as_index=False
        )
        .agg(
            spending=("debit", "sum"),
            transaction_count=("date", "size"),
        )
    )

    return result.sort_values("date").round(2)


def calculate_monthly_trends(monthly):
    """Calculate month-over-month spending changes."""

    trends = monthly[
        ["month", "recognized_spending"]
    ].copy()

    trends = trends.sort_values("month")

    trends["previous_month_spending"] = (
        trends["recognized_spending"].shift(1)
    )

    trends["spending_change"] = (
        trends["recognized_spending"]
        - trends["previous_month_spending"]
    )

    trends["mom_change_pct"] = (
        trends["spending_change"]
        / trends["previous_month_spending"].replace(0, float("nan"))
        * 100
    )

    # Flag a >10% increase where comparison is possible.
    trends["spending_spike"] = (
        trends["mom_change_pct"] > 10
    )

    return trends.round(2)


def calculate_classification_coverage(df):
    """Identify spending movements requiring review."""

    debit_df = df[df["debit"] > 0].copy()

    result = (
        debit_df.groupby(
            ["month", "spending_type"],
            as_index=False,
        )
        .agg(
            transactions=("date", "size"),
            debit=("debit", "sum"),
        )
    )

    return result.round(2)


def main():
    print("\nStarting SBI Financial Analytics...")

    df = load_transactions()

    monthly = calculate_monthly_summary(df)
    categories = calculate_category_breakdown(df)
    daily = calculate_daily_spending(df)
    trends = calculate_monthly_trends(monthly)
    coverage = calculate_classification_coverage(df)

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with pd.ExcelWriter(
        OUTPUT_FILE,
        engine="openpyxl",
    ) as writer:

        monthly.to_excel(
            writer,
            sheet_name="Monthly Summary",
            index=False,
        )

        categories.to_excel(
            writer,
            sheet_name="Category Breakdown",
            index=False,
        )

        daily.to_excel(
            writer,
            sheet_name="Daily Spending",
            index=False,
        )

        trends.to_excel(
            writer,
            sheet_name="Monthly Trends",
            index=False,
        )

        coverage.to_excel(
            writer,
            sheet_name="Classification Coverage",
            index=False,
        )

    print("\nFinancial analytics completed successfully.")
    print(f"Transactions processed: {len(df)}")
    print(f"Months analyzed: {len(monthly)}")
    print(f"Output: {OUTPUT_FILE}")

    print("\nMonthly Summary:")
    print(monthly.to_string(index=False))


if __name__ == "__main__":
    main()
