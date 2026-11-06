from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from superagent.connectors import ConnectorConfigError, build_connector, parse_specs
from superagent.connectors.agents import AgentsConnector
from superagent.ledger import Ledger
from superagent.polling import poll_sources
from superagent.relaunch import relaunch_sessions

READY = "API Error: Overloaded\n" + "─" * 40 + "\n❯ \n" + "─" * 40 + "\n  ? for shortcuts\n"
WORKING = "✳ Thinking… (2m 3s · ↓ 1.2k tokens)\n" + "─" * 40 + "\n❯ \n" + "─" * 40 + "\n"
DIALOG = "Do you want to proceed?\n❯ 1. Yes\n  2. No\n"
TYPED = "─" * 40 + "\n❯ half a sentence\n" + "─" * 40 + "\n"
FAKE = """#!{python}
import json, sys
state = json.load(open({fixture!r}))
with open({log!r}, "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[1:]
if "display-message" in args:
    print(state["pane_pid"])
elif "capture-pane" in args or "read-screen" in args:
    print(state["screen"])
sys.exit(state.get("fail", 0) if "send" in args or "send-keys" in args else 0)
"""


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("SUPERAGENT_SESSIONS_DIR", str(sessions))
    paths = {"sessions": sessions, "fixture": tmp_path / "fake.json", "log": tmp_path / "calls.jsonl"}
    for name in ("tmux", "cmux"):
        fake = tmp_path / name
        fake.write_text(FAKE.format(python=sys.executable, fixture=str(paths["fixture"]), log=str(paths["log"])))
        fake.chmod(0o755)
        paths[name] = fake
    stage(paths, screen=READY, pane_pid=os.getpid())
    return paths


@pytest.fixture
def claude(tmp_path: Path) -> Iterator[int]:
    named = tmp_path / "bin" / "claude"
    named.parent.mkdir()
    named.symlink_to(shutil.which("sleep") or "/bin/sleep")
    process = subprocess.Popen([str(named), "120"])
    yield process.pid
    process.kill()
    process.wait()


def stage(paths: dict[str, Path], **state: object) -> None:
    paths["fixture"].write_text(json.dumps(state))


def calls(paths: dict[str, Path]) -> list[list[str]]:
    if not paths["log"].exists():
        return []
    return [json.loads(line) for line in paths["log"].read_text().splitlines()]


def session_log(paths: dict[str, Path], session: str, *events: dict[str, Any]) -> float:
    with (paths["sessions"] / f"{session}.jsonl").open("a") as file:
        for event in events:
            file.write(json.dumps(event) + "\n")
    return max(float(event["t"]) for event in events)


def failure(at: float, pane: dict[str, str], pid: int, error: str = "overloaded") -> list[dict[str, Any]]:
    start = {"event": "UserPromptSubmit", "t": at - 60, "cwd": "/tmp", "pane": pane, "pid": pid}
    return [start, {"event": "StopFailure", "t": at, "cwd": "/tmp", "error": error, "pane": pane, "pid": pid}]


def connector(paths: dict[str, Path], **extra: object) -> AgentsConnector:
    body = {"type": "agents", "every": "0s", "relaunch": True, "tmux": str(paths["tmux"]), "cmux": str(paths["cmux"])}
    built = build_connector(parse_specs({"agents": {**body, **extra}})[0])
    assert isinstance(built, AgentsConnector)
    return built


TMUX_PANE = {"TMUX_PANE": "%3", "TMUX": "/tmp/tmux-test/default,1,0"}
CMUX_PANE = {"CMUX_WORKSPACE_ID": "W-1", "CMUX_SURFACE_ID": "S-1"}


def test_a_session_at_an_empty_prompt_gets_one_continue_in_its_tmux_pane(host: dict[str, Path]) -> None:
    since = session_log(host, "s-nudge-1", *failure(time.time(), TMUX_PANE, os.getpid()))
    assert connector(host).act("s-nudge-1", "nudge", repr(since)) == (True, "typed 'continue' in tmux pane %3")
    sent = [call for call in calls(host) if "send-keys" in call]
    assert sent == [
        ["-S", "/tmp/tmux-test/default", "send-keys", "-t", "%3", "-l", "continue"],
        ["-S", "/tmp/tmux-test/default", "send-keys", "-t", "%3", "Enter"],
    ]


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"screen": WORKING}, "still working"),
        ({"screen": DIALOG}, "'do you want'"),
        ({"screen": TYPED}, "no empty input box"),
        ({"screen": "Interrupted · What should Claude do instead?\n" + READY}, "'interrupted'"),
        ({"pane_pid": 1}, "doesn't run in tmux pane %3"),
    ],
)
def test_nothing_is_typed_unless_the_pane_is_waiting_at_an_empty_prompt(
    host: dict[str, Path], state: dict[str, object], reason: str
) -> None:
    stage(host, **{"screen": READY, "pane_pid": os.getpid(), **state})
    since = session_log(host, "s-held-1", *failure(time.time(), TMUX_PANE, os.getpid()))
    done, detail = connector(host).act("s-held-1", "nudge", repr(since))
    assert not done and reason in detail
    assert not [call for call in calls(host) if "send-keys" in call]


