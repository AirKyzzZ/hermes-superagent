from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from superagent.brief import BriefSettings, build_brief
from superagent.config import parse_config
from superagent.connectors import ConnectorConfigError, ConnectorSpec, build_connector, parse_specs
from superagent.ledger import Ledger, Upstream
from superagent.polling import poll_sources
from superagent.starter import Choices, starter_config

FAKE = Path(__file__).with_name("fake_codexbar.py")
USAGE = json.loads(Path(__file__).with_name("fixtures").joinpath("codexbar_usage.synthetic.json").read_text())
CODEX, CLAUDE, ANTIGRAVITY, OPENROUTER, DEEPSEEK = USAGE
SETTINGS = BriefSettings(stale_after=86400.0, timezone="Europe/Paris")


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "codexbar.json"
    monkeypatch.setenv("FAKE_CODEXBAR_FIXTURE", str(path))
    return path


def write(path: Path, **data: Any) -> None:
    path.write_text(json.dumps({"enabled": USAGE, "providers": {}, **data}), encoding="utf-8")


def spec_for(tmp_path: Path, **extra: object) -> ConnectorSpec:
    wrapper = tmp_path / "codexbar"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    body = {"type": "codexbar", "every": "0s", "codexbar": str(wrapper), "env": ["FAKE_CODEXBAR_FIXTURE"], **extra}
    return parse_specs({"quota": body})[0]


def with_used(entry: dict[str, Any], used: int) -> dict[str, Any]:
    secondary = {**entry["usage"]["secondary"], "usedPercent": used}
    return {**entry, "usage": {**entry["usage"], "secondary": secondary}}


def test_each_enabled_provider_becomes_a_reading_titled_with_its_quota(tmp_path: Path, fixture: Path) -> None:
    write(fixture, enabled=[CLAUDE, ANTIGRAVITY, OPENROUTER, DEEPSEEK])
    poll = build_connector(spec_for(tmp_path)).poll("")
    assert poll.problems == ()
    assert [(item.external_id, item.title) for item in poll.items] == [
        ("claude", "claude: Weekly 21%"),
        ("antigravity", "antigravity: Gemini Models 12%, Claude and GPT 3.5%"),
        ("openrouter", "openrouter: 7.25/50 USD"),
    ]
    assert poll.items[0].meta == {
        "provider": "claude",
        "source": "web",
        "plan": "Claude Max 20x",
        "secondary_label": "Weekly",
        "secondary_used": "21",
        "secondary_resets_at": "2026-11-15T19:00:00Z",
        "extra": "Fable only 4%",
        "cost": "3.5/20 USD",
    }


def test_a_provider_in_error_is_a_problem_and_the_others_still_record(tmp_path: Path, fixture: Path) -> None:
    write(fixture)
    poll = build_connector(spec_for(tmp_path)).poll("")
    assert len(poll.items) == 3 and poll.problems == ("codex: Codex login expired, run codex login again.",)


def test_listed_providers_are_asked_one_by_one(tmp_path: Path, fixture: Path) -> None:
    write(fixture, enabled=[], providers={"claude": [CLAUDE], "openrouter": [OPENROUTER]}, fail=["grok"])
    poll = build_connector(spec_for(tmp_path, providers=["claude", "grok", "openrouter"])).poll("")
    assert [item.external_id for item in poll.items] == ["claude", "openrouter"]
    assert len(poll.problems) == 1 and poll.problems[0].startswith("grok: ") and "exited 1" in poll.problems[0]


def test_malformed_output_fails_and_records_nothing(tmp_path: Path, fixture: Path) -> None:
    write(fixture, broken=True)
    spec = spec_for(tmp_path)
    ledger = Ledger(tmp_path / "s.db")
    poll_sources(ledger, [spec], {spec.name: build_connector(spec)}, 300)
    assert ledger.items() == [] and "JSON" in ledger.source("quota").last_error


@pytest.mark.parametrize(
    ("entries", "problem"),
    [
        ({"not": "a list"}, "unexpected shape"),
        ([], "no provider"),
        (["text"], "without a provider"),
        ([{"provider": "x"}], "no usage"),
    ],
)
def test_odd_readings_are_reported(tmp_path: Path, fixture: Path, entries: object, problem: str) -> None:
    write(fixture, enabled=entries)
    poll = build_connector(spec_for(tmp_path)).poll("")
    assert poll.items == () and problem in poll.problems[0]


def test_a_reading_only_shows_in_the_brief_when_it_moved(tmp_path: Path, fixture: Path) -> None:
    write(fixture, enabled=[CLAUDE])
    spec = spec_for(tmp_path)
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    since = time.time()
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    assert "claude:" not in build_brief(ledger, [spec], since, time.time(), SETTINGS).text
    write(fixture, enabled=[with_used(CLAUDE, 55)])
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    brief = build_brief(ledger, [spec], since, time.time(), SETTINGS).text
    assert "claude: Weekly 55%" in brief and "Reopened" not in brief


def test_a_provider_switched_off_closes_nothing(tmp_path: Path, fixture: Path) -> None:
    write(fixture, enabled=[CLAUDE, ANTIGRAVITY])
    spec = spec_for(tmp_path)
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    write(fixture, enabled=[CLAUDE])
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    assert all(item.missing_since is None for item in ledger.items())
    assert connector.confirm("antigravity") is Upstream.UNKNOWN


def test_the_command_can_be_a_list_to_reach_another_machine(tmp_path: Path, fixture: Path) -> None:
    write(fixture, enabled=[CLAUDE])
    spec = spec_for(tmp_path)
    remote = parse_specs(
        {
            "quota": {
                "type": "codexbar",
                "codexbar": [sys.executable, str(FAKE)],
                "env": ["FAKE_CODEXBAR_FIXTURE"],
            }
        }
    )[0]
    assert build_connector(remote).poll("").items == build_connector(spec).poll("").items


def test_codexbar_is_informational() -> None:
    [spec] = parse_specs({"quota": {"type": "codexbar"}})
    assert not spec.track
    with pytest.raises(ConnectorConfigError, match="informational"):
        parse_specs({"quota": {"type": "codexbar", "track": True}})


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"providers": "claude"}, "providers"),
        ({"providers": ["--all"]}, "providers"),
        ({"providers": ["all"]}, "one by one"),
        ({"providers": ["both"]}, "one by one"),
        ({"codexbar": ""}, "codexbar"),
        ({"codexbar": []}, "codexbar"),
        ({"codexbar": ["ssh", 1]}, "codexbar"),
        ({"timeout": 301}, "timeout"),
        ({"env": "X"}, "env"),
    ],
)
def test_bad_settings_are_refused(extra: dict[str, object], message: str) -> None:
    with pytest.raises(ConnectorConfigError, match=message):
        parse_specs({"quota": {"type": "codexbar", **extra}})


def test_setup_writes_a_codexbar_block_that_parses() -> None:
    [spec] = parse_config(starter_config(Choices(hermes="/h/hermes", alerts="discord:1", codexbar=True))).connectors
    assert (spec.type, spec.every_seconds, spec.track) == ("codexbar", 3600.0, False)
