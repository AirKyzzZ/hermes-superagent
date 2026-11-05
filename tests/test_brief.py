from __future__ import annotations

import random
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from superagent.brief import BriefSettings, build_brief, excused
from superagent.cli import main
from superagent.config import ConfigError, parse_config
from superagent.connectors import ConnectorSpec, Poll
from superagent.ledger import ItemState, Ledger, SourceItem, Upstream
from superagent.polling import poll_sources
from superagent.runtime import open_runtime, send_brief_when_due
from superagent.store import Store

OUTBOUND = Path(__file__).with_name("e2e") / "outbound.py"
SETTINGS = BriefSettings(stale_after=3 * 86400, timezone="UTC")
TASKS = ConnectorSpec("tasks", "command")
SLEEP = ConnectorSpec("sleep", "command", track=False)
AGENTS = ConnectorSpec("agents-mac", "agents", owned=True)


def age(ledger: Ledger, identifier: str, days: float) -> None:
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "UPDATE items SET last_changed = last_changed - ?, first_seen = first_seen - ? WHERE id = ?",
            (days * 86400, days * 86400, identifier),
        )


def brief(ledger: Ledger, specs: list[ConnectorSpec], since: float | None = None) -> str:
    now = time.time()
    return build_brief(ledger, specs, now - 86400 if since is None else since, now, SETTINGS)