def test_a_pane_another_session_took_over_is_left_alone(host: dict[str, Path]) -> None:
    since = session_log(host, "s-old-01", *failure(time.time() - 30, TMUX_PANE, os.getpid()))
    session_log(host, "s-new-01", {"event": "SessionStart", "t": time.time(), "pane": TMUX_PANE, "pid": os.getpid()})
    done, detail = connector(host).act("s-old-01", "nudge", repr(since))
    assert not done and "taken over" in detail


def test_a_session_that_moved_since_the_check_is_left_alone(host: dict[str, Path]) -> None:
    since = session_log(host, "s-moved-1", *failure(time.time() - 30, TMUX_PANE, os.getpid()))
    session_log(host, "s-moved-1", {"event": "UserPromptSubmit", "t": time.time(), "pane": TMUX_PANE})
    assert connector(host).act("s-moved-1", "nudge", repr(since)) == (False, "it moved on since it was checked")


def test_a_cmux_session_gets_its_continue_through_cmux_send(host: dict[str, Path]) -> None:
    since = session_log(host, "s-cmux-1", *failure(time.time(), CMUX_PANE, os.getpid()))
    assert connector(host).act("s-cmux-1", "nudge", repr(since))[0]
    assert calls(host)[-1] == ["send", "--workspace", "W-1", "--surface", "S-1", "--", "continue\\n"]


def test_a_cmux_surface_running_another_claude_is_left_alone(host: dict[str, Path]) -> None:
    pane = {**CMUX_PANE, "CMUX_CLAUDE_PID": "999999"}
    since = session_log(host, "s-cmux-2", *failure(time.time(), pane, os.getpid()))
    assert connector(host).act("s-cmux-2", "nudge", repr(since)) == (
        False,
        "its cmux surface runs another claude process",
    )


def dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_a_session_lost_to_a_reboot_is_resumed_in_a_new_tmux_session(host: dict[str, Path], tmp_path: Path) -> None:
    gone = [{"event": "PostToolUse", "t": 1000.0, "cwd": str(tmp_path), "pane": TMUX_PANE, "pid": dead_pid()}]
    since = session_log(host, "s-gone-1", *gone)
    done, detail = connector(host, claude="/opt/claude").act("s-gone-1", "resume", repr(since))
    assert (done, detail) == (True, "resumed in tmux session resume-s-gone-1")
    command = ["new-session", "-d", "-s", "resume-s-gone-1", "-c", str(tmp_path), "/opt/claude --resume s-gone-1"]
    assert calls(host)[-1] == command


def test_a_session_closed_without_a_reboot_is_not_resumed(host: dict[str, Path], tmp_path: Path) -> None:
    gone = [{"event": "PostToolUse", "t": time.time(), "cwd": str(tmp_path), "pane": TMUX_PANE, "pid": dead_pid()}]
    since = session_log(host, "s-closed-1", *gone)
    done, detail = connector(host).act("s-closed-1", "resume", repr(since))
    assert not done and "closed on purpose" in detail and calls(host) == []


def test_the_tick_nudges_once_then_hands_the_session_to_the_owner(
    host: dict[str, Path], tmp_path: Path, claude: int
) -> None:
    stage(host, screen=READY, pane_pid=claude)
    session_log(host, "s-tick-1", *failure(time.time() - 3600, TMUX_PANE, claude))
    agents = connector(host, channel="discord:agents", retry_after="0s")
    [spec] = parse_specs({"agents": {"type": "agents", "every": "0s", "channel": "discord:agents"}})
    ledger = Ledger(tmp_path / "s.db")
    sent: list[tuple[str, str]] = []
    for _ in range(3):
        poll_sources(ledger, [spec], {"agents": agents}, 60)
        relaunch_sessions(ledger, [spec], {"agents": agents}, lambda channel, text: sent.append((channel, text)))
    nudges = [text for _, text in sent if "got a nudge" in text]
    assert len(nudges) == 1 and "typed 'continue'" in nudges[0]
    [item] = ledger.items()
    assert [event.note for event in ledger.history(item.id) if event.actor == "relaunch"] == [
        "nudge done: typed 'continue' in tmux pane %3"
    ]
    session_log(host, "s-tick-1", *failure(time.time(), TMUX_PANE, claude))
    for _ in range(2):
        poll_sources(ledger, [spec], {"agents": agents}, 60)
        relaunch_sessions(ledger, [spec], {"agents": agents}, lambda channel, text: sent.append((channel, text)))
    assert "already given its nudge" in sent[-1][1] and "needs you" in sent[-1][1]
    assert len([call for call in calls(host) if call[-1] == "Enter"]) == 1


