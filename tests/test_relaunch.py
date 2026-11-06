from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import termios
import time
from collections.abc import Iterator
from dataclasses import dataclass
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
    print("\\t".join(str(state[key]) for key in ("pane_pid", "pane_tty", "in_mode", "sync", "dead")))
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
    return paths


@dataclass(frozen=True)
class Claude:
    pid: int
    tty: str


@pytest.fixture
def claude(tmp_path: Path) -> Iterator[Claude]:
    named = tmp_path / "bin" / "claude"
    named.parent.mkdir()
    named.symlink_to(shutil.which("sleep") or "/bin/sleep")
    leader, follower = os.openpty()

    def attach() -> None:
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    process = subprocess.Popen([str(named), "120"], stdin=follower, stdout=follower, stderr=follower, preexec_fn=attach)
    yield Claude(process.pid, os.ttyname(follower))
    process.kill()
    process.wait()
    os.close(leader)
    os.close(follower)


def stage(paths: dict[str, Path], claude: Claude, **state: object) -> None:
    base = {"screen": READY, "pane_pid": claude.pid, "pane_tty": claude.tty, "in_mode": 0, "sync": 0, "dead": 0}
    paths["fixture"].write_text(json.dumps({**base, **state}))


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


def test_a_session_at_an_empty_prompt_gets_one_continue_in_its_tmux_pane(host: dict[str, Path], claude: Claude) -> None:
    stage(host, claude)
    since = session_log(host, "s-nudge-1", *failure(time.time(), TMUX_PANE, claude.pid))
    assert connector(host).act("s-nudge-1", "nudge", repr(since)) == (True, "typed 'continue' in tmux pane %3", "")
    sent = [call for call in calls(host) if "send-keys" in call]
    assert sent == [
        ["-S", "/tmp/tmux-test/default", "send-keys", "-t", "%3", "-l", "continue"],
        ["-S", "/tmp/tmux-test/default", "send-keys", "-t", "%3", "Enter"],
    ]


PRINTED_BOX = "─" * 40 + "\n❯\n" + "─" * 40 + "\nThat is the input box.\n"


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"screen": WORKING}, "still working"),
        ({"screen": DIALOG}, "'do you want'"),
        ({"screen": TYPED}, "isn't empty"),
        ({"screen": PRINTED_BOX + TYPED}, "isn't empty"),
        ({"screen": PRINTED_BOX + "─" * 40 + "\n❯ \n  second line of a draft\n" + "─" * 40}, "no empty input box"),
        ({"screen": "-- NORMAL --\n" + READY}, "'-- normal --'"),
        ({"screen": "Interrupted · What should Claude do instead?\n" + READY}, "'interrupted'"),
        ({"in_mode": 1}, "scrolled back"),
        ({"sync": 1}, "synchronized"),
        ({"dead": 1}, "dead"),
        ({"pane_pid": 1}, "doesn't run in tmux pane %3"),
        ({"pane_tty": "/dev/ttys999"}, "doesn't run in tmux pane %3"),
    ],
)
def test_nothing_is_typed_unless_the_pane_is_waiting_at_an_empty_prompt(
    host: dict[str, Path], claude: Claude, state: dict[str, object], reason: str
) -> None:
    stage(host, claude, **state)
    since = session_log(host, "s-held-1", *failure(time.time(), TMUX_PANE, claude.pid))
    done, detail, _ = connector(host).act("s-held-1", "nudge", repr(since))
    assert not done and reason in detail
    assert not [call for call in calls(host) if "send-keys" in call]


def test_a_claude_in_the_background_of_its_terminal_is_left_alone(host: dict[str, Path], tmp_path: Path) -> None:
    named = tmp_path / "bin" / "claude"
    named.parent.mkdir(exist_ok=True)
    named.symlink_to(shutil.which("sleep") or "/bin/sleep")
    process = subprocess.Popen([str(named), "120"])
    try:
        stage(host, Claude(process.pid, "/dev/ttys000"))
        since = session_log(host, "s-bg-0001", *failure(time.time(), TMUX_PANE, process.pid))
        assert connector(host).act("s-bg-0001", "nudge", repr(since))[1] == (
            "its claude process isn't in the foreground of its terminal"
        )
    finally:
        process.kill()
        process.wait()


def test_a_pane_shared_with_another_live_session_is_left_alone(host: dict[str, Path], claude: Claude) -> None:
    stage(host, claude)
    since = session_log(host, "s-old-01", *failure(time.time(), TMUX_PANE, claude.pid))
    session_log(
        host, "s-new-01", {"event": "SessionStart", "t": time.time() - 600, "pane": TMUX_PANE, "pid": claude.pid}
    )
    assert connector(host).act("s-old-01", "nudge", repr(since))[1] == "another live session shares its pane"


