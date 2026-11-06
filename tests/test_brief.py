from __future__ import annotations

import random
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from superagent.brief import Brief, BriefSettings, build_brief
from superagent.cli import main
from superagent.config import ConfigError, parse_config
from superagent.connectors import ConnectorSpec, Poll
from superagent.ledger import ItemState, Ledger, SourceItem, Upstream
from superagent.polling import poll_sources
from superagent.runtime import open_runtime, send_brief_when_due
from superagent.store import Store

OUTBOUND = Path(__file__).with_name("e2e") / "outbound.py"
DAY = 86400.0
SETTINGS = BriefSettings(stale_after=3 * DAY, timezone="UTC")
TASKS = ConnectorSpec("tasks", "command")
SLEEP = ConnectorSpec("sleep", "command", track=False)
AGENTS = ConnectorSpec("agents-mac", "agents", owned=True)


def shift(ledger: Ledger, identifier: str, days: float) -> None:
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "UPDATE items SET last_changed = last_changed - ?, first_seen = first_seen - ? WHERE id = ?",
            (days * DAY, days * DAY, identifier),
        )


def brief(ledger: Ledger, specs: list[ConnectorSpec], since: float | None = None, now: float | None = None) -> Brief:
    moment = time.time() if now is None else now
    return build_brief(ledger, specs, moment - DAY if since is None else since, moment, SETTINGS)


def sections(text: str) -> dict[str, str]:
    return {block.splitlines()[0].rsplit(" (", 1)[0]: block for block in text.split("\n\n")}


