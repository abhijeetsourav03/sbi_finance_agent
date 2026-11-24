
from src.finance_genie.query_engine import (
    load_transactions,
    execute_query,
)

df = load_transactions()

query = {
    "metric": "total_spending",
    "start_date": "2026-10-01",
    "end_date": "2026-10-31",
    "category": None,
}

result = execute_query(df, query)

print("Metric:", result["metric"])
print("Period:", result["start_date"], "to",
      result["end_date"])
print("Amount: ₹{:,.2f}".format(result["value"]))
print(
    "Matched transactions:",
    result["matched_transactions"]
)
