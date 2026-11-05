from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from superagent.agents import HOOK_EVENTS, HOOK_SCRIPT, PROBE_SCRIPT, SessionState, classify
from superagent.cli import main
from superagent.connectors import ConnectorSpec, build_connector, parse_specs
from superagent.ledger import ItemState, Ledger
from superagent.polling import poll_sources
from superagent.runtime import open_runtime, tick

OUTBOUND = Path(__file__).with_name("e2e") / "outbound.py"


@pytest.fixture
def sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "sessions"
    monkeypatch.setenv("SUPERAGENT_SESSIONS_DIR", str(directory))
    return directory


def hook(payload: object, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        timeout=30,
        check=False,
    )


def event(name: str, session: str = "s-1", cwd: str = "/work/app", **fields: object) -> dict[str, object]:
    return {"session_id": session, "hook_event_name": name, "cwd": cwd, "transcript_path": "/t.jsonl", **fields}


def lines(sessions: Path, session: str = "s-1") -> list[dict[str, Any]]:
    path = sessions / f"{session}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_the_hook_appends_one_quiet_line_per_event(sessions: Path) -> None:
    done = hook(
        event("SessionStart", source="startup"),
        TMUX_PANE="%3",
        CMUX_SURFACE_ID="surface-9",
        CMUX_CUA_SOCKET_AUTH_TOKEN="secret-token",
        CMUX_AGENT_LAUNCH_ARGV_B64="x" * 6000,
    )
    assert done.returncode == 0 and done.stdout == ""
    [line] = lines(sessions)
    assert line["event"] == "SessionStart" and line["cwd"] == "/work/app"
    assert line["pane"]["TMUX_PANE"] == "%3" and line["pane"]["CMUX_SURFACE_ID"] == "surface-9"
    assert "secret-token" not in json.dumps(line) and "CMUX_AGENT_LAUNCH_ARGV_B64" not in line["pane"]


@pytest.mark.parametrize("payload", ["not json", "{}", json.dumps({"session_id": "../../etc", "hook_event_name": ""})])
def test_the_hook_never_fails_and_never_writes_garbage(sessions: Path, payload: str) -> None:
    done = hook(payload)
    assert done.returncode == 0 and done.stdout == ""
    assert not sessions.exists() or not any(sessions.iterdir())


def test_the_hook_keeps_the_question_being_asked(sessions: Path) -> None:
    hook(
        event(
            "PreToolUse", tool_name="AskUserQuestion", tool_input={"questions": [{"question": "Postgres or SQLite?"}]}
        )
    )
    hook(event("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build", "description": "clean"}))
    hook(event("Stop", last_assistant_message="Done, should I open the PR?", background_tasks=[]))
    texts = [line["text"] for line in lines(sessions)]
    assert texts == ["Postgres or SQLite?", "permission for Bash: clean", "Done, should I open the PR?"]


def at(name: str, seconds_ago: float, now: float = 10_000.0, **fields: object) -> dict[str, object]:
    return {"event": name, "t": now - seconds_ago, "cwd": "/work/app", **fields}


NOW = 10_000.0


@pytest.mark.parametrize(
    ("events", "alive", "state"),
    [
        ([at("SessionStart", 60)], True, SessionState.IDLE),
        ([at("UserPromptSubmit", 60)], True, SessionState.WORKING),
        ([at("PreToolUse", 3000, tool="Bash")], True, SessionState.STUCK),
        ([at("PreToolUse", 1500, tool="Bash", timeout_ms=1_800_000)], True, SessionState.WORKING),
        ([at("PermissionRequest", 5, tool="Bash")], True, SessionState.WAITING),
        ([at("PreToolUse", 5, tool="AskUserQuestion")], True, SessionState.WAITING),
        ([at("PreToolUse", 5, tool="ExitPlanMode")], True, SessionState.WAITING),
        ([at("Notification", 5, notification="idle_prompt")], True, SessionState.WAITING),
        ([at("Notification", 5, notification="quota_auto_resume_fired")], True, SessionState.WORKING),
        ([at("Stop", 5, background=0, text="All done?")], True, SessionState.WAITING),
        ([at("Stop", 5, background=2)], True, SessionState.BACKGROUND),
        ([at("StopFailure", 5, error="rate_limit")], True, SessionState.LIMITED),
        ([at("SessionEnd", 5, reason="prompt_input_exit")], False, SessionState.ENDED),
        ([at("UserPromptSubmit", 60)], False, SessionState.GONE),
        (
            [at("Stop", 3000, background=0), at("PreToolUse", 30, tool="Read", agent="sub-1")],
            True,
            SessionState.WAITING,
        ),
        ([at("UserPromptSubmit", 3000), at("PostToolUse", 30, tool="Read", agent="sub-1")], True, SessionState.WORKING),
    ],
)
def test_sessions_are_classified_from_main_thread_events(
    events: list[dict[str, object]], alive: bool, state: SessionState
) -> None:
    assert classify(events, alive, NOW, stall_after=1200).state is state


