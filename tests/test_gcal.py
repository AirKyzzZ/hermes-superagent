from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from superagent.brief import BriefSettings, build_brief
from superagent.connectors import ConnectorConfigError, ConnectorError, ConnectorSpec, gcal, parse_specs
from superagent.ledger import Ledger, Upstream
from superagent.polling import poll_sources

FAKE_GOG = Path(__file__).with_name("fake_gog_calendar.py")
FIXTURES = Path(__file__).with_name("fixtures")
CALENDARS = json.loads((FIXTURES / "gog_calendar_calendars.synthetic.json").read_text())
EVENTS = json.loads((FIXTURES / "gog_calendar_events.synthetic.json").read_text())
PRIMARY = "owner@example.com"
TEAM = "team.example_abc123@group.calendar.google.com"
HOLIDAYS = "en.french#holiday@group.v.calendar.google.com"
PARIS = ZoneInfo("Europe/Paris")
MONDAY = datetime(2026, 11, 9, 8, 0, tzinfo=PARIS).timestamp()
TUESDAY = datetime(2026, 11, 10, 8, 0, tzinfo=PARIS).timestamp()


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "gog.json"
    monkeypatch.setenv("FAKE_GOG_CALENDAR_FIXTURE", str(path))
    return path


def write(path: Path, **data: Any) -> None:
    path.write_text(json.dumps({"calendars": CALENDARS, "events": {PRIMARY: EVENTS}, **data}), encoding="utf-8")


def event(identifier: str, summary: str, **extra: object) -> dict[str, object]:
    start = {"dateTime": "2026-11-09T18:00:00+01:00"}
    return {"id": identifier, "summary": summary, "start": start, "end": start, "status": "confirmed", **extra}


def page(*events: object, token: str = "") -> dict[str, object]:
    return {"events": list(events), "nextPageToken": token}


def calendar_spec(tmp_path: Path, **extra: object) -> ConnectorSpec:
    wrapper = tmp_path / "gog"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE_GOG} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    body = {
        "type": "gcal",
        "every": "0s",
        "account": PRIMARY,
        "gog": str(wrapper),
        "env": ["FAKE_GOG_CALENDAR_FIXTURE"],
        **extra,
    }
    return parse_specs({"calendar": body})[0]


def connector(spec: ConnectorSpec, at: float = MONDAY) -> gcal.GcalConnector:
    return replace(gcal.build(spec), clock=lambda: at)


def test_an_event_becomes_an_item_titled_with_when_it_is(tmp_path: Path, fixture: Path) -> None:
    write(fixture)
    poll = connector(calendar_spec(tmp_path)).poll("")
    assert poll.problems == ()
    dentist = poll.items[0]
    assert dentist.external_id == f"{PRIMARY}/a1b2c3d4e5f6g7h8"
    assert dentist.title == "today 14:00 Dentist"
    assert dentist.due == "2026-11-09T14:00:00+01:00"
    assert dentist.url == "https://www.google.com/calendar/event?eid=YTFiMmMzZDQ"
    assert dentist.excerpt == "Bring the x-ray"
    assert dentist.meta == {
        "calendar": "Personal",
        "calendar_id": PRIMARY,
        "end": "2026-11-09T14:30:00+01:00",
        "location": "1 Example Street, Bordeaux",
        "organizer": PRIMARY,
    }


def test_titles_say_tomorrow_a_date_or_ongoing(tmp_path: Path, fixture: Path) -> None:
    write(fixture)
    items = {item.title: item for item in connector(calendar_spec(tmp_path)).poll("").items}
    assert set(items) == {
        "today 14:00 Dentist",
        "tomorrow 09:00 Team standup",
        "ongoing Conference",
        "Wed 11 Nov Day off",
    }
    standup = items["tomorrow 09:00 Team standup"]
    assert standup.meta["response"] == "needsAction"
    assert standup.meta["meeting"] == "https://meet.google.com/abc-defg-hij"
    day_off = items["Wed 11 Nov Day off"]
    assert (day_off.due, day_off.meta["all_day"], day_off.meta["status"]) == ("2026-11-11", "true", "tentative")


def test_a_declined_event_and_a_working_location_are_left_out(tmp_path: Path, fixture: Path) -> None:
    write(fixture)
    titles = " ".join(item.title for item in connector(calendar_spec(tmp_path)).poll("").items)
    assert "Vendor pitch" not in titles and "Home" not in titles


def test_only_the_calendars_shown_in_google_are_read_by_default(tmp_path: Path, fixture: Path) -> None:
    write(fixture, events={TEAM: page(event("t1", "Retro")), HOLIDAYS: page(event("h1", "Armistice"))})
    [item] = connector(calendar_spec(tmp_path)).poll("").items
    assert (item.title, item.meta["calendar"]) == ("today 18:00 Retro", "Team")


def test_listed_calendars_replace_the_default(tmp_path: Path, fixture: Path) -> None:
    events = {PRIMARY: page(event("p1", "Mine")), TEAM: page(event("t1", "Retro")), HOLIDAYS: page(event("h1", "Fete"))}
    write(fixture, events=events)
    spec = calendar_spec(tmp_path, calendars=["primary", HOLIDAYS])
    assert {item.title for item in connector(spec).poll("").items} == {"today 18:00 Mine", "today 18:00 Fete"}


def test_a_listed_calendar_missing_from_the_account_is_reported(tmp_path: Path, fixture: Path) -> None:
    write(fixture)
    poll = connector(calendar_spec(tmp_path, calendars=["primary", "gone@example.com"])).poll("")
    assert "gone@example.com" in poll.problems[0] and len(poll.items) == 4


