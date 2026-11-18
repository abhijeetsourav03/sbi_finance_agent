# SBI Finance Agent — Phase 1 (Gmail statement fetcher)

## What this phase does
- Authenticates to Gmail using a Desktop OAuth client.
- Uses Gmail's read-only scope.
- Searches messages using a configurable Gmail query.
- Downloads PDF, CSV, XLS and XLSX attachments.
- Avoids re-downloading the same message attachment using a local manifest.
- Does not send, delete, archive, label or modify email.

## Windows 11 + Cursor setup

Open this folder in Cursor. Open Terminal → New Terminal (PowerShell), then run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

If activation is blocked in this terminal session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

In Cursor, select the interpreter at `.venv\Scripts\python.exe` if it does not select automatically.

## Google Cloud OAuth setup

1. Go to https://console.cloud.google.com/ and create a project.
2. Enable **Gmail API** under APIs & Services → Library.
3. Configure Google Auth Platform / OAuth consent screen. For a personal Gmail account, choose External if required, and add your own Gmail address as a test user while the app is in Testing.
4. Under Google Auth Platform → Clients (or APIs & Services → Credentials), create an OAuth Client ID with application type **Desktop app**.
5. Download the JSON and put it in this project root named exactly `credentials.json`.
6. Do not share this file or `token.json`. They are credentials.

Google's official Python quickstart: https://developers.google.com/workspace/gmail/api/quickstart/python

## Configure the Gmail query

Edit `.env`. The initial query `from:(sbi) has:attachment` is only a starting point and may match irrelevant messages or miss your bank's statement email.

First, search your Gmail manually for SBI statement emails and inspect the actual sender and subject. Then narrow the query, for example:

`GMAIL_QUERY=from:actual-sender@example.com has:attachment filename:pdf`

Replace the example address with the real sender shown in your own email. Gmail search syntax: https://support.google.com/mail/answer/7190

## Run

```powershell
python -m src.main --max-results 15
```

On first run a browser window opens. Sign in to the Gmail account receiving the SBI statement and approve read-only access. Attachments are saved under `data/raw_statements/`; `data/manifest.json` records metadata and deduplication keys.

You can override the query for one run:

```powershell
python -m src.main --query "from:actual-sender@example.com has:attachment" --max-results 10
```

## Security notes
- Keep `.env`, `credentials.json`, `token.json`, and `data/` out of Git and cloud-shared folders.
- This code only requests `gmail.readonly`.
- The downloaded bank statements are sensitive. Keep this project on a trusted, encrypted Windows account/device.
- This is a local development prototype, not a production security review.
