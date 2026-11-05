from __future__ import annotations

import fcntl
import json
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from superagent.config import LAST_GOOD_FILENAME, ConfigError, parse_config
from superagent.domain import ReportState, Trigger
from superagent.gate import SemanticRule
from superagent.router import ASSESS_ATTEMPTS, MAX_ASSESS_BATCH, MAX_SEND_ATTEMPTS, Assessment
from superagent.runtime import open_runtime, tick
from superagent.worker import IDENTITY_FILENAME

CONFIG = f"""
[sources.github]
policy = "candidate"
allow = ["alice"]

[[semantic]]
name = "r"
text = "t"

[worker]
argv = ["{sys.executable}", "-c", "pass", "{{brief}}"]

[outbound]
argv = ["{sys.executable}", "-c", "raise SystemExit(1)", "{{channel}}", "{{text}}"]
"""


class BrokenAssessor:
    def assess(self, batch: Sequence[Trigger], rules: Sequence[SemanticRule]) -> Sequence[Assessment]:
        raise ValueError("assessor returned nothing usable")


def home_with_config(tmp_path: Path, config: str = CONFIG) -> Path:
    (tmp_path / "superagent.toml").write_text(config, encoding="utf-8")
    return tmp_path


def test_a_broken_assessor_does_not_stop_the_watchdog(tmp_path: Path) -> None:
    runtime = open_runtime(home_with_config(tmp_path))
    runtime.router.ingest(Trigger("c1", "github", "pr/1", "alice", "looks broken"))
    runtime.router.ingest(Trigger("c2", "github", "pr/2", "alice", "fix it", "pr/2", True))
    result = tick(runtime, assessor=BrokenAssessor())
    assert result.errors == ("assess: assessor returned nothing usable",)
    assert len(result.checks) == 1


def test_a_failed_outbound_command_does_not_crash_the_tick(tmp_path: Path) -> None:
    runtime = open_runtime(home_with_config(tmp_path))
    runtime.router.ingest(Trigger("t1", "github", "pr/1", "alice", "fix it", "pr/1", True))
    assignment = runtime.store.running_assignments()[0]
    runtime.store.end_assignment(assignment.id)
    assert tick(runtime).errors == ()


def test_overlapping_ticks_skip(tmp_path: Path) -> None:
    runtime = open_runtime(home_with_config(tmp_path))
    fd = os.open(tmp_path / "tick.lock", os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert tick(runtime).skipped is True
    finally:
        os.close(fd)
    assert tick(runtime).skipped is False


def test_config_rejects_a_typo_in_watchdog_keys() -> None:
    with pytest.raises(ConfigError, match="unknown"):
        parse_config('[worker]\nargv = ["w", "{brief}"]\n[watchdog]\nstall_after_second = 5')


def test_config_requires_a_brief_placeholder() -> None:
    with pytest.raises(ConfigError, match="brief"):
        parse_config('[worker]\nargv = ["w"]')


def test_cli_ingest_defaults_the_channel_to_the_thread(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from superagent.cli import main

    home_with_config(tmp_path)
    code = main(
        ["--home", str(tmp_path), "ingest", "--source", "github", "--thread", "pr/9", "--actor", "alice"]
        + ["--text", "fix", "--addressed"]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "dispatched"
    assert open_runtime(tmp_path).store.list_tickets()[0].channel == "pr/9"


class RecordingAssessor:
    def __init__(self) -> None:
        self.batches: list[int] = []

    def assess(self, batch: Sequence[Trigger], rules: Sequence[SemanticRule]) -> Sequence[Assessment]:
        self.batches.append(len(batch))
        return []


def test_a_broken_config_keeps_the_last_good_one_and_says_so(tmp_path: Path) -> None:
    home = home_with_config(tmp_path)
    open_runtime(home)
    assert stat.S_IMODE((home / LAST_GOOD_FILENAME).stat().st_mode) == 0o600
    (home / "superagent.toml").write_text(CONFIG.replace('policy = "candidate"', 'policy = "candidate'))
    runtime = open_runtime(home)
    assert runtime.config.rules.sources["github"].allow == frozenset({"alice"})
    errors = tick(runtime).errors
    assert any(error.startswith("config: ") and "last good config" in error for error in errors)


def test_a_broken_config_with_no_good_one_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / "superagent.toml").write_text("[sources.github\n")
    with pytest.raises(ConfigError):
        open_runtime(tmp_path)


@pytest.mark.parametrize(
    "typo",
    [
        '[sources.github]\npolicy = "candidate"\nallowed = ["alice"]\n',
        "[workers]\nargv = []\n",
        '[[semantic]]\nname = "r"\ntxt = "t"\n',
    ],
)
def test_config_rejects_unknown_keys(typo: str) -> None:
    with pytest.raises(ConfigError, match="unknown"):
        parse_config(typo + '\n[worker]\nargv = ["w", "{brief}"]\n')


def test_candidate_assessment_takes_a_bounded_batch(tmp_path: Path) -> None:
    runtime = open_runtime(home_with_config(tmp_path))
    for number in range(MAX_ASSESS_BATCH + 5):
        runtime.router.ingest(Trigger(f"c{number}", "github", f"pr/{number}", "alice", "looks broken"))
    assessor = RecordingAssessor()
    tick(runtime, assessor=assessor)
    tick(runtime, assessor=assessor)
    assert assessor.batches == [MAX_ASSESS_BATCH, 5]


def test_a_batch_that_keeps_failing_is_given_up(tmp_path: Path) -> None:
    runtime = open_runtime(home_with_config(tmp_path))
    runtime.router.ingest(Trigger("c1", "github", "pr/1", "alice", "looks broken"))
    for _ in range(ASSESS_ATTEMPTS):
        tick(runtime, assessor=BrokenAssessor())
    assert runtime.store.pending_candidates() == []
    assert runtime.store.assessment_of("c1") == "assessment failed"


def test_undelivered_and_dropped_messages_fail_the_tick(tmp_path: Path) -> None:
    home = home_with_config(tmp_path)
    runtime = open_runtime(home)
    runtime.router.ingest(Trigger("c2", "github", "pr/2", "alice", "fix it", "pr/2", True))
    [assignment] = runtime.store.running_assignments()
    identity = home / "workers" / assignment.ticket_id / assignment.id / IDENTITY_FILENAME
    token = json.loads(identity.read_text())["token"]
    runtime.store.add_report(assignment.id, token, ReportState.READY, "done")
    errors = [tick(runtime).errors for _ in range(MAX_SEND_ATTEMPTS)]
    assert any("not delivered yet" in error for error in errors[0])
    assert any("dropped after" in error for error in errors[-1])
    assert runtime.store.undelivered_count() == 1


def test_old_ignored_events_are_pruned(tmp_path: Path) -> None:
    runtime = open_runtime(home_with_config(tmp_path))
    runtime.router.ingest(Trigger("x1", "unknown", "", "mallory", "secret stuff"))
    assert runtime.store.prune_ignored_triggers(0.0) == 1
    assert runtime.store.get_trigger("x1") is None
