from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from superagent.cli import main
from superagent.config import ConfigError, parse_config
from superagent.connectors import ConnectorError, ConnectorSpec, Poll, build_connector, parse_specs
from superagent.ledger import ItemState, Ledger, SourceItem, Upstream
from superagent.polling import poll_one, poll_sources, source_health
from superagent.runtime import open_runtime, tick

PRINT_FILE = (
    "import sys, pathlib; text = pathlib.Path(sys.argv[1]).read_text(); sys.exit(3) if text == 'FAIL' else print(text)"
)


def source_file(tmp_path: Path, entries: object, name: str = "source.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(entries) if not isinstance(entries, str) else entries, encoding="utf-8")
    return path


def command_spec(path: Path, **settings: Any) -> ConnectorSpec:
    body = {
        "argv": [sys.executable, "-c", PRINT_FILE, str(path)],
        "fields": {"id": "id", "title": "content", "url": "url", "status": "state", "repo": "repo.name"},
        **settings,
    }
    return parse_specs({"tasks": {"type": "command", "every": "0s", **body}})[0]


def task(external_id: str, title: str = "Pay the invoice", **extra: object) -> dict[str, object]:
    return {"id": external_id, "content": title, "url": f"https://example.test/{external_id}", **extra}


def test_the_command_connector_maps_fields_and_meta(tmp_path: Path) -> None:
    path = source_file(tmp_path, [task("1", repo={"name": "acme/app"}), task("2", state="completed")])
    poll = build_connector(command_spec(path)).poll("")
    assert poll.complete
    first, second = poll.items
    assert (first.external_id, first.title, first.url, first.meta) == (
        "1",
        "Pay the invoice",
        "https://example.test/1",
        {"repo": "acme/app"},
    )
    assert second.upstream is Upstream.CLOSED


def test_templates_and_nested_item_lists(tmp_path: Path) -> None:
    path = source_file(tmp_path, {"nights": [{"date": "2026-11-05", "sleep": "6h12", "hrv": 48}]})
    spec = parse_specs(
        {
            "sleep": {
                "type": "command",
                "argv": [sys.executable, "-c", PRINT_FILE, str(path)],
                "items": "nights",
                "fields": {"id": "date", "title": "Sleep {sleep}, HRV {hrv}"},
            }
        }
    )[0]
    [item] = build_connector(spec).poll("").items
    assert item.title == "Sleep 6h12, HRV 48"


@pytest.mark.parametrize(
    "output",
    ['[{"content": "no id"}]', '[{"id": "1"}]', '{"tasks": []}', "not json", "FAIL", '[{"id": " ", "content": "x"}]'],
)
def test_output_the_connector_cannot_read_fails_the_poll(tmp_path: Path, output: str) -> None:
    connector = build_connector(command_spec(source_file(tmp_path, output)))
    with pytest.raises(ConnectorError):
        connector.poll("")


def test_a_hanging_command_times_out(tmp_path: Path) -> None:
    spec = parse_specs(
        {
            "slow": {
                "type": "command",
                "argv": [sys.executable, "-c", "import time; time.sleep(5)"],
                "fields": {"id": "id", "title": "title"},
                "timeout": 0.5,
            }
        }
    )[0]
    with pytest.raises(ConnectorError, match="longer than"):
        build_connector(spec).poll("")


def test_only_listed_environment_variables_reach_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKS_TOKEN", "t0ken")
    monkeypatch.setenv("UNRELATED_SECRET", "nope")
    script = (
        "import json, os; names = ('TASKS_TOKEN', 'UNRELATED_SECRET'); "
        "print(json.dumps([{'id': k, 'title': k} for k in names if k in os.environ]))"
    )
    spec = parse_specs(
        {
            "env": {
                "type": "command",
                "argv": [sys.executable, "-c", script],
                "fields": {"id": "id", "title": "title"},
                "env": ["TASKS_TOKEN"],
            }
        }
    )[0]
    assert [item.external_id for item in build_connector(spec).poll("").items] == ["TASKS_TOKEN"]


def test_a_delta_source_gets_the_previous_cursor_with_an_overlap(tmp_path: Path) -> None:
    script = "import json, sys; print(json.dumps([{'id': 'since', 'title': sys.argv[1] or 'empty'}]))"
    spec = parse_specs(
        {
            "mail": {
                "type": "command",
                "argv": [sys.executable, "-c", script, "{since}"],
                "fields": {"id": "id", "title": "title"},
                "complete": False,
                "overlap": "10m",
            }
        }
    )[0]
    connector = build_connector(spec)
    first = connector.poll("")
    assert not first.complete and first.items[0].title == "empty"
    second = connector.poll(first.cursor)
    assert second.items[0].title == first.cursor


@pytest.mark.parametrize(
    "body",
    [
        {"type": "nope"},
        {"type": "command", "argv": [], "fields": {"id": "id", "title": "t"}},
        {"type": "command", "argv": ["x"], "fields": {"id": "id"}},
        {"type": "command", "argv": ["x"], "fields": {"id": "id", "title": "t"}, "every": "soon"},
        {"type": "command", "argv": ["x"], "fields": {"id": "id", "title": "t"}, "colour": "red"},
        {"type": "command", "argv": "x", "fields": {"id": "id", "title": "t"}},
    ],
)
def test_bad_connector_config_is_a_config_error(body: dict[str, object]) -> None:
    raw = '[worker]\nargv = ["w", "{brief}"]\n[connectors.tasks]\n' + "".join(
        f"{key} = {json.dumps(value)}\n" for key, value in body.items() if not isinstance(value, dict)
    )
    fields = body.get("fields")
    if isinstance(fields, dict):
        raw += "fields = { " + ", ".join(f"{key} = {json.dumps(value)}" for key, value in fields.items()) + " }\n"
    with pytest.raises(ConfigError):
        parse_config(raw)


def test_connector_names_are_checked() -> None:
    with pytest.raises(Exception, match="connector names"):
        parse_specs({"Tasks:x": {"type": "command", "argv": ["x"], "fields": {"id": "id", "title": "t"}}})


class ScriptedConnector:
    def __init__(self, polls: list[Poll | Exception], confirms: dict[str, Upstream] | None = None) -> None:
        self.polls = polls
        self.confirms = confirms or {}
        self.cursors: list[str] = []

    def poll(self, cursor: str) -> Poll:
        self.cursors.append(cursor)
        result = self.polls.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def confirm(self, external_id: str) -> Upstream:
        return self.confirms.get(external_id, Upstream.UNKNOWN)


def snapshot(*ids: str) -> Poll:
    return Poll(tuple(SourceItem(external_id, f"item {external_id}") for external_id in ids), complete=True)


SPEC = ConnectorSpec("tasks", "command", every_seconds=0)


def run(ledger: Ledger, connector: ScriptedConnector, spec: ConnectorSpec = SPEC) -> list[str]:
    return poll_sources(ledger, [spec], {spec.name: connector})


def test_a_snapshot_records_new_items_and_marks_vanished_ones(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    connector = ScriptedConnector([snapshot("a", "b"), snapshot("b")])
    assert run(ledger, connector) == []
    run(ledger, connector)
    gone = ledger.get("tasks:a")
    assert gone.missing_since is not None and gone.is_open
    assert ledger.source("tasks").applied_count == 1


def test_a_confirmed_close_closes_a_vanished_item(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    run(ledger, ScriptedConnector([snapshot("a", "b")]))
    run(ledger, ScriptedConnector([snapshot("b")], confirms={"a": Upstream.CLOSED}))
    assert ledger.get("tasks:a").state is ItemState.DONE


def test_a_snapshot_that_shrinks_a_lot_is_held_back_until_accepted(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    many = [str(n) for n in range(10)]
    run(ledger, ScriptedConnector([snapshot(*many)]))
    warnings = run(ledger, ScriptedConnector([snapshot("new")]))
    assert any("shrank from 10 to 1" in warning for warning in warnings)
    assert all(item.missing_since is None for item in ledger.items())
    assert ledger.get("tasks:new").is_open
    held = ScriptedConnector([snapshot("new")])
    assert any("shrank" in warning for warning in run(ledger, held))
    ledger.accept_next_snapshot("tasks")
    assert run(ledger, ScriptedConnector([snapshot("new")])) == []
    assert len(ledger.items(missing=True)) == 10
    assert not ledger.source("tasks").accept_next


def test_an_empty_snapshot_after_a_full_one_marks_nothing_gone(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    run(ledger, ScriptedConnector([snapshot(*[str(n) for n in range(6)])]))
    warnings = run(ledger, ScriptedConnector([snapshot()]))
    assert any("shrank from 6 to 0" in warning for warning in warnings)
    assert ledger.items(missing=True) == []


def test_repeated_failures_raise_a_warning_and_a_success_clears_it(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    connector = ScriptedConnector([ConnectorError("token expired")] * 3 + [snapshot("a")])
    assert run(ledger, connector) == []
    assert run(ledger, connector) == []
    warnings = run(ledger, connector)
    assert any("3 failed polls in a row, last: token expired" in warning for warning in warnings)
    assert run(ledger, connector) == []


def test_a_source_with_no_success_for_too_long_is_stale(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    run(ledger, ScriptedConnector([snapshot("a")]))
    state = ledger.source("tasks")
    later = (state.last_ok or 0) + 2 * 3600
    assert any("no successful poll for 120 minutes" in warning for warning in source_health(SPEC, state, later))


def test_a_quiet_delta_source_is_flagged(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    spec = ConnectorSpec("chats", "command", every_seconds=0, quiet_after_seconds=3600)
    run(ledger, ScriptedConnector([Poll((), complete=False, cursor="c1")]), spec)
    state = ledger.source("chats")
    warnings = source_health(spec, ledger.source("chats"), (state.first_attempt or 0) + 7200)
    assert "connector chats: nothing new for 2 hours" in warnings


def test_a_cursor_only_moves_after_a_successful_poll(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    spec = ConnectorSpec("mail", "command", every_seconds=0)
    connector = ScriptedConnector(
        [
            Poll((SourceItem("m1", "hello"),), complete=False, cursor="c1"),
            ConnectorError("offline"),
            Poll((), complete=False, cursor="c2"),
        ]
    )
    for _ in range(3):
        run(ledger, connector, spec)
    assert connector.cursors == ["", "c1", "c1"]
    assert ledger.source("mail").cursor == "c2"


def test_items_from_an_untracked_source_are_kept_but_need_no_owner(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    spec = ConnectorSpec("sleep", "command", every_seconds=0, track=False)
    run(ledger, ScriptedConnector([snapshot("2026-11-05")]), spec)
    item = ledger.get("sleep:2026-11-05")
    assert item.state is ItemState.IGNORED and item.reason == "informational source"


def test_a_source_is_polled_only_as_often_as_configured(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    spec = ConnectorSpec("tasks", "command", every_seconds=3600)
    connector = ScriptedConnector([snapshot("a")])
    run(ledger, connector, spec)
    run(ledger, connector, spec)
    assert connector.cursors == [""]


def test_a_ledger_error_on_one_item_fails_the_poll_without_moving_the_cursor(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    spec = ConnectorSpec("mail", "command", every_seconds=0)
    poll_one(ledger, spec, ScriptedConnector([RuntimeError("boom")]), ledger.source("mail"))
    assert ledger.source("mail").failures == 1


def test_a_tick_polls_configured_sources_and_the_cli_accepts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = source_file(tmp_path, [task(str(n)) for n in range(6)])
    (tmp_path / "superagent.toml").write_text(
        f"""
[worker]
argv = ["{sys.executable}", "-c", "pass", "{{brief}}"]

[connectors.tasks]
type = "command"
every = "0s"
argv = {json.dumps([sys.executable, "-c", PRINT_FILE, str(path)])}
fields = {{ id = "id", title = "content" }}
""",
        encoding="utf-8",
    )
    result = tick(open_runtime(tmp_path))
    assert result.errors == () and result.warnings == ()
    assert len(Ledger(tmp_path / "superagent.db").items()) == 6
    path.write_text("[]", encoding="utf-8")
    assert any("shrank from 6 to 0" in warning for warning in tick(open_runtime(tmp_path)).warnings)
    assert main(["--home", str(tmp_path), "ledger", "accept", "tasks"]) == 0
    assert tick(open_runtime(tmp_path)).warnings == ()
    assert len(Ledger(tmp_path / "superagent.db").items(missing=True)) == 6
    capsys.readouterr()
    assert main(["--home", str(tmp_path), "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["sources"][0]["connector"] == "tasks"


def test_accepting_an_unknown_source_fails(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="no source"):
        Ledger(tmp_path / "s.db").accept_next_snapshot("nope")


def test_poll_time_is_recorded(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "s.db")
    before = time.time()
    run(ledger, ScriptedConnector([snapshot("a")]))
    assert (ledger.source("tasks").last_ok or 0) >= before


@pytest.mark.parametrize("external_id", ["--upload-pack=touch /tmp/x", "-rf", "a b", "a;b", ""])
def test_unsafe_ids_never_reach_the_confirm_command(tmp_path: Path, external_id: str) -> None:
    marker = tmp_path / "ran"
    spec = command_spec(
        source_file(tmp_path, []),
        confirm_argv=[sys.executable, "-c", f"open({str(marker)!r}, 'w')", "{id}"],
    )
    with pytest.raises(ConnectorError, match="refusing"):
        build_connector(spec).confirm(external_id)
    assert not marker.exists()


def test_a_safe_id_is_confirmed(tmp_path: Path) -> None:
    script = "import json, sys; print(json.dumps({'state': 'closed' if sys.argv[1] == 'acme/app#7' else 'open'}))"
    spec = command_spec(source_file(tmp_path, []), confirm_argv=[sys.executable, "-c", script, "{id}"])
    assert build_connector(spec).confirm("acme/app#7") is Upstream.CLOSED
