
from pathlib import Path

import numpy as np
import pandas as pd


# ============================================================
# Configuration
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

MASTER_FILE = PROCESSED_DIR / "sbi_master_transactions.xlsx"

CATEGORIZED_FILE = (
    PROCESSED_DIR / "sbi_categorized_transactions.xlsx"
)

OUTPUT_FILE = PROCESSED_DIR / "sbi_enriched_transactions.xlsx"

MASTER_SHEET = "Master Transactions"
CATEGORIZED_SHEET = "Categorized Transactions"

JOIN_KEYS = ["date", "daily_txn"]

AMOUNT_COLUMNS = ["credit", "debit", "balance"]

MASTER_REQUIRED_COLUMNS = {
    "transaction_id",
    "date",
    "daily_txn_no",
    "details",
    "credit",
    "debit",
    "balance",
}

CATEGORIZED_REQUIRED_COLUMNS = {
    "date",
    "daily_txn",
    "transaction_type",
    "category",
    "credit",
    "debit",
    "balance",
}


# ============================================================
# Validation utilities
# ============================================================

def validate_required_columns(df, required_columns, name):

    missing = required_columns - set(df.columns)

    if missing:
        raise ValueError(
            f"{name}: Missing columns: {sorted(missing)}"
        )


def validate_unique_keys(df, keys, name):

    if df[keys].isna().any().any():
        raise ValueError(
            f"{name}: Null values found in {keys}"
        )

    duplicates = df.duplicated(
        subset=keys,
        keep=False
    )

    if duplicates.any():
        raise ValueError(
            f"{name}: Found {duplicates.sum()} rows "
            f"with duplicate keys: {keys}"
        )


def normalize_transactions(df, name):

    df = df.copy()

    # df["date"] = pd.to_datetime(
    #     df["date"],
    #     errors="raise",
    #     dayfirst=True
    # ).dt.normalize()

    if pd.api.types.is_datetime64_any_dtype(df["date"]):

        df["date"] = df["date"].dt.normalize()

    else:

        df["date"] = pd.to_datetime(
            df["date"].astype(str).str.strip(),
            format="%Y-%m-%d",
            errors="raise"
        ).dt.normalize()

    if df["date"].isna().any():
        raise ValueError(f"{name}: Missing transaction dates")

    daily_txn = pd.to_numeric(
        df["daily_txn"],
        errors="raise"
    )

    if (
        daily_txn.isna().any()
        or not np.isfinite(daily_txn).all()
        or (daily_txn % 1 != 0).any()
        or (daily_txn < 1).any()
    ):
        raise ValueError(
            f"{name}: Invalid daily transaction numbers"
        )

    df["daily_txn"] = daily_txn.astype("int64")

    for column in AMOUNT_COLUMNS:

        df[column] = pd.to_numeric(
            df[column],
            errors="raise"
        )

        if not np.isfinite(df[column]).all():
            raise ValueError(
                f"{name}: Invalid values in {column}"
            )

        # Validate that amounts have at most two decimal places.
        scaled = df[column] * 100

        if not np.isclose(
            scaled,
            scaled.round(),
            atol=0.000001,
            rtol=0
        ).all():
            raise ValueError(
                f"{name}: Invalid currency precision in {column}"
            )

        df[column] = df[column].round(2)

    if (df[["credit", "debit"]] < 0).any().any():
        raise ValueError(
            f"{name}: Negative credit or debit detected"
        )

    return df


# ============================================================
# Load master transactions
# ============================================================

def load_master_transactions():

    if not MASTER_FILE.exists():
        raise FileNotFoundError(
            f"Master workbook not found: {MASTER_FILE}"
        )

    df = pd.read_excel(
        MASTER_FILE,
        sheet_name=MASTER_SHEET,
        dtype={"transaction_id": "string"}
    )

    validate_required_columns(
        df,
        MASTER_REQUIRED_COLUMNS,
        "Master Transactions"
    )

    df = df.rename(
        columns={"daily_txn_no": "daily_txn"}
    )

    df = normalize_transactions(
        df,
        "Master Transactions"
    )

    df["transaction_id"] = (
        df["transaction_id"].str.strip()
    )

    if df["transaction_id"].eq("").any():
        raise ValueError("Empty transaction ID found")

    validate_unique_keys(
        df,
        ["transaction_id"],
        "Master Transactions"
    )

    validate_unique_keys(
        df,
        JOIN_KEYS,
        "Master Transactions"
    )

    if (
        df["details"].isna().any()
        or df["details"].astype(str).str.strip().eq("").any()
    ):
        raise ValueError(
            "Missing original transaction descriptions"
        )

    print(
        f"Master transactions loaded: {len(df):,}"
    )

    return df


