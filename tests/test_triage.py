from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from superagent.brief import BriefSettings, build_brief
from superagent.cli import main
from superagent.config import CONFIG_FILENAME, DB_FILENAME, ConfigError, parse_config
from superagent.ledger import ItemState, Ledger, SourceItem
from superagent.runtime import open_runtime, tick
from superagent.triage import parse_triage, triage_new

RULES = """
[areas.work]
channel = "discord:work"

[areas.home]

[[triage]]
name = "newsletters"
match = { connector = "mail", from = ["*@news.*", "*@digest.*"] }
ignore = "newsletter"

[[triage]]
match = { connector = "github", repo = "Acme/*" }
area = "work"

[[triage]]
match = { labels = "home" }
area = "home"
"""


def config_rules(extra: str = "") -> tuple:
    config = parse_config(RULES + extra)
    return config.triage, config.area_channels


def record(ledger: Ledger, connector: str, external_id: str, **meta: str) -> str:
    return ledger.record(connector, SourceItem(external_id=external_id, title=f"Item {external_id}", meta=meta)).id


def test_the_first_matching_rule_files_or_ignores_each_new_item(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    paper = record(ledger, "mail", "m1", **{"from": "Weekly <weekly@news.example.com>"})
    issue = record(ledger, "github", "acme/app#1", repo="acme/app", labels="bug,home")
    chore = record(ledger, "todo", "t1", labels="errand,home")
    other = record(ledger, "todo", "t2", labels="errand")
    sent: list[tuple[str, str]] = []
    rules, channels = config_rules()
    triage_new(ledger, rules, channels, lambda channel, text: sent.append((channel, text)))
    assert ledger.get(paper).state is ItemState.IGNORED and ledger.get(paper).reason == "newsletter"
    assert (ledger.get(issue).state, ledger.get(issue).area) == (ItemState.TRIAGED, "work")
    assert (ledger.get(chore).state, ledger.get(chore).area) == (ItemState.TRIAGED, "home")
    assert ledger.get(other).state is ItemState.NEW
    assert [event.actor for event in ledger.history(paper)][-1] == "rule:newsletters"
    assert [event.actor for event in ledger.history(issue)][-1] == "rule:2"
    assert sent == [("discord:work", f"New in work:\n- Item acme/app#1 [github] ({ledger.get(issue).handle})")]
    triage_new(ledger, rules, channels, lambda channel, text: sent.append((channel, text)))
    assert len(sent) == 1


def test_rules_never_touch_items_someone_already_moved(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    item = record(ledger, "github", "acme/app#2", repo="acme/app")
    ledger.move(item, ItemState.TRIAGED, "owner", "", area="personal")
    triage_new(ledger, *config_rules())
    assert ledger.get(item).area == "personal" and ledger.history(item)[-1].actor == "owner"


def test_an_ignored_item_shows_in_the_brief_with_its_reason(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    record(ledger, "mail", "m2", **{"from": "x@digest.example.org"})
    triage_new(ledger, *config_rules())
    brief = build_brief(ledger, (), 0, 4_000_000_000, BriefSettings(3 * 86400, "UTC")).text
    assert "newsletter" in brief and "Item m2" in brief


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ('[[triage]]\nmatch = { connector = "x" }\n', "exactly one of area or ignore"),
        ('[[triage]]\nmatch = { connector = "x" }\narea = "a"\nignore = "b"\n', "exactly one"),
        ('[[triage]]\nmatch = { connector = "x" }\nignore = " "\n', "exactly one"),
        ('[[triage]]\narea = "work"\n', "needs a match table"),
        ('[[triage]]\nmatch = { repo = 3 }\narea = "work"\n', "match.repo"),
        ('[[triage]]\nmatch = { repo = "a/*" }\narea = "Work Stuff"\n', "lowercase"),
        ('[areas.work]\n[[triage]]\nmatch = { repo = "a/*" }\narea = "play"\n', "isn't one of the [areas]"),
        ('[areas.work]\ncolour = "red"\n', "only a channel"),
        ('[[triage]]\nmatch = { repo = "a/*" }\narea = "w"\nassign = "vps"\n', "takes name, match"),
        (
            '[[triage]]\nname = "a"\nmatch = { x = "1" }\narea = "w"\n'
            '[[triage]]\nname = "a"\nmatch = { x = "2" }\narea = "w"\n',
            "reuses",
        ),
        ('[[triage]]\nname = "untracked"\nmatch = { x = "1" }\nignore = "x"\n', "can't be named"),
    ],
)
def test_a_broken_rule_is_refused(section: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
        parse_config(section)


def test_no_rules_means_no_changes(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    item = record(ledger, "github", "acme/app#3", repo="acme/app")
    triage_new(ledger, *parse_triage([], {}))
    assert ledger.get(item).state is ItemState.NEW


def test_the_tick_files_new_items_including_ones_seen_before_the_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "sa"
    home.mkdir()
    source = tmp_path / "items.json"
    source.write_text(json.dumps([{"id": "1", "title": "Ship the release", "repo": "acme/app"}]))
    argv = json.dumps([sys.executable, "-c", "import sys; print(open(sys.argv[1]).read())", str(source)])
    connector = (
        f'[connectors.github]\ntype = "command"\nevery = "0s"\nargv = {argv}\n'
        'fields = { id = "id", title = "title", repo = "repo" }\n'
    )
    (home / CONFIG_FILENAME).write_text(connector)
    tick(open_runtime(home))
    assert Ledger(home / DB_FILENAME).items()[0].state is ItemState.NEW
    (home / CONFIG_FILENAME).write_text(connector + RULES)
    result = tick(open_runtime(home))
    assert result.errors == ()
    [item] = Ledger(home / DB_FILENAME).items()
    assert (item.state, item.area) == (ItemState.TRIAGED, "work")
    assert [(channel, text) for _, channel, text in result.delivered] == [
        ("discord:work", f"New in work:\n- Ship the release [github] ({item.handle})")
    ]
    assert main(["--home", str(home), "ledger", "list"]) == 0
    assert "work" in capsys.readouterr().out


def rule(**match: str) -> tuple:
    return parse_triage([{"match": match, "ignore": "set aside"}], {})


@pytest.mark.parametrize(
    ("match", "value", "hit"),
    [
        ({"author": "*[bot]"}, {"author": "dependabot[bot]"}, True),
        ({"author": "*[bot]"}, {"author": "octocat"}, False),
        ({"title": "[WIP]*"}, {"title": "[WIP] refactor"}, True),
        ({"title": "[WIP]*"}, {"title": "Weekly plan"}, False),
        ({"title": "draft*"}, {"title": "Contract renewal, draft attached"}, False),
        ({"labels": "urgent"}, {"labels": "bug, urgent"}, True),
        ({"due": "*"}, {"due": ""}, False),
        ({"id": "acme/app#1?"}, {"id": "acme/app#12"}, True),
        ({"repo": "acme/*"}, {"repo": "ACME/App"}, True),
    ],
)
def test_globs_mean_only_star_and_question_mark(tmp_path: Path, match: dict, value: dict, hit: bool) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    title, due, external_id = value.pop("title", "Item"), value.pop("due", ""), value.pop("id", "x1")
    item = ledger.record("github", SourceItem(external_id=external_id, title=title, due=due, meta=value)).id
    triage_new(ledger, *rule(**match))
    assert (ledger.get(item).state is ItemState.IGNORED) is hit


def test_an_ignore_with_any_reason_reaches_the_brief(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    record(ledger, "mail", "m9")
    triage_new(ledger, *parse_triage([{"match": {"connector": "mail"}, "ignore": "informational source"}], {}))
    brief = build_brief(ledger, (), 0, 4_000_000_000, BriefSettings(3 * 86400, "UTC")).text
    assert "Item m9" in brief and "because informational source" in brief


def test_a_backlog_sends_one_capped_message_per_area(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    for number in range(30):
        record(ledger, "github", f"acme/app#{number}", repo="acme/app")
    sent: list[tuple[str, str]] = []
    triage_new(ledger, *config_rules(), lambda channel, text: sent.append((channel, text)))
    [(channel, text)] = sent
    assert channel == "discord:work" and text.count("\n- Item") == 20 and "and 10 more" in text
    triage_new(ledger, *config_rules(), lambda channel, text: sent.append((channel, text)))
    assert len(sent) == 1


def test_a_failed_announcement_is_retried_on_the_next_tick(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    record(ledger, "github", "acme/app#5", repo="acme/app")

    def broken(channel: str, text: str) -> None:
        raise OSError("database is locked")

    with pytest.raises(OSError):
        triage_new(ledger, *config_rules(), broken)
    sent: list[tuple[str, str]] = []
    record(ledger, "github", "acme/app#6", repo="acme/app")
    triage_new(ledger, *config_rules(), lambda channel, text: sent.append((channel, text)))
    assert sent and "acme/app#6" in sent[0][1]


def test_rules_leave_assigned_and_waiting_items_alone(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    held = record(ledger, "github", "acme/app#7", repo="acme/app")
    ledger.move(held, ItemState.ASSIGNED, "owner", "", ticket_id="tk-1")
    triage_new(ledger, *config_rules())
    assert ledger.get(held).state is ItemState.ASSIGNED and ledger.get(held).area == ""


def test_area_lines_redact_secrets_in_titles(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / DB_FILENAME)
    ledger.record(
        "github", SourceItem(external_id="acme/app#8", title="Rotate token=abcdef123456", meta={"repo": "acme/app"})
    )
    sent: list[tuple[str, str]] = []
    triage_new(ledger, *config_rules(), lambda channel, text: sent.append((channel, text)))
    assert "abcdef123456" not in sent[0][1]
