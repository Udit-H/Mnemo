"""Google Calendar access: read events, free/busy, and create events the user approved.

Created events use sendUpdates='none': attendees are never emailed. Same token as Gmail (scripts.gmail_auth).
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from pydantic import BaseModel

from mnemo.gmail.client import CALENDAR_SCOPES, google_credentials


class Attendee(BaseModel):
    email: str
    name: str | None = None
    response: str = "needsAction"  # accepted / declined / tentative / needsAction
    organizer: bool = False

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[-1]


class CalEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: str | None = None
    description: str | None = None
    status: str = "confirmed"
    link: str | None = None
    meet_link: str | None = None
    recurring_id: str | None = None
    attendees: list[Attendee] = []

    def to_graph(self, self_emails: list[str]) -> dict:
        utc = {"start": self.start.astimezone(timezone.utc), "end": self.end.astimezone(timezone.utc)}  # driver
        return {**self.model_dump(exclude={"attendees"}), **utc, "attendees": [                     # needs UTC
            {**a.model_dump(), "domain": a.domain} for a in self.attendees if a.email not in self_emails]}


def get_calendar(interactive: bool = False):
    return build("calendar", "v3", credentials=google_credentials(CALENDAR_SCOPES, interactive),
                 cache_discovery=False)


def calendar_timezone(cal) -> ZoneInfo:
    return ZoneInfo(cal.settings().get(setting="timezone").execute()["value"])


def _when(raw: dict, tz: ZoneInfo) -> tuple[datetime, bool]:
    if "dateTime" in raw:
        return datetime.fromisoformat(raw["dateTime"]), False
    return datetime.combine(date.fromisoformat(raw["date"]), time.min, tzinfo=tz), True


def _parse(raw: dict, tz: ZoneInfo) -> CalEvent | None:
    if raw.get("status") == "cancelled" or "start" not in raw:
        return None
    start, all_day = _when(raw["start"], tz)
    end, _ = _when(raw["end"], tz)
    return CalEvent(
        id=raw["id"], title=raw.get("summary") or "(no title)", start=start, end=end, all_day=all_day,
        location=raw.get("location"), description=(raw.get("description") or "")[:1000] or None,
        status=raw.get("status", "confirmed"), link=raw.get("htmlLink"), meet_link=raw.get("hangoutLink"),
        recurring_id=raw.get("recurringEventId"),
        attendees=[Attendee(email=a["email"].lower(), name=a.get("displayName"),
                            response=a.get("responseStatus", "needsAction"), organizer=a.get("organizer", False))
                   for a in raw.get("attendees", []) if "@" in a.get("email", "")
                   and not a.get("resource")],
    )


def fetch_events(cal, days_back: int = 30, days_ahead: int = 14) -> list[CalEvent]:
    tz = calendar_timezone(cal)
    now = datetime.now(timezone.utc)
    events, page = [], None
    while True:
        resp = cal.events().list(
            calendarId="primary", singleEvents=True, orderBy="startTime", maxResults=250, pageToken=page,
            timeMin=(now - timedelta(days=days_back)).isoformat(),
            timeMax=(now + timedelta(days=days_ahead)).isoformat()).execute()
        events += [e for e in (_parse(r, tz) for r in resp.get("items", [])) if e]
        page = resp.get("nextPageToken")
        if not page:
            return events


def busy_intervals(cal, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    resp = cal.freebusy().query(body={"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                                      "items": [{"id": "primary"}]}).execute()
    return [(datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
             datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
            for b in resp["calendars"]["primary"].get("busy", [])]


def free_slots(cal, day: date, minutes: int = 60, work_start: int = 9, work_end: int = 21) -> list[dict]:
    """Free gaps of at least `minutes` within working hours on `day` (calendar's timezone)."""
    tz = calendar_timezone(cal)
    start = datetime.combine(day, time(work_start), tzinfo=tz)
    end = datetime.combine(day, time(work_end), tzinfo=tz)
    start = max(start, datetime.now(tz).replace(second=0, microsecond=0))
    slots, cursor = [], start
    for b_start, b_end in sorted(busy_intervals(cal, start, end)):
        if (b_start - cursor) >= timedelta(minutes=minutes):
            slots.append((cursor, b_start))
        cursor = max(cursor, b_end)
    if (end - cursor) >= timedelta(minutes=minutes):
        slots.append((cursor, end))
    return [{"start": s.astimezone(tz).isoformat(), "end": e.astimezone(tz).isoformat()} for s, e in slots]


def create_event(cal, title: str, start: datetime, end: datetime, description: str = "",
                 attendees: list[str] | None = None) -> dict:
    """Create an event on the primary calendar. sendUpdates='none': nobody gets an invite email."""
    body = {"summary": title, "description": description,
            "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
            "attendees": [{"email": a} for a in attendees or []]}
    created = cal.events().insert(calendarId="primary", body=body, sendUpdates="none").execute()
    return {"id": created["id"], "link": created.get("htmlLink")}
