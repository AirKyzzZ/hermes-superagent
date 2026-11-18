from __future__ import annotations

import argparse
import http.client
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from superagent.cli import run, setup_parser
from superagent.config import DB_FILENAME, Config, load_config
from superagent.domain import Mandate, RepairAction, ReportState, Strength, TicketStatus, Trigger
from superagent.ledger import ItemState, Ledger, SourceItem
from superagent.panel import (
    ACTION_BODY_LIMIT,
    HOST_SHOWN_CHARS,
    TOKEN_FILENAME,
    TOKEN_HEADER,
    allowed_hosts,
    host_allowed,
    panel_handler,
    panel_token,
    snapshot,
    ticket_detail,
)
from superagent.router import Router, project_name
from superagent.store import Store

from .conftest import FakeBackend

CONFIG = """
[sources.chat]
policy = "explicit_only"
allow = ["alice"]

[worker]
argv = ["true", "{brief}"]

[connectors.vps]
type = "agents"

[connectors.tasks]
type = "command"
argv = ["true"]
fields = { id = "id", title = "title" }
"""


def request(number: int, text: str) -> Trigger:
    return Trigger(f"t{number}", "chat", f"th{number}", "alice", text, "chat:c1", True, False)


def build(home: Path) -> tuple[Router, FakeBackend, Ledger, Config]:
    (home / "superagent.toml").write_text(CONFIG, encoding="utf-8")
    config = load_config(home)
    backend = FakeBackend()
    return Router(Store(home / DB_FILENAME), config.rules, backend), backend, Ledger(home / DB_FILENAME), config


def latest(router: Router, ticket_number: int) -> tuple[str, str]:
    ticket = router.store.ticket_for_trigger(f"t{ticket_number}")
    assert ticket is not None
    assignment = next(a for a in router.store.running_assignments() if a.ticket_id == ticket.id)
    return ticket.id, assignment.id


def busy_home(home: Path) -> Config:
    router, backend, ledger, config = build(home)
    for number, text in ((1, "fix the login bug"), (2, "pick a database"), (3, "loop forever")):
        router.ingest(request(number, text))
    tokens = {assignment_id: token for _, assignment_id, token in backend.started}
    ticket, assignment = latest(router, 1)
    router.store.add_report(assignment, tokens[assignment], ReportState.PROGRESS, "sent Bearer abcdefghijklmnop")
    ticket, assignment = latest(router, 2)
    router.store.add_report(assignment, tokens[assignment], ReportState.BLOCKED, "two options", "Postgres or SQLite?")
    ticket, assignment = latest(router, 3)
    router.store.add_watchdog_event(assignment, ticket, "stuck", RepairAction.ESCALATE, "budgets spent")
    router.store.end_assignment(assignment)
    router.store.end_ticket(ticket)
    waiting = {"state": "waiting", "cwd": "/srv/code/api", "branch": "main"}
    ledger.record("vps", SourceItem("s1", "api session", meta=waiting))
    ledger.record("vps", SourceItem("s2", "web session", meta={"state": "working", "cwd": "/srv/code/web"}))
    ledger.begin_poll("tasks")
    for _ in range(3):
        ledger.poll_failed("tasks", "export cut short")
    return config


def test_without_a_database_the_snapshot_says_to_run_a_tick(tmp_path: Path) -> None:
    (tmp_path / "superagent.toml").write_text(CONFIG, encoding="utf-8")
    assert "run a tick" in str(snapshot(tmp_path, load_config(tmp_path))["missing"])


def test_what_needs_you_comes_first_and_in_order(tmp_path: Path) -> None:
    shot = snapshot(tmp_path, busy_home(tmp_path))
    attention = shot["attention"]
    assert isinstance(attention, list)
    assert [card["kind"] for card in attention] == ["escalation", "blocked", "session", "source"]
    assert attention[0]["quote"] == "budgets spent"
    assert attention[1]["quote"] == "Postgres or SQLite?"
    assert attention[2]["title"] == "api · main"
    assert "3 failed polls" in str(attention[3]["detail"])