def test_each_kind_of_item_lands_in_its_section(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    new = ledger.record("tasks", SourceItem("t1", "Renew the domain"))
    old = ledger.record("tasks", SourceItem("t2", "Pay rent"))
    ledger.move(old.id, ItemState.TRIAGED, "owner", "mine")
    age(ledger, old.id, 5)
    gone = ledger.record("tasks", SourceItem("t3", "Call the bank"))
    ledger.mark_missing("tasks", ["t1", "t2"])
    noise = ledger.record("tasks", SourceItem("t4", "Weekly newsletter"))
    ledger.move(noise.id, ItemState.IGNORED, "rule:newsletters", "newsletter")
    ledger.record("sleep", SourceItem("2026-11-05", "Sleep 6h12, HRV 48"))
    ledger.move("sleep:2026-11-05", ItemState.IGNORED, "rule:untracked", "informational source")
    session = ledger.record("agents-mac", SourceItem("s1", "site", meta={"state": "waiting"}, excerpt="Ship it?"))
    ledger.move(session.id, ItemState.TRIAGED, "rule:owned", "")
    text = brief(ledger, [TASKS, SLEEP, AGENTS])
    sections = {block.splitlines()[0]: block for block in text.split("\n\n")}
    assert new.handle in sections["New, no owner yet (1)"]
    assert old.handle in sections["Untouched for a while (1)"] and "[5d]" in sections["Untouched for a while (1)"]
    assert gone.handle in sections["Gone from their source without a close (1)"]
    assert "Weekly newsletter [because newsletter]" in sections["Ignored since the last brief (1)"]
    assert "Sleep 6h12, HRV 48" in sections["Today (1)"]
    assert session.handle in sections["Waiting on you (1)"] and "[waiting]" in sections["Waiting on you (1)"]
    assert "tasks: 3 open" in sections["Sources (2)"]


def test_source_problems_come_first(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    spec = ConnectorSpec("mail", "command", every_seconds=0)

    class Broken:
        timeout = 1.0

        def poll(self, cursor: str) -> Poll:
            raise RuntimeError("token expired")

        def confirm(self, external_id: str) -> Upstream:
            raise RuntimeError("unused")

    for _ in range(3):
        poll_sources(ledger, [spec], {"mail": Broken()}, 60)
    text = brief(ledger, [spec])
    assert "! connector mail: 3 failed polls in a row, last: token expired" in text


def test_every_open_item_is_shown_unless_it_is_excused(tmp_path: Path) -> None:
    generator = random.Random(11)
    ledger = Ledger(tmp_path / "s.db")
    specs = [TASKS, AGENTS, SLEEP]
    now = time.time()
    for index in range(300):
        connector = generator.choice(["tasks", "agents-mac", "sleep"])
        state = generator.choice(["working", "waiting", "idle", "stuck", "background"])
        item = ledger.record(connector, SourceItem(f"x{index}", f"item {index}", meta={"state": state}))
        roll = generator.random()
        if roll < 0.2:
            ledger.move(item.id, ItemState.TRIAGED, "owner", "mine")
        elif roll < 0.3:
            ledger.move(item.id, ItemState.WAITING, "owner", "needs me")
        elif roll < 0.4:
            ledger.move(item.id, ItemState.DONE, "owner", "done")
        elif roll < 0.5:
            ledger.snooze(item.id, now + 86400, "owner")
        if generator.random() < 0.4:
            age(ledger, item.id, generator.choice([1, 4, 10]))
    ledger.mark_missing("tasks", [f"x{n}" for n in range(0, 300, 2)])
    store = Store(tmp_path / "s.db")
    store.queue_message("brief", "discord:brief", brief(ledger, specs))
    text = "\n".join(message.text for message in store.pending_outbox())
    assert len(store.pending_outbox()) > 1
    sessions = {"agents-mac"}
    missed = [
        item.handle
        for item in ledger.items(open_only=True)
        if item.connector != "sleep"
        and not excused(item, now, SETTINGS.stale_after, sessions)
        and item.handle not in text
    ]
    assert missed == []


def test_nothing_outside_the_excused_cases_is_ever_left_out(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    item = ledger.record("tasks", SourceItem("t1", "Old but triaged"))
    ledger.move(item.id, ItemState.TRIAGED, "owner", "mine")
    assert item.handle not in brief(ledger, [TASKS])
    age(ledger, item.id, 4)
    assert item.handle in brief(ledger, [TASKS])


def config(tmp_path: Path, extra: str = "") -> None:
    sent = tmp_path / "sent.tsv"
    (tmp_path / "superagent.toml").write_text(
        f"""
[outbound]
argv = ["{sys.executable}", "{OUTBOUND}", "{sent}", "{{channel}}", "{{text}}"]

[channels]
brief = "discord:brief"

[brief]
hour = 8
timezone = "Europe/Berlin"
{extra}
""",
        encoding="utf-8",
    )


def at(hour: int, day: int = 6) -> float:
    return datetime(2026, 11, day, hour, 30, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()


def test_the_brief_goes_out_once_a_day_at_or_after_its_hour(tmp_path: Path) -> None:
    config(tmp_path)
    runtime = open_runtime(tmp_path)
    runtime.ledger.record("tasks", SourceItem("t1", "Renew the domain"))
    assert not send_brief_when_due(runtime, at(7))
    assert send_brief_when_due(runtime, at(8))
    assert not send_brief_when_due(runtime, at(15))
    assert send_brief_when_due(runtime, at(19, day=7))
    texts = [message.text for message in runtime.store.pending_outbox()]
    assert len(texts) == 2 and all(text.startswith("Brief for") for text in texts)


def test_the_brief_command_prints_and_can_send(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config(tmp_path)
    Ledger(tmp_path / "superagent.db").record("tasks", SourceItem("t1", "Renew the domain"))
    assert main(["--home", str(tmp_path), "brief"]) == 0
    assert "Renew the domain" in capsys.readouterr().out
    assert main(["--home", str(tmp_path), "brief", "--send"]) == 0
    assert open_runtime(tmp_path).store.pending_outbox()[0].channel == "discord:brief"


@pytest.mark.parametrize(
    "section",
    [
        "[brief]\nhour = 24",
        "[brief]\nhour = true",
        '[brief]\ntimezone = "Mars/Olympus"',
        '[brief]\nstale_after = "soon"',
    ],
)
def test_bad_brief_settings_are_refused(section: str) -> None:
    with pytest.raises(ConfigError):
        parse_config(f"{section}\n")
