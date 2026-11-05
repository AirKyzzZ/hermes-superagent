from __future__ import annotations

import fcntl
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from superagent.config import ConfigError, parse_config
from superagent.domain import Trigger
from superagent.gate import SemanticRule
from superagent.router import Assessment
from superagent.runtime import open_runtime, tick

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
