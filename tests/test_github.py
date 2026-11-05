from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from superagent.connectors import ConnectorError, ConnectorSpec, build_connector, parse_specs
from superagent.connectors.github import GithubConnector
from superagent.ledger import ItemState, Ledger, Upstream
from superagent.polling import poll_sources

FAKE_GH = Path(__file__).with_name("fake_gh.py")
ASSIGNED = "is:open assignee:@me"
REVIEW = "is:open is:pr review-requested:@me"


def issue(repo: str, number: int, title: str = "Fix the login", pr: bool = False) -> dict[str, object]:
    entry: dict[str, object] = {
        "repository_url": f"https://api.github.com/repos/{repo}",
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "updated_at": "2026-11-05T09:00:00Z",
        "labels": [{"name": "bug"}],
        "user": {"login": "someone"},
    }
    if pr:
        entry["pull_request"] = {"url": "x"}
    return entry


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "gh.json"
    monkeypatch.setenv("FAKE_GH_FIXTURE", str(path))
    return path


def write(path: Path, **data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def github_spec(tmp_path: Path, **extra: object) -> ConnectorSpec:
    wrapper = tmp_path / "gh"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE_GH} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    body = {"type": "github", "every": "0s", "gh": str(wrapper), "env": ["FAKE_GH_FIXTURE"], **extra}
    return parse_specs({"github": body})[0]


def test_every_page_of_every_query_reaches_the_snapshot(tmp_path: Path, fixture: Path) -> None:
    assigned = [issue("acme/app", n) for n in range(1, 151)]
    write(fixture, search={ASSIGNED: assigned, REVIEW: [issue("acme/app", 3, pr=True), issue("acme/site", 9, pr=True)]})
    poll = build_connector(github_spec(tmp_path, queries={"assigned": ASSIGNED, "review": REVIEW})).poll("")
    by_id = {item.external_id: item for item in poll.items}
    assert poll.complete and len(by_id) == 151
    assert by_id["acme/app#3"].meta["matched"] == "assigned,review"
    assert by_id["acme/site#9"].meta["kind"] == "pr" and by_id["acme/site#9"].title == "acme/site#9 Fix the login"
    assert by_id["acme/app#1"].meta["labels"] == "bug"


@pytest.mark.parametrize(
    "setup",
    [
        {"totals": {ASSIGNED: 140}},
        {"incomplete": [ASSIGNED]},
        {"fail": [ASSIGNED]},
        {"totals": {ASSIGNED: 1500}},
        {"sso": [ASSIGNED]},
        {"shift": [ASSIGNED]},
    ],
)
def test_a_search_that_cannot_be_trusted_is_a_problem_but_other_queries_still_land(
    tmp_path: Path, fixture: Path, setup: dict[str, object]
) -> None:
    assigned = [issue("acme/app", n) for n in range(1, 131)]
    write(fixture, search={ASSIGNED: assigned, REVIEW: [issue("acme/site", 9, pr=True)]}, **setup)
    poll = build_connector(github_spec(tmp_path, queries={"assigned": ASSIGNED, "review": REVIEW})).poll("")
    assert not poll.complete and poll.problems
    assert "acme/site#9" in {item.external_id for item in poll.items}


def test_a_problem_poll_records_what_it_found_but_marks_nothing_gone(tmp_path: Path, fixture: Path) -> None:
    write(fixture, search={ASSIGNED: [issue("acme/app", 1)], REVIEW: [issue("acme/site", 9, pr=True)]})
    spec = github_spec(tmp_path, queries={"assigned": ASSIGNED, "review": REVIEW})
    ledger = Ledger(tmp_path / "s.db")
    connectors = {spec.name: build_connector(spec)}
    poll_sources(ledger, [spec], connectors, 600)
    write(
        fixture, search={ASSIGNED: [issue("acme/app", 1)], REVIEW: [issue("acme/site", 10, pr=True)]}, fail=[ASSIGNED]
    )
    poll_sources(ledger, [spec], connectors, 600)
    assert ledger.get("github:acme/app#1").missing_since is None
    assert ledger.get("github:acme/site#10").is_open
    assert ledger.source("github").failures == 1


def test_a_negated_open_filter_is_refused(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="is:open"):
        github_spec(tmp_path, queries={"closed": "-is:open assignee:@me"})


def test_a_closed_issue_is_confirmed_closed(tmp_path: Path, fixture: Path) -> None:
    write(fixture, search={ASSIGNED: [issue("acme/app", 1), issue("acme/app", 2)]})
    spec = github_spec(tmp_path, queries={"assigned": ASSIGNED})
    ledger = Ledger(tmp_path / "s.db")
    connectors = {spec.name: build_connector(spec)}
    poll_sources(ledger, [spec], connectors, 600)
    write(fixture, search={ASSIGNED: [issue("acme/app", 2)]}, issues={"acme/app#1": "closed"})
    poll_sources(ledger, [spec], connectors, 600)
    assert ledger.get("github:acme/app#1").state is ItemState.DONE
    assert ledger.get("github:acme/app#2").is_open


def test_an_issue_reassigned_away_stays_listed_as_vanished(tmp_path: Path, fixture: Path) -> None:
    write(fixture, search={ASSIGNED: [issue("acme/app", 1), issue("acme/app", 2)]})
    spec = github_spec(tmp_path, queries={"assigned": ASSIGNED})
    ledger = Ledger(tmp_path / "s.db")
    connectors = {spec.name: build_connector(spec)}
    poll_sources(ledger, [spec], connectors, 600)
    write(fixture, search={ASSIGNED: [issue("acme/app", 2)]}, issues={"acme/app#1": "open"})
    poll_sources(ledger, [spec], connectors, 600)
    gone = ledger.get("github:acme/app#1")
    assert gone.is_open and gone.missing_since is not None


def test_confirm_refuses_ids_that_are_not_issues(tmp_path: Path, fixture: Path) -> None:
    write(fixture, search={})
    connector = build_connector(github_spec(tmp_path, queries={"assigned": ASSIGNED}))
    for bad in ("../../etc", "acme/app#1;rm", "-X POST"):
        with pytest.raises(ConnectorError):
            connector.confirm(bad)
    write(fixture, search={}, issues={"acme/app#5": "closed"})
    assert connector.confirm("acme/app#5") is Upstream.CLOSED


@pytest.mark.parametrize(
    "extra",
    [{"queries": {}}, {"queries": {"mine": "assignee:@me"}}, {"queries": "is:open"}, {"timeout": 0}, {"gh": ""}],
)
def test_bad_github_settings_are_refused(tmp_path: Path, extra: dict[str, object]) -> None:
    with pytest.raises(Exception, match="connectors.github"):
        github_spec(tmp_path, **extra)


def test_the_default_queries_cover_assigned_reviews_and_mentions(tmp_path: Path) -> None:
    spec = parse_specs({"github": {"type": "github"}})[0]
    connector = build_connector(spec)
    assert isinstance(connector, GithubConnector)
    assert set(connector.queries) == {"assigned", "review", "mentioned"}
