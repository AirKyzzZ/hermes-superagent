from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from superagent.connectors import ConnectorConfigError, ConnectorError, ConnectorSpec, anki, parse_specs
from superagent.ledger import Ledger, Upstream
from superagent.polling import poll_sources

FAKE = Path(__file__).with_name("fake_curl_anki.py")
ANKI = json.loads(Path(__file__).with_name("fixtures").joinpath("ankiconnect.synthetic.json").read_text())
MONDAY = datetime(2026, 11, 9, 8, 0).timestamp()
TUESDAY = datetime(2026, 11, 10, 8, 0).timestamp()


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "anki.json"
    monkeypatch.setenv("FAKE_ANKI_FIXTURE", str(path))
    return path


def write(path: Path, **data: Any) -> None:
    path.write_text(json.dumps({**ANKI, **data}), encoding="utf-8")


def spec_for(tmp_path: Path, **extra: object) -> ConnectorSpec:
    wrapper = tmp_path / "curl"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    body = {"type": "anki", "every": "0s", "curl": str(wrapper), "env": ["FAKE_ANKI_FIXTURE"], **extra}
    return parse_specs({"anki": body})[0]


def connector(spec: ConnectorSpec, at: float = MONDAY) -> anki.AnkiConnector:
    return replace(anki.build(spec), clock=lambda: at)


def test_todays_due_cards_become_one_reading_rolled_up_by_top_deck(tmp_path: Path, fixture: Path) -> None:
    write(fixture)
    poll = connector(spec_for(tmp_path)).poll("")
    assert poll.problems == ()
    [item] = poll.items
    assert item.external_id == "2026-11-09"
    assert item.title == "Anki Mon 09 Nov: 6 due, 15 reviewed"
    assert item.meta == {"due": "6", "reviewed": "15", "decks": "Japanese 4, Medicine 2"}


def test_nothing_due_is_still_a_reading(tmp_path: Path, fixture: Path) -> None:
    write(fixture, findCards=[], getNumCardsReviewedToday=0)
    [item] = connector(spec_for(tmp_path)).poll("").items
    assert item.title == "Anki Mon 09 Nov: nothing due, 0 reviewed" and "decks" not in item.meta


@pytest.mark.parametrize(
    "state",
    [
        {"exit": 7},
        {"errors": {"findCards": "Collection lifecycle operation is active."}},
        {"errors": {"getNumCardsReviewedToday": "collection is not available"}},
    ],
)
def test_anki_not_being_open_is_not_a_failure(tmp_path: Path, fixture: Path, state: dict[str, object]) -> None:
    write(fixture, **state)
    spec = spec_for(tmp_path)
    ledger = Ledger(tmp_path / "s.db")
    poll_sources(ledger, [spec], {spec.name: connector(spec)}, 300)
    assert ledger.items() == [] and ledger.source("anki").failures == 0


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"exit": 255}, "exited 255"),
        ({"body": "<html>nope</html>"}, "JSON"),
        ({"body": '{"unexpected": true}'}, "unexpected reply"),
        ({"errors": {"findCards": "invalid search"}}, "invalid search"),
        ({"findCards": {"not": "a list"}}, "card list"),
        ({"getDecks": ["text"]}, "decks"),
        ({"getDecks": {"Japanese": "text"}}, "decks"),
        ({"getNumCardsReviewedToday": "lots"}, "reviewed"),
    ],
)
def test_anything_else_wrong_fails_the_poll(
    tmp_path: Path, fixture: Path, state: dict[str, object], message: str
) -> None:
    write(fixture, **state)
    with pytest.raises(ConnectorError, match=message):
        connector(spec_for(tmp_path)).poll("")


def test_one_reading_per_day_updated_in_place(tmp_path: Path, fixture: Path) -> None:
    write(fixture)
    spec = spec_for(tmp_path)
    ledger = Ledger(tmp_path / "s.db")
    poll_sources(ledger, [spec], {spec.name: connector(spec, MONDAY)}, 300)
    write(fixture, getNumCardsReviewedToday=40)
    poll_sources(ledger, [spec], {spec.name: connector(spec, MONDAY)}, 300)
    [item] = ledger.items()
    assert "40 reviewed" in item.title
    poll_sources(ledger, [spec], {spec.name: connector(spec, TUESDAY)}, 300)
    assert {item.external_id for item in ledger.items()} == {"2026-11-09", "2026-11-10"}
    assert all(item.missing_since is None for item in ledger.items())
    assert connector(spec).confirm("2026-11-09") is Upstream.UNKNOWN


def test_anki_is_informational() -> None:
    [spec] = parse_specs({"anki": {"type": "anki"}})
    assert not spec.track
    with pytest.raises(ConnectorConfigError, match="informational"):
        parse_specs({"anki": {"type": "anki", "track": True}})


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"url": "ftp://x"}, "url"),
        ({"url": "--upload-file=/etc/hosts"}, "url"),
        ({"curl": ""}, "curl"),
        ({"curl": ["ssh", 1]}, "curl"),
        ({"timeout": 0}, "timeout"),
        ({"env": "X"}, "env"),
    ],
)
def test_bad_settings_are_refused(extra: dict[str, object], message: str) -> None:
    with pytest.raises(ConnectorConfigError, match=message):
        parse_specs({"anki": {"type": "anki", **extra}})