def test_each_kind_of_item_lands_in_its_section(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    new = ledger.record("tasks", SourceItem("t1", "Renew the domain"))
    old = ledger.record("tasks", SourceItem("t2", "Pay rent"))
    ledger.move(old.id, ItemState.TRIAGED, "owner", "mine")
    shift(ledger, old.id, 5)
    gone = ledger.record("tasks", SourceItem("t3", "Call the bank"))
    ledger.mark_missing("tasks", ["t1", "t2"])
    noise = ledger.record("tasks", SourceItem("t4", "Weekly newsletter"))
    ledger.move(noise.id, ItemState.IGNORED, "rule:newsletters", "newsletter")
    ledger.record("sleep", SourceItem("2026-11-05", "Sleep 6h12, HRV 48"))
    ledger.move("sleep:2026-11-05", ItemState.IGNORED, "rule:untracked", "informational source")
    session = ledger.record("agents-mac", SourceItem("s1", "site", meta={"state": "waiting"}))
    ledger.move(session.id, ItemState.TRIAGED, "rule:owned", "")
    found = sections(brief(ledger, [TASKS, SLEEP, AGENTS]).text)
    also = found["Also open, each shown at least every 3 days"]
    assert new.handle in found["New since the last brief"]
    assert old.handle in also and "[triaged, 5d]" in also
    assert gone.handle in found["Gone from their source without a close"]
    assert "Weekly newsletter [because newsletter]" in found["Ignored since the last brief"]
    assert "Sleep 6h12, HRV 48" in found["Today"]
    assert session.handle in found["Waiting on you"]
    assert "tasks: 3 open" in found["Sources"]


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
    assert "! connector mail: 3 failed polls in a row, last: token expired" in brief(ledger, [spec]).text


def test_every_open_item_is_delivered_unless_shown_recently_or_snoozed(tmp_path: Path) -> None:
    generator = random.Random(11)
    store = Store(tmp_path / "s.db")
    ledger = Ledger(tmp_path / "s.db")
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
            ledger.snooze(item.id, now + DAY, "owner")
        if generator.random() < 0.4:
            shift(ledger, item.id, generator.choice([1, 4, 10]))
    ledger.mark_missing("tasks", [f"x{n}" for n in range(0, 300, 2)])
    built = brief(ledger, [TASKS, AGENTS, SLEEP], now=now)
    store.queue_message("probe", "discord:brief", built.text)
    delivered = "".join(message.text for message in store.pending_outbox())
    assert len(store.pending_outbox()) > 1
    missed = [
        item.handle
        for item in ledger.items(open_only=True)
        if item.connector != "sleep"
        and not (item.snoozed_until and item.snoozed_until > now)
        and item.handle not in delivered
    ]
    assert missed == []


def test_an_item_that_keeps_changing_still_shows_every_few_days(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    item = ledger.record("tasks", SourceItem("pr1", "Busy PR", updated="0"))
    ledger.move(item.id, ItemState.TRIAGED, "owner", "mine")
    now = time.time()
    with sqlite3.connect(ledger.path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS outbox (seq INTEGER PRIMARY KEY, ticket_id, channel, text)")
    ledger.save_brief("d0", now, "discord:brief", [], brief(ledger, [TASKS], now=now).shown)
    for day in range(1, 5):
        ledger.record("tasks", SourceItem("pr1", "Busy PR", updated=str(day)))
        shown = item.handle in brief(ledger, [TASKS], now=now + day * DAY).text
        assert shown is (day > 3)


def test_a_reopened_item_on_an_owned_source_is_listed(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    session = ledger.record("agents-mac", SourceItem("s1", "site", meta={"state": "working"}))
    ledger.move(session.id, ItemState.DONE, "owner", "closed it")
    before = time.time()
    ledger.record("agents-mac", SourceItem("s1", "site", meta={"state": "idle"}))
    ledger.move(session.id, ItemState.TRIAGED, "rule:owned", "", expected=ItemState.NEW)
    text = brief(ledger, [AGENTS], since=before - 1, now=time.time() + 1).text
    assert session.handle in sections(text)["Reopened at the source"]


def test_a_brief_is_saved_once_and_since_only_moves_once_it_is_delivered(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.db")
    ledger = Ledger(tmp_path / "s.db")
    assert ledger.save_brief("2026-11-06", 1000.0, "discord:brief", ["part 1", "part 2"], ())
    assert not ledger.save_brief("2026-11-06", 2000.0, "discord:brief", ["again"], ())
    assert ledger.last_brief(5000.0) is None
    first, second = store.pending_outbox()
    store.mark_sent(first.seq)
    assert ledger.last_brief(5000.0) is None
    store.mark_sent(second.seq)
    assert ledger.last_brief(5000.0) == 1000.0
    assert ledger.last_brief(500.0) is None


def test_ignores_are_counted_only_up_to_the_brief_moment(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    item = ledger.record("tasks", SourceItem("t1", "Spam"))
    ledger.move(item.id, ItemState.IGNORED, "owner", "spam")
    now = time.time()
    assert "Spam" not in brief(ledger, [TASKS], since=now - DAY, now=now - 60).text
    assert "Spam [because spam]" in brief(ledger, [TASKS], since=now - 60, now=now + 1).text


def test_a_reading_that_changes_shows_again_today(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    ledger.record("sleep", SourceItem("quota", "Claude quota 80%"))
    shift(ledger, "sleep:quota", 2)
    assert "Claude quota" not in brief(ledger, [SLEEP]).text
    ledger.record("sleep", SourceItem("quota", "Claude quota 12%"))
    assert "Claude quota 12%" in brief(ledger, [SLEEP]).text


def test_titles_cannot_forge_lines_attach_files_or_ping_everyone(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    ledger.record("tasks", SourceItem("t1", "x\nWaiting on you (1)\nit-fake"))
    ledger.record("tasks", SourceItem("t2", "MEDIA:/etc/passwd [[as_document]] @everyone <@&123>"))
    text = brief(ledger, [TASKS]).text
    assert "\nWaiting on you" not in text and "\nit-fake" not in text
    assert "MEDIA:" not in text and "[[" not in text
    assert "@everyone" not in text and "<@&" not in text


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


def test_the_brief_goes_out_once_per_day_and_catches_up_past_midnight(tmp_path: Path) -> None:
    config(tmp_path)
    runtime = open_runtime(tmp_path)
    runtime.ledger.record("tasks", SourceItem("t1", "Renew the domain"))
    assert send_brief_when_due(runtime, at(8))
    assert not send_brief_when_due(runtime, at(15))
    assert not send_brief_when_due(runtime, at(7, day=7))
    assert send_brief_when_due(runtime, at(1, day=9))
    assert send_brief_when_due(runtime, at(9, day=9))
    texts = [message.text for message in runtime.store.pending_outbox()]
    assert len(texts) == 3 and all(text.startswith("Brief for") for text in texts)


def test_the_brief_command_prints_and_can_send(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config(tmp_path)
    Ledger(tmp_path / "superagent.db").record("tasks", SourceItem("t1", "Renew the domain"))
    assert main(["--home", str(tmp_path), "brief"]) == 0
    assert "Renew the domain" in capsys.readouterr().out
    assert main(["--home", str(tmp_path), "brief", "--send"]) == 0
    runtime = open_runtime(tmp_path)
    assert runtime.store.pending_outbox()[0].channel == "discord:brief"
    assert runtime.ledger.get("tasks:t1").last_briefed is not None


@pytest.mark.parametrize(
    "section",
    [
        '[channels]\nbrief = "b"\n[brief]\nhour = 24',
        '[channels]\nbrief = "b"\n[brief]\nhour = true',
        '[channels]\nbrief = "b"\n[brief]\ntimezone = "Mars/Olympus"',
        '[channels]\nbrief = "b"\n[brief]\nstale_after = "soon"',
        "[brief]\nhour = 8",
    ],
)
def test_bad_brief_settings_are_refused(section: str) -> None:
    with pytest.raises(ConfigError):
        parse_config(f"{section}\n")
