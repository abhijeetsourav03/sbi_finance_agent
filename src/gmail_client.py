import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import (
    ALLOWED_EXTENSIONS, CREDENTIALS_PATH, SCOPES,
    STATEMENTS_DIR, TOKEN_PATH
)


def get_gmail_service():
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            "credentials.json is missing. Download a Desktop OAuth client JSON "
            "from Google Cloud Console and place it in the project root."
        )

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_PATH), SCOPES
        )
        creds = flow.run_local_server(port=0)

    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def safe_filename(filename):
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return name[:140] or "attachment"


def statement_filename(filename):
    """Extract statement month from the last 8 digits (DDMMYYYY)."""
    match = re.search(r"(\d{8})$", Path(filename).stem)

    if not match:
        return None

    try:
        statement_date = datetime.strptime(match.group(1), "%d%m%Y")
    except ValueError:
        return None

    return f"SBI_{statement_date:%Y-%m}.pdf"

def walk_parts(payload):
    for part in payload.get("parts", []) or []:
        yield part
        yield from walk_parts(part)


def header_value(headers, wanted):
    for item in headers or []:
        if item.get("name", "").lower() == wanted.lower():
            return item.get("value", "")
    return ""


def read_manifest(path):
    if not path.exists():
        return {"downloaded": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Manifest JSON is invalid: {path}") from exc


def write_manifest(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temp.replace(path)


def download_attachments_for_message(service, message_id, manifest):
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()

    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    subject = header_value(headers, "Subject")
    sender = header_value(headers, "From")
    internal_date = msg.get("internalDate")
    received_at = (
        datetime.fromtimestamp(int(internal_date) / 1000, timezone.utc).isoformat()
        if internal_date else None
    )

    seen = {
        row["attachment_key"]
        for row in manifest.get("downloaded", [])
        if row.get("attachment_key")
    }
    new_count = 0

    for part in walk_parts(payload):
        original_name = part.get("filename")
        if not original_name:
            continue

        filename = safe_filename(original_name)
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            print(f"  Skipped unsupported file: {filename}")
            continue

        body = part.get("body", {})
        attachment_id = body.get("attachmentId")
        if attachment_id:
            attachment = service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id
            ).execute()
            encoded = attachment.get("data", "")
        else:
            encoded = body.get("data", "")

        if not encoded:
            print(f"  Skipped empty attachment: {filename}")
            continue

        key = f"{message_id}:{part.get('partId', '')}:{filename}"
        if key in seen:
            print(f"  Already downloaded: {filename}")
            continue

        file_bytes = base64.urlsafe_b64decode(encoded.encode("utf-8"))
        STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)
        output_name = (
            statement_filename(filename)
            if ext == ".pdf"
            else None
        ) or f"{message_id[:12]}_{filename}"

        output_path = STATEMENTS_DIR / output_name

        # Never overwrite an existing local file.
        if output_path.exists():
            stem = Path(output_name).stem
            suffix = Path(output_name).suffix
            counter = 2

            while output_path.exists():
                output_path = STATEMENTS_DIR / f"{stem}_{counter}{suffix}"
                counter += 1

        output_path.write_bytes(file_bytes)

        manifest.setdefault("downloaded", []).append({
            "attachment_key": key,
            "message_id": message_id,
            "filename": filename,
            "saved_path": str(output_path),
            "sender": sender,
            "subject": subject,
            "received_at_utc": received_at,
            "size_bytes": len(file_bytes),
            "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        seen.add(key)
        new_count += 1
        print(f"  Saved: {output_path.name} ({len(file_bytes):,} bytes)")

    return new_count


def fetch_statements(query, max_results, manifest_path):
    service = get_gmail_service()
    manifest = read_manifest(manifest_path)

    response = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = response.get("messages", [])
    print(f"Matched {len(messages)} email(s) for query: {query}")

    total = 0
    for item in messages:
        message_id = item["id"]
        print(f"Processing {message_id[:12]}...")
        try:
            total += download_attachments_for_message(service, message_id, manifest)
            write_manifest(manifest_path, manifest)
        except HttpError as exc:
            print(f"Gmail API error for {message_id}: {exc}")

    print(f"New attachments downloaded: {total}")
    print(f"Files: {STATEMENTS_DIR}")
    print(f"Manifest: {manifest_path}")
