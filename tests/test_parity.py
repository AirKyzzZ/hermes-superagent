from __future__ import annotations

import json
from pathlib import Path

import pytest

from superagent.briefwriter import BriefWriter
from superagent.config import CONFIG_FILENAME
from superagent.ledger import Ledger
from superagent.parity import PastTaskError, compare, load_past_tasks
from tests.test_briefwriter import cli, scripted

PAST = Path(__file__).with_name("fixtures") / "past_tasks.synthetic.json"


def test_each_past_task_gets_a_new_brief_beside_the_one_that_ran(tmp_path: Path) -> None:
    tasks = load_past_tasks(PAST)
    llm = scripted()
    sheet, key = compare(tasks, BriefWriter(llm))
    assert len(llm.calls) == 3 * len(tasks)
    assert sheet.count("### Brief A") == 2 and sheet.count("### Brief B") == 2
    assert "You typed: fix the login loop on app" in sheet
    assert "Fix the login redirect loop in acme/app (issue 12)." in sheet
    assert sheet.count("Brief for past-") == 2
    assert "Probably the session cookie" in llm.calls[0]["input"][0]["text"]
    assert key.count(" is the new brief") == 2
    assert compare(tasks, BriefWriter(scripted()))[1] == key


def test_the_key_names_the_side_the_new_brief_landed_on() -> None:
    tasks = load_past_tasks(PAST)
    sheet, key = compare(tasks, BriefWriter(scripted()))
    for number, task in enumerate(tasks, 1):
        block = sheet.split(f"\n## {number}. {task.name}")[1].split(f"\n## {number + 1}. ")[0]
        new_side = "A" if f"- {number}. {task.name}: A" in key else "B"
        brief = block.split(f"### Brief {new_side}")[1]
        assert brief.lstrip().startswith("```text\nBrief for past-")


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


def test_the_cli_writes_the_sheet_and_the_key_without_touching_the_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "sa"
    home.mkdir()
    (home / CONFIG_FILENAME).write_text("", encoding="utf-8")
    sheet, key = tmp_path / "sheet.md", tmp_path / "key.md"
    argv = ["briefs", "--compare", str(PAST), "--out", str(sheet), "--key", str(key)]
    assert cli(home, *argv, llm=scripted()) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == 2
    assert "### Brief A" in sheet.read_text() and "is the new brief" in key.read_text()
    assert Ledger(home / "superagent.db").items() == []
    assert cli(home, "briefs", "--compare", str(PAST), "--out", str(sheet)) == 2
    assert cli(home, "briefs") == 2
