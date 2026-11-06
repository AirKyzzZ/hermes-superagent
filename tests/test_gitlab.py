from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from superagent.cli import repo_key
from superagent.connectors import ConnectorConfigError, ConnectorError, ConnectorSpec, build_connector, parse_specs
from superagent.ledger import ItemState, Ledger, Upstream
from superagent.polling import poll_sources

FAKE_GLAB = Path(__file__).with_name("fake_glab.py")
ASSIGNED = "issues?scope=assigned_to_me&state=opened"
MRS = "merge_requests?scope=assigned_to_me&state=opened"
REVIEW = "merge_requests?scope=all&state=opened&reviewer_username=owner"
TODOS = "todos?state=pending"


def work(project: str, iid: int, kind: str = "#", title: str = "Fix the login", **extra: object) -> dict[str, object]:
    return {
        "id": hash((project, iid, kind)) % 10**9,
        "iid": iid,
        "title": title,
        "references": {"full": f"{project}{kind}{iid}"},
        "web_url": f"https://gitlab.com/{project}/-/{'issues' if kind == '#' else 'merge_requests'}/{iid}",
        "updated_at": "2026-11-05T09:00:00Z",
        "author": {"username": "someone"},
        "labels": ["bug", "backend"],
        **extra,
    }


def todo(number: int, target: dict[str, object] | None = None, kind: str = "Issue") -> dict[str, object]:
    return {
        "id": number,
        "action_name": "mentioned",
        "target_type": kind,
        "target": target or {"title": "Design review"},
        "target_url": f"https://gitlab.com/acme/app/-/todo/{number}",
        "project": {"path_with_namespace": "acme/app"},
        "updated_at": "2026-11-05T10:00:00Z",
    }


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "glab.json"
    monkeypatch.setenv("FAKE_GLAB_FIXTURE", str(path))
    return path


def write(path: Path, **data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def gitlab_spec(tmp_path: Path, **extra: object) -> ConnectorSpec:
    wrapper = tmp_path / "glab"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE_GLAB} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    body = {"type": "gitlab", "every": "0s", "glab": str(wrapper), "env": ["FAKE_GLAB_FIXTURE"], **extra}
    return parse_specs({"gitlab": body})[0]


def test_every_page_of_every_list_reaches_the_snapshot(tmp_path: Path, fixture: Path) -> None:
    issues = [work("acme/app", n, due_date="2026-11-20" if n == 3 else None) for n in range(1, 151)]
    review = work("acme/web", 4, "!", title="Add dark mode")
    lists = {ASSIGNED: issues, MRS: [], REVIEW: [review], TODOS: [todo(1, issues[2])]}
    write(fixture, lists=lists)
    poll = build_connector(gitlab_spec(tmp_path)).poll("")
    by_id = {item.external_id: item for item in poll.items}
    assert poll.complete and len(by_id) == 152
    assert by_id["acme/app#3"].meta["matched"] == "assigned" and by_id["acme/app#3"].due == "2026-11-20"
    assert by_id["acme/web!4"].title == "acme/web!4 Add dark mode" and by_id["acme/web!4"].meta["kind"] == "mr"
    assert by_id["acme/app#1"].meta["labels"] == "bug,backend" and by_id["acme/app#1"].meta["repo"] == "acme/app"
    assert (
        by_id["todo-1"].title == "mentioned: acme/app#3 Fix the login"
        and by_id["todo-1"].meta["target"] == "acme/app#3"
    )


def test_assignment_todos_for_tracked_work_are_left_to_the_work_item(tmp_path: Path, fixture: Path) -> None:
    open_issue, closed_issue = work("acme/app", 5), work("acme/app", 6, state="closed")
    assigned = [
        {**todo(10, open_issue), "action_name": "assigned"},
        {**todo(11, closed_issue), "action_name": "assigned"},
    ]
    write(fixture, lists={ASSIGNED: [open_issue], TODOS: assigned})
    poll = build_connector(gitlab_spec(tmp_path, queries={"assigned": ASSIGNED, "todos": TODOS})).poll("")
    assert sorted(item.external_id for item in poll.items) == ["acme/app#5", "todo-11"]
    alone = build_connector(gitlab_spec(tmp_path, queries={"todos": TODOS})).poll("")
    assert sorted(item.external_id for item in alone.items) == ["todo-10", "todo-11"]


