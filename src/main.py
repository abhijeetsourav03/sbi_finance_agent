import argparse

from .config import GMAIL_QUERY, MANIFEST_PATH, STATEMENTS_DIR
from .gmail_client import fetch_statements


def main():
    parser = argparse.ArgumentParser(
        description="Fetch SBI statement attachments from Gmail (read-only)."
    )
    parser.add_argument("--query", default=GMAIL_QUERY, help="Gmail search query")
    parser.add_argument("--max-results", type=int, default=20)
    args = parser.parse_args()

    if not 1 <= args.max_results <= 100:
        parser.error("--max-results must be between 1 and 100")

    STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    fetch_statements(args.query, args.max_results, MANIFEST_PATH)


if __name__ == "__main__":
    main()
