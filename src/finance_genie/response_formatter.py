
METRIC_LABELS = {
    "total_spending": "Recognized spending",
    "total_inflow": "Total bank inflow",
    "total_outflow": "Total bank outflow",
    "transaction_count": "Transaction count",
}


def format_response(result: dict) -> str:
    metric = result["metric"]
    label = METRIC_LABELS.get(metric, metric)

    if result["matched_transactions"] == 0:
        return (
            "No matching transactions were found "
            "for the requested filters. "
            "This does not necessarily mean "
            "the actual amount was zero."
        )

    value = result["value"]

    if metric == "transaction_count":
        formatted_value = f"{value:,}"
    else:
        formatted_value = f"₹{value:,.2f}"

    lines = [
        f"{label}: {formatted_value}",
        (
            f"Period: {result['start_date']} "
            f"to {result['end_date']}"
        ),
        (
            "Matching transactions: "
            f"{result['matched_transactions']}"
        ),
    ]

    if result.get("category"):
        lines.append(
            f"Category: {result['category']}"
        )

    if metric == "total_spending":
        lines.append(
            "Includes transactions classified as "
            "Expense, including provisional ATM "
            "cash spending. Excludes unclassified "
            "debits and other transaction types."
        )

    return "\n".join(lines)
