
from .intent_parser import parse_intent
from .query_validator import validate_query
from .query_engine import (
    load_transactions,
    execute_query,
)
from .response_formatter import format_response


def main():
    print("\nFinance Genie - Phase 3")
    print("Type 'exit' to quit.\n")

    df = load_transactions()

    print(f"Loaded {len(df):,} transactions.\n")

    while True:
        question = input("You: ").strip()

        if question.lower() in {"exit", "quit"}:
            break

        if not question:
            continue

        try:
            query = parse_intent(question)

            query = validate_query(query, df)

            result = execute_query(df, query)

            print("\nFinance Genie:")
            print(format_response(result))
            print()

        except (ValueError, RuntimeError) as exc:
            print(f"\nUnable to answer: {exc}\n")


if __name__ == "__main__":
    main()
