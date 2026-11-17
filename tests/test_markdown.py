from __future__ import annotations

import os
import threading
from collections.abc import Callable
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest

from superagent.connectors import (
    ConnectorConfigError,
    ConnectorError,
    ConnectorSpec,
    Poll,
    build_connector,
    parse_specs,
)
from superagent.connectors import markdown as markdown_module
from superagent.ledger import ItemState, Ledger, Upstream
from superagent.polling import poll_sources


def note(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def markdown_spec(*paths: Path | str, **extra: object) -> ConnectorSpec:
    body = {"type": "markdown", "every": "0s", "paths": [str(path) for path in paths], **extra}
    return parse_specs({"notes": body})[0]


POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="needs FIFOs and symlinks")


def titles(*paths: Path | str) -> list[str]:
    return [item.title for item in build_connector(markdown_spec(*paths)).poll("").items]


def without_blocking(call: Callable[[], object]) -> object:
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(call())
        except Exception as error:
            outcome.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(10)
    assert outcome, "the poll blocked"
    return outcome[0]


def test_only_open_tasks_outside_code_become_items(tmp_path: Path) -> None:
    todo = note(
        tmp_path / "TODO.md",
        "- [ ] Dash\n* [ ] Star\n+ [ ] Plus\n- [x] Done\n- [X] Also done\n- [-] Cancelled\n"
        "- [ ]\n-[ ] Not a list\n[ ] Not a list either\nSome prose with - [ ] inside\n"
        "```md\n- [ ] Fenced\n```\n~~~\n- [ ] Tilde fenced\n```\n- [ ] Still fenced\n~~~\n- [ ] After the fences\n",
    )
    poll = build_connector(markdown_spec(todo)).poll("")
    assert poll.complete and poll.problems == ()
    assert [item.title for item in poll.items] == ["Dash", "Star", "Plus", "After the fences"]
    assert {item.url for item in poll.items} == {""}


def test_nested_and_numbered_tasks_count(tmp_path: Path) -> None:
    todo = note(
        tmp_path / "TODO.md",
        "- [ ] Parent\n  - [ ] Child\n\t- [ ] Tabbed\n        * [ ] Deep\n1. [ ] First\n12) [ ] Twelfth\n",
    )
    assert titles(todo) == ["Parent", "Child", "Tabbed", "Deep", "First", "Twelfth"]


def test_links_stay_readable_in_the_title(tmp_path: Path) -> None:
    todo = note(
        tmp_path / "TODO.md",
        '- [ ] Review [[Budget 2026]] with [[Sam Lee|Sam]]\n- [ ] Read [the brief](https://example.com/b "Brief")\n'
        "- [ ] Open [https://example.com](https://example.com)\n",
    )
    assert titles(todo) == [
        "Review Budget 2026 with Sam",
        "Read the brief (https://example.com/b)",
        "Open https://example.com",
    ]


def test_ids_stay_put_when_lines_move_and_change_with_the_text(tmp_path: Path) -> None:
    todo = note(tmp_path / "TODO.md", "- [ ] Pay rent\n- [ ] Call the bank\n- [ ] Pay rent\n")
    connector = build_connector(markdown_spec(todo))
    before = {item.external_id: item.title for item in connector.poll("").items}
    assert len(before) == 3 and all(external_id.startswith("TODO.md#") for external_id in before)
    note(todo, "# Today\n\n- [ ] New one\n- [ ]   call the BANK 📅 2026-11-20\n\n- [ ] Pay rent\n- [ ] Pay rent\n")
    after = {item.external_id: item.title for item in connector.poll("").items}
    assert set(before) < set(after) and len(after) == 4
    note(todo, "- [ ] Pay the rent\n")
    [edited] = connector.poll("").items
    assert edited.external_id not in before


def test_due_dates_come_from_obsidian_tasks_or_due_tags(tmp_path: Path) -> None:
    todo = note(
        tmp_path / "TODO.md",
        "- [ ] Renew the domain 📅 2026-11-20\n- [ ] Book flights 📅\ufe0f2026-12-01 ⏳ 2026-11-25\n"
        "- [ ] File taxes due:2026-11-30\n- [ ] Someday\n- [ ] Overdue:2026-01-01 isn't a tag\n",
    )
    items = build_connector(markdown_spec(todo)).poll("").items
    assert [(item.title, item.due) for item in items] == [
        ("Renew the domain", "2026-11-20"),
        ("Book flights", "2026-12-01"),
        ("File taxes", "2026-11-30"),
        ("Someday", ""),
        ("Overdue:2026-01-01 isn't a tag", ""),
    ]


