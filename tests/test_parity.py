from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from superagent.briefwriter import BriefWriter, build_pack
from superagent.config import CONFIG_FILENAME
from superagent.ledger import Ledger
from superagent.parity import PastTask, PastTaskError, balanced_sides, compare, fenced, load_past_tasks
from tests.test_briefwriter import ScriptedLlm, cli, scripted

PAST = Path(__file__).with_name("fixtures") / "past_tasks.synthetic.json"


def plain_pack(task: PastTask):  # type: ignore[no-untyped-def]
    return build_pack(None, task.item, context=task.context)


def test_both_briefs_look_alike_and_the_verdict_goes_to_the_key() -> None:
    tasks = load_past_tasks(PAST)
    llm = scripted()
    sheet, key = compare(tasks, BriefWriter(llm), plain_pack, rng=random.Random(3))
    assert len(llm.calls) == 3 * len(tasks)
    for marker in ("Brief for", "Sources", "[p", "NEEDS APPROVAL", "Report through"):
        assert marker not in sheet
    assert "Goal: Renew the domain before it lapses" in sheet
    assert "Fix the login redirect loop in acme/app (issue 12)." in sheet
    assert "Probably the session cookie" in llm.calls[0]["input"][0]["text"]
    assert key.count(" is new (") == 2 and "lines new" in key and "the lint found:" in key


def test_the_order_is_random_but_balanced() -> None:
    assert sorted(balanced_sides(10, random.Random(1))) == [False] * 5 + [True] * 5
    orders = {tuple(balanced_sides(10, random.Random(seed))) for seed in range(5)}
    assert len(orders) > 1


def test_the_key_names_the_side_the_new_brief_landed_on() -> None:
    tasks = load_past_tasks(PAST)
    sheet, key = compare(tasks, BriefWriter(scripted()), plain_pack, rng=random.Random(7))
    for number, task in enumerate(tasks, 1):
        block = sheet.split(f"\n## {number}. {task.name}")[1].split(f"\n## {number + 1}. ")[0]
        new_side = "A" if f"- {number}. {task.name}: A is new" in key else "B"
        assert block.split(f"### Brief {new_side}")[1].lstrip().startswith("```text\nGoal: ")


def test_one_failed_task_doesnt_lose_the_others() -> None:
    tasks = load_past_tasks(PAST)
    replies: dict[str, Any] = {"draft": {"goal": 3}, "challenge": {}, "revise": {}}
    calls = {"n": 0}

    def flaky(kwargs: dict[str, Any]) -> Any:
        calls["n"] += 1
        return scripted().replies[kwargs["purpose"].rsplit(".", 1)[1]] if calls["n"] <= 3 else replies["draft"]

    llm = ScriptedLlm({"draft": flaky, "challenge": flaky, "revise": flaky})
    sheet, key = compare(tasks, BriefWriter(llm), plain_pack, rng=random.Random(1))
    assert "## 1. Fix the login redirect loop" in sheet and "## 2." not in sheet
    assert "2. Renew the domain: not compared, the new brief couldn't be written" in key


def test_a_brief_holding_its_own_code_block_keeps_the_page_intact() -> None:
    lines = fenced("Run this:\n```\nmake test\n```\nthen report")
    assert lines[0] == "````text" and lines[-1] == "````"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[]", "list of past tasks"),
        ('[{"title": "x", "typed": "y"}]', "needs ran"),
        ('[{"title": "x", "typed": "y", "ran": "z", "context": [{"label": "a"}]}]', "context"),
    ],
)
def test_a_malformed_file_of_past_tasks_is_refused(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "past.json"
    path.write_text(content)
    with pytest.raises(PastTaskError, match=message):
        load_past_tasks(path)


def home_with(tmp_path: Path, config: str = "") -> Path:
    home = tmp_path / "sa"
    home.mkdir()
    (home / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    return home


def test_the_cli_writes_the_sheet_and_the_key_without_touching_the_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = home_with(tmp_path, '[briefs]\nnotes = "Never touch billing"\n')
    sheet, key = tmp_path / "sheet.md", tmp_path / "key.md"
    llm = scripted()
    argv = ["briefs", "--compare", str(PAST), "--out", str(sheet), "--key", str(key)]
    assert cli(home, *argv, llm=llm) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == 2
    assert "### Brief A" in sheet.read_text() and " is new " in key.read_text()
    assert "Never touch billing" in llm.calls[0]["input"][0]["text"]
    assert Ledger(home / "superagent.db").items() == []
    assert cli(home, *argv, llm=scripted()) == 2
    assert "exists" in capsys.readouterr().err
    assert cli(home, *argv, "--force", llm=scripted()) == 0


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--out", "SAME", "--key", "SAME"], "three different files"),
        (["--out", "missing/sheet.md", "--key", "key.md"], "doesn't exist"),
        (["--out", "sheet.md"], "needs --out"),
        (["--out", "sheet.md", "--key", "key.md", "--send"], "without an item"),
    ],
)
def test_the_cli_refuses_risky_output_before_spending_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str], message: str
) -> None:
    home = home_with(tmp_path)
    resolved = [str(tmp_path / part) if part not in ("--out", "--key", "--send") else part for part in extra]
    llm = scripted()
    assert cli(home, "briefs", "--compare", str(PAST), *resolved, llm=llm) == 2
    assert message in capsys.readouterr().err and llm.calls == []


def test_more_tasks_than_the_daily_limit_need_a_yes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = home_with(tmp_path, "[briefs]\ndaily_limit = 1\n")
    argv = ["briefs", "--compare", str(PAST), "--out", str(tmp_path / "s.md"), "--key", str(tmp_path / "k.md")]
    assert cli(home, *argv, llm=scripted()) == 2
    assert "daily limit" in capsys.readouterr().err
    assert cli(home, *argv, "--yes", llm=scripted()) == 0
