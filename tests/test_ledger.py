from __future__ import annotations

import json
import random
import sqlite3
import time
from pathlib import Path

import pytest

from superagent.cli import main
from superagent.ledger import ItemState, Ledger, LedgerError, SourceItem, Upstream


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "superagent.db")


def task(external_id: str = "t1", title: str = "Renew passport", upstream: Upstream = Upstream.OPEN) -> SourceItem:
    return SourceItem(external_id, title, url=f"https://example.test/{external_id}", upstream=upstream)


def row_count(ledger: Ledger) -> int:
    with sqlite3.connect(ledger.path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0])


def test_a_new_item_is_new_with_a_short_handle(ledger: Ledger) -> None:
    item = ledger.record("todoist", task())
    assert item.state is ItemState.NEW
    assert item.handle.startswith("it-") and len(item.handle) == 7
    assert ledger.get(item.handle).id == "todoist:t1"
    assert [event.note for event in ledger.history(item.handle)] == ["first seen"]


def test_recording_the_same_item_again_changes_nothing(ledger: Ledger) -> None:
    ledger.record("todoist", task())
    ledger.record("todoist", task())
    assert row_count(ledger) == 1
    assert len(ledger.history("todoist:t1")) == 1


def test_a_change_at_the_source_is_kept_with_its_content(ledger: Ledger) -> None:
    ledger.record("todoist", task())
    ledger.record("todoist", task(title="Renew passport before May"))
    history = ledger.history("todoist:t1")
    assert history[-1].note == "changed at the source"
    assert history[0].content and history[0].content["title"] == "Renew passport"
    assert history[-1].content and history[-1].content["title"] == "Renew passport before May"


def test_vanishing_never_closes_an_item(ledger: Ledger) -> None:
    ledger.record("todoist", task("t1"))
    ledger.record("todoist", task("t2"))
    [gone] = ledger.mark_missing("todoist", ["t2"])
    assert gone.id == "todoist:t1"
    assert gone.state is ItemState.NEW and gone.missing_since is not None
    assert ledger.mark_missing("todoist", ["t2"]) == []
    assert ledger.history("todoist:t1")[-1].note == "gone from the source"
    back = ledger.record("todoist", task("t1"))
    assert back.missing_since is None and back.state is ItemState.NEW


def test_only_a_confirmed_close_closes_a_vanished_item(ledger: Ledger) -> None:
    ledger.record("github", task("i1"))
    ledger.mark_missing("github", [])
    for answer in (Upstream.OPEN, Upstream.UNKNOWN):
        assert ledger.confirm("github:i1", answer, "connector:github").state is ItemState.NEW
    assert ledger.confirm("github:i1", Upstream.CLOSED, "connector:github").state is ItemState.DONE


def test_a_close_at_the_source_closes_and_a_reopen_reopens(ledger: Ledger) -> None:
    ledger.record("github", task("i1"))
    assert ledger.record("github", task("i1", upstream=Upstream.CLOSED)).state is ItemState.DONE
    reopened = ledger.record("github", task("i1"))
    assert reopened.state is ItemState.NEW
    assert ledger.history("github:i1")[-1].note == "reopened at the source"


def test_an_owner_close_holds_until_the_source_changes(ledger: Ledger) -> None:
    ledger.record("todoist", task())
    ledger.move("todoist:t1", ItemState.DONE, "owner", "did it on paper")
    assert ledger.record("todoist", task()).state is ItemState.DONE
    assert ledger.record("todoist", task(title="Renew passport, new form")).state is ItemState.NEW


def test_ignoring_needs_a_reason(ledger: Ledger) -> None:
    ledger.record("gmail", task("m1"))
    with pytest.raises(LedgerError, match="reason"):
        ledger.move("gmail:m1", ItemState.IGNORED, "rule:newsletters", " ")
    assert ledger.move("gmail:m1", ItemState.IGNORED, "rule:newsletters", "newsletter").reason == "newsletter"


