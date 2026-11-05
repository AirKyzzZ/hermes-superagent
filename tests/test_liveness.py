from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from superagent.config import ConfigError, parse_config
from superagent.domain import Trigger
from superagent.gate import RuleMode, SemanticRule
from superagent.judges import MODEL_TIMEOUT_SECONDS, LlmAssessor, LlmDriftJudge
from superagent.runtime import open_runtime, tick
from superagent.store import MESSAGE_LIMIT, Store, split_message

OUTBOUND = Path(__file__).with_name("e2e") / "outbound.py"


def write_config(home: Path, sent: Path, extra: str = "") -> None:
    (home / "superagent.toml").write_text(
        f"""
[sources.github]
policy = "candidate"

[worker]
argv = ["{sys.executable}", "-c", "pass", "{{brief}}"]

[outbound]
argv = ["{sys.executable}", "{OUTBOUND}", "{sent}", "{{channel}}", "{{text}}"]

[channels]
alerts = "discord:alerts"

[tick]
alert_after_seconds = 0
{extra}
""",
        encoding="utf-8",
    )


def sent_lines(sent: Path) -> list[str]:
    return sent.read_text(encoding="utf-8").splitlines() if sent.exists() else []


def test_a_short_message_stays_whole() -> None:
    assert split_message("hello") == ["hello"]


def test_a_long_message_splits_on_lines_in_order() -> None:
    lines = [f"line {index:04d} " + "x" * 80 for index in range(60)]
    parts = split_message("\n".join(lines))
    assert len(parts) > 1
    assert all(len(part) <= MESSAGE_LIMIT for part in parts)
    assert all(part.startswith(f"({index}/{len(parts)}) ") for index, part in enumerate(parts, 1))
    rejoined = "\n".join(part.split(") ", 1)[1] for part in parts)
    assert rejoined == "\n".join(lines)


def test_a_single_huge_line_is_cut_to_fit() -> None:
    parts = split_message("y" * (MESSAGE_LIMIT * 3))
    assert all(len(part) <= MESSAGE_LIMIT for part in parts)
    assert "".join(part.split(") ", 1)[1] for part in parts) == "y" * (MESSAGE_LIMIT * 3)