# ============================================================
# Load existing Phase 2 classifications
# ============================================================

def load_categorized_transactions():

    if not CATEGORIZED_FILE.exists():
        raise FileNotFoundError(
            f"Categorized workbook not found: "
            f"{CATEGORIZED_FILE}"
        )

    df = pd.read_excel(
        CATEGORIZED_FILE,
        sheet_name=CATEGORIZED_SHEET
    )

    validate_required_columns(
        df,
        CATEGORIZED_REQUIRED_COLUMNS,
        "Categorized Transactions"
    )

    df = normalize_transactions(
        df,
        "Categorized Transactions"
    )

    validate_unique_keys(
        df,
        JOIN_KEYS,
        "Categorized Transactions"
    )

    df = df.rename(
        columns={
            "transaction_type": "original_transaction_type",
            "category": "original_category",
        }
    )

    print(
        f"Categorized transactions loaded: {len(df):,}"
    )

    return df


# ============================================================
# Merge and reconcile transactions
# ============================================================

def merge_transactions(master_df, categorized_df):

    if len(master_df) != len(categorized_df):
        raise ValueError(
            "Transaction count mismatch. "
            f"Master: {len(master_df)}, "
            f"Categorized: {len(categorized_df)}"
        )

    merged = master_df.merge(
        categorized_df,
        on=JOIN_KEYS,
        how="outer",
        suffixes=("", "_categorized"),
        validate="one_to_one",
        indicator=True
    )

    unmatched = merged["_merge"] != "both"

    if unmatched.any():
        raise ValueError(
            f"Found {unmatched.sum()} unmatched transactions"
        )

    print("Transaction matching: PASSED")

    # Compare currency values in integer paise.
    for column in AMOUNT_COLUMNS:

        categorized_column = f"{column}_categorized"

        master_paise = (
            merged[column] * 100
        ).round().astype("int64")

        categorized_paise = (
            merged[categorized_column] * 100
        ).round().astype("int64")

        mismatch = master_paise != categorized_paise

        if mismatch.any():
            raise ValueError(
                f"{column} reconciliation failed: "
                f"{mismatch.sum()} mismatched transactions"
            )

        print(f"{column.capitalize()} reconciliation: PASSED")

    merged = merged.drop(
        columns=[
            "_merge",
            "credit_categorized",
            "debit_categorized",
            "balance_categorized",
        ]
    )

    return merged


# ============================================================
# Initialize enhanced transaction schema
# ============================================================

def initialize_enriched_transactions(df):

    df = df.copy()

    # Direction is derived from amounts.
    # This is not financial categorization.

    credit_only = (
        (df["credit"] > 0)
        & (df["debit"] == 0)
    )

    debit_only = (
        (df["debit"] > 0)
        & (df["credit"] == 0)
    )

    df["direction"] = np.select(
        [credit_only, debit_only],
        ["credit", "debit"],
        default="unknown"
    )

    # All enhanced classifications remain empty.
    # Future AI-generated rules will populate them.

    empty_columns = [
        "financial_category",
        "financial_subcategory",
        "financial_treatment",
        "counterparty",
        "payment_mode",
        "applied_rule_id",
        "classification_source",
        "classification_notes",
    ]

    for column in empty_columns:
        df[column] = pd.Series(
            pd.NA,
            index=df.index,
            dtype="string"
        )

    df["review_status"] = "pending"

    preferred_columns = [
        "transaction_id",
        "date",
        "daily_txn",
        "details",
        "direction",
        "credit",
        "debit",
        "balance",
        "original_transaction_type",
        "original_category",
        "financial_category",
        "financial_subcategory",
        "financial_treatment",
        "counterparty",
        "payment_mode",
        "applied_rule_id",
        "classification_source",
        "classification_notes",
        "review_status",
    ]

    # Preserve remaining master workbook metadata.

    remaining_columns = [
        column
        for column in df.columns
        if column not in preferred_columns
    ]

    df = df[
        preferred_columns + remaining_columns
    ]

    df = df.sort_values(
        ["date", "daily_txn"]
    ).reset_index(drop=True)

    return df