def test_a_rate_limited_session_waits_for_claude_code_to_give_up_resuming(
    host: dict[str, Path], tmp_path: Path, claude: int
) -> None:
    stage(host, screen=READY, pane_pid=claude)
    session_log(host, "s-quota-1", *failure(time.time() - 3600, TMUX_PANE, claude, error="rate_limit"))
    agents = connector(host, retry_after="0s")
    [spec] = parse_specs({"agents": {"type": "agents", "every": "0s"}})
    ledger = Ledger(tmp_path / "s.db")
    for _ in range(2):
        poll_sources(ledger, [spec], {"agents": agents}, 60)
        relaunch_sessions(ledger, [spec], {"agents": agents})
    assert calls(host) == []
    notice = {"event": "Notification", "t": time.time(), "notification": "quota_auto_resume_stale", "pane": TMUX_PANE}
    session_log(host, "s-quota-1", notice)
    for _ in range(2):
        poll_sources(ledger, [spec], {"agents": agents}, 60)
        relaunch_sessions(ledger, [spec], {"agents": agents})
    assert [call for call in calls(host) if call[-1] == "Enter"]


def test_relaunch_is_off_unless_turned_on(host: dict[str, Path], tmp_path: Path, claude: int) -> None:
    session_log(host, "s-off-01", *failure(time.time() - 3600, TMUX_PANE, claude))
    [spec] = parse_specs({"agents": {"type": "agents", "every": "0s", "retry_after": "0s"}})
    agents = build_connector(spec)
    ledger = Ledger(tmp_path / "s.db")
    for _ in range(3):
        poll_sources(ledger, [spec], {"agents": agents}, 60)
        relaunch_sessions(ledger, [spec], {"agents": agents})
    assert calls(host) == []


@pytest.mark.parametrize("nudge", ["", "continue\nrm -rf ~", "x" * 300, "continue\\n"])
def test_the_nudge_is_one_short_plain_line(nudge: str) -> None:
    with pytest.raises(ConnectorConfigError, match="nudge"):
        parse_specs({"agents": {"type": "agents", "nudge": nudge}})


FAKE_CLAUDE = """
import sys
print("API Error: Overloaded")
print("\\u2500" * 40)
print("\\u276f ")
print("\\u2500" * 40)
sys.stdout.flush()
with open(sys.argv[1], "a") as out:
    for line in sys.stdin:
        out.write(line)
        out.flush()
"""


def screen_of(base: list[str], target: str) -> str:
    return subprocess.run([*base, "capture-pane", "-p", "-t", target], capture_output=True, text=True).stdout


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")
def test_a_real_tmux_pane_receives_the_continue(host: dict[str, Path], tmp_path: Path) -> None:
    tmux = str(shutil.which("tmux"))
    script, received = tmp_path / "claude.py", tmp_path / "received.txt"
    script.write_text(FAKE_CLAUDE)
    base = [tmux, "-L", f"superagent-test-{os.getpid()}"]
    command = [sys.executable, str(script), str(received)]
    subprocess.run([*base, "new-session", "-d", "-s", "t", "-x", "100", "-y", "20", *command], check=True)
    try:
        query = [*base, "display-message", "-p", "-t", "t", "#{pane_id} #{pane_pid} #{socket_path}"]
        pane_id, pane_pid, socket = subprocess.run(query, capture_output=True, text=True, check=True).stdout.split()
        deadline = time.time() + 10
        while "\u276f" not in screen_of(base, pane_id):
            assert time.time() < deadline
            time.sleep(0.1)
        pane = {"TMUX_PANE": pane_id, "TMUX": f"{socket},1,0"}
        since = session_log(host, "s-real-1", *failure(time.time(), pane, int(pane_pid)))
        done, detail = connector(host, tmux=tmux).act("s-real-1", "nudge", repr(since))
        assert done, detail
        while "continue" not in (received.read_text() if received.exists() else ""):
            assert time.time() < deadline
            time.sleep(0.1)
    finally:
        subprocess.run([*base, "kill-server"], check=False)
