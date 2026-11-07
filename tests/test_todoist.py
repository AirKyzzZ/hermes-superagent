from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from superagent.config import parse_config
from superagent.connectors import ConnectorConfigError, ConnectorSpec, build_connector, parse_specs
from superagent.ledger import ItemState, Ledger, Upstream
from superagent.polling import poll_sources
from superagent.starter import Choices, starter_config

FAKE_TD = Path(__file__).with_name("fake_td.py")
FIXTURES = Path(__file__).with_name("fixtures")
TASKS = json.loads((FIXTURES / "td_task_list.json").read_text())["results"]
PROJECTS = json.loads((FIXTURES / "td_project_list.json").read_text())["results"]


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "td.json"
    monkeypatch.setenv("FAKE_TD_FIXTURE", str(path))
    return path


def write(path: Path, **data: Any) -> None:
    path.write_text(json.dumps({"tasks": TASKS, "projects": PROJECTS, **data}), encoding="utf-8")


def todoist_spec(tmp_path: Path, **extra: object) -> ConnectorSpec:
    wrapper = tmp_path / "td"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE_TD} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    body = {"type": "todoist", "every": "0s", "td": str(wrapper), "env": ["FAKE_TD_FIXTURE"], **extra}
    return parse_specs({"todoist": body})[0]


def task(identifier: str, content: str, **extra: object) -> dict[str, object]:
    return {**TASKS[0], "id": identifier, "content": content, "due": None, "labels": [], **extra}


def test_every_open_task_in_every_project_becomes_an_item(tmp_path: Path, fixture: Path) -> None:
    write(fixture)
    poll = build_connector(todoist_spec(tmp_path)).poll("")
    assert poll.complete and poll.problems == ()
    first, second = poll.items
    assert (first.external_id, first.title, first.due, first.excerpt) == (
        "6Xm5HVVRcX00",
        "Renew the domain",
        "2026-11-12",
        "The registrar emails a reminder first",
    )
    assert first.meta == {
        "project": "Home",
        "project_id": "6Jf8VQXxpwv56VQ7",
        "labels": "admin",
        "priority": "p1",
        "added_at": "2026-11-01T09:00:00.000000Z",
        "added_by": "1001",
    }
    assert first.url == "https://app.todoist.com/app/task/renew-the-domain-6Xm5HVVRcX00"
    assert second.due == "2026-11-20" and second.meta["project"] == "Work"
    assert second.meta["assignee"] == "1002" and second.meta["priority"] == "p4"


def test_a_list_cut_before_its_last_page_marks_nothing_gone(tmp_path: Path, fixture: Path) -> None:
    write(fixture, next_cursor="abc")
    poll = build_connector(todoist_spec(tmp_path)).poll("")
    assert not poll.complete and "last page" in poll.problems[0] and len(poll.items) == 2


def test_one_bad_task_doesnt_hide_the_others(tmp_path: Path, fixture: Path) -> None:
    write(fixture, tasks=[*TASKS, {"id": "x9", "content": "  "}, {"content": "no id"}])
    poll = build_connector(todoist_spec(tmp_path)).poll("")
    assert not poll.complete and len(poll.problems) == 2 and len(poll.items) == 2


def test_a_failed_project_list_records_nothing_and_changes_nothing(tmp_path: Path, fixture: Path) -> None:
    spec = todoist_spec(tmp_path)
    write(fixture)
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    [first, _] = ledger.items()
    ledger.move(first.id, ItemState.IGNORED, "owner", "not now")
    write(fixture, projects_fail=True)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    assert ledger.get(first.id).state is ItemState.IGNORED
    assert ledger.source("todoist").failures == 1 and "projects" in ledger.source("todoist").last_error


def test_ticking_off_a_recurring_task_never_reopens_or_closes_it(tmp_path: Path, fixture: Path) -> None:
    spec = todoist_spec(tmp_path)
    every_day = {"date": "2026-11-08", "string": "every day", "isRecurring": True, "datetime": None}
    vitamins = task("r1", "Take vitamins", due=every_day, updatedAt="2026-11-07T08:00:00Z")
    write(fixture, tasks=[vitamins])
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    [item] = ledger.items()
    assert (item.due, item.meta["recurring"]) == ("every day", "true")
    ledger.move(item.id, ItemState.IGNORED, "owner", "habit, not a task")
    next_day = {**vitamins, "due": {**every_day, "date": "2026-11-09"}, "updatedAt": "2026-11-08T08:00:00Z"}
    write(fixture, tasks=[next_day])
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    assert ledger.get(item.id).state is ItemState.IGNORED


def test_a_deleted_task_stays_open_until_someone_decides(tmp_path: Path, fixture: Path) -> None:
    write(fixture, views={"d1": {**TASKS[0], "id": "d1", "checked": False, "isDeleted": True}})
    connector = build_connector(todoist_spec(tmp_path))
    assert connector.confirm("d1") is Upstream.UNKNOWN
    assert connector.confirm("gone1") is Upstream.UNKNOWN


