from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll
from .process import run_json

KEYS = frozenset({"account", "calendars", "days", "gog", "timeout", "env"})
DEFAULT_DAYS = 3
MAX_DAYS = 30
PAGE_SIZE = 250
EXCERPT_CHARS = 500
PRIMARY = "primary"
LEFT_OUT_TYPES = frozenset({"workingLocation"})
_SAFE_CALENDAR = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+#@-]{0,255}")
_DEFAULT_ENV = ("GOG_HOME", "GOG_TIMEZONE", "DBUS_SESSION_BUS_ADDRESS")


@dataclass(frozen=True)
class GcalConnector:
    account: str
    calendars: tuple[str, ...]
    days: int
    gog: str
    timeout: float
    env_names: tuple[str, ...]
    clock: Callable[[], float] = time.time

    def poll(self, cursor: str) -> Poll:
        deadline = time.monotonic() + self.timeout
        chosen, problems = self._chosen(deadline)
        now = self.clock()
        items: dict[str, SourceItem] = {}
        for calendar_id, name in chosen.items():
            try:
                events = self._events(calendar_id, deadline)
            except ConnectorError as error:
                problems.append(f"calendar {calendar_id}: {error}")
                continue
            for entry in events:
                if _left_out(entry):
                    continue
                try:
                    item = _event(entry, calendar_id, name, now)
                except ConnectorError as error:
                    problems.append(str(error))
                    continue
                items[item.external_id] = item
        return Poll(tuple(items.values()), complete=False, problems=tuple(problems))

    def confirm(self, external_id: str) -> Upstream:
        return Upstream.UNKNOWN

    def _base(self) -> list[str]:
        return [self.gog, "--readonly", "--json", "--no-input", "--account", self.account]

    def _chosen(self, deadline: float) -> tuple[dict[str, str], list[str]]:
        argv = [*self._base(), "calendar", "calendars", "--all"]
        listing = run_json(argv, self.env_names, deadline - time.monotonic())
        entries = listing.get("calendars") if isinstance(listing, Mapping) else None
        if not isinstance(entries, list):
            raise ConnectorError("gog returned an unexpected shape for the calendar list")
        wanted = set(self.calendars)
        chosen: dict[str, str] = {}
        matched: set[str] = set()
        problems: list[str] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                problems.append("gog listed a calendar that isn't an object")
                continue
            identifier, primary = entry.get("id"), entry.get("primary") is True
            names = {identifier, PRIMARY} if primary else {identifier}
            if not (names & wanted if wanted else primary or entry.get("selected") is True):
                continue
            if not isinstance(identifier, str) or not _SAFE_CALENDAR.fullmatch(identifier):
                problems.append(f"calendar with an unusable id {str(identifier)[:40]!r} in gog's list")
                continue
            chosen[identifier] = str(entry.get("summaryOverride") or entry.get("summary") or identifier)
            matched |= {name for name in names if isinstance(name, str)}
        problems += [f"calendar {name} isn't in this account's list" for name in sorted(wanted - matched)]
        if not chosen and not problems:
            raise ConnectorError("gog listed no calendar to read")
        return chosen, problems

    def _events(self, calendar_id: str, deadline: float) -> list[object]:
        # gog's --all logs a calendar that fails and still exits 0, so each one is read on its own
        argv = [
            *self._base(),
            "calendar",
            "events",
            calendar_id,
            "--days",
            str(self.days),
            "--all-pages",
            "--max",
            str(PAGE_SIZE),
        ]
        result = run_json(argv, self.env_names, deadline - time.monotonic())
        events = result.get("events") if isinstance(result, Mapping) else None
        if not isinstance(result, Mapping) or not isinstance(events, list):
            raise ConnectorError("gog returned an unexpected shape for the events")
        if result.get("nextPageToken"):
            raise ConnectorError("gog stopped before the last page of events")
        return events


def _self_attendee(entry: Mapping[str, object]) -> Mapping[str, object]:
    attendees = entry.get("attendees")
    if isinstance(attendees, list):
        for attendee in attendees:
            if isinstance(attendee, Mapping) and attendee.get("self") is True:
                return attendee
    return {}