def test_workers_flow_and_what_runs_now(tmp_path: Path) -> None:
    shot = snapshot(tmp_path, busy_home(tmp_path))
    workers = shot["workers"]
    assert isinstance(workers, list)
    assert {worker["goal"]: worker["state"] for worker in workers} == {
        "fix the login bug": "running",
        "pick a database": "parked",
    }
    now = shot["now"]
    assert isinstance(now, list)
    assert [entry.get("goal") or entry.get("project") for entry in now] == ["fix the login bug", "web"]
    flow = shot["flow"]
    assert isinstance(flow, dict)
    assert flow["workers"] == {"running": 1, "queued": 0, "parked": 1}
    assert flow["router"] == {"dispatched": 3, "forwarded": 0}
    assert flow["gate"]["admitted"] == 3
    assert flow["watchdog"]["escalations"] == 1
    assert flow["sources"] == {"polling": 2, "failing": 1}
    assert shot["feed"]


def test_text_from_reports_is_redacted(tmp_path: Path) -> None:
    shot = json.dumps(snapshot(tmp_path, busy_home(tmp_path)))
    assert "abcdefghijklmnop" not in shot
    assert "[redacted]" in shot


@contextmanager
def serving(home: Path, *hosts: str, read_only: bool = False) -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), panel_handler(home, "the-token", hosts, read_only))
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def served(tmp_path: Path) -> Iterator[int]:
    busy_home(tmp_path)
    with serving(tmp_path) as port:
        yield port


