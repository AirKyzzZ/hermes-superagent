from __future__ import annotations

import argparse
import http.client
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Iterator
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from superagent.cli import run, setup_parser
from superagent.config import DB_FILENAME, Config, load_config
from superagent.domain import Mandate, RepairAction, ReportState, Strength, Trigger
from superagent.ledger import Ledger, SourceItem
from superagent.panel import (
    TOKEN_FILENAME,
    TOKEN_HEADER,
    allowed_hosts,
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


def test_a_host_passed_to_allow_host_is_answered(tmp_path: Path) -> None:
    busy_home(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), panel_handler(tmp_path, "the-token", ["Panel.Example.ts.net"]))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_port
        assert fetch(port, "/", {"Host": "panel.example.ts.net"})[0] == 200
        assert fetch(port, "/", {"Host": "other.example.ts.net"})[0] == 421
    finally:
        server.shutdown()
        server.server_close()


def test_allow_host_refuses_anything_but_a_host_name() -> None:
    with pytest.raises(ValueError, match="allow-host"):
        allowed_hosts(8765, ["evil.example.com/path"])
    with pytest.raises(ValueError, match="allow-host"):
        allowed_hosts(8765, ["*"])


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