@pytest.mark.parametrize(
    ("target", "actor"),
    [
        (ItemState.DONE, "worker:tk-1"),
        (ItemState.DONE, "model"),
        (ItemState.DONE, "rule:auto"),
        (ItemState.IGNORED, "worker:tk-1"),
        (ItemState.ASSIGNED, "model"),
    ],
)
def test_who_may_set_a_state_is_enforced(ledger: Ledger, target: ItemState, actor: str) -> None:
    ledger.record("github", task("i1"))
    with pytest.raises(LedgerError):
        ledger.move("github:i1", target, actor, "because")
    assert ledger.get("github:i1").state is ItemState.NEW


def test_a_worker_can_only_hand_its_own_item_back_to_the_owner(ledger: Ledger) -> None:
    ledger.record("github", task("i1"))
    ledger.move("github:i1", ItemState.ASSIGNED, "owner", "go", ticket_id="tk-1")
    with pytest.raises(LedgerError, match="doesn't hold"):
        ledger.move("github:i1", ItemState.WAITING, "worker:tk-2", "PR ready")
    assert ledger.move("github:i1", ItemState.WAITING, "worker:tk-1", "PR ready").state is ItemState.WAITING


def test_new_activity_ends_a_snooze(ledger: Ledger) -> None:
    ledger.record("todoist", task())
    assert ledger.snooze("todoist:t1", time.time() + 86400, "owner").snoozed_until
    assert ledger.record("todoist", task(title="Renew passport, urgent")).snoozed_until is None
    with pytest.raises(LedgerError):
        ledger.snooze("todoist:t1", time.time() - 1, "owner")


def test_nothing_in_the_ledger_ever_deletes_an_item(ledger: Ledger) -> None:
    generator = random.Random(7)
    seen = 0
    for step in range(400):
        external_id = f"x{generator.randrange(40)}"
        choice = generator.random()
        if choice < 0.5:
            upstream = Upstream.CLOSED if generator.random() < 0.2 else Upstream.OPEN
            ledger.record("mix", task(external_id, title=f"t{generator.randrange(3)}", upstream=upstream))
        elif choice < 0.7:
            ledger.mark_missing("mix", [f"x{n}" for n in range(40) if generator.random() < 0.5])
        elif seen:
            item = generator.choice(ledger.items())
            target = generator.choice([ItemState.TRIAGED, ItemState.DONE, ItemState.IGNORED, ItemState.WAITING])
            ledger.move(item.id, target, "owner", f"step {step}")
        current = row_count(ledger)
        assert current >= seen
        seen = current
    assert all(ledger.history(item.id) for item in ledger.items())


def test_handles_stay_unique_across_many_items(ledger: Ledger) -> None:
    for index in range(300):
        ledger.record("bulk", task(f"n{index}"))
    handles = [item.handle for item in ledger.items()]
    assert len(set(handles)) == 300