def test_a_session_that_moved_since_the_check_is_left_alone(host: dict[str, Path], claude: Claude) -> None:
    stage(host, claude)
    since = session_log(host, "s-moved-1", *failure(time.time() - 30, TMUX_PANE, claude.pid))
    session_log(host, "s-moved-1", {"event": "UserPromptSubmit", "t": time.time(), "pane": TMUX_PANE})
    assert connector(host).act("s-moved-1", "nudge", repr(since)) == (False, "it moved on since it was checked", "")


def test_a_cmux_session_gets_its_continue_through_cmux_send(host: dict[str, Path], claude: Claude) -> None:
    stage(host, claude)
    since = session_log(host, "s-cmux-1", *failure(time.time(), CMUX_PANE, claude.pid))
    assert connector(host).act("s-cmux-1", "nudge", repr(since))[0]
    assert calls(host)[-1] == ["send", "--workspace", "W-1", "--surface", "S-1", "--", "continue\\n"]


def test_a_failing_tool_is_reported_without_its_output(host: dict[str, Path], claude: Claude) -> None:
    stage(host, claude, screen=READY)
    host["tmux"].write_text("#!/bin/sh\necho 'secret socket detail' >&2\nexit 1\n")
    since = session_log(host, "s-fail-01", *failure(time.time(), TMUX_PANE, claude.pid))
    done, detail, log = connector(host).act("s-fail-01", "nudge", repr(since))
    assert (done, detail) == (False, "the relaunch script failed") and "secret socket detail" in log


def test_the_tick_nudges_once_then_hands_the_session_to_the_owner(
    host: dict[str, Path], tmp_path: Path, claude: Claude
) -> None:
    stage(host, claude)
    session_log(host, "s-tick-1", *failure(time.time() - 3600, TMUX_PANE, claude.pid))
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
    session_log(host, "s-tick-1", *failure(time.time(), TMUX_PANE, claude.pid))
    for _ in range(2):
        poll_sources(ledger, [spec], {"agents": agents}, 60)
        relaunch_sessions(ledger, [spec], {"agents": agents}, lambda channel, text: sent.append((channel, text)))
    assert "already given its nudge" in sent[-1][1] and "needs you" in sent[-1][1]
    assert len([call for call in calls(host) if call[-1] == "Enter"]) == 1


def test_a_relaunch_that_times_out_spends_the_nudge(host: dict[str, Path], tmp_path: Path, claude: Claude) -> None:
    stage(host, claude)
    session_log(host, "s-slow-01", *failure(time.time() - 3600, TMUX_PANE, claude.pid))
    agents = connector(host, retry_after="0s", timeout=1)
    host["tmux"].write_text("#!/bin/sh\nsleep 5\n")
    [spec] = parse_specs({"agents": {"type": "agents", "every": "0s"}})
    ledger = Ledger(tmp_path / "s.db")
    for _ in range(2):
        poll_sources(ledger, [spec], {"agents": connector(host)}, 60)
    relaunch_sessions(ledger, [spec], {"agents": agents})
    [item] = ledger.items()
    [note] = [event.note for event in ledger.history(item.id) if event.actor == "relaunch"]
    assert note.startswith("nudge unknown: the relaunch didn't answer in time")


@pytest.mark.parametrize(
    ("notice", "nudged"), [("quota_auto_resume_stale", True), ("quota_auto_resume_disabled", False)]
)
def test_a_rate_limited_session_waits_for_claude_code_to_give_up_resuming(
    host: dict[str, Path], tmp_path: Path, claude: Claude, notice: str, nudged: bool
) -> None:
    stage(host, claude)
    session_log(host, "s-quota-1", *failure(time.time() - 3600, TMUX_PANE, claude.pid, error="rate_limit"))
    agents = connector(host, retry_after="0s")
    [spec] = parse_specs({"agents": {"type": "agents", "every": "0s"}})
    ledger = Ledger(tmp_path / "s.db")
    for _ in range(2):
        poll_sources(ledger, [spec], {"agents": agents}, 60)
        relaunch_sessions(ledger, [spec], {"agents": agents})
    assert calls(host) == []
    session_log(
        host, "s-quota-1", {"event": "Notification", "t": time.time(), "notification": notice, "pane": TMUX_PANE}
    )
    for _ in range(2):
        poll_sources(ledger, [spec], {"agents": agents}, 60)
        relaunch_sessions(ledger, [spec], {"agents": agents})
    assert bool([call for call in calls(host) if call[-1] == "Enter"]) is nudged


def test_relaunch_is_off_unless_turned_on(host: dict[str, Path], tmp_path: Path, claude: Claude) -> None:
    stage(host, claude)
    session_log(host, "s-off-01", *failure(time.time() - 3600, TMUX_PANE, claude.pid))
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
        done, detail, _ = connector(host, tmux=tmux).act("s-real-1", "nudge", repr(since))
        assert done, detail
        while "continue" not in (received.read_text() if received.exists() else ""):
            assert time.time() < deadline
            time.sleep(0.1)
    finally:
        subprocess.run([*base, "kill-server"], check=False)
