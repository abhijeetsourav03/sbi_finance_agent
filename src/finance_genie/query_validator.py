
from datetime import date, datetime

from .metric_registry import METRICS


def validate_query(query: dict, df=None) -> dict:
    if not isinstance(query, dict):
        raise ValueError("Query must be a JSON object")

    if query.get("error"):
        raise ValueError(str(query["error"]))

    required = {
        "metric",
        "start_date",
        "end_date",
        "category",
    }

    if set(query.keys()) != required:
        raise ValueError(
            "Query must contain exactly: "
            + ", ".join(sorted(required))
        )

    if query["metric"] not in METRICS:
        raise ValueError(
            f"Unsupported metric: {query['metric']}"
        )

    for field in ("start_date", "end_date"):
        value = query[field]

        if not isinstance(value, str):
            raise ValueError(f"Invalid {field}")

        try:
            parsed = datetime.strptime(
                value, "%Y-%m-%d"
            ).date()
        except ValueError as exc:
            raise ValueError(
                f"Invalid {field}: {value}"
            ) from exc

        if parsed.isoformat() != value:
            raise ValueError(f"Invalid {field}")

    start = date.fromisoformat(query["start_date"])
    end = date.fromisoformat(query["end_date"])

    if start > end:
        raise ValueError(
            "Start date must be before end date"
        )

    category = query["category"]

    if category is not None:
        if not isinstance(category, str) or not category.strip():
            raise ValueError("Invalid category")

        if df is not None:
            allowed_categories = set(
                df["category"].dropna().astype(str)
            )

            if category not in allowed_categories:
                raise ValueError(
                    f"Unknown category: {category}. "
                    "Ask using an existing category."
                )

    return query
