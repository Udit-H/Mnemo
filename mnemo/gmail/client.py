"""Gmail API access: OAuth, thread fetching, draft creation. This module never sends mail.

OAuth client: secrets/gmail_credentials.json (Desktop app, from Google Cloud console).
Token:        secrets/gmail_token.json (written by `python -m scripts.gmail_auth`).
"""

import base64
import html
import re
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from mnemo.gmail.models import Address, EmailMsg, EmailThread

ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS = ROOT / "secrets" / "gmail_credentials.json"
TOKEN = ROOT / "secrets" / "gmail_token.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",  # drafts; we never call send
]
HEADERS = ["From", "To", "Cc", "Subject", "Message-ID", "References", "List-Unsubscribe", "Precedence"]
BATCH_SIZE = 25  # threads.get costs 10 quota units; Gmail allows 250 units/s per user
_BULK_SENDER = re.compile(r"^(no-?reply|do-?not-?reply|notifications?|mailer-daemon|newsletter|alerts?)\b", re.I)


def get_service(interactive: bool = False):
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json())
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError("Gmail is not authorised yet. Run: python -m scripts.gmail_auth")
        if not CREDENTIALS.exists():
            raise FileNotFoundError(f"Missing OAuth client file: {CREDENTIALS}")
        creds = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES).run_local_server(port=0)
        TOKEN.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def my_address(service) -> str:
    return service.users().getProfile(userId="me").execute()["emailAddress"].lower()


def _address(raw: str) -> Address | None:
    name, email = parseaddr(raw)
    return Address(email=email.lower(), name=name.strip().strip('"') or None) if "@" in email else None


def _addresses(raw: str | None) -> list[Address]:
    found = (_address(f"{n} <{e}>") for n, e in getaddresses([raw or ""]) if "@" in e)
    return [a for a in found if a]


def _parse_message(msg: dict) -> EmailMsg | None:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    sender = _address(headers.get("from", ""))
    if not sender:
        return None
    precedence = headers.get("precedence", "").lower()
    return EmailMsg(
        id=msg["id"],
        thread_id=msg["threadId"],
        at=datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc),
        sender=sender,
        to=_addresses(headers.get("to")),
        cc=_addresses(headers.get("cc")),
        subject=headers.get("subject", ""),
        snippet=html.unescape(msg.get("snippet", "")),
        labels=msg.get("labelIds", []),
        message_id=headers.get("message-id"),
        references=headers.get("references"),
        is_bulk=bool(headers.get("list-unsubscribe")) or precedence in ("bulk", "list", "junk")
                or bool(_BULK_SENDER.match(sender.email)),
    )


def _parse_thread(raw: dict) -> EmailThread | None:
    msgs = sorted((m for m in map(_parse_message, raw.get("messages", [])) if m), key=lambda m: m.at)
    return EmailThread(id=raw["id"], messages=msgs) if msgs else None


def list_thread_ids(service, query: str, max_threads: int) -> list[str]:
    ids, page = [], None
    while len(ids) < max_threads:
        resp = service.users().threads().list(
            userId="me", q=query, maxResults=min(500, max_threads - len(ids)), pageToken=page).execute()
        ids += [t["id"] for t in resp.get("threads", [])]
        page = resp.get("nextPageToken")
        if not page:
            break
    return ids


def fetch_threads(service, query: str = "newer_than:90d -in:chats", max_threads: int = 500,
                  progress=None) -> list[EmailThread]:
    """Headers + snippets only (format=metadata): fast, and enough for the relationship graph."""
    ids = list_thread_ids(service, query, max_threads)
    raw: dict[str, dict] = {}

    def collect(request_id, response, exception):
        if exception is None:
            raw[request_id] = response

    def get(tid: str):
        return service.users().threads().get(userId="me", id=tid, format="metadata", metadataHeaders=HEADERS)

    for i in range(0, len(ids), BATCH_SIZE):
        batch = service.new_batch_http_request(callback=collect)
        for tid in ids[i:i + BATCH_SIZE]:
            batch.add(get(tid), request_id=tid)
        batch.execute()
        if progress:
            progress(min(i + BATCH_SIZE, len(ids)), len(ids))
        time.sleep(1.0)

    for tid in (t for t in ids if t not in raw):  # retry anything the batch rate-limited
        for attempt in range(3):
            try:
                raw[tid] = get(tid).execute()
                break
            except Exception:
                time.sleep(2 ** attempt)

    threads = (_parse_thread(raw[t]) for t in ids if t in raw)
    return [t for t in threads if t]


def create_draft(service, thread_id: str, to: str, subject: str, body: str,
                 in_reply_to: str | None = None, references: str | None = None) -> str:
    """Create a reply draft in the user's Gmail (shows up in Drafts, threaded). Returns the draft id."""
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": thread_id}}).execute()
    return draft["id"]
