from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from superagent.cli import main
from superagent.config import CONFIG_FILENAME, DB_FILENAME, LAST_GOOD_FILENAME, parse_config
from superagent.connectors import build_connector, parse_specs
from superagent.connectors.agents import NOT_HOOKED
from superagent.doctor import Check, Status, diagnose
from superagent.ledger import Ledger
from superagent.polling import poll_sources
from superagent.runtime import TICK_LOCK_FILENAME, open_runtime, tick
from superagent.starter import Choices, local_timezone, starter_config
from tests.test_agents import event, hook

ITEMS = "import json; print(json.dumps([{'id': '1', 'title': 'Pay rent'}]))"


def named(checks: list[Check], name: str) -> Check:
    return next(check for check in checks if check.name == name)


def write(home: Path, text: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    (home / CONFIG_FILENAME).write_text(text, encoding="utf-8")
    return home


def command(name: str, code: str) -> str:
    argv = json.dumps([sys.executable, "-c", code])
    return (
        f'[connectors.{name}]\ntype = "command"\nevery = "0s"\nargv = {argv}\nfields = {{id = "id", title = "title"}}\n'
    )


@pytest.fixture
def bare_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("superagent.starter.which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    return tmp_path / "sa"


def test_doctor_without_a_config_points_to_setup(tmp_path: Path) -> None:
    [check] = diagnose(tmp_path)
    assert check.status is Status.FAIL and "run superagent setup" in check.detail


def test_setup_on_a_bare_machine_writes_a_config_that_runs_with_no_connectors(
    bare_machine: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--home", str(bare_machine), "setup"]) == 0
    assert "with no connectors" in capsys.readouterr().out
    assert parse_config((bare_machine / CONFIG_FILENAME).read_text(encoding="utf-8")).connectors == ()
    assert tick(open_runtime(bare_machine)).errors == ()
    checks = diagnose(bare_machine)
    assert named(checks, "tick").status is Status.OK
    assert named(checks, "config").status is Status.WARN and "alerts" in named(checks, "config").detail
    assert not [check for check in checks if check.status is Status.FAIL]
    assert main(["--home", str(bare_machine), "doctor"]) == 0


def test_setup_never_overwrites_a_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = write(tmp_path, '[channels]\nalerts = "discord:9"\n')
    assert main(["--home", str(home), "setup", "--alerts", "discord:1"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert "discord:9" in (home / CONFIG_FILENAME).read_text(encoding="utf-8")


def test_the_starter_config_carries_the_channels_and_the_tools_found() -> None:
    choices = Choices("/opt/hermes/bin/hermes", "discord:1", "discord:2", "discord:3", "Europe/Paris", True, True)
    config = parse_config(starter_config(choices))
    assert config.send_argv == ("/opt/hermes/bin/hermes", "send", "-q", "--to", "{channel}", "--", "{text}")
    assert (config.alerts_channel, config.brief_channel, config.brief_timezone) == (
        "discord:1",
        "discord:2",
        "Europe/Paris",
    )
    assert [(spec.name, spec.type, spec.channel) for spec in config.connectors] == [
        ("github", "github", ""),
        ("agents", "agents", "discord:3"),
    ]


def test_setup_through_hermes_sends_through_that_hermes(
    bare_machine: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = bare_machine.parent / "bin" / "hermes"
    monkeypatch.setattr(sys, "argv", [str(launcher), "superagent"])
    assert main(["--home", str(bare_machine), "setup", "--alerts", "discord:1"]) == 0
    config = parse_config((bare_machine / CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert config.send_argv[0] == str(launcher)
    assert f"{launcher} superagent --home {bare_machine} doctor" in capsys.readouterr().out


def test_the_timezone_falls_back_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("superagent.starter.os.path.realpath", lambda path: "/nowhere")
    monkeypatch.setenv("TZ", "Not/AZone")
    assert local_timezone() == "UTC"
    monkeypatch.setenv("TZ", ":Europe/Paris")
    assert local_timezone() == "Europe/Paris"


def test_doctor_names_each_failure_and_never_records_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SUPERAGENT_SESSIONS_DIR", str(tmp_path / "no-sessions"))
    home = write(
        tmp_path / "sa",
        '[outbound]\nargv = ["no-such-sender-here", "{channel}", "{text}"]\n'
        f'[channels]\nalerts = "discord:1"\n[heartbeat]\nargv = {json.dumps([sys.executable, "-c", "pass"])}\n'
        + command("tasks", "import sys; sys.exit(3)")
        + command("notes", ITEMS)
        + '[connectors.agents]\ntype = "agents"\n',
    )
    checks = diagnose(home)
    assert named(checks, "outbound").status is Status.FAIL
    assert named(checks, "heartbeat").status is Status.OK
    assert named(checks, "tasks").status is Status.FAIL and "exited 3" in named(checks, "tasks").detail
    assert named(checks, "notes") == Check("notes", Status.OK, named(checks, "notes").detail)
    assert named(checks, "notes").detail.startswith("1 item in")
    assert named(checks, "agents") == Check("agents", Status.FAIL, NOT_HOOKED.format(where=tmp_path / "no-sessions"))
    assert Ledger(home / DB_FILENAME).items() == []
    assert Ledger(home / DB_FILENAME).sources() == []
    assert main(["--home", str(home), "doctor", "--json"]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert {"name": "tasks", "status": "fail"} in [{key: entry[key] for key in ("name", "status")} for entry in printed]


def test_doctor_without_polling_still_shows_how_sources_have_been_doing(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    home = write(tmp_path / "sa", command("tasks", f"open({str(marker)!r}, 'w')"))
    ledger = Ledger(home / DB_FILENAME)
    for _ in range(3):
        ledger.begin_poll("tasks")
        ledger.poll_failed("tasks", "gh: bad credentials")
    checks = diagnose(home, poll=False)
    assert not marker.exists()
    assert Check("tasks", Status.WARN, "connector tasks: 3 failed polls in a row, last: gh: bad credentials") in checks


def test_doctor_flags_a_stopped_timer_a_dead_tick_and_live_alerts(tmp_path: Path) -> None:
    home = write(tmp_path / "sa", '[channels]\nalerts = "discord:1"\n')
    runtime = open_runtime(home)
    lock = home / "tick.lock"
    lock.write_text(json.dumps({"pid": 1, "started": time.time() - 3700, "finished": time.time() - 3600}))
    assert named(diagnose(home), "tick").status is Status.FAIL
    lock.write_text(json.dumps({"pid": 1, "started": time.time() - 7200}))
    assert named(diagnose(home), "tick").status is Status.FAIL
    runtime.store.raise_alert_now("k", "connector github: failing", "discord:1")
    alerts = named(diagnose(home), "alerts")
    assert alerts.status is Status.FAIL and "connector github: failing" in alerts.detail


def test_doctor_reports_a_message_that_keeps_failing_to_send(tmp_path: Path) -> None:
    home = write(
        tmp_path / "sa", '[channels]\nalerts = "discord:1"\n[outbound]\nargv = ["false", "{channel}", "{text}"]\n'
    )
    tick(open_runtime(home))
    open_runtime(home).store.raise_alert_now("k", "it broke", "discord:1")
    tick(open_runtime(home))
    outbox = named(diagnose(home), "outbox")
    assert outbox.status is Status.FAIL and "failed to send" in outbox.detail


def test_an_agents_host_without_the_hook_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("SUPERAGENT_SESSIONS_DIR", str(sessions))
    [spec] = parse_specs({"agents": {"type": "agents", "every": "0s"}})
    connector = build_connector(spec)
    assert connector.poll("").problems == (NOT_HOOKED.format(where=sessions),)
    ledger = Ledger(tmp_path / "s.db")
    poll_sources(ledger, [spec], {spec.name: connector}, 60)
    assert ledger.source("agents").failures == 1 and "hook" in ledger.source("agents").last_error
    sessions.mkdir()
    poll = connector.poll("")
    assert poll.complete and poll.problems == ()


def test_setup_without_a_sender_refuses_channels_that_would_get_nothing(
    bare_machine: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--home", str(bare_machine), "setup", "--alerts", "discord:1"]) == 2
    assert "--hermes" in capsys.readouterr().err and not (bare_machine / CONFIG_FILENAME).exists()
    assert main(["--home", str(bare_machine), "setup", "--alerts", "discord:1", "--hermes", "/opt/bin/hermes"]) == 0
    assert parse_config((bare_machine / CONFIG_FILENAME).read_text(encoding="utf-8")).send_argv[0] == "/opt/bin/hermes"


def test_setup_leaves_a_config_that_only_survives_as_its_last_good_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / LAST_GOOD_FILENAME).write_text(command("tasks", ITEMS), encoding="utf-8")
    assert main(["--home", str(tmp_path), "setup"]) == 2
    assert f"cp {tmp_path / LAST_GOOD_FILENAME}" in capsys.readouterr().err
    assert not (tmp_path / CONFIG_FILENAME).exists()


def test_doctor_fails_when_channels_are_set_but_nothing_sends(tmp_path: Path) -> None:
    home = write(tmp_path / "sa", '[channels]\nalerts = "discord:1"\n')
    outbound = named(diagnose(home, poll=False), "outbound")
    assert outbound.status is Status.FAIL and "lost" in outbound.detail


def test_doctor_fails_once_a_config_has_waited_too_long_for_its_first_tick(tmp_path: Path) -> None:
    home = write(tmp_path / "sa", "")
    assert named(diagnose(home), "tick").status is Status.WARN
    old = time.time() - 3600
    os.utime(home / CONFIG_FILENAME, (old, old))
    assert named(diagnose(home), "tick").status is Status.FAIL


def test_any_channel_name_still_makes_valid_toml() -> None:
    config = parse_config(starter_config(Choices(hermes="/h/hermes", alerts='discord:🚨 "alerts"')))
    assert config.alerts_channel == 'discord:🚨 "alerts"'


def test_an_agents_channel_adds_the_local_agents_connector_even_without_claude_here(bare_machine: Path) -> None:
    argv = ["--home", str(bare_machine), "setup", "--agents-channel", "discord:3", "--hermes", "/h/hermes"]
    assert main(argv) == 0
    config = parse_config((bare_machine / CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert [(spec.type, spec.channel) for spec in config.connectors] == [("agents", "discord:3")]


def test_doctor_leaves_the_polls_to_a_running_tick(tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    home = write(tmp_path / "sa", command("tasks", f"open({str(marker)!r}, 'w'); print('[]')"))
    descriptor = os.open(home / TICK_LOCK_FILENAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        assert named(diagnose(home), "connectors").status is Status.WARN
    finally:
        os.close(descriptor)
    assert not marker.exists()
    assert named(diagnose(home), "tasks").status is Status.OK and marker.exists()


def test_a_host_that_loses_its_hook_keeps_its_sessions_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = tmp_path / "sessions"
    monkeypatch.setenv("SUPERAGENT_SESSIONS_DIR", str(sessions))
    hook(event("UserPromptSubmit", session="kept"))
    [spec] = parse_specs({"agents": {"type": "agents", "every": "0s"}})
    connectors = {spec.name: build_connector(spec)}
    ledger = Ledger(tmp_path / "s.db")
    poll_sources(ledger, [spec], connectors, 60)
    shutil.rmtree(sessions)
    poll_sources(ledger, [spec], connectors, 60)
    [item] = ledger.items()
    assert item.is_open and item.missing_since is None
    assert ledger.source("agents").failures == 1 and str(sessions) in ledger.source("agents").last_error