def fetch(port: int, path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_the_page_loads_without_the_token_and_locks_everything_down(served: int) -> None:
    status, headers, body = fetch(served, "/")
    assert status == 200
    assert b'src="panel.js"' in body
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "no-referrer"
    assert fetch(served, "/panel.js")[0] == 200


def test_the_snapshot_needs_the_token(served: int) -> None:
    assert fetch(served, "/api/snapshot")[0] == 403
    assert fetch(served, "/api/snapshot", {TOKEN_HEADER: "wrong"})[0] == 403
    status, _, body = fetch(served, "/api/snapshot", {TOKEN_HEADER: "the-token"})
    assert status == 200
    assert len(json.loads(body)["workers"]) == 2
    assert json.loads(body)["actions"] is True


def test_a_request_for_another_host_is_refused(served: int) -> None:
    assert fetch(served, "/", {"Host": "attacker.example"})[0] == 421
    assert fetch(served, "/api/snapshot", {"Host": "attacker.example", TOKEN_HEADER: "the-token"})[0] == 421


def test_panel_json_prints_one_snapshot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    busy_home(tmp_path)
    parser = argparse.ArgumentParser()
    setup_parser(parser)
    assert run(parser.parse_args(["--home", str(tmp_path), "panel", "--json"])) == 0
    assert len(json.loads(capsys.readouterr().out)["attention"]) == 4


def test_an_escalation_leaves_once_someone_asks_again_on_its_thread(tmp_path: Path) -> None:
    config = busy_home(tmp_path)
    router = Router(Store(tmp_path / DB_FILENAME), config.rules, FakeBackend())
    router.ingest(Trigger("t9", "chat", "th3", "alice", "try that loop again, smaller", "chat:c1", True, False))
    attention = snapshot(tmp_path, config)["attention"]
    assert isinstance(attention, list)
    assert "escalation" not in [card["kind"] for card in attention]


def test_a_database_from_an_older_version_asks_for_a_tick(tmp_path: Path) -> None:
    config = busy_home(tmp_path)
    with closing(sqlite3.connect(tmp_path / DB_FILENAME)) as connection:
        connection.execute("ALTER TABLE tickets DROP COLUMN queued_at")
        connection.commit()
    assert "run a tick" in str(snapshot(tmp_path, config)["missing"])


def test_a_token_outside_ascii_is_refused_cleanly(served: int) -> None:
    assert fetch(served, "/api/snapshot", {TOKEN_HEADER: "tökén"})[0] == 403


def test_projects_group_workers_sessions_and_repo_items(tmp_path: Path) -> None:
    config = busy_home(tmp_path)
    ledger = Ledger(tmp_path / DB_FILENAME)
    pull = "https://github.com/acme/api/pull/7"
    ledger.record("tasks", SourceItem("p7", "Add rate limits", url=pull, meta={"repo": "acme/api"}))
    store = Store(tmp_path / DB_FILENAME)
    store.create_ticket("tidy the web build", Mandate("v", "x", Strength.EXPLICIT, "alice", "chat", "t-web"), "c", "")
    shot = snapshot(tmp_path, config)
    projects = {str(project["name"]): project for project in shot["projects"]}
    assert len(projects["api"]["sessions"]) == 1
    assert [item["kind"] for item in projects["api"]["items"]] == ["pr"]
    workers = {str(worker["goal"]): worker for worker in shot["workers"]}
    assert workers["tidy the web build"]["project"] == "web"
    assert "No project" in projects


def test_dispatch_keeps_the_project_the_router_names(tmp_path: Path) -> None:
    assert project_name(" acme/api-gateway/ ") == "api-gateway"
    assert project_name("x" * 61) == ""
    assert project_name("two\nlines") == ""


def test_ticket_detail_has_the_whole_conversation(tmp_path: Path) -> None:
    busy_home(tmp_path)
    store = Store(tmp_path / DB_FILENAME)
    parked = next(ticket for ticket in store.live_tickets() if ticket.goal == "pick a database")
    assignment = store.get_assignment(next(a for a in store.ticket_reports(parked.id)).assignment_id)
    workdir = tmp_path / "workers" / parked.id / assignment.id
    workdir.mkdir(parents=True)
    (workdir / "brief.md").write_text("# Task brief\npick a database", encoding="utf-8")
    (workdir / "worker.log").write_text("\x1b[1mthinking\x1b[0m with Bearer abcdefghijklmnop", encoding="utf-8")
    detail = ticket_detail(tmp_path, parked.id)
    assert detail is not None
    kinds = [event["kind"] for event in detail["timeline"]]
    assert kinds[0] == "opened" and "blocked" in kinds
    assert detail["brief"] == "# Task brief\npick a database"
    assert detail["log"] == "thinking with Bearer [redacted]"
    assert ticket_detail(tmp_path, "../../etc") is None
    assert ticket_detail(tmp_path, "tk-00000000") is None


def test_the_ticket_endpoint_needs_the_token_and_a_real_ticket(served: int) -> None:
    assert fetch(served, "/api/ticket?id=tk-00000000")[0] == 403
    assert fetch(served, "/api/ticket?id=tk-00000000", {TOKEN_HEADER: "the-token"})[0] == 404


def test_ticket_detail_never_follows_a_link_out_of_the_worker_directory(tmp_path: Path) -> None:
    busy_home(tmp_path)
    store = Store(tmp_path / DB_FILENAME)
    running = store.running_assignments()[0]
    workdir = tmp_path / "workers" / running.ticket_id / running.id
    workdir.mkdir(parents=True)
    secret = tmp_path / "elsewhere.txt"
    secret.write_text("not the worker's", encoding="utf-8")
    (workdir / "worker.log").symlink_to(secret)
    os.mkfifo(workdir / "brief.md")
    detail = ticket_detail(tmp_path, running.ticket_id)
    assert detail is not None
    assert (detail["log"], detail["brief"]) == ("", "")


def test_the_token_survives_a_restart_until_renewed(tmp_path: Path) -> None:
    first = panel_token(tmp_path)
    assert panel_token(tmp_path) == first
    assert stat.S_IMODE((tmp_path / TOKEN_FILENAME).stat().st_mode) == 0o600
    renewed = panel_token(tmp_path, renew=True)
    assert renewed != first
    assert panel_token(tmp_path) == renewed


@pytest.mark.parametrize(
    ("allowed", "host", "status"),
    [
        ("Panel.Example.ts.net", "panel.example.ts.net", 200),
        ("panel.example.ts.net", "Panel.Example.ts.net:8443", 200),
        ("panel.example.ts.net", "other.example.ts.net", 421),
        ("panel.example.ts.net", "panel.example.ts.net:abc", 421),
        ("panel.example.ts.net:8443", "panel.example.ts.net:8443", 200),
        ("panel.example.ts.net:8443", "panel.example.ts.net:9443", 421),
        ("panel.example.ts.net:8443", "panel.example.ts.net", 421),
        ("panel.example.ts.net:443", "panel.example.ts.net", 200),
        ("panel.example.ts.net", "localhost:8443", 421),
        ("panel.example.ts.net", "127.0.0.1:8443", 421),
        ("panel.example.ts.net", "localhost", 421),
    ],
)
def test_a_name_passed_to_allow_host_is_answered_on_any_port_unless_one_is_given(
    tmp_path: Path, allowed: str, host: str, status: int
) -> None:
    with serving(tmp_path, allowed) as port:
        assert fetch(port, "/", {"Host": host})[0] == status


@pytest.mark.parametrize(
    "host",
    [
        "localhost.",
        "127.0.0.1.",
        "[::1]:{port}",
        "name@panel.example.ts.net",
        "panel.example.ts.net:08443",
        "panel.example.ts.net:65536",
        "panel.example.ts.net:0",
    ],
)
def test_a_hostile_host_is_refused(tmp_path: Path, host: str) -> None:
    with serving(tmp_path, "panel.example.ts.net") as port:
        assert fetch(port, "/", {"Host": host.format(port=port)})[0] == 421


def test_a_name_with_the_panel_port_is_answered_on_that_port_only() -> None:
    hosts = allowed_hosts(8765, ["panel.example.ts.net:8765"])
    assert host_allowed("panel.example.ts.net:8765", hosts)
    assert host_allowed("localhost:8765", hosts)
    assert not host_allowed("panel.example.ts.net", hosts)
    assert not host_allowed("panel.example.ts.net:8443", hosts)


def test_a_name_with_a_port_is_not_answered_on_the_panel_port(tmp_path: Path) -> None:
    with serving(tmp_path, "panel.example.ts.net:8443") as port:
        assert fetch(port, "/", {"Host": f"panel.example.ts.net:{port}"})[0] == 421
        assert fetch(port, "/", {"Host": f"localhost:{port}"})[0] == 200


def test_a_refused_host_is_named_next_to_the_names_the_panel_answers_to(tmp_path: Path) -> None:
    with serving(tmp_path, "panel.example.ts.net", "other.example.ts.net:8443") as port:
        status, headers, body = fetch(port, "/", {"Host": "evil.example:80"})
        assert status == 421
        assert headers["Content-Type"] == "text/plain; charset=utf-8"
        assert body.decode() == (
            f"this panel answers to 127.0.0.1:{port}, localhost:{port}, panel.example.ts.net and "
            'other.example.ts.net:8443, not "evil.example:80". --allow-host adds a name.'
        )
        assert b'not "evil\\".example"' in fetch(port, "/", {"Host": 'evil".example'})[2]
        assert b'not "' + b"a" * HOST_SHOWN_CHARS + b'"' in fetch(port, "/", {"Host": "a" * 500})[2]


@pytest.mark.parametrize(
    "entry",
    [
        "evil.example.com/path",
        "*",
        "https://panel.example.ts.net",
        "panel.example.ts.net:0",
        "panel.example.ts.net:99999",
        "panel.example.ts.net:abc",
        "panel.example.ts.net:",
    ],
)
def test_allow_host_refuses_anything_but_a_name_or_a_name_and_a_port(entry: str) -> None:
    with pytest.raises(ValueError, match="allow-host"):
        allowed_hosts(8765, [entry])


@pytest.mark.parametrize("entry", ["127.0.0.1", "10.0.0.1", "10.0.0.1:8443", "100.64.0.1"])
def test_allow_host_refuses_an_ip_address(entry: str) -> None:
    with pytest.raises(ValueError, match="never an IP address"):
        allowed_hosts(8765, [entry])


@pytest.mark.parametrize("saved", [b"\xff\xfe not text", b"a" * 31, b"a" * 40])
def test_a_broken_or_weak_saved_token_is_replaced(tmp_path: Path, saved: bytes) -> None:
    (tmp_path / TOKEN_FILENAME).write_bytes(saved)
    (tmp_path / TOKEN_FILENAME).chmod(0o600)
    token = panel_token(tmp_path)
    assert len(token) == 32 and token.encode() != saved


def test_a_token_others_can_read_is_replaced(tmp_path: Path) -> None:
    first = panel_token(tmp_path)
    (tmp_path / TOKEN_FILENAME).chmod(0o644)
    assert panel_token(tmp_path) != first
    assert stat.S_IMODE((tmp_path / TOKEN_FILENAME).stat().st_mode) == 0o600


def panel_cli(home: Path, *argv: str) -> int:
    parser = argparse.ArgumentParser()
    setup_parser(parser)
    return run(parser.parse_args(["--home", str(home), "panel", *argv]))


def test_a_bad_host_leaves_the_saved_token_alone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    token = panel_token(tmp_path)
    assert panel_cli(tmp_path, "--allow-host", "*", "--new-token") == 2
    assert panel_token(tmp_path) == token
    assert "allow-host" in capsys.readouterr().err


def test_a_token_that_cant_be_written_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / TOKEN_FILENAME).mkdir()
    assert panel_cli(tmp_path, "--new-token") == 1
    assert "panel token" in capsys.readouterr().err