def test_a_deadline_is_kept_beside_a_due_date(tmp_path: Path, fixture: Path) -> None:
    both = task("dd1", "File taxes", due={"date": "2026-11-10", "isRecurring": False}, deadline={"date": "2026-11-15"})
    write(fixture, tasks=[both])
    [item] = build_connector(todoist_spec(tmp_path)).poll("").items
    assert (item.due, item.meta["deadline"]) == ("2026-11-10", "2026-11-15")


def test_a_task_closes_only_when_todoist_says_it_was_completed(tmp_path: Path, fixture: Path) -> None:
    spec = todoist_spec(tmp_path)
    staying = [*TASKS, *(task(f"f{n}", f"Filler {n}") for n in range(4))]
    tasks = [task("a1", "Pay rent"), task("a2", "Call the bank"), task("a3", "Order keys"), *staying]
    write(fixture, tasks=tasks)
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    views = {"a1": {**tasks[0], "checked": True}, "a2": {**tasks[1], "checked": False}}
    write(fixture, tasks=staying, views=views)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    states = {item.external_id: (item.state, item.missing_since is not None) for item in ledger.items()}
    assert states["a1"] == (ItemState.DONE, True)
    assert states["a2"] == (ItemState.NEW, True)
    assert states["a3"] == (ItemState.NEW, True)
    assert connector.confirm("a3") is Upstream.UNKNOWN


def test_new_tasks_are_flagged_once_per_poll_after_the_first(tmp_path: Path, fixture: Path) -> None:
    spec = todoist_spec(tmp_path, announce_new=True, channel="discord:tasks")
    write(fixture)
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    sent: list[tuple[str, str]] = []
    poll_sources(ledger, [spec], {spec.name: connector}, 300, lambda channel, text: sent.append((channel, text)))
    assert sent == []
    write(fixture, tasks=[*TASKS, task("b1", "Added by an agent"), task("b2", "Added on the phone")])
    poll_sources(ledger, [spec], {spec.name: connector}, 300, lambda channel, text: sent.append((channel, text)))
    [(channel, text)] = sent
    handles = {item.external_id: item.handle for item in ledger.items()}
    assert channel == "discord:tasks"
    assert text == (
        "New in todoist since the last poll:\n"
        f"- Added by an agent ({handles['b1']})\n"
        f"- Added on the phone ({handles['b2']})"
    )
    poll_sources(ledger, [spec], {spec.name: connector}, 300, lambda channel, text: sent.append((channel, text)))
    assert len(sent) == 1


def test_todoist_can_be_marked_as_the_owners_own_words() -> None:
    [spec] = parse_specs({"todoist": {"type": "todoist", "trusted": True}})
    assert spec.trusted
    with pytest.raises(ConnectorConfigError, match="announce_new"):
        parse_specs({"todoist": {"type": "todoist", "announce_new": "yes"}})


def test_a_task_first_seen_in_a_failing_poll_is_still_flagged_later(tmp_path: Path, fixture: Path) -> None:
    spec = todoist_spec(tmp_path, announce_new=True, channel="discord:tasks")
    write(fixture)
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    sent: list[tuple[str, str]] = []
    poll_sources(ledger, [spec], {spec.name: connector}, 300, lambda channel, text: sent.append((channel, text)))
    write(fixture, tasks=[*TASKS, task("c1", "Added during a glitch"), {"content": "no id"}])
    poll_sources(ledger, [spec], {spec.name: connector}, 300, lambda channel, text: sent.append((channel, text)))
    assert len(sent) == 1 and "Added during a glitch" in sent[0][1]


def test_a_burst_of_new_tasks_is_cut_to_twenty_lines(tmp_path: Path, fixture: Path) -> None:
    spec = todoist_spec(tmp_path, announce_new=True, channel="discord:tasks")
    write(fixture)
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    sent: list[tuple[str, str]] = []
    poll_sources(ledger, [spec], {spec.name: connector}, 300, lambda channel, text: sent.append((channel, text)))
    write(fixture, tasks=[*TASKS, *(task(f"n{number}", f"New {number}") for number in range(25))])
    poll_sources(ledger, [spec], {spec.name: connector}, 300, lambda channel, text: sent.append((channel, text)))
    [(_, text)] = sent
    assert text.count("\n- New ") == 20 and "and 5 more, see superagent ledger list --connector todoist" in text


def test_announce_new_needs_somewhere_to_post() -> None:
    with pytest.raises(ConnectorConfigError, match="needs a channel"):
        parse_specs({"todoist": {"type": "todoist", "announce_new": True}})


def test_setup_writes_a_todoist_block_that_parses() -> None:
    choices = Choices(hermes="/h/hermes", alerts="discord:1", todoist=True)
    [spec] = parse_config(starter_config(choices)).connectors
    assert (spec.type, spec.announce_new, spec.channel) == ("todoist", True, "discord:1")