def test_queued_parts_go_out_in_order(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    store.queue_message("digest", "discord:brief", "\n".join("z" * 100 for _ in range(50)))
    texts = [message.text for message in store.pending_outbox()]
    assert len(texts) == len(split_message("\n".join("z" * 100 for _ in range(50))))
    assert texts[0].startswith("(1/") and texts[-1].startswith(f"({len(texts)}/")


class Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = 2_000_000.0
        monkeypatch.setattr("superagent.store.time.time", lambda: self.now)


def test_an_alert_fires_once_it_persists_and_resolves_once_it_stays_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(monkeypatch)
    store = Store(tmp_path / "s.db")
    channel = "discord:alerts"
    assert store.sync_alerts({"relay": "relay: 1"}, 300, channel) == ([], [])
    clock.now += 200
    assert store.sync_alerts({}, 300, channel) == ([], [])
    clock.now += 200
    assert store.sync_alerts({"relay": "relay: 3"}, 300, channel) == (["relay: 3"], [])
    clock.now += 200
    assert store.sync_alerts({"relay": "relay: 4"}, 300, channel) == ([], [])
    clock.now += 100
    assert store.sync_alerts({}, 300, channel) == ([], [])
    clock.now += 300
    assert store.sync_alerts({}, 300, channel) == ([], ["relay: 4"])
    assert store.active_alerts() == []
    assert [message.text for message in store.pending_outbox()] == ["Alert: relay: 3", "Resolved: relay: 4"]


def test_a_blip_that_never_alerted_clears_silently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(monkeypatch)
    store = Store(tmp_path / "s.db")
    store.sync_alerts({"assess": "assess: timeout"}, 300, "discord:alerts")
    clock.now += 300
    assert store.sync_alerts({}, 300, "discord:alerts") == ([], [])
    assert store.pending_outbox() == []


def test_an_alert_waits_for_a_channel_instead_of_being_lost(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    assert store.sync_alerts({"relay": "relay: down"}, 0, "") == ([], [])
    assert store.sync_alerts({"relay": "relay: down"}, 0, "discord:alerts") == (["relay: down"], [])
    assert [message.text for message in store.pending_outbox()] == ["Alert: relay: down"]


def test_an_immediate_alert_is_queued_once(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    assert store.raise_alert_now("tick", "tick: stuck", "discord:alerts")
    assert not store.raise_alert_now("tick", "tick: stuck", "discord:alerts")
    assert [message.text for message in store.pending_outbox()] == ["Alert: tick: stuck"]


def test_a_broken_config_reaches_the_alerts_channel_and_its_fix_too(tmp_path: Path) -> None:
    sent = tmp_path / "sent.tsv"
    write_config(tmp_path, sent)
    assert tick(open_runtime(tmp_path)).errors == ()
    config = tmp_path / "superagent.toml"
    good = config.read_text(encoding="utf-8")
    config.write_text(good.replace("[channels]", "[channels"), encoding="utf-8")
    assert tick(open_runtime(tmp_path)).errors
    assert any(line.startswith("discord:alerts\tAlert: config:") for line in sent_lines(sent))
    config.write_text(good, encoding="utf-8")
    assert tick(open_runtime(tmp_path)).errors == ()
    assert any(line.startswith("discord:alerts\tResolved: config:") for line in sent_lines(sent))


def write_lock(home: Path, record: dict[str, object], *, hold: bool) -> int:
    descriptor = os.open(home / "tick.lock", os.O_CREAT | os.O_RDWR, 0o600)
    if hold:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.ftruncate(descriptor, 0)
    os.pwrite(descriptor, json.dumps(record).encode(), 0)
    return descriptor


def test_a_tick_held_up_past_its_deadline_fails_loudly_and_alerts(tmp_path: Path) -> None:
    sent = tmp_path / "sent.tsv"
    write_config(tmp_path, sent, "deadline_seconds = 60\n")
    descriptor = write_lock(tmp_path, {"pid": 4242, "started": time.time() - 600}, hold=True)
    try:
        result = tick(open_runtime(tmp_path))
    finally:
        os.close(descriptor)
    assert result.skipped
    assert any("pid 4242" in error and "held the lock for 10 minutes" in error for error in result.errors)
    assert any("Alert: tick: another tick (pid 4242)" in line for line in sent_lines(sent))


def test_an_overlapping_tick_inside_its_deadline_skips_quietly(tmp_path: Path) -> None:
    write_config(tmp_path, tmp_path / "sent.tsv")
    descriptor = write_lock(tmp_path, {"pid": 1, "started": time.time()}, hold=True)
    try:
        result = tick(open_runtime(tmp_path))
    finally:
        os.close(descriptor)
    assert result.skipped and result.errors == ()


def test_the_record_of_an_old_finished_tick_is_not_a_stuck_holder(tmp_path: Path) -> None:
    write_config(tmp_path, tmp_path / "sent.tsv", "deadline_seconds = 60\n")
    old = time.time() - 3600
    descriptor = write_lock(tmp_path, {"pid": 1, "started": old, "finished": old + 5}, hold=True)
    try:
        result = tick(open_runtime(tmp_path))
    finally:
        os.close(descriptor)
    assert result.skipped and result.errors == ()


def test_a_tick_killed_at_its_deadline_is_alerted_by_the_next_one(tmp_path: Path) -> None:
    sent = tmp_path / "sent.tsv"
    write_config(tmp_path, sent)
    os.close(write_lock(tmp_path, {"pid": 4343, "started": time.time() - 700}, hold=False))
    result = tick(open_runtime(tmp_path))
    assert any("never finished" in error for error in result.errors)
    assert any("Alert: tick: the tick started 12 minutes ago (pid 4343)" in line for line in sent_lines(sent))
    assert tick(open_runtime(tmp_path)).errors == ()


def test_the_heartbeat_only_beats_after_a_clean_tick(tmp_path: Path) -> None:
    beat = tmp_path / "beat"
    script = f"open({str(beat)!r}, 'a').write('x')"
    write_config(
        tmp_path, tmp_path / "sent.tsv", f"\n[heartbeat]\nargv = {json.dumps([sys.executable, '-c', script])}\n"
    )
    assert tick(open_runtime(tmp_path)).errors == ()
    assert beat.read_text() == "x"
    config = tmp_path / "superagent.toml"
    config.write_text(config.read_text(encoding="utf-8").replace(str(OUTBOUND), "/nonexistent.py"), encoding="utf-8")
    runtime = open_runtime(tmp_path)
    runtime.store.queue_message("tk-x", "discord:alerts", "will fail")
    assert tick(runtime).errors
    assert beat.read_text() == "x"


class RecordingLlm:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        raise TimeoutError("slow")


def test_model_calls_carry_a_timeout() -> None:
    llm = RecordingLlm()
    with pytest.raises(TimeoutError):
        LlmAssessor(llm).assess(
            [Trigger("c1", "github", "pr/1", "bot", "ci failed")], [SemanticRule("r", "t", RuleMode.RECORD)]
        )
    LlmDriftJudge(llm).judge("goal", ())
    assert [call["timeout"] for call in llm.calls] == [MODEL_TIMEOUT_SECONDS, MODEL_TIMEOUT_SECONDS]


def test_a_cli_tick_past_its_deadline_is_killed() -> None:
    code = "from superagent.cli import arm_deadline; import time; arm_deadline(0.2); time.sleep(10)"
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30, check=False)
    assert done.returncode == 3
    assert "deadline" in done.stdout
    assert time.monotonic() - started < 8


@pytest.mark.parametrize(
    "extra",
    [
        '[channels]\nalerts = "x"\nbrief = "y"\n',
        "[tick]\ndeadline_seconds = 0\n",
        '[tick]\ndeadline_seconds = "soon"\n',
    ],
)
def test_bad_liveness_settings_are_refused(extra: str) -> None:
    with pytest.raises(ConfigError):
        parse_config(f'[worker]\nargv = ["w", "{{brief}}"]\n{extra}')


def test_splitting_keeps_blank_lines_and_drops_carriage_returns() -> None:
    text = "\r\n".join(["a" * 1000, "", "b" * 1000, "", "c" * 10])
    parts = split_message(text)
    body = "\n".join(part.split(") ", 1)[1] for part in parts)
    assert "\r" not in body
    assert body.replace("\n", "") == "a" * 1000 + "b" * 1000 + "c" * 10
    assert body.count("\n") >= 3


@pytest.mark.parametrize("top", ["tick = 5", 'channels = "x"', "heartbeat = []"])
def test_a_liveness_section_that_is_not_a_table_is_a_config_error(top: str) -> None:
    with pytest.raises(ConfigError, match="must be a table"):
        parse_config(f'{top}\n[worker]\nargv = ["w", "{{brief}}"]\n')


@pytest.mark.parametrize(
    "section",
    [
        '[heartbeat]\nargv = "curl -fsS https://example.test"',
        "[tick]\ndeadline_seconds = nan",
        "[tick]\ndeadline_seconds = inf",
        "[tick]\nalert_after_seconds = nan",
        "[tick]\nalert_after_seconds = true",
    ],
)
def test_malformed_liveness_values_are_config_errors(section: str) -> None:
    with pytest.raises(ConfigError):
        parse_config(f'[worker]\nargv = ["w", "{{brief}}"]\n{section}\n')