def test_each_task_keeps_its_file_heading_and_project(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note(
        vault / "Projects" / "Greenhouse" / "launch.md",
        "- [ ] Before any heading\n# Launch\n## This week ##\n- [ ] Ship it\n```\n# not a heading\n```\n"
        "- [ ] Still this week\n### Later\n- [ ] Polish\n",
    )
    note(vault / "Inbox.md", "- [ ] Sort the inbox\n")
    items = build_connector(markdown_spec(vault)).poll("").items
    assert [(item.title, dict(item.meta)) for item in items] == [
        ("Sort the inbox", {"file": "Inbox.md", "project": "Inbox"}),
        ("Before any heading", {"file": "Projects/Greenhouse/launch.md", "project": "Greenhouse"}),
        ("Ship it", {"file": "Projects/Greenhouse/launch.md", "heading": "This week", "project": "Greenhouse"}),
        ("Still this week", {"file": "Projects/Greenhouse/launch.md", "heading": "This week", "project": "Greenhouse"}),
        ("Polish", {"file": "Projects/Greenhouse/launch.md", "heading": "Later", "project": "Greenhouse"}),
    ]


def test_directories_are_searched_for_markdown_skipping_hidden_ones(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note(vault / "a.md", "- [ ] Top\n")
    note(vault / "deep" / "er" / "b.md", "- [ ] Deep\n")
    note(vault / "notes.txt", "- [ ] Not markdown\n")
    note(vault / ".obsidian" / "c.md", "- [ ] Settings\n")
    note(vault / ".trash" / "d.md", "- [ ] Deleted\n")
    assert titles(vault) == ["Top", "Deep"]


def test_globs_and_single_files_are_read_relative_to_where_they_start(tmp_path: Path) -> None:
    code = tmp_path / "code"
    note(code / "api" / "TODO.md", "- [ ] Fix the api\n")
    note(code / "web" / "TODO.md", "- [ ] Fix the web\n")
    note(code / "web" / "README.md", "- [ ] Not a TODO file\n")
    note(code / ".hidden" / "TODO.md", "- [ ] Hidden\n")
    inbox = note(tmp_path / "Inbox.md", "- [ ] Single file\n")
    items = build_connector(markdown_spec(code / "*" / "TODO.md", inbox)).poll("").items
    assert [(item.title, item.meta["file"], item.meta["project"]) for item in items] == [
        ("Fix the api", "api/TODO.md", "api"),
        ("Fix the web", "web/TODO.md", "web"),
        ("Single file", "Inbox.md", "Inbox"),
    ]
    assert titles(code / "**" / "*.md") == ["Fix the api", "Not a TODO file", "Fix the web"]


def test_a_file_reached_through_two_paths_is_read_once(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note(vault / "Projects" / "a.md", "- [ ] Once\n")
    assert titles(vault, vault / "Projects" / "*.md") == ["Once"]


def test_two_files_that_would_share_ids_fail_the_poll(tmp_path: Path) -> None:
    first = note(tmp_path / "work" / "TODO.md", "- [ ] Work\n")
    second = note(tmp_path / "home" / "TODO.md", "- [ ] Home\n")
    with pytest.raises(ConnectorError, match="separate markdown connectors") as error:
        build_connector(markdown_spec(first, second)).poll("")
    assert str(first) in str(error.value) and str(second) in str(error.value)


@pytest.mark.parametrize("missing", ["nowhere", "nowhere/TODO.md", "nowhere/*.md"])
def test_a_missing_path_fails_the_poll_with_the_path(tmp_path: Path, missing: str) -> None:
    note(tmp_path / "TODO.md", "- [ ] Real\n")
    with pytest.raises(ConnectorError, match="nowhere"):
        build_connector(markdown_spec(tmp_path / "TODO.md", tmp_path / missing)).poll("")


def test_a_glob_that_matches_nothing_fails_the_poll(tmp_path: Path) -> None:
    note(tmp_path / "TODO.md", "- [ ] Real\n")
    with pytest.raises(ConnectorError, match="matched no files"):
        build_connector(markdown_spec(tmp_path / "*.mdx")).poll("")


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs file permissions that apply")
def test_an_unreadable_note_fails_the_poll_with_its_path(tmp_path: Path) -> None:
    locked = note(tmp_path / "vault" / "locked.md", "- [ ] Secret\n")
    locked.chmod(0)
    try:
        with pytest.raises(ConnectorError, match="locked.md"):
            build_connector(markdown_spec(tmp_path / "vault")).poll("")
    finally:
        locked.chmod(0o644)


def test_a_walk_past_its_timeout_stops_even_before_any_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "vault" / "a" / "b").mkdir(parents=True)
    connector = build_connector(markdown_spec(tmp_path / "vault", timeout=5))
    ticks = count(step=10)
    monkeypatch.setattr(markdown_module, "time", SimpleNamespace(monotonic=lambda: float(next(ticks))))
    with pytest.raises(ConnectorError, match="longer than 5s"):
        connector.poll("")


def test_tilde_fences_and_an_unterminated_fence_hide_their_tasks(tmp_path: Path) -> None:
    todo = note(
        tmp_path / "TODO.md",
        "~~~~\n- [ ] Inside\n~~~\n- [ ] A shorter fence doesn't close it\n~~~~\n- [ ] Outside\n"
        "```\n- [ ] Never closed\n",
    )
    assert titles(todo) == ["Outside"]


def test_a_duplicate_keeps_its_id_after_the_first_copy_is_ticked(tmp_path: Path) -> None:
    todo = note(tmp_path / "TODO.md", "- [ ] Review the PR\n- [ ] Review the PR\n")
    connector = build_connector(markdown_spec(todo))
    first, second = (item.external_id for item in connector.poll("").items)
    assert second == f"{first}~2"
    note(todo, "- [x] Review the PR\n- [ ] Review the PR\n")
    [left] = connector.poll("").items
    assert left.external_id == second
    assert connector.confirm(first) is Upstream.CLOSED


@POSIX_ONLY
def test_a_fifo_or_a_folder_named_like_a_note_is_never_read(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note(vault / "a.md", "- [ ] Top\n")
    os.mkfifo(vault / "pipe.md")
    (vault / "folder.md").mkdir()
    poll = without_blocking(lambda: build_connector(markdown_spec(vault)).poll(""))
    assert isinstance(poll, Poll) and [item.title for item in poll.items] == ["Top"]
    with pytest.raises(ConnectorError, match="isn't a file or a directory"):
        build_connector(markdown_spec(vault / "pipe.md")).poll("")


@POSIX_ONLY
def test_a_fifo_that_slips_past_the_walk_fails_instead_of_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    os.mkfifo(vault / "pipe.md")
    monkeypatch.setattr(markdown_module, "_regular_inside", lambda path, inside: True)
    error = without_blocking(lambda: build_connector(markdown_spec(vault)).poll(""))
    assert isinstance(error, ConnectorError) and "pipe.md isn't a regular file" in str(error)


@POSIX_ONLY
def test_dangling_and_looping_links_are_skipped(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note(vault / "a.md", "- [ ] Top\n")
    (vault / "dead.md").symlink_to(vault / "missing.md")
    (vault / "loop.md").symlink_to(vault / "loop.md")
    connector = build_connector(markdown_spec(vault))
    for _ in range(2):
        assert [item.title for item in connector.poll("").items] == ["Top"]


@POSIX_ONLY
def test_links_never_lead_outside_the_root(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note(vault / "inside.md", "- [ ] Inside\n")
    secret = note(tmp_path / "secret.md", "- [ ] Outside file\n")
    note(tmp_path / "elsewhere" / "far.md", "- [ ] Outside folder\n")
    (vault / "escape.md").symlink_to(secret)
    (vault / "elsewhere").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    (vault / "again").symlink_to(vault, target_is_directory=True)
    assert titles(vault) == ["Inside"]
    assert titles(vault / "**" / "*.md") == ["Inside"]


def test_a_note_over_the_size_cap_fails_the_poll(tmp_path: Path) -> None:
    note(tmp_path / "vault" / "a.md", "- [ ] Top\n")
    with (tmp_path / "vault" / "huge.md").open("wb") as huge:
        huge.truncate(markdown_module.MAX_NOTE_BYTES + 1)
    with pytest.raises(ConnectorError, match="huge.md is over 8 MB"):
        build_connector(markdown_spec(tmp_path / "vault")).poll("")


def test_confirm_closes_a_ticked_task_and_never_a_vanished_one(tmp_path: Path) -> None:
    todo = note(tmp_path / "TODO.md", "- [ ] Pay rent 📅 2026-11-20\n- [ ] Call the bank\n- [ ] Order keys\n")
    other = note(tmp_path / "vault" / "Other.md", "- [ ] Water plants\n")
    connector = build_connector(markdown_spec(todo, tmp_path / "vault"))
    rent, bank, keys, plants = (item.external_id for item in connector.poll("").items)
    note(todo, "- [ ] Order keys\n- [x] Pay rent 📅 2026-11-20 ✅ 2026-11-17\n- [-] Call the bank\n")
    assert connector.confirm(rent) is Upstream.CLOSED
    assert connector.confirm(bank) is Upstream.UNKNOWN
    assert connector.confirm(keys) is Upstream.OPEN
    note(todo, "- [ ] Order keys\n")
    assert connector.confirm(rent) is Upstream.UNKNOWN
    other.unlink()
    assert connector.confirm(plants) is Upstream.UNKNOWN
    assert connector.confirm("Other.md") is Upstream.UNKNOWN


def test_ticking_a_task_closes_its_item_and_deleting_one_leaves_it_open(tmp_path: Path) -> None:
    todo = note(tmp_path / "TODO.md", "- [ ] Pay rent\n- [ ] Call the bank\n- [ ] Order keys\n")
    spec = markdown_spec(todo)
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    ids = {item.title: item.id for item in ledger.items()}
    note(todo, "- [x] Pay rent\n- [ ] Order keys\n")
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    rent, bank, keys = (ledger.get(ids[title]) for title in ("Pay rent", "Call the bank", "Order keys"))
    assert rent.state is ItemState.DONE
    assert bank.state is ItemState.NEW and bank.missing_since is not None
    assert keys.state is ItemState.NEW and keys.missing_since is None


def test_moving_lines_around_leaves_decided_items_alone(tmp_path: Path) -> None:
    todo = note(tmp_path / "TODO.md", "- [ ] Learn Rust\n- [ ] Pay rent\n")
    spec = markdown_spec(todo)
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    rust = next(item for item in ledger.items() if item.title == "Learn Rust")
    ledger.move(rust.id, ItemState.IGNORED, "owner", "someday")
    note(todo, "Some new prose\n\n- [ ] Pay rent\n- [ ] Brand new\n- [ ] Learn Rust\n")
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    assert ledger.get(rust.id).state is ItemState.IGNORED
    assert len(ledger.items()) == 3


def test_a_missing_path_is_a_failed_poll_that_marks_nothing_gone(tmp_path: Path) -> None:
    todo = note(tmp_path / "TODO.md", "- [ ] Pay rent\n")
    spec = markdown_spec(todo)
    ledger, connector = Ledger(tmp_path / "s.db"), build_connector(spec)
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    todo.unlink()
    poll_sources(ledger, [spec], {spec.name: connector}, 300)
    [item] = ledger.items()
    assert item.missing_since is None
    source = ledger.source("notes")
    assert source.failures == 1 and str(todo) in source.last_error


def test_paths_expand_the_home_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    note(tmp_path / "notes" / "TODO.md", "- [ ] From home\n")
    assert titles("~/notes") == ["From home"]


def test_markdown_is_tracked_and_can_be_marked_as_the_owners_own_words(tmp_path: Path) -> None:
    [spec] = parse_specs({"notes": {"type": "markdown", "paths": ["~/notes"], "trusted": True}})
    assert spec.trusted and spec.track


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"paths": []},
        {"paths": "~/notes"},
        {"paths": [""]},
        {"paths": [3]},
        {"paths": ["notes/TODO.md"]},
        {"paths": ["~/notes"], "timeout": 0},
        {"paths": ["~/notes"], "timeout": True},
        {"paths": ["~/notes"], "exclude": [".trash"]},
    ],
)
def test_bad_markdown_config_is_refused(body: dict[str, object]) -> None:
    with pytest.raises(ConnectorConfigError):
        parse_specs({"notes": {"type": "markdown", **body}})
