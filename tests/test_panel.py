from __future__ import annotations

import argparse
import http.client
import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from superagent.cli import run, setup_parser
from superagent.config import DB_FILENAME, Config, load_config
from superagent.domain import RepairAction, ReportState, Trigger
from superagent.ledger import Ledger, SourceItem
from superagent.panel import TOKEN_HEADER, panel_handler, snapshot
from superagent.router import Router
from superagent.store import Store

from .conftest import FakeBackend

CONFIG = """
[sources.chat]
policy = "explicit_only"
allow = ["alice"]

[worker]
argv = ["w", "{brief}"]

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


@pytest.fixture
def served(tmp_path: Path) -> Iterator[int]:
    busy_home(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), panel_handler(tmp_path, "the-token"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()


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


def test_a_request_for_another_host_is_refused(served: int) -> None:
    assert fetch(served, "/", {"Host": "attacker.example"})[0] == 421
    assert fetch(served, "/api/snapshot", {"Host": "attacker.example", TOKEN_HEADER: "the-token"})[0] == 421


def test_panel_json_prints_one_snapshot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    busy_home(tmp_path)
    parser = argparse.ArgumentParser()
    setup_parser(parser)
    assert run(parser.parse_args(["--home", str(tmp_path), "panel", "--json"])) == 0
    assert len(json.loads(capsys.readouterr().out)["attention"]) == 4