# ============================================================
# Final integrity validation
# ============================================================

def validate_enriched_transactions(master_df, enriched_df):

    if len(master_df) != len(enriched_df):
        raise ValueError(
            "Transaction count changed during enrichment"
        )

    validate_unique_keys(
        enriched_df,
        ["transaction_id"],
        "Enriched Transactions"
    )

    master_ids = set(master_df["transaction_id"])
    enriched_ids = set(enriched_df["transaction_id"])

    if master_ids != enriched_ids:
        raise ValueError(
            "Transaction IDs changed during enrichment"
        )

    for column in ["credit", "debit"]:

        master_total = int(
            (master_df[column] * 100)
            .round()
            .astype("int64")
            .sum()
        )

        enriched_total = int(
            (enriched_df[column] * 100)
            .round()
            .astype("int64")
            .sum()
        )

        if master_total != enriched_total:
            raise ValueError(
                f"Total {column} changed during enrichment"
            )

    print("Final data integrity validation: PASSED")


# ============================================================
# Build enriched dataset
# ============================================================

def build_enriched_transactions():

    print("\n" + "=" * 60)
    print("PHASE 3.3 - ENHANCED TRANSACTION LOADER")
    print("=" * 60)

    master_df = load_master_transactions()

    categorized_df = load_categorized_transactions()

    merged_df = merge_transactions(
        master_df,
        categorized_df
    )

    enriched_df = initialize_enriched_transactions(
        merged_df
    )

    validate_enriched_transactions(
        master_df,
        enriched_df
    )

    return enriched_df


# ============================================================
# Export enriched transactions
# ============================================================

def export_enriched_transactions(df, overwrite=False):

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    if OUTPUT_FILE.exists() and not overwrite:
        raise FileExistsError(
            f"{OUTPUT_FILE.name} already exists. "
            "Export stopped to prevent overwriting "
            "existing classifications."
        )

    summary = pd.DataFrame({
        "metric": [
            "Total transactions",
            "Total credit",
            "Total debit",
            "Net cash flow",
            "Credit transactions",
            "Debit transactions",
            "Unknown direction",
            "Enhanced categorized transactions",
            "Pending transactions",
            "Earliest transaction",
            "Latest transaction",
        ],
        "value": [
            len(df),
            round(df["credit"].sum(), 2),
            round(df["debit"].sum(), 2),
            round(
                df["credit"].sum() - df["debit"].sum(),
                2
            ),
            int((df["direction"] == "credit").sum()),
            int((df["direction"] == "debit").sum()),
            int((df["direction"] == "unknown").sum()),
            int(df["financial_category"].notna().sum()),
            int((df["review_status"] == "pending").sum()),
            df["date"].min().strftime("%Y-%m-%d"),
            df["date"].max().strftime("%Y-%m-%d"),
        ]
    })

    with pd.ExcelWriter(
        OUTPUT_FILE,
        engine="openpyxl"
    ) as writer:

        df.to_excel(
            writer,
            sheet_name="Enriched Transactions",
            index=False
        )

        summary.to_excel(
            writer,
            sheet_name="Overview",
            index=False
        )

        worksheet = writer.sheets["Enriched Transactions"]

        worksheet.freeze_panes = "D2"
        worksheet.auto_filter.ref = worksheet.dimensions

    print(f"\nWorkbook saved: {OUTPUT_FILE}")

    print("\n" + "=" * 60)
    print("ENRICHMENT SUMMARY")
    print("=" * 60)

    print(summary.to_string(index=False))


# ============================================================
# Entry point
# ============================================================

def main():

    df = build_enriched_transactions()

    export_enriched_transactions(df)

    print("\nPhase 3.3 completed successfully.")


if __name__ == "__main__":
    main()
