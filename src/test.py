
from pathlib import Path
import pymupdf
from config import SBI_PDF_PASSWORD

pdf_path = Path(
    "data/raw_statements/"
    "1a1cb7729902_9031172219531102026.pdf"
)

with pymupdf.open(pdf_path) as doc:
    if doc.needs_pass:
        if not doc.authenticate(SBI_PDF_PASSWORD):
            raise ValueError("PDF authentication failed")

    for page_number, page in enumerate(doc, start=1):
        text = page.get_text("text")

        if "Transaction Reference" not in text:
            continue

        print(f"\nPAGE {page_number}")

        tables = page.find_tables()
        print("Tables detected:", len(tables.tables))

        for table_index, table in enumerate(tables.tables):
            rows = table.extract()

            print(f"Table {table_index + 1}")
            print("Rows:", len(rows))
            print("Columns:", table.col_count)
            print("First 3 rows:")

            for row in rows[:3]:
                print(repr(row))
"""
This uses PyMuPDF's native table-detection capability, which may recognize the transaction tables from their geometry and text positions.

If it detects the six-column tables correctly, we can eliminate most of the OCR pipeline and build a much simpler, faster extractor.

If it doesn't, we can use `page.get_text("words")` to obtain each word's bounding box and reconstruct rows and columns from their coordinates.

**Share the output showing the number of detected tables, their row and column counts, and the first three rows.** That will determine the correct implementation for your Phase 2 extraction engine.
"""