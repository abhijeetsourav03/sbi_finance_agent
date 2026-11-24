
from src.intelligent_categorization.transaction_loader import (
    load_master_transactions,
    load_categorized_transactions,
    build_enriched_transactions,
)


def test_transaction_loader():

    print("\n" + "=" * 60)
    print("TESTING ENHANCED TRANSACTION LOADER")
    print("=" * 60)

    master_df = load_master_transactions()
    categorized_df = load_categorized_transactions()
    df = build_enriched_transactions()

    tests = []

    def check(name, condition):
        passed = bool(condition)
        tests.append((name, passed))

        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}")

    print("\n--- Transaction Integrity ---")

    check(
        "Dataset is not empty",
        not df.empty
    )

    check(
        "Master and categorized row counts match",
        len(master_df) == len(categorized_df)
    )

    check(
        "Enriched transaction count matches master",
        len(df) == len(master_df)
    )

    check(
        "Transaction IDs are unique",
        df["transaction_id"].is_unique
    )

    check(
        "Transaction IDs are not null",
        df["transaction_id"].notna().all()
    )

    check(
        "Transaction IDs are preserved",
        set(df["transaction_id"])
        == set(master_df["transaction_id"])
    )

    check(
        "Transaction dates are not null",
        df["date"].notna().all()
    )

    print("\n--- Original Transaction Data ---")

    check(
        "Original descriptions are available",
        df["details"].notna().all()
    )

    check(
        "Original descriptions are not empty",
        df["details"].astype(str).str.strip().ne("").all()
    )

    master_sorted = master_df.set_index(
        "transaction_id"
    ).sort_index()

    enriched_sorted = df.set_index(
        "transaction_id"
    ).sort_index()

    check(
        "Original descriptions are unchanged",
        master_sorted["details"].equals(
            enriched_sorted["details"]
        )
    )

    check(
        "Original transaction types are preserved",
        df["original_transaction_type"].notna().all()
    )

    check(
        "Original categories are preserved",
        df["original_category"].notna().all()
    )

    print("\n--- Financial Reconciliation ---")

    for column in ["credit", "debit", "balance"]:

        master_values = (
            master_sorted[column] * 100
        ).round().astype("int64")

        enriched_values = (
            enriched_sorted[column] * 100
        ).round().astype("int64")

        check(
            f"{column.capitalize()} values match master",
            master_values.equals(enriched_values)
        )

    check(
        "Transaction directions are valid",
        df["direction"].isin(
            ["credit", "debit", "unknown"]
        ).all()
    )

    print("\n--- AI Categorization Readiness ---")

    # No enhanced financial categories should be
    # assigned before receiving user instructions.

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

        check(
            f"{column} is initially empty",
            df[column].isna().all()
        )

    check(
        "All transactions await enhanced classification",
        df["review_status"].eq("pending").all()
    )

    # Test summary

    passed = sum(result for _, result in tests)
    failed = len(tests) - passed

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    print(f"Total transactions : {len(df):,}")
    print(f"Total tests        : {len(tests)}")
    print(f"Passed             : {passed}")
    print(f"Failed             : {failed}")

    if failed:

        print("\nFailed tests:")

        for name, result in tests:
            if not result:
                print(f" - {name}")

        raise AssertionError(
            f"{failed} transaction loader tests failed."
        )

    print("\nAll transaction loader tests passed.")
    print("Dataset is ready for AI-driven categorization.")


if __name__ == "__main__":
    test_transaction_loader()
