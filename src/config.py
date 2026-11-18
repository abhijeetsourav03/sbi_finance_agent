import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

GMAIL_QUERY = os.getenv("GMAIL_QUERY", "from:(sbi) has:attachment").strip()
STATEMENTS_DIR = ROOT / os.getenv("STATEMENTS_DIR", "data/raw_statements")
MANIFEST_PATH = ROOT / "data" / "manifest.json"
CREDENTIALS_PATH = ROOT / "credentials.json"
TOKEN_PATH = ROOT / "token.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
ALLOWED_EXTENSIONS = {".pdf", ".csv", ".xls", ".xlsx"}
