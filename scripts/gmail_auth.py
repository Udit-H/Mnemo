"""One-time Gmail login. Opens a browser for Google consent and saves secrets/gmail_token.json.

Run from the repo root:  python -m scripts.gmail_auth
Google will warn the app is unverified (it's your own test app): Advanced → Go to Mnemo.
"""

import sys

from mnemo.gmail.client import TOKEN, get_service


def main() -> int:
    service = get_service(interactive=True)
    profile = service.users().getProfile(userId="me").execute()
    print(f"✓ Authorised as {profile['emailAddress']} ({profile['messagesTotal']:,} messages). Token: {TOKEN}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
