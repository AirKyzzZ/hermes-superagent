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


def test_an_alert_fires_once_it_persists_and_resolves_when_it_clears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock(monkeypatch)
    store = Store(tmp_path / "s.db")
    assert store.sync_alerts({"relay": "relay: 1 waiting"}, 300) == ([], [])
    clock.now += 200
    assert store.sync_alerts({"relay": "relay: 2 waiting"}, 300) == ([], [])
    clock.now += 200
    assert store.sync_alerts({"relay": "relay: 3 waiting"}, 300) == ([("relay", "relay: 3 waiting")], [])
    clock.now += 200
    assert store.sync_alerts({"relay": "relay: 4 waiting"}, 300) == ([], [])
    assert store.sync_alerts({}, 300) == ([], [("relay", "relay: 4 waiting")])
    assert store.active_alerts() == []


def test_a_blip_that_never_alerted_clears_silently(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    store.sync_alerts({"assess": "assess: timeout"}, 300)
    assert store.sync_alerts({}, 300) == ([], [])


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


def hold_lock(home: Path, started: float) -> int:
    descriptor = os.open(home / "tick.lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.ftruncate(descriptor, 0)
    os.pwrite(descriptor, json.dumps({"pid": 4242, "started": started}).encode(), 0)
    return descriptor


def test_a_tick_held_up_past_its_deadline_fails_loudly(tmp_path: Path) -> None:
    write_config(tmp_path, tmp_path / "sent.tsv", "deadline_seconds = 60\n")
    descriptor = hold_lock(tmp_path, time.time() - 600)
    try:
        result = tick(open_runtime(tmp_path))
    finally:
        os.close(descriptor)
    assert result.skipped
    assert any("pid 4242" in error and "held the lock for 10 minutes" in error for error in result.errors)


def test_an_overlapping_tick_inside_its_deadline_skips_quietly(tmp_path: Path) -> None:
    write_config(tmp_path, tmp_path / "sent.tsv")
    descriptor = hold_lock(tmp_path, time.time())
    try:
        result = tick(open_runtime(tmp_path))
    finally:
        os.close(descriptor)
    assert result.skipped and result.errors == ()


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