def test_a_waiting_session_carries_its_question_and_pane() -> None:
    view = classify([at("Stop", 5, background=0, text="Merge it?", pane={"TMUX_PANE": "%1"})], True, NOW, 1200)
    assert view.question == "Merge it?" and view.pane == {"TMUX_PANE": "%1"}


def test_the_probe_reports_sessions_and_dead_processes(sessions: Path) -> None:
    hook(event("UserPromptSubmit", session="live"))
    sessions.mkdir(exist_ok=True)
    finished = subprocess.Popen(["true"])
    finished.wait()
    dead = sessions / "dead.jsonl"
    dead.write_text(
        json.dumps({"event": "UserPromptSubmit", "t": time.time(), "cwd": "/x", "pid": finished.pid}) + "\n"
    )
    done = subprocess.run(
        [sys.executable, "-"], input=PROBE_SCRIPT.read_text(), capture_output=True, text=True, timeout=30, check=True
    )
    report = json.loads(done.stdout)
    by_id = {session["session"]: session for session in report["sessions"]}
    assert set(by_id) == {"live", "dead"}
    assert by_id["dead"]["alive"] is False
    assert by_id["live"]["events"][0]["event"] == "UserPromptSubmit"


def agents_spec(**extra: object) -> ConnectorSpec:
    return parse_specs({"agents-here": {"type": "agents", "every": "0s", **extra}})[0]


def test_the_agents_connector_turns_sessions_into_owned_items(sessions: Path, tmp_path: Path) -> None:
    hook(event("UserPromptSubmit", session="a", cwd="/work/app"))
    hook(event("Stop", session="b", cwd="/work/site", last_assistant_message="Ship it?", background_tasks=[]))
    hook(event("SessionEnd", session="c", cwd="/work/old", reason="other"))
    ledger = Ledger(tmp_path / "s.db")
    spec = agents_spec()
    poll_sources(ledger, [spec], {spec.name: build_connector(spec)}, 60)
    items = {item.external_id: item for item in ledger.items()}
    assert items["a"].meta["state"] == "working" and items["a"].state is ItemState.TRIAGED
    assert items["b"].meta["state"] == "waiting" and items["b"].excerpt == "Ship it?"
    assert items["b"].title == "site"
    assert items["c"].state is ItemState.DONE