def test_an_account_with_nothing_to_read_fails_rather_than_looking_empty(tmp_path: Path, fixture: Path) -> None:
    write(fixture, calendars={"calendars": [], "nextPageToken": ""})
    with pytest.raises(ConnectorError, match="no calendar"):
        connector(calendar_spec(tmp_path)).poll("")


def test_one_failing_calendar_fails_the_poll_but_keeps_the_others(tmp_path: Path, fixture: Path) -> None:
    write(fixture, events={PRIMARY: page(event("p1", "Mine"))}, failing=[TEAM])
    poll = connector(calendar_spec(tmp_path)).poll("")
    assert len(poll.items) == 1 and TEAM in poll.problems[0] and "404" in poll.problems[0]
    spec = calendar_spec(tmp_path)
    ledger = Ledger(tmp_path / "s.db")
    poll_sources(ledger, [spec], {spec.name: connector(spec)}, 300)
    assert ledger.source("calendar").failures == 1 and len(ledger.items()) == 1


def test_a_failed_calendar_list_records_nothing(tmp_path: Path, fixture: Path) -> None:
    write(fixture, calendars_fail=True)
    spec = calendar_spec(tmp_path)
    ledger = Ledger(tmp_path / "s.db")
    poll_sources(ledger, [spec], {spec.name: connector(spec)}, 300)
    assert ledger.items() == [] and "403" in ledger.source("calendar").last_error


def test_a_page_left_unread_is_a_problem(tmp_path: Path, fixture: Path) -> None:
    write(fixture, events={PRIMARY: page(event("p1", "Mine"), token="more")})
    poll = connector(calendar_spec(tmp_path)).poll("")
    assert "last page" in poll.problems[0]


def test_one_unreadable_event_doesnt_hide_the_others(tmp_path: Path, fixture: Path) -> None:
    bad = [{"summary": "no id"}, event("b1", "No start", start="soon"), event("b2", "Bad", start={"date": "soon"})]
    write(fixture, events={PRIMARY: page(event("p1", "Mine"), *bad, "not an object")})
    poll = connector(calendar_spec(tmp_path)).poll("")
    assert len(poll.items) == 1 and len(poll.problems) == 4


def test_an_event_without_a_title_still_shows(tmp_path: Path, fixture: Path) -> None:
    untitled = event("p1", "")
    write(fixture, events={PRIMARY: page(untitled)})
    [item] = connector(calendar_spec(tmp_path)).poll("").items
    assert item.title == "today 18:00 (no title)"


def test_an_all_day_event_with_an_unknown_timezone_still_shows(tmp_path: Path, fixture: Path) -> None:
    write(fixture, events={PRIMARY: page(event("p1", "Fair", start={"date": "2026-11-20"}, timezone="Nope/Zone"))})
    [item] = connector(calendar_spec(tmp_path)).poll("").items
    assert item.title == "Fri 20 Nov Fair"


def test_each_brief_relists_an_event_as_its_day_gets_closer(tmp_path: Path, fixture: Path) -> None:
    write(fixture)
    spec = calendar_spec(tmp_path)
    ledger = Ledger(tmp_path / "s.db")
    settings = BriefSettings(stale_after=86400.0, timezone="Europe/Paris")
    poll_sources(ledger, [spec], {spec.name: connector(spec, MONDAY)}, 300)
    assert "tomorrow 09:00 Team standup" in build_brief(ledger, [spec], 0.0, time.time(), settings).text
    since = time.time()
    poll_sources(ledger, [spec], {spec.name: connector(spec, MONDAY)}, 300)
    assert "Team standup" not in build_brief(ledger, [spec], since, time.time(), settings).text
    since = time.time()
    poll_sources(ledger, [spec], {spec.name: connector(spec, TUESDAY)}, 300)
    brief = build_brief(ledger, [spec], since, time.time(), settings).text
    assert "today 09:00 Team standup" in brief and "Reopened" not in brief and "Ignored" not in brief


def test_an_event_leaving_the_window_closes_nothing(tmp_path: Path, fixture: Path) -> None:
    write(fixture)
    spec = calendar_spec(tmp_path)
    ledger, source = Ledger(tmp_path / "s.db"), connector(spec)
    assert not source.poll("").complete and source.confirm(f"{PRIMARY}/a1b2c3d4e5f6g7h8") is Upstream.UNKNOWN
    poll_sources(ledger, [spec], {spec.name: source}, 300)
    write(fixture, events={})
    poll_sources(ledger, [spec], {spec.name: source}, 300)
    assert all(item.missing_since is None for item in ledger.items())
    assert ledger.source("calendar").held_count is None


def test_calendars_are_informational_and_can_never_be_trusted() -> None:
    [spec] = parse_specs({"calendar": {"type": "gcal", "account": PRIMARY}})
    assert not spec.track
    with pytest.raises(ConnectorConfigError, match="informational"):
        parse_specs({"calendar": {"type": "gcal", "account": PRIMARY, "track": True}})
    with pytest.raises(ConnectorConfigError, match="other people's words"):
        parse_specs({"calendar": {"type": "gcal", "account": PRIMARY, "trusted": True}})


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"account": "nope"}, "account"),
        ({"days": 0}, "days"),
        ({"days": 31}, "days"),
        ({"days": True}, "days"),
        ({"calendars": "primary"}, "calendars"),
        ({"calendars": ["--all"]}, "calendars"),
        ({"gog": ""}, "gog"),
        ({"timeout": 0}, "timeout"),
        ({"env": "X"}, "env"),
    ],
)
def test_bad_settings_are_refused(extra: dict[str, object], message: str) -> None:
    with pytest.raises(ConnectorConfigError, match=message):
        parse_specs({"calendar": {"type": "gcal", "account": PRIMARY, **extra}})
