"""Normalized email shapes. The graph writer only sees these."""

from datetime import datetime

from pydantic import BaseModel

FREEMAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com", "live.com",
    "icloud.com", "me.com", "proton.me", "protonmail.com", "aol.com", "rediffmail.com", "zoho.com",
}


class Address(BaseModel):
    email: str
    name: str | None = None

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[-1]


class EmailMsg(BaseModel):
    id: str
    thread_id: str
    at: datetime
    sender: Address
    to: list[Address] = []
    cc: list[Address] = []
    subject: str = ""
    snippet: str = ""
    labels: list[str] = []
    message_id: str | None = None  # RFC Message-ID, needed to thread a reply draft
    references: str | None = None
    is_bulk: bool = False  # newsletters, notifications (List-Unsubscribe / Precedence headers)


class EmailThread(BaseModel):
    id: str
    messages: list[EmailMsg]  # oldest first

    @property
    def subject(self) -> str:
        return next((m.subject for m in self.messages if m.subject), "(no subject)")

    @property
    def labels(self) -> list[str]:
        return sorted({label for m in self.messages for label in m.labels})

    def to_graph(self) -> dict:
        def addr(a: Address) -> dict:
            return {"email": a.email, "name": a.name, "domain": a.domain}

        return {
            "id": self.id,
            "subject": self.subject,
            "last_at": self.messages[-1].at,
            "snippet": self.messages[-1].snippet,
            "labels": self.labels,
            "unread": "UNREAD" in self.labels,
            "in_inbox": "INBOX" in self.labels,
            "is_bulk": any(m.is_bulk for m in self.messages),
            "messages": [{
                "id": m.id, "at": m.at, "subject": m.subject, "snippet": m.snippet, "labels": m.labels,
                "message_id": m.message_id, "references": m.references, "is_bulk": m.is_bulk,
                "sender": addr(m.sender), "to": [addr(a) for a in m.to], "cc": [addr(a) for a in m.cc],
            } for m in self.messages],
        }