def test_the_cli_lists_shows_closes_and_ignores(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger = Ledger(tmp_path / "superagent.db")
    first = ledger.record("todoist", task("t1"))
    second = ledger.record("todoist", task("t2", title="Call the bank"))
    home = ["--home", str(tmp_path)]
    assert main([*home, "ledger", "list"]) == 0
    listed = capsys.readouterr().out
    assert first.handle in listed and "Call the bank" in listed
    assert main([*home, "ledger", "close", first.handle]) == 0
    with pytest.raises(SystemExit):
        main([*home, "ledger", "ignore", second.handle])
    capsys.readouterr()
    assert main([*home, "ledger", "ignore", second.handle, "--reason", "done by phone"]) == 0
    capsys.readouterr()
    assert main([*home, "ledger", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main([*home, "ledger", "show", second.handle]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["item"]["state"] == "ignored"
    assert [event["note"] for event in shown["history"]] == ["first seen", "done by phone"]
    assert main([*home, "ledger", "show", "it-nope"]) == 1


@pytest.mark.parametrize(
    ("setup", "target", "actor"),
    [
        (ItemState.TRIAGED, ItemState.IGNORED, "model"),
        (ItemState.TRIAGED, ItemState.IGNORED, "rule:newsletters"),
        (ItemState.DONE, ItemState.WAITING, "worker:tk-1"),
        (ItemState.DONE, ItemState.NEW, "connector:github"),
        (ItemState.TRIAGED, ItemState.NEW, "owner"),
    ],
)
def test_transitions_depend_on_the_current_state(
    ledger: Ledger, setup: ItemState, target: ItemState, actor: str
) -> None:
    ledger.record("github", task("i1"))
    ledger.move("github:i1", setup, "owner", "set up")
    with pytest.raises(LedgerError):
        ledger.move("github:i1", target, actor, "because")
    assert ledger.get("github:i1").state is setup


def test_a_late_model_suggestion_cannot_override_the_owner(ledger: Ledger) -> None:
    ledger.record("gmail", task("m1"))
    ledger.move("gmail:m1", ItemState.TRIAGED, "owner", "mine")
    with pytest.raises(LedgerError):
        ledger.move("gmail:m1", ItemState.IGNORED, "model", "looks like a newsletter", expected=ItemState.NEW)
    assert ledger.get("gmail:m1").state is ItemState.TRIAGED


def test_only_the_items_own_connector_can_confirm_it(ledger: Ledger) -> None:
    ledger.record("todoist", task())
    with pytest.raises(LedgerError):
        ledger.confirm("todoist:t1", Upstream.CLOSED, "connector:github")
    assert ledger.get("todoist:t1").is_open


def test_a_confirmed_close_reopens_when_the_item_comes_back_open(ledger: Ledger) -> None:
    ledger.record("gmail", task("m1"))
    ledger.mark_missing("gmail", [])
    closed = ledger.confirm("gmail:m1", Upstream.CLOSED, "connector:gmail")
    assert closed.state is ItemState.DONE and closed.upstream is Upstream.CLOSED
    assert ledger.record("gmail", task("m1")).state is ItemState.NEW


def test_an_owner_closed_item_that_vanished_and_came_back_reopens(ledger: Ledger) -> None:
    ledger.record("github", task("i1"))
    ledger.move("github:i1", ItemState.DONE, "owner", "not mine")
    ledger.mark_missing("github", [])
    assert ledger.record("github", task("i1")).state is ItemState.NEW


def test_only_the_owner_snoozes_and_a_move_or_vanish_ends_it(ledger: Ledger) -> None:
    ledger.record("todoist", task("t1"))
    ledger.record("todoist", task("t2"))
    later = time.time() + 86400
    with pytest.raises(LedgerError):
        ledger.snooze("todoist:t1", later, "worker:tk-1")
    ledger.snooze("todoist:t1", later, "owner")
    assert ledger.move("todoist:t1", ItemState.TRIAGED, "owner", "today").snoozed_until is None
    ledger.snooze("todoist:t2", later, "owner")
    ledger.mark_missing("todoist", ["t1"])
    assert ledger.get("todoist:t2").snoozed_until is None


def test_closing_needs_a_reason(ledger: Ledger) -> None:
    ledger.record("todoist", task())
    with pytest.raises(LedgerError, match="reason"):
        ledger.move("todoist:t1", ItemState.DONE, "owner", "")


def test_connector_names_cannot_make_ids_collide(ledger: Ledger) -> None:
    ledger.record("a", task("b:c"))
    with pytest.raises(LedgerError):
        ledger.record("a:b", task("c"))
    assert ledger.get("a:b:c").external_id == "b:c"


def test_handles_work_whatever_the_case(ledger: Ledger) -> None:
    item = ledger.record("todoist", task())
    assert ledger.get(item.handle.upper()).id == item.id


def test_an_area_change_is_logged(ledger: Ledger) -> None:
    ledger.record("github", task("i1"))
    moved = ledger.move("github:i1", ItemState.TRIAGED, "rule:work-repos", "work repo", area="work")
    assert moved.area == "work"
    assert ledger.history("github:i1")[-1].area == "work"


def test_vanished_items_can_be_listed_for_a_retry(ledger: Ledger) -> None:
    ledger.record("github", task("i1"))
    ledger.record("github", task("i2"))
    ledger.mark_missing("github", ["i2"])
    assert [item.id for item in ledger.items(missing=True)] == ["github:i1"]
