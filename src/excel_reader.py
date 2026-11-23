import os
from io import BytesIO
from pathlib import Path

import msoffcrypto
import pandas as pd
from dotenv import load_dotenv


# Load .env from the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


from dotenv import dotenv_values

def get_excel_passwords():
    """Read passwords directly from the project .env file."""

    values = dotenv_values(PROJECT_ROOT / ".env")

    passwords = [
        values.get("SBI_EXCEL_PASSWORD_1"),
        values.get("SBI_EXCEL_PASSWORD_2"),
    ]

    passwords = list(dict.fromkeys(
        password for password in passwords
        if password is not None and password != ""
    ))

    if not passwords:
        raise RuntimeError(
            "No SBI Excel passwords found in .env"
        )

    return passwords


def read_encrypted_excel(file_path, passwords):
    """
    Try each configured password until decryption succeeds.
    Return workbook sheets and the successful password index.
    """

    file_path = Path(file_path)

    for index, password in enumerate(passwords, start=1):
        try:
            with file_path.open("rb") as file:
                office = msoffcrypto.OfficeFile(file)

                if office.is_encrypted():
                    office.load_key(password=password)

                    decrypted = BytesIO()
                    # office.decrypt(decrypted, verify_integrity=True)
                    office.decrypt(decrypted)
                    decrypted.seek(0)
                else:
                    decrypted = BytesIO(file.read())

            workbook = pd.ExcelFile(decrypted)

            sheets = {
                sheet: pd.read_excel(
                    workbook,
                    sheet_name=sheet,
                    header=None,
                    dtype=str,
                )
                for sheet in workbook.sheet_names
            }

            return sheets, index

        except msoffcrypto.exceptions.InvalidKeyError:
            # Wrong password: try the next one.
            continue

    raise RuntimeError(
        f"None of the configured passwords could decrypt {file_path.name}"
    )


def inspect_sbi_statements():
    folder = PROJECT_ROOT / "data" / "raw_statements"

    files = sorted(folder.glob("*Email_Statement*.xlsx"))
    passwords = get_excel_passwords()

    print(f"Found {len(files)} SBI Excel statements")
    print(f"Loaded {len(passwords)} candidate password(s) from .env")

    successful = 0
    failed = 0

    for file_path in files:
        print(f"\nFILE: {file_path.name}")

        try:
            sheets, password_index = read_encrypted_excel(
                file_path,
                passwords,
            )

            print(f"Decryption successful using password #{password_index}")

            for sheet_name, df in sheets.items():
                print(f"Sheet: {sheet_name}")
                print(f"Rows: {len(df)}, Columns: {len(df.columns)}")
                print("Column positions:", list(range(len(df.columns))))

            successful += 1

        except Exception as exc:
            failed += 1
            print(f"Failed: {type(exc).__name__}: {exc}")

    print("\n========== SUMMARY ==========")
    print(f"Total files: {len(files)}")
    print(f"Successfully decrypted: {successful}")
    print(f"Failed: {failed}")



def inspect_statement_structure():
    folder = PROJECT_ROOT / "data" / "raw_statements"
    files = sorted(folder.glob("*Email_Statement*.xlsx"))
    passwords = get_excel_passwords()

    if not files:
        print("No Excel statements found.")
        return

    # Inspect one representative workbook first.
    file_path = files[0]
    sheets, password_index = read_encrypted_excel(
        file_path, passwords
    )

    print(f"File: {file_path.name}")
    print(f"Password used: #{password_index}")

    for sheet_name, df in sheets.items():
        print(f"\nSheet: {sheet_name}")
        print(f"Shape: {df.shape}")

        # Show only cell types and whether cells are populated.
        # Avoid printing account details or transactions.
        for row_index in range(min(25, len(df))):
            cells = df.iloc[row_index].tolist()
            pattern = [
                "EMPTY" if pd.isna(value)
                else "FILLED"
                for value in cells
            ]
            print(f"Row {row_index}: {pattern}")


if __name__ == "__main__":
    inspect_statement_structure()