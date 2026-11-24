
METRICS = {
    "total_spending": {
        "description": "Recognized everyday expenses",
        "transaction_types": ["Expense"],
        "aggregation": "sum",
        "column": "debit",
    },
    "total_inflow": {
        "description": "All incoming bank credits",
        "aggregation": "sum",
        "column": "credit",
    },
    "total_outflow": {
        "description": "All outgoing bank debits",
        "aggregation": "sum",
        "column": "debit",
    },
    "transaction_count": {
        "description": "Number of transactions",
        "aggregation": "count",
        "column": "date",
    },
}

def get_metric(name):
    if name not in METRICS:
        raise ValueError(f"Unsupported metric: {name}")

    return METRICS[name]