GOOD = {TOKEN_HEADER: "the-token", "Content-Type": "application/json"}


def send(port: int, data: bytes, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request("POST", "/api/action", body=data, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def post(port: int, body: object, headers: dict[str, str] = GOOD) -> tuple[int, dict[str, object]]:
    return send(port, json.dumps(body).encode(), headers)


def ticket_of(home: Path, number: int) -> str:
    ticket = Store(home / DB_FILENAME).ticket_for_trigger(f"t{number}")
    assert ticket is not None
    return ticket.id


def status_of(home: Path, ticket_id: str) -> TicketStatus:
    return Store(home / DB_FILENAME).get_ticket(ticket_id).status


def inputs_of(home: Path, ticket_id: str) -> list[tuple[str, str, int]]:
    with closing(sqlite3.connect(home / DB_FILENAME)) as connection:
        return connection.execute(
            "SELECT label, text, trusted FROM inputs WHERE ticket_id = ? ORDER BY seq", (ticket_id,)
        ).fetchall()


def attention_of(home: Path) -> list[dict[str, object]]:
    attention = snapshot(home, load_config(home))["attention"]
    assert isinstance(attention, list)
    return attention


def test_an_answer_from_the_panel_resumes_the_parked_worker_with_it(served: int, tmp_path: Path) -> None:
    parked = ticket_of(tmp_path, 2)
    status, reply = post(served, {"action": "answer", "ticket": parked, "text": "  SQLite,\nkeep it simple "})
    assert (status, reply) == (200, {"ticket": parked, "status": "open"})
    [(label, text, trusted)] = inputs_of(tmp_path, parked)
    assert (label, trusted) == ("the panel", 1)
    assert "<untrusted-data label='panel answer from the panel'" in text and "SQLite,\nkeep it simple\n" in text
    store = Store(tmp_path / DB_FILENAME)
    [resumed] = [assignment for assignment in store.running_assignments() if assignment.ticket_id == parked]
    assert resumed.attempt == 2
    brief = (tmp_path / "workers" / parked / resumed.id / "brief.md").read_text(encoding="utf-8")
    assert "keep it simple" in brief


def test_a_follow_up_to_a_running_task_waits_for_its_worker(tmp_path: Path) -> None:
    router, backend, _, _ = build(tmp_path)
    router.ingest(request(1, "fix the login bug"))
    ticket, _ = latest(router, 1)
    assert router.answer(ticket, "the bug is in auth.py", "Sam").status is TicketStatus.OPEN
    assert len(backend.started) == 1
    assert [(label, trusted) for _, label, _, trusted in router.store.pending_inputs(ticket)] == [("Sam", True)]


def test_cancel_from_the_panel_ends_the_task_in_the_owner_name(served: int, tmp_path: Path) -> None:
    config = CONFIG.replace("[worker]\n", '[worker]\non_behalf_of = "Sam"\n')
    (tmp_path / "superagent.toml").write_text(config, encoding="utf-8")
    parked = ticket_of(tmp_path, 2)
    assert post(served, {"action": "answer", "ticket": parked, "text": "SQLite"})[0] == 200
    assert [label for label, _, _ in inputs_of(tmp_path, parked)] == ["Sam"]
    status, reply = post(served, {"action": "cancel", "ticket": parked})
    assert (status, reply) == (200, {"ticket": parked, "status": "ended"})
    texts = [message.text for message in Store(tmp_path / DB_FILENAME).pending_outbox()]
    assert any(text.startswith("Cancelled") and text.endswith("by Sam") for text in texts)


def test_an_ended_or_unknown_ticket_is_refused(served: int, tmp_path: Path) -> None:
    ended = ticket_of(tmp_path, 3)
    for action in ("answer", "cancel"):
        status, reply = post(served, {"action": action, "ticket": ended, "text": "try again"})
        assert status == 409 and "ended" in str(reply["error"])
        status, reply = post(served, {"action": action, "ticket": "tk-00000000", "text": "hello"})
        assert (status, reply) == (404, {"error": "no ticket tk-00000000"})
        assert post(served, {"action": action, "ticket": "../../etc", "text": "hello"})[0] == 400
    assert inputs_of(tmp_path, ended) == []


@pytest.mark.parametrize(
    ("text", "error"),
    [(" \n ", "write an answer"), ("x" * 2001, "2000 characters"), ("red \x1b[31m", "printable"), (7, "write")],
)
def test_an_answer_is_short_printable_text(served: int, tmp_path: Path, text: object, error: str) -> None:
    parked = ticket_of(tmp_path, 2)
    status, reply = post(served, {"action": "answer", "ticket": parked, "text": text})
    assert status == 400 and error in str(reply["error"])
    assert status_of(tmp_path, parked) is TicketStatus.WAITING


def test_accept_applies_a_held_snapshot_on_the_next_poll(served: int, tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    ledger.begin_poll("tasks")
    ledger.poll_succeeded("tasks", count=1, new_items=0, cursor="", applied=False, held=True)
    [card] = [card for card in attention_of(tmp_path) if card["kind"] == "source"]
    assert (card["connector"], card["held"]) == ("tasks", True)
    status, reply = post(served, {"action": "accept", "connector": "tasks"})
    assert (status, reply) == (200, {"accepted": "tasks", "applies": "on its next poll"})
    assert ledger.source("tasks").accept_next
    [card] = [card for card in attention_of(tmp_path) if card["kind"] == "source"]
    assert card["held"] is False
    assert post(served, {"action": "accept", "connector": "vps"})[0] == 409
    unknown = post(served, {"action": "accept", "connector": "nope"})
    assert unknown == (404, {"error": "no connector nope in superagent.toml"})
    assert post(served, {"action": "accept", "connector": "Bad Name"})[0] == 400


def test_snooze_hides_an_overdue_item_until_then(served: int, tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    item = ledger.record("tasks", SourceItem("x1", "renew the domain", due="2020-01-01"))
    ledger.move(item.id, ItemState.TRIAGED, "owner", "")
    assert [card["handle"] for card in attention_of(tmp_path) if card["kind"] == "overdue"] == [item.handle]
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    status, reply = post(served, {"action": "snooze", "item": item.handle, "until": tomorrow})
    assert status == 200 and reply["item"] == item.handle
    assert "overdue" not in [card["kind"] for card in attention_of(tmp_path)]
    status, reply = post(served, {"action": "snooze", "item": item.handle, "until": "2020-01-01"})
    assert status == 400 and "future" in str(reply["error"])
    assert post(served, {"action": "snooze", "item": item.handle, "until": "next week"})[0] == 400
    unknown = post(served, {"action": "snooze", "item": "it-zzzz", "until": tomorrow})
    assert unknown == (404, {"error": "no item it-zzzz"})
    assert post(served, {"action": "snooze", "item": "it-; drop", "until": tomorrow})[0] == 400


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Content-Type": "application/json"}, 403),
        ({TOKEN_HEADER: "wrong", "Content-Type": "application/json"}, 403),
        ({TOKEN_HEADER: "the-token", "Content-Type": "text/plain"}, 415),
        ({TOKEN_HEADER: "the-token", "Content-Type": "application/x-www-form-urlencoded"}, 415),
        ({TOKEN_HEADER: "the-token"}, 415),
        ({**GOOD, "Origin": "https://attacker.example"}, 403),
        ({**GOOD, "Origin": "null"}, 403),
        ({**GOOD, "Origin": "http://127.0.0.1:1"}, 403),
        ({**GOOD, "Host": "attacker.example"}, 421),
    ],
)
def test_an_action_needs_the_token_json_and_the_panel_origin(
    served: int, tmp_path: Path, headers: dict[str, str], status: int
) -> None:
    parked = ticket_of(tmp_path, 2)
    got, reply = post(served, {"action": "cancel", "ticket": parked}, headers)
    assert got == status and reply["error"]
    assert status_of(tmp_path, parked) is TicketStatus.WAITING


def test_the_panel_own_origins_may_act(tmp_path: Path) -> None:
    busy_home(tmp_path)
    parked = ticket_of(tmp_path, 2)
    with serving(tmp_path, "panel.example.ts.net") as port:
        tailnet = {**GOOD, "Host": "panel.example.ts.net"}
        cancel = {"action": "cancel", "ticket": parked}
        assert post(port, cancel, {**tailnet, "Origin": "https://other.example.ts.net"})[0] == 403
        assert post(port, cancel, {**tailnet, "Origin": "https://panel.example.ts.net:99999"})[0] == 403
        assert post(port, cancel, {**tailnet, "Origin": "https://user@panel.example.ts.net"})[0] == 403
        answer = {"action": "answer", "ticket": parked, "text": "SQLite"}
        assert post(port, answer, {**GOOD, "Origin": f"http://localhost:{port}"})[0] == 200
        assert post(port, cancel, {**tailnet, "Origin": "https://panel.example.ts.net"})[0] == 200


def test_an_action_on_a_named_port_comes_from_that_port(tmp_path: Path) -> None:
    busy_home(tmp_path)
    parked = ticket_of(tmp_path, 2)
    with serving(tmp_path, "panel.example.ts.net:8443") as port:
        named = {**GOOD, "Host": "panel.example.ts.net:8443"}
        cancel = {"action": "cancel", "ticket": parked}
        assert post(port, cancel, {**named, "Origin": "https://panel.example.ts.net"})[0] == 403
        status, reply = post(port, cancel, {**GOOD, "Host": "panel.example.ts.net"})
        assert status == 421 and '"panel.example.ts.net"' in str(reply["error"])
        assert post(port, cancel, {**named, "Origin": "https://panel.example.ts.net:8443"})[0] == 200


def test_an_oversized_or_malformed_action_is_refused(served: int, tmp_path: Path) -> None:
    too_big = {**GOOD, "Content-Length": str(ACTION_BODY_LIMIT + 1)}
    assert send(served, b"", too_big)[0] == 413
    assert send(served, b"{not json", GOOD)[0] == 400
    assert post(served, ["cancel"])[0] == 400
    assert post(served, {"action": "delete", "ticket": ticket_of(tmp_path, 2)})[0] == 400
    assert fetch(served, "/api/action", GOOD)[0] == 404


def test_read_only_hides_the_buttons_and_refuses_every_action(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    busy_home(tmp_path)
    parked = ticket_of(tmp_path, 2)
    with serving(tmp_path, read_only=True) as port:
        assert json.loads(fetch(port, "/api/snapshot", {TOKEN_HEADER: "the-token"})[2])["actions"] is False
        status, reply = post(port, {"action": "cancel", "ticket": parked})
        assert status == 403 and "--read-only" in str(reply["error"])
    assert status_of(tmp_path, parked) is TicketStatus.WAITING
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("superagent.cli.serve", lambda *args: calls.append(args))
    assert panel_cli(tmp_path, "--read-only") == 0
    assert calls[0][-1] is True
