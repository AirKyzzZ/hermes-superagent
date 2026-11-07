from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from superagent.connectors import ConnectorConfigError, ConnectorSpec, build_connector, parse_specs
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
        "labels": "INBOX,UNREAD,CATEGORY_UPDATES",
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


def test_mail_is_never_trusted_wholesale_and_needs_an_account() -> None:
    with pytest.raises(ConnectorConfigError, match="trusted"):
        parse_specs({"gmail": {"type": "gmail", "account": ACCOUNT, "trusted": True}})
    with pytest.raises(ConnectorConfigError, match="account"):
        parse_specs({"gmail": {"type": "gmail"}})


def chat(*messages: dict[str, Any], nested: bool = False) -> dict[str, Any]:
    data: Any = {"messages": list(messages)} if nested else list(messages)
    return {"success": True, "data": data, "error": None}


def message(text: str, *, from_me: bool, at: str, sender: str = "A Friend") -> dict[str, Any]:
    return {
        **MESSAGES["data"][0],
        "Text": text,
        "DisplayText": text,
        "FromMe": from_me,
        "Timestamp": at,
        "SenderName": sender,
    }


def test_a_chat_waits_while_its_last_message_isnt_yours(tmp_path: Path, wacli: Path) -> None:
    replied = chat(message("Sounds good", from_me=True, at="2026-11-07T21:00:00+01:00"), nested=True)
    write(wacli, chats={FRIEND: MESSAGES, COLLEAGUE: replied})
    poll = build_connector(whatsapp_spec(tmp_path, [FRIEND, COLLEAGUE])).poll("")
    assert poll.complete
    [item] = poll.items
    assert (item.external_id, item.title, item.excerpt) == (
        FRIEND,
        "A Friend is waiting for a reply",
        "Are we still on for Sunday?",
    )
    assert item.meta["last_at"] == "2026-11-07T20:15:00+01:00"


def test_replying_closes_the_chat_and_a_failing_chat_marks_nothing_gone(tmp_path: Path, wacli: Path) -> None:
    spec = whatsapp_spec(tmp_path, [FRIEND, COLLEAGUE])
    waiting = chat(message("Can you call me?", from_me=False, at="2026-11-07T19:00:00+01:00", sender="A Colleague"))
    write(wacli, chats={FRIEND: MESSAGES, COLLEAGUE: waiting})
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    write(wacli, chats={FRIEND: MESSAGES, COLLEAGUE: waiting}, fail=[FRIEND])
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    assert all(item.missing_since is None for item in ledger.items())
    reply = chat(message("Calling now", from_me=True, at="2026-11-07T19:05:00+01:00"))
    write(wacli, chats={FRIEND: MESSAGES, COLLEAGUE: reply})
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    states = {item.external_id: item.state for item in ledger.items()}
    assert states == {FRIEND: ItemState.NEW, COLLEAGUE: ItemState.DONE}


def test_whatsapp_watches_only_the_chats_listed_by_jid() -> None:
    for chats in ([], ["A Friend"], "33600000001@s.whatsapp.net"):
        with pytest.raises(ConnectorConfigError, match="chats"):
            parse_specs({"whatsapp": {"type": "whatsapp", "chats": chats}})
    with pytest.raises(ConnectorConfigError, match="trusted"):
        parse_specs({"whatsapp": {"type": "whatsapp", "chats": [FRIEND], "trusted": True}})