def test_a_closed_issue_with_a_pending_todo_still_closes(tmp_path: Path, fixture: Path) -> None:
    spec = gitlab_spec(tmp_path, queries={"assigned": ASSIGNED, "todos": TODOS})
    issue = work("acme/app", 5)
    write(fixture, lists={ASSIGNED: [issue], TODOS: [todo(10, issue)]})
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    closed = {**issue, "state": "closed"}
    write(fixture, lists={ASSIGNED: [], TODOS: [todo(10, closed)]}, states={"acme/app#5": "closed"})
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    states = {item.external_id: item.state for item in ledger.items()}
    assert states == {"acme/app#5": ItemState.DONE, "todo-10": ItemState.NEW}


def test_a_todo_closes_once_gitlab_lists_it_as_done(tmp_path: Path, fixture: Path) -> None:
    spec = gitlab_spec(tmp_path, queries={"todos": TODOS})
    write(fixture, lists={TODOS: [todo(10), todo(11)]})
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    write(fixture, lists={TODOS: [], "todos?state=done": [todo(10)]})
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    states = {item.external_id: item.state for item in ledger.items()}
    assert states == {"todo-10": ItemState.DONE, "todo-11": ItemState.NEW}


def test_todos_gitlab_hides_from_the_list_dont_block_the_snapshot(tmp_path: Path, fixture: Path) -> None:
    write(fixture, lists={TODOS: [todo(1), todo(2)], ASSIGNED: [work("acme/app", 1)]}, totals={TODOS: 3})
    poll = build_connector(gitlab_spec(tmp_path, queries={"assigned": ASSIGNED, "todos": TODOS})).poll("")
    assert poll.complete and len(poll.items) == 3


def test_one_bad_entry_doesnt_hide_the_good_ones(tmp_path: Path, fixture: Path) -> None:
    write(fixture, lists={ASSIGNED: [work("acme/app", 1), {"id": 99, "title": "no reference"}, work("acme/app", 2)]})
    poll = build_connector(gitlab_spec(tmp_path, queries={"assigned": ASSIGNED})).poll("")
    assert not poll.complete and len(poll.problems) == 1
    assert sorted(item.external_id for item in poll.items) == ["acme/app#1", "acme/app#2"]


def test_glab_never_runs_inside_the_current_checkout(tmp_path: Path, fixture: Path) -> None:
    log = tmp_path / "cwd.log"
    write(fixture, lists={ASSIGNED: []}, cwd_log=str(log))
    build_connector(gitlab_spec(tmp_path, queries={"assigned": ASSIGNED})).poll("")
    assert set(log.read_text().split()) == {"/"}


def test_a_moved_project_is_reported_instead_of_closed(tmp_path: Path, fixture: Path) -> None:
    moved = {"state": "opened", "references": {"full": "acme/renamed#1"}}
    write(fixture, states={"acme/app#1": moved})
    with pytest.raises(ConnectorError, match="moved to acme/renamed#1"):
        build_connector(gitlab_spec(tmp_path)).confirm("acme/app#1")


@pytest.mark.parametrize(
    ("setup", "problem"),
    [
        ({"fail": [ASSIGNED]}, "query assigned"),
        ({"no_total": [ASSIGNED]}, "no total"),
        ({"shift": [ASSIGNED]}, "moved from 150 to 149"),
        ({"totals": {ASSIGNED: 151}}, "said 151 items but returned 150"),
    ],
)
def test_a_list_that_cant_prove_its_complete_marks_nothing_gone(
    tmp_path: Path, fixture: Path, setup: dict[str, object], problem: str
) -> None:
    write(
        fixture,
        lists={ASSIGNED: [work("acme/app", n) for n in range(1, 151)], MRS: [work("acme/app", 9, "!")]},
        **setup,
    )
    poll = build_connector(gitlab_spec(tmp_path, queries={"assigned": ASSIGNED, "mrs": MRS})).poll("")
    assert not poll.complete and any(problem in found for found in poll.problems), poll.problems
    assert "acme/app!9" in {item.external_id for item in poll.items}


