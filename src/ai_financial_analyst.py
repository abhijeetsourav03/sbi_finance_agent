
import json
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent

ANALYTICS_FILE = (
    ROOT / "data" / "processed"
    / "sbi_monthly_analytics.xlsx"
)

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3:4b"


def load_financial_data():
    if not ANALYTICS_FILE.exists():
        raise FileNotFoundError(
            f"Analytics file not found: {ANALYTICS_FILE}\n"
            "Run python -m src.financial_analytics first."
        )

    monthly = pd.read_excel(
        ANALYTICS_FILE,
        sheet_name="Monthly Summary",
    )

    categories = pd.read_excel(
        ANALYTICS_FILE,
        sheet_name="Category Breakdown",
    )

    return monthly, categories


def build_context(monthly, categories):
    # Include only calculated aggregates, not
    # individual transactions or bank identifiers.
    return json.dumps(
        {
            "monthly_summary": monthly.to_dict(
                orient="records"
            ),
            "category_breakdown": categories.to_dict(
                orient="records"
            ),
        },
        default=str,
    )


def ask_financial_analyst(question, context):
    system_prompt = """
You are a personal financial analytics assistant.

You receive verified financial aggregates
calculated from SBI bank transactions.

Rules:
1. Use only the supplied financial data.
2. Never invent amounts, budgets or transactions.
3. Distinguish cash inflow from actual income.
4. Distinguish bank outflow from everyday expenses.
5. Exclude trading, loans and personal transfers
   when interpreting everyday spending.
6. Do not treat unclassified outflows as expenses.
7. Explain meaningful spending trends clearly.
8. Give practical, concise recommendations.
9. If data is insufficient, explain what is missing.
10. Do not claim overspending against a budget
    unless an actual budget was provided.
"""

    payload = {
        "model": MODEL,
        "stream": False,
        "think": False,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": (
                    f"Financial data:\n{context}\n\n"
                    f"Question: {question}"
                ),
            },
        ],
        "options": {
            "temperature": 0.2,
            "num_ctx": 8192,
        },
    }

    response = requests.post(
        OLLAMA_URL,
        json=payload,
        timeout=180,
    )
    response.raise_for_status()

    result = response.json()

    return result["message"]["content"]


def main():
    monthly, categories = load_financial_data()

    if monthly.empty:
        print("No monthly financial data available.")
        return

    context = build_context(monthly, categories)

    print("\nSBI AI Financial Analyst")
    print(f"Model: {MODEL}")
    print(f"Months available: {len(monthly)}")
    print("Type 'exit' to quit.\n")

    while True:
        question = input("You: ").strip()

        if question.lower() in {"exit", "quit"}:
            break

        if not question:
            continue

        try:
            answer = ask_financial_analyst(
                question, context
            )
            print(f"\nAI: {answer}\n")

        except requests.RequestException as exc:
            print(f"Ollama API error: {exc}")


if __name__ == "__main__":
    main()
