"""One-time Google login for every tool Mnemo uses (Gmail + Calendar). Saves secrets/gmail_token.json.

Run from the repo root:  python -m scripts.gmail_auth
Re-run it whenever a new tool is added; it only opens the browser if a scope is missing.
Google will warn the app is unverified (it's your own test app): Advanced → Go to Mnemo.
"""

import sys

from googleapiclient.discovery import build

from mnemo.gmail.client import SCOPES, TOKEN, google_credentials


def main() -> int:
    creds = google_credentials(SCOPES, interactive=True)
    profile = build("gmail", "v1", credentials=creds, cache_discovery=False).users().getProfile(userId="me").execute()
    print(f"✓ Gmail: {profile['emailAddress']} ({profile['messagesTotal']:,} messages)")
    cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
    primary = cal.calendars().get(calendarId="primary").execute()
    print(f"✓ Calendar: {primary['summary']} ({primary.get('timeZone')})")
    print(f"Token: {TOKEN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