def test_vanished_items_close_only_when_gitlab_says_so(tmp_path: Path, fixture: Path) -> None:
    lists = {ASSIGNED: [work("acme/app", 1), work("acme/app", 2)], MRS: [work("acme/app", 3, "!")]}
    spec = gitlab_spec(tmp_path, queries={"assigned": ASSIGNED, "mrs": MRS})
    write(fixture, lists=lists)
    ledger = Ledger(tmp_path / "s.db")
    connector = build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    write(
        fixture,
        lists={ASSIGNED: [], MRS: []},
        states={"acme/app#1": "closed", "acme/app#2": "opened", "acme/app!3": "merged"},
    )
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    states = {item.external_id: item.state for item in ledger.items()}
    assert states == {"acme/app#1": ItemState.DONE, "acme/app#2": ItemState.NEW, "acme/app!3": ItemState.DONE}
    assert connector.confirm("todo-7") is Upstream.UNKNOWN
    assert connector.confirm("acme/app&3") is Upstream.UNKNOWN


@pytest.mark.parametrize(
    ("queries", "message"),
    [
        ({"all": "issues?scope=assigned_to_me"}, "state=opened"),
        ({"all": "issues?scope=assigned_to_me&state=opened&page=2"}, "page"),
        ({"all": "issues?scope=assigned_to_me&state=closed"}, "state=opened"),
        ({"all": "issues?scope=assigned_to_me&state=opened&state=all"}, "exactly state=opened"),
        ({"all": "projects?state=opened"}, "issues, merge_requests or todos"),
    ],
)
def test_queries_must_ask_for_whats_open_and_leave_paging_alone(queries: dict[str, str], message: str) -> None:
    with pytest.raises(ConnectorConfigError, match=message):
        parse_specs({"gitlab": {"type": "gitlab", "queries": queries}})


def test_gitlab_is_never_trusted_wholesale() -> None:
    with pytest.raises(ConnectorConfigError, match="trusted"):
        parse_specs({"gitlab": {"type": "gitlab", "trusted": True}})


def test_a_gitlab_project_never_borrows_a_github_checkout(tmp_path: Path) -> None:
    assert repo_key("github", "acme/app") == "acme/app"
    assert repo_key("gitlab", "acme/app") == "gitlab:acme/app"
    assert repo_key("command", "acme/app") == repo_key("", "acme/app") == "acme/app"


def test_a_narrowed_issue_query_never_hides_an_assignment_todo(tmp_path: Path, fixture: Path) -> None:
    backend = "issues?scope=assigned_to_me&state=opened&labels=backend"
    unlabelled = work("acme/app", 8, labels=[])
    write(fixture, lists={backend: [], TODOS: [{**todo(30, unlabelled), "action_name": "assigned"}]})
    poll = build_connector(gitlab_spec(tmp_path, queries={"backend": backend, "todos": TODOS})).poll("")
    assert [item.external_id for item in poll.items] == ["todo-30"]


def test_a_todo_done_long_ago_is_still_found(tmp_path: Path, fixture: Path) -> None:
    done = [todo(number) for number in range(1000, 600, -1)] + [todo(5)]
    write(fixture, lists={"todos?state=done": done})
    connector = build_connector(gitlab_spec(tmp_path))
    assert connector.confirm("todo-5") is Upstream.CLOSED
    assert connector.confirm("todo-700") is Upstream.CLOSED
    assert connector.confirm("todo-6") is Upstream.UNKNOWN