def test_a_waiting_session_is_announced_once_it_holds_for_two_polls(sessions: Path, tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    spec = agents_spec(channel="discord:agents", notify_detail=True)
    connectors = {spec.name: build_connector(spec)}
    said: list[tuple[str, str]] = []

    def notify(channel: str, text: str) -> None:
        said.append((channel, text))

    def poll() -> None:
        poll_sources(ledger, [spec], connectors, 600, notify)

    hook(event("Stop", session="b", cwd="/work/site", last_assistant_message="Ship it?", background_tasks=[]))
    poll()
    assert said == []
    poll()
    poll()
    assert len(said) == 1
    assert said[0][0] == "discord:agents" and "site [agents-here] is waiting: Ship it?" in said[0][1]
    hook(event("UserPromptSubmit", session="b", cwd="/work/site"))
    poll()
    hook(event("Stop", session="b", cwd="/work/site", last_assistant_message="Now?", background_tasks=[]))
    poll()
    poll()
    assert len(said) == 2 and "Now?" in said[1][1]


def test_without_notify_detail_no_session_text_leaves_the_box(sessions: Path, tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    spec = agents_spec(channel="discord:agents")
    connectors = {spec.name: build_connector(spec)}
    said: list[str] = []
    hook(
        event("Stop", session="b", cwd="/work/site", last_assistant_message="password=hunter2 ok?", background_tasks=[])
    )
    for _ in range(2):
        poll_sources(ledger, [spec], connectors, 600, lambda channel, text: said.append(text))
    assert len(said) == 1 and "hunter2" not in said[0] and ":" not in said[0].split(" is waiting")[1].split("(")[0]


def test_a_tick_posts_waiting_sessions_and_the_board_lists_them_first(
    sessions: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sent = tmp_path / "sent.tsv"
    (tmp_path / "superagent.toml").write_text(
        f"""
[outbound]
argv = ["{sys.executable}", "{OUTBOUND}", "{sent}", "{{channel}}", "{{text}}"]

[connectors.agents-here]
type = "agents"
every = "0s"
channel = "discord:agents"
notify_detail = true
""",
        encoding="utf-8",
    )
    hook(event("UserPromptSubmit", session="a", cwd="/work/app"))
    hook(event("PermissionRequest", session="b", cwd="/work/site", tool_name="Bash", tool_input={"command": "make"}))
    result = tick(open_runtime(tmp_path))
    assert result.errors == () and result.warnings == ()
    assert not sent.exists()
    tick(open_runtime(tmp_path))
    delivered = sent.read_text(encoding="utf-8")
    assert "discord:agents\tsite [agents-here] is waiting: permission for Bash (it-" in delivered
    assert main(["--home", str(tmp_path), "agents"]) == 0
    board = capsys.readouterr().out.splitlines()
    assert "waiting" in board[0] and "site" in board[0]
    assert any("working" in line and "app" in line for line in board)


def test_the_cli_prints_the_hook_and_its_settings(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["--home", str(tmp_path), "agents", "--hook-settings"]) == 0
    settings = json.loads(capsys.readouterr().out)
    assert set(settings["hooks"]) == set(HOOK_EVENTS)
    assert main(["--home", str(tmp_path), "agents", "--hook-script"]) == 0
    assert capsys.readouterr().out == HOOK_SCRIPT.read_text()


@pytest.mark.parametrize("extra", [{"host": "ssh mac"}, {"timeout": 0}, {"stall_after": "soon"}, {"python": ""}])
def test_bad_agents_settings_are_refused(extra: dict[str, object]) -> None:
    with pytest.raises(Exception, match="connectors.agents-here"):
        agents_spec(**extra)


def test_the_hook_never_keeps_commands_or_tokens(sessions: Path) -> None:
    hook(
        event(
            "PermissionRequest",
            tool_name="Bash",
            tool_input={"command": "curl -H 'Authorization: Bearer abcdef123456'"},
        )
    )
    hook(
        event(
            "Stop", last_assistant_message="Set GH token ghp_" + "a" * 36 + " and api_key=s3cr3t", background_tasks=[]
        )
    )
    stored = json.dumps(lines(sessions))
    assert "curl" not in stored and "abcdef123456" not in stored
    assert "ghp_" not in stored and "s3cr3t" not in stored


@pytest.mark.parametrize(
    ("events", "busy", "state"),
    [
        ([at("PreToolUse", 600, tool="Bash"), at("PermissionRequest", 590, tool="Bash")], True, SessionState.WORKING),
        ([at("Stop", 3000, background=1, text="Merge it?")], False, SessionState.WAITING),
        (
            [at("UserPromptSubmit", 60), at("PermissionRequest", 5, tool="Bash", agent="sub-1")],
            False,
            SessionState.WAITING,
        ),
        (
            [
                at("StopFailure", 60, error="rate_limit"),
                at("Notification", 5, notification="quota_auto_resume_disabled"),
            ],
            False,
            SessionState.LIMITED,
        ),
        ([at("UserPromptSubmit", 60), at("SessionStart", 5, source="compact")], False, SessionState.WORKING),
        (
            [at("UserPromptSubmit", 60), at("PostToolUseFailure", 5, tool="Bash", interrupt=True)],
            False,
            SessionState.WAITING,
        ),
    ],
)
def test_the_reviewed_edge_cases(events: list[dict[str, object]], busy: bool, state: SessionState) -> None:
    assert classify(events, True, NOW, stall_after=1200, busy=busy).state is state


def test_idle_prompt_keeps_the_real_question() -> None:
    events = [
        at("Stop", 120, background=0, text="Postgres or SQLite?"),
        at("Notification", 60, notification="idle_prompt", text="Claude is waiting for your input"),
    ]
    view = classify(events, True, NOW, 1200)
    assert view.state is SessionState.WAITING and view.question == "Postgres or SQLite?"


def test_a_reused_pid_that_is_not_claude_counts_as_dead(sessions: Path) -> None:
    other = subprocess.Popen(["sleep", "30"])
    try:
        sessions.mkdir(exist_ok=True)
        (sessions / "reused.jsonl").write_text(
            json.dumps({"event": "UserPromptSubmit", "t": time.time(), "cwd": "/x", "pid": other.pid, "pid_start": ""})
            + "\n"
        )
        done = subprocess.run(
            [sys.executable, "-"],
            input=PROBE_SCRIPT.read_text(),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        [session] = json.loads(done.stdout)["sessions"]
        assert session["alive"] is False
    finally:
        other.kill()


def test_a_session_gone_for_an_hour_is_closed_and_never_announced(sessions: Path, tmp_path: Path) -> None:
    finished = subprocess.Popen(["true"])
    finished.wait()
    sessions.mkdir(exist_ok=True)
    (sessions / "crashed.jsonl").write_text(
        json.dumps({"event": "UserPromptSubmit", "t": time.time() - 7200, "cwd": "/w/app", "pid": finished.pid}) + "\n"
    )
    ledger = Ledger(tmp_path / "s.db")
    spec = agents_spec(channel="discord:agents")
    said: list[str] = []
    for _ in range(2):
        poll_sources(ledger, [spec], {spec.name: build_connector(spec)}, 600, lambda channel, text: said.append(text))
    assert ledger.get("agents-here:crashed").state is ItemState.DONE
    assert said == []