def _left_out(entry: object) -> bool:
    if not isinstance(entry, Mapping):
        return False
    return entry.get("eventType") in LEFT_OUT_TYPES or _self_attendee(entry).get("responseStatus") == "declined"


def _event(entry: object, calendar_id: str, calendar_name: str, now: float) -> SourceItem:
    identifier = entry.get("id") if isinstance(entry, Mapping) else None
    if not isinstance(entry, Mapping) or not isinstance(identifier, str) or not identifier:
        raise ConnectorError(f"event without an id in calendar {calendar_id}")
    start, end = entry.get("start"), entry.get("end")
    if not isinstance(start, Mapping):
        raise ConnectorError(f"event {identifier} has no start")
    all_day = not start.get("dateTime")
    raw = start.get("date") if all_day else entry.get("startLocal") or start.get("dateTime")
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError as error:
        raise ConnectorError(f"event {identifier} has an unreadable start {str(raw)[:40]!r}") from error
    zone = _zone(entry.get("timezone")) if all_day else moment.tzinfo
    today = datetime.fromtimestamp(now, zone).date()
    summary = " ".join(str(entry.get("summary") or "(no title)").split())
    label = _label(moment.date(), today)
    clock = "" if all_day or label == "ongoing" else f" {moment:%H:%M}"
    organizer = entry.get("organizer")
    description = entry.get("description")
    meta = {
        "calendar": calendar_name,
        "calendar_id": calendar_id,
        "all_day": "true" if all_day else "",
        "end": _end(entry, end),
        "location": str(entry.get("location") or ""),
        "organizer": str(organizer.get("email") or "") if isinstance(organizer, Mapping) else "",
        "response": str(_self_attendee(entry).get("responseStatus") or ""),
        "status": "tentative" if entry.get("status") == "tentative" else "",
        "meeting": str(entry.get("hangoutLink") or ""),
        "event_type": str(entry.get("eventType") or "") if entry.get("eventType") != "default" else "",
    }
    return SourceItem(
        external_id=f"{calendar_id}/{identifier}",
        title=f"{label}{clock} {summary}",
        url=str(entry.get("htmlLink") or ""),
        due=str(raw),
        excerpt=description[:EXCERPT_CHARS] if isinstance(description, str) else "",
        meta={key: value for key, value in meta.items() if value},
    )


def _end(entry: Mapping[str, object], end: object) -> str:
    if isinstance(end, Mapping) and end.get("date"):
        return str(end["date"])
    return str(entry.get("endLocal") or (end.get("dateTime") if isinstance(end, Mapping) else "") or "")


def _zone(name: object) -> ZoneInfo | None:
    try:
        return ZoneInfo(name) if isinstance(name, str) and name else None
    except (ValueError, OSError, KeyError):
        return None


def _label(day: date, today: date) -> str:
    offset = (day - today).days
    if offset < 0:
        return "ongoing"
    return {0: "today", 1: "tomorrow"}.get(offset, day.strftime("%a %d %b"))


def build(spec: ConnectorSpec) -> GcalConnector:
    where = f"[connectors.{spec.name}]"
    account = spec.settings.get("account")
    if not isinstance(account, str) or "@" not in account:
        raise ConnectorConfigError(f"{where} account must be the Google account's address, one block per account")
    calendars = spec.settings.get("calendars", [])
    if not isinstance(calendars, list) or not all(
        isinstance(name, str) and _SAFE_CALENDAR.fullmatch(name) for name in calendars
    ):
        raise ConnectorConfigError(f'{where} calendars must be a list of calendar ids, or "primary"')
    days = spec.settings.get("days", DEFAULT_DAYS)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise ConnectorConfigError(f"{where} days must be a whole number from 1 to {MAX_DAYS}")
    gog = spec.settings.get("gog", "gog")
    if not isinstance(gog, str) or not gog:
        raise ConnectorConfigError(f"{where} gog must be a command name or path")
    timeout = spec.settings.get("timeout", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds")
    env = spec.settings.get("env", [])
    if not isinstance(env, list) or not all(isinstance(name, str) for name in env):
        raise ConnectorConfigError(f"{where} env must be a list of variable names")
    return GcalConnector(account, tuple(calendars), days, gog, float(timeout), (*_DEFAULT_ENV, *env))
