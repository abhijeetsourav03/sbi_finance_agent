
import csv
import json
from pathlib import Path

from .statement_parser import parse_statement

ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT / "data" / "raw_statements"
OUTPUT_DIR = ROOT / "data" / "processed"
REPORT_DIR = ROOT / "data" / "reports"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(INPUT_DIR.glob("SBI_*.pdf"))

    if not pdfs:
        raise FileNotFoundError(
            f"No SBI statement PDFs found in {INPUT_DIR}"
        )

    results = []

    for pdf in pdfs:
        print(f"\nProcessing: {pdf.name}")

        try:
            result = parse_statement(pdf, OUTPUT_DIR)

            row = {
                "statement": pdf.name,
                "status": result.status,
                "transactions_extracted": None,
                "transactions_flagged": None,
                "pages_scanned": None,
                "total_pages": None,
                "csv_path": str(result.csv_path) if result.csv_path else "",
                "report_path": str(result.report_path) if result.report_path else "",
                "error": result.error or "",
            }

            if result.report_path and result.report_path.exists():
                report = json.loads(
                    result.report_path.read_text(encoding="utf-8")
                )
                row["transactions_extracted"] = report.get(
                    "transactions_extracted"
                )
                row["transactions_flagged"] = report.get(
                    "transactions_flagged"
                )
                row["pages_scanned"] = report.get("pages_scanned")
                row["total_pages"] = report.get("total_pages")

            results.append(row)

            print(
                f"  Status: {row['status']}, "
                f"Flagged: {row['transactions_flagged']}"
            )

        except Exception as exc:
            results.append({
                "statement": pdf.name,
                "status": "FAILED",
                "transactions_extracted": None,
                "transactions_flagged": None,
                "pages_scanned": None,
                "total_pages": None,
                "csv_path": "",
                "report_path": "",
                "error": str(exc),
            })
            print(f"  FAILED: {exc}")

    summary_path = REPORT_DIR / "phase2_validation_summary.csv"

    with summary_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    print("\n=== PHASE 2 SUMMARY ===")
    for row in results:
        print(
            row["statement"],
            row["status"],
            row["transactions_extracted"],
            row["transactions_flagged"],
        )

    print(f"\nSummary saved: {summary_path}")


if __name__ == "__main__":
    main()
