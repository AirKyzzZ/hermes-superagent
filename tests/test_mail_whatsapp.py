from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from superagent.connectors import ConnectorConfigError, ConnectorError, ConnectorSpec, build_connector, parse_specs
from superagent.ledger import ItemState, Ledger, Upstream
from superagent.polling import poll_sources

TESTS = Path(__file__).parent
SEARCH = json.loads((TESTS / "fixtures" / "gog_gmail_search.synthetic.json").read_text())
THREAD = json.loads((TESTS / "fixtures" / "gog_gmail_thread.synthetic.json").read_text())
MESSAGES = json.loads((TESTS / "fixtures" / "wacli_messages.synthetic.json").read_text())
FRIEND, COLLEAGUE = "33600000001@s.whatsapp.net", "33600000002@s.whatsapp.net"
ACCOUNT = "owner@example.com"


def wrapper(tmp_path: Path, name: str, fake: str) -> str:
    path = tmp_path / name
    path.write_text(f'#!/bin/sh\nexec {sys.executable} {TESTS / fake} "$@"\n', encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def gog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "gog.json"
    monkeypatch.setenv("FAKE_GOG_FIXTURE", str(path))
    return path


@pytest.fixture
def wacli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "wacli.json"
    monkeypatch.setenv("FAKE_WACLI_FIXTURE", str(path))
    return path


def write(path: Path, **data: Any) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def gmail_spec(tmp_path: Path, **extra: object) -> ConnectorSpec:
    body = {"type": "gmail", "every": "0s", "account": ACCOUNT, "gog": wrapper(tmp_path, "gog", "fake_gog.py")}
    return parse_specs({"gmail": {**body, "env": ["FAKE_GOG_FIXTURE"], **extra}})[0]


def whatsapp_spec(tmp_path: Path, chats: list[str]) -> ConnectorSpec:
    body = {"type": "whatsapp", "every": "0s", "chats": chats, "wacli": wrapper(tmp_path, "wacli", "fake_wacli.py")}
    return parse_specs({"whatsapp": {**body, "env": ["FAKE_WACLI_FIXTURE"]}})[0]


def search(*threads: dict[str, Any], **envelope: Any) -> dict[str, Any]:
    return {"threads": list(threads), "nextPageToken": "", "totalMatches": len(threads), **envelope}


def thread(identifier: str, subject: str = "Hello") -> dict[str, Any]:
    return {**SEARCH["threads"][0], "id": identifier, "subject": subject}


def test_every_inbox_thread_becomes_an_item(tmp_path: Path, gog: Path) -> None:
    write(gog, search=SEARCH)
    poll = build_connector(gmail_spec(tmp_path)).poll("")
    assert poll.complete and len(poll.items) == 2
    first = poll.items[0]
    assert (first.external_id, first.title, first.updated) == (
        "18c2f0a1b2c3d4e5",
        "Your domain expires in 7 days",
        "2026-11-06T18:04:11+01:00",
    )
    assert first.meta == {
        "account": ACCOUNT,
        "from": "Registrar Support <support@registrar.example>",
        "labels": "INBOX,CATEGORY_UPDATES",
        "messages": "1",
    }
    assert first.url == f"https://mail.google.com/mail/u/?authuser={ACCOUNT}#all/18c2f0a1b2c3d4e5"


@pytest.mark.parametrize(
    ("envelope", "problem"),
    [
        ({"nextPageToken": "abc"}, "last page"),
        ({"totalMatches": 3}, "counted 3 threads but returned 2"),
        ({"totalMatches": None, "totalMatchesAtLeast": 2}, "no exact count"),
    ],
)
def test_an_inbox_that_cant_prove_its_whole_marks_nothing_gone(
    tmp_path: Path, gog: Path, envelope: dict[str, Any], problem: str
) -> None:
    write(gog, search={**SEARCH, **envelope})
    poll = build_connector(gmail_spec(tmp_path)).poll("")
    assert not poll.complete and any(problem in found for found in poll.problems), poll.problems


def test_a_thread_closes_only_once_it_leaves_the_inbox(tmp_path: Path, gog: Path) -> None:
    staying = [thread(f"aa{n}") for n in range(5)]
    spec = gmail_spec(tmp_path)
    write(gog, search=search(thread("bb1", "Archived"), thread("bb2", "Moved back"), *staying))
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    still_inbox = copy.deepcopy(THREAD)
    still_inbox["thread"]["messages"][0]["labelIds"].append("INBOX")
    write(gog, search=search(*staying), threads={"bb1": THREAD, "bb2": still_inbox})
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    states = {item.external_id: item.state for item in ledger.items() if item.external_id.startswith("bb")}
    assert states == {"bb1": ItemState.DONE, "bb2": ItemState.NEW}
    assert connector.confirm("ffffff") is Upstream.UNKNOWN


def test_reading_a_mail_never_reopens_a_thread(tmp_path: Path, gog: Path) -> None:
    spec = gmail_spec(tmp_path)
    write(gog, search=search(thread("cc1", "Weekly newsletter")))
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    [item] = ledger.items()
    ledger.move(item.id, ItemState.IGNORED, "owner", "newsletter")
    read = {**thread("cc1", "Weekly newsletter"), "labels": ["INBOX", "CATEGORY_UPDATES"]}
    write(gog, search=search(read))
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    assert ledger.get(item.id).state is ItemState.IGNORED


def test_mail_is_never_trusted_wholesale_and_needs_an_account() -> None:
    with pytest.raises(ConnectorConfigError, match="trusted"):
        parse_specs({"gmail": {"type": "gmail", "account": ACCOUNT, "trusted": True}})
    with pytest.raises(ConnectorConfigError, match="account"):
        parse_specs({"gmail": {"type": "gmail"}})
    with pytest.raises(ConnectorConfigError, match="in:inbox"):
        parse_specs({"gmail": {"type": "gmail", "account": ACCOUNT, "query": "is:unread"}})


DOCTOR = json.loads((TESTS / "fixtures" / "wacli_doctor.synthetic.json").read_text())


def chat(*messages: dict[str, Any], has_more: bool = False) -> dict[str, Any]:
    page = {"has_more": has_more, "next_cursor": "c1" if has_more else ""}
    data = {"messages": list(messages), "fts": False, "page": page, "source": {}}
    return {"success": True, "data": data, "error": None}


def message(text: str, *, from_me: bool, at: str, sender: str = "A Friend", **extra: Any) -> dict[str, Any]:
    base = MESSAGES["data"]["messages"][0]
    fields = {"Text": text, "DisplayText": text, "FromMe": from_me, "Timestamp": at, "SenderName": sender}
    return {**base, **fields, **extra}


def fresh_doctor(**fields: Any) -> dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    store = {**DOCTOR["data"]["store"], "last_sync_at": now, "last_activity_at": now}
    return {**DOCTOR, "data": {**DOCTOR["data"], "store": store, **fields}}


def write_wacli(path: Path, chats: dict[str, Any], **extra: Any) -> None:
    write(path, chats=chats, doctor=extra.pop("doctor", fresh_doctor()), **extra)


def test_a_chat_waits_while_its_last_message_isnt_yours(tmp_path: Path, wacli: Path) -> None:
    replied = chat(message("Sounds good", from_me=True, at="2026-11-07T20:00:00Z"))
    write_wacli(wacli, {FRIEND: MESSAGES, COLLEAGUE: replied})
    poll = build_connector(whatsapp_spec(tmp_path, [FRIEND, COLLEAGUE])).poll("")
    assert poll.complete
    [item] = poll.items
    assert (item.external_id, item.title, item.excerpt) == (
        FRIEND,
        "A Friend is waiting for a reply",
        "Are we still on for Sunday?",
    )
    assert item.meta["last_at"] == "2026-11-07T19:15:00Z"


def call(outcome: str, *, from_me: bool, at: str, kind: str = "audio") -> dict[str, Any]:
    return message("", from_me=from_me, at=at, DisplayText=f"WhatsApp {kind} call {outcome}")


@pytest.mark.parametrize(
    "last",
    [
        message("", from_me=True, at="2026-11-07T10:05:00Z", ReactionToID="3EB0", ReactionEmoji="+1"),
        call("missed", from_me=True, at="2026-11-07T10:05:00Z"),
        call("rejected", from_me=True, at="2026-11-07T10:05:00Z", kind="video"),
        message("Poll vote", from_me=True, at="2026-11-07T10:05:00Z"),
        message("Poll option added", from_me=True, at="2026-11-07T10:05:00Z"),
        message("(message)", from_me=True, at="2026-11-07T10:05:00Z"),
    ],
)
def test_nothing_but_a_real_message_counts_as_your_reply(tmp_path: Path, wacli: Path, last: dict[str, Any]) -> None:
    question = message("Send the contract by Friday?", from_me=False, at="2026-11-07T10:00:00Z")
    write_wacli(wacli, {FRIEND: chat(last, question)})
    assert [item.external_id for item in build_connector(whatsapp_spec(tmp_path, [FRIEND])).poll("").items] == [FRIEND]


def test_their_missed_call_opens_the_chat_and_their_reaction_doesnt(tmp_path: Path, wacli: Path) -> None:
    answered = message("Here it is", from_me=True, at="2026-11-07T09:00:00Z")
    heart = message("", from_me=False, at="2026-11-07T09:01:00Z", ReactionToID="3EB1", ReactionEmoji="<3")
    rang = call("missed", from_me=False, at="2026-11-07T09:02:00Z", kind="video")
    write_wacli(wacli, {FRIEND: chat(rang, answered), COLLEAGUE: chat(heart, answered)})
    poll = build_connector(whatsapp_spec(tmp_path, [FRIEND, COLLEAGUE])).poll("")
    assert [item.external_id for item in poll.items] == [FRIEND]


def test_a_window_of_only_reactions_is_a_problem_when_more_history_exists(tmp_path: Path, wacli: Path) -> None:
    thumbs = [
        message("", from_me=False, at="2026-11-07T10:00:00Z", ReactionToID="x", ReactionEmoji="+") for _ in range(20)
    ]
    write_wacli(wacli, {FRIEND: chat(*thumbs, has_more=True)})
    poll = build_connector(whatsapp_spec(tmp_path, [FRIEND])).poll("")
    assert not poll.complete and "who spoke last is unknown" in poll.problems[0]


def test_replying_closes_the_chat_and_a_failing_chat_marks_nothing_gone(tmp_path: Path, wacli: Path) -> None:
    spec = whatsapp_spec(tmp_path, [FRIEND, COLLEAGUE])
    waiting = chat(message("Can you call me?", from_me=False, at="2026-11-07T18:00:00Z", sender="A Colleague"))
    write_wacli(wacli, {FRIEND: MESSAGES, COLLEAGUE: waiting})
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    write_wacli(wacli, {FRIEND: MESSAGES, COLLEAGUE: waiting}, fail=[FRIEND])
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    assert all(item.missing_since is None for item in ledger.items())
    reply = chat(
        message("Calling now", from_me=True, at="2026-11-07T18:05:00Z"),
        message("Can you call me?", from_me=False, at="2026-11-07T18:00:00Z"),
    )
    write_wacli(wacli, {FRIEND: MESSAGES, COLLEAGUE: reply})
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    states = {item.external_id: item.state for item in ledger.items()}
    assert states == {FRIEND: ItemState.NEW, COLLEAGUE: ItemState.DONE}


def test_a_chat_with_no_stored_messages_blocks_nothing(tmp_path: Path, wacli: Path) -> None:
    write_wacli(wacli, {FRIEND: MESSAGES})
    poll = build_connector(whatsapp_spec(tmp_path, [FRIEND, COLLEAGUE])).poll("")
    assert poll.complete and [item.external_id for item in poll.items] == [FRIEND]


@pytest.mark.parametrize(
    ("doctor", "problem"),
    [
        ({"lock_held": False}, "sync isn't running"),
        (
            {"store": {"last_sync_at": "2026-11-07T00:00:00Z", "last_activity_at": "2026-10-01T00:00:00Z"}},
            "stuck reconnecting",
        ),
        ({"authenticated": False}, "isn't linked"),
        ({"session_revoked": True}, "isn't linked"),
    ],
)
def test_a_dead_or_unlinked_sync_fails_the_poll(
    tmp_path: Path, wacli: Path, doctor: dict[str, Any], problem: str
) -> None:
    write_wacli(wacli, {FRIEND: MESSAGES}, doctor=fresh_doctor(**doctor))
    with pytest.raises(ConnectorError, match=problem):
        build_connector(whatsapp_spec(tmp_path, [FRIEND])).poll("")


def test_a_timer_driven_sync_must_be_recent(tmp_path: Path, wacli: Path) -> None:
    stale = {**DOCTOR["data"], "lock_held": False, "store": {"last_sync_at": "2026-10-01T00:00:00Z"}}
    write_wacli(wacli, {FRIEND: MESSAGES}, doctor={**DOCTOR, "data": stale})
    spec = parse_specs(
        {
            "whatsapp": {
                "type": "whatsapp",
                "chats": [FRIEND],
                "sync": "once",
                "wacli": wrapper(tmp_path, "wacli", "fake_wacli.py"),
                "env": ["FAKE_WACLI_FIXTURE"],
            }
        }
    )[0]
    with pytest.raises(ConnectorError, match="older than sync_max_age"):
        build_connector(spec).poll("")


def test_whatsapp_watches_only_the_chats_listed_by_jid() -> None:
    for chats in ([], ["A Friend"], "33600000001@s.whatsapp.net", ["news@newsletter"]):
        with pytest.raises(ConnectorConfigError, match="chats"):
            parse_specs({"whatsapp": {"type": "whatsapp", "chats": chats}})
    with pytest.raises(ConnectorConfigError, match="trusted"):
        parse_specs({"whatsapp": {"type": "whatsapp", "chats": [FRIEND], "trusted": True}})
