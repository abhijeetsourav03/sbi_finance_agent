
from pathlib import Path
import pandas as pd

from .metric_registry import get_metric


INPUT_FILE = Path(
    "data/processed/sbi_categorized_transactions.xlsx"
)

SHEET_NAME = "Categorized Transactions"


def load_transactions():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Missing transaction file: {INPUT_FILE}"
        )

    df = pd.read_excel(INPUT_FILE, sheet_name=SHEET_NAME)

    required = {
        "date",
        "transaction_type",
        "category",
        "credit",
        "debit",
    }

    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {sorted(missing)}"
        )

    df["date"] = pd.to_datetime(
        df["date"], errors="raise"
    ).dt.normalize()

    for column in ["credit", "debit"]:
        df[column] = pd.to_numeric(
            df[column], errors="raise"
        ).fillna(0)

    if (df[["credit", "debit"]] < 0).any().any():
        raise ValueError("Negative transaction amount found")

    return df


def execute_query(df, query):
    metric = get_metric(query["metric"])

    start = pd.Timestamp(query["start_date"])
    end = pd.Timestamp(query["end_date"])

    if start > end:
        raise ValueError(
            "Start date cannot be after end date"
        )

    filtered = df.loc[
        (df["date"] >= start)
        & (df["date"] <= end)
    ].copy()

    if "transaction_types" in metric:
        filtered = filtered.loc[
            filtered["transaction_type"].isin(
                metric["transaction_types"]
            )
        ]

    category = query.get("category")

    if category:
        filtered = filtered.loc[
            filtered["category"] == category
        ]

    aggregation = metric["aggregation"]
    column = metric["column"]

    if aggregation == "sum":
        value = float(filtered[column].sum())
    elif aggregation == "count":
        value = int(len(filtered))
    else:
        raise ValueError(
            f"Unsupported aggregation: {aggregation}"
        )

    return {
        "metric": query["metric"],
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "category": category,
        "value": round(value, 2)
        if aggregation == "sum" else value,
        "matched_transactions": len(filtered),
    }
