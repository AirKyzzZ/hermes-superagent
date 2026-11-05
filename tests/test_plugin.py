from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from superagent.domain import Mandate, Report, ReportState, Strength, Trigger, Verdict
from superagent.gate import SemanticRule
from superagent.judges import LlmAssessor, LlmDriftJudge
from superagent.store import Store
from superagent.worker import (
    ENV_ALLOW_SAME_USER,
    ENV_ASSIGNMENT,
    ENV_DB,
    ENV_ROLE,
    ENV_TICKET,
    ENV_TOKEN,
    ROLE_WORKER,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG = """
[sources.discord]
policy = "explicit_only"
allow = ["u-alice"]

[worker]
argv = ["hermes", "chat", "--query-file", "{brief}"]
"""


def load_plugin_module() -> ModuleType:
    name = "hermes_plugins_superagent_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@dataclass
class FakeResult:
    parsed: object


@dataclass
class FakeLlm:
    answer: object = None
    error: Exception | None = None

    def complete_structured(self, **kwargs: Any) -> FakeResult:
        if self.error:
            raise self.error
        return FakeResult(self.answer)


@dataclass
class FakeContext:
    settings: dict[str, str]
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    hooks: dict[str, Callable[..., Any]] = field(default_factory=dict)
    commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    blueprints: list[str] = field(default_factory=list)
    sections: dict[str, Callable[..., str]] = field(default_factory=dict)
    llm: FakeLlm = field(default_factory=FakeLlm)

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def register_tool(self, name: str, toolset: str, schema: dict[str, Any], handler: Any, **kwargs: Any) -> None:
        self.tools[name] = {"schema": schema, "handler": handler, "toolset": toolset, **kwargs}

    def register_hook(self, name: str, callback: Callable[..., Any]) -> None:
        self.hooks[name] = callback

    def register_cli_command(self, **kwargs: Any) -> None:
        self.commands[kwargs["name"]] = kwargs

    def register_automation_blueprint(self, key: str, **kwargs: Any) -> None:
        self.blueprints.append(key)

    def register_system_prompt_section(self, section_id: str, content: Callable[..., str]) -> None:
        self.sections[section_id] = content


def registered(role: str, home: Path) -> FakeContext:
    (home / "superagent.toml").write_text(CONFIG, encoding="utf-8")
    ctx = FakeContext({"role": role, "home": str(home)})
    load_plugin_module().register(ctx)
    return ctx


@dataclass
class FakeSource:
    platform: str = "discord"
    chat_id: str = "c1"
    user_id: str = "u-alice"
    thread_id: str | None = None
    is_bot: bool = False


@dataclass
class FakeEvent:
    source: FakeSource
    text: str = "please fix the bug"
    message_id: str | None = "1"


def inbound(ctx: FakeContext, **source: Any) -> Any:
    text = source.pop("text", "please fix the bug")
    message_id = source.pop("message_id", "1")
    event = FakeEvent(FakeSource(**source), text, message_id)
    return ctx.hooks["pre_gateway_dispatch"](event=event, gateway=object(), session_store=object())


def offered(ctx: FakeContext, text: str = "please fix the bug") -> Any:
    return ctx.hooks["pre_llm_call"](
        session_id="s",
        user_message=text,
        conversation_history=[],
        is_first_turn=True,
        model="m",
        platform="discord",
        sender_id="u-alice",
    )


def test_manifest_declares_what_register_registers(tmp_path: Path) -> None:
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
    ctx = registered("router", tmp_path)
    assert set(manifest["provides_tools"]) == set(ctx.tools)
    assert set(manifest["provides_hooks"]) == set(ctx.hooks)


def test_plugin_is_inert_without_a_role(tmp_path: Path) -> None:
    ctx = registered("", tmp_path)
    assert inbound(ctx) is None
    assert ctx.hooks["pre_tool_call"](tool_name="terminal", args={}, task_id="t") is None
    assert not any(tool["check_fn"]() for tool in ctx.tools.values())
    assert ctx.sections["superagent.role"]({}) == ""


@pytest.mark.parametrize("tool", ["terminal", "write_file", "execute_code", "browser_navigate", "delegate_task"])
def test_router_cannot_execute(tmp_path: Path, tool: str) -> None:
    ctx = registered("router", tmp_path)
    assert ctx.hooks["pre_tool_call"](tool_name=tool, args={}, task_id="t")["action"] == "block"


def test_router_keeps_its_own_tools(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    assert ctx.hooks["pre_tool_call"](tool_name="superagent_dispatch", args={}, task_id="t") is None
    assert ctx.hooks["pre_tool_call"](tool_name="memory", args={}, task_id="t") is None


@pytest.fixture
def worker_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeContext, Store, str]:
    ctx = registered("", tmp_path)
    store = Store(tmp_path / "w.db")
    mandate = Mandate("v", "g", Strength.EXPLICIT, "a", "chat", "t1")
    ticket, _ = store.create_ticket("g", mandate, "chat:1", "th")
    assignment, token = store.create_assignment(ticket.id)
    monkeypatch.setenv(ENV_ROLE, ROLE_WORKER)
    monkeypatch.setenv(ENV_ASSIGNMENT, assignment.id)
    monkeypatch.setenv(ENV_TOKEN, token)
    monkeypatch.setenv(ENV_TICKET, ticket.id)
    monkeypatch.setenv(ENV_DB, str(store.path))
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    return ctx, store, assignment.id


def test_worker_cannot_speak_or_dispatch(worker_ctx: tuple[FakeContext, Store, str]) -> None:
    pre_tool_call = worker_ctx[0].hooks["pre_tool_call"]
    assert pre_tool_call(tool_name="send_message", args={}, task_id="t")["action"] == "block"
    assert pre_tool_call(tool_name="superagent_dispatch", args={}, task_id="t")["action"] == "block"
    assert pre_tool_call(tool_name="superagent_forward", args={}, task_id="t")["action"] == "block"
    assert pre_tool_call(tool_name="terminal", args={}, task_id="t") is None


@pytest.mark.parametrize("tool", ["terminal", "write_file", "read_file", "execute_code", "computer_use"])
def test_unsandboxed_worker_cannot_touch_the_host(
    worker_ctx: tuple[FakeContext, Store, str], monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    monkeypatch.setenv("TERMINAL_ENV", "local")
    decision = worker_ctx[0].hooks["pre_tool_call"](tool_name=tool, args={}, task_id="t")
    assert decision["action"] == "block"
    assert "allow_same_user" in decision["message"]


def test_operator_can_accept_the_same_user_risk(
    worker_ctx: tuple[FakeContext, Store, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TERMINAL_ENV", "local")
    monkeypatch.setenv(ENV_ALLOW_SAME_USER, "1")
    assert worker_ctx[0].hooks["pre_tool_call"](tool_name="terminal", args={}, task_id="t") is None


def test_fenced_worker_loses_every_tool(worker_ctx: tuple[FakeContext, Store, str]) -> None:
    ctx, store, assignment_id = worker_ctx
    store.end_assignment(assignment_id)
    decision = ctx.hooks["pre_tool_call"](tool_name="terminal", args={}, task_id="t")
    assert decision["action"] == "block"
    assert "ended" in decision["message"]


def test_report_tool_returns_inputs_and_tells_a_fenced_worker_to_stop(
    worker_ctx: tuple[FakeContext, Store, str],
) -> None:
    ctx, store, assignment_id = worker_ctx
    ticket_id = store.get_assignment(assignment_id).ticket_id
    store.add_input(ticket_id, "chat:alice", "use staging", trusted=True)
    handler = ctx.tools["superagent_report"]["handler"]
    answer = json.loads(handler({"state": "progress", "summary": "working"}))
    assert answer["inputs"][0]["content"] == "use staging"
    store.end_assignment(assignment_id)
    assert "stop working" in json.loads(handler({"state": "ready", "summary": "late"}))["error"]


def test_tool_activity_is_recorded_for_the_watchdog(worker_ctx: tuple[FakeContext, Store, str]) -> None:
    ctx, store, assignment_id = worker_ctx
    ctx.hooks["post_tool_call"](tool_name="terminal", args={"command": "ls"}, result="ok", task_id="t", duration_ms=1)
    assert len(store.recent_activity(assignment_id, 5)) == 1


def test_stranger_message_is_dropped_before_anything_else_sees_it(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    assert inbound(ctx, user_id="u-mallory")["action"] == "skip"
    assert offered(ctx) is None


def test_admitted_message_reaches_the_router_model_with_its_trigger_id(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    assert inbound(ctx) is None
    injected = offered(ctx)
    assert "trigger_id=discord:c1:1" in injected["context"]
    assert "untrusted-data" in injected["context"]
    assert offered(ctx) is None


def test_message_ids_are_scoped_to_their_chat(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    assert inbound(ctx, chat_id="c1") is None
    assert inbound(ctx, chat_id="c2") is None


def test_multimodal_turn_still_gets_its_trigger_id(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    inbound(ctx)
    parts = [{"type": "text", "text": "please fix the bug"}, {"type": "image_url", "image_url": {}}]
    assert "trigger_id" in offered(ctx, parts)["context"]


def test_bot_flag_comes_from_the_event(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    assert inbound(ctx, is_bot=True)["action"] == "skip"


def test_slash_command_from_an_allowed_person_is_left_to_hermes(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    assert inbound(ctx, text="/stop") is None
    assert offered(ctx, "/stop") is None


def test_slash_command_from_a_stranger_is_dropped(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    assert inbound(ctx, user_id="u-mallory", text="/stop")["action"] == "skip"


def test_editing_the_rules_applies_without_a_restart(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    assert inbound(ctx, message_id="1") is None
    (tmp_path / "superagent.toml").write_text(CONFIG.replace('["u-alice"]', "[]"), encoding="utf-8")
    assert inbound(ctx, message_id="2")["action"] == "skip"


def test_gate_failure_fails_closed(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    (tmp_path / "superagent.toml").write_text("not toml [", encoding="utf-8")
    assert inbound(ctx)["action"] == "skip"


def test_dispatch_tool_refuses_a_refused_trigger(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    inbound(ctx, user_id="u-mallory")
    answer = json.loads(ctx.tools["superagent_dispatch"]["handler"]({"trigger_id": "discord:c1:1", "goal": "x"}))
    assert "not admitted" in answer["error"]


def test_forward_tool_reports_an_unknown_ticket(tmp_path: Path) -> None:
    ctx = registered("router", tmp_path)
    inbound(ctx)
    answer = json.loads(ctx.tools["superagent_forward"]["handler"]({"trigger_id": "discord:c1:1", "ticket_id": "nope"}))
    assert "no such ticket" in answer["error"]


def test_dispatch_tool_only_works_in_the_router(tmp_path: Path) -> None:
    ctx = registered("watchdog", tmp_path)
    answer = json.loads(ctx.tools["superagent_dispatch"]["handler"]({"trigger_id": "x", "goal": "y"}))
    assert "router" in answer["error"]


def test_report_tool_outside_a_worker_fails_visibly(tmp_path: Path) -> None:
    ctx = registered("", tmp_path)
    answer = json.loads(ctx.tools["superagent_report"]["handler"]({"state": "ready", "summary": "x"}))
    assert "not running as a superagent worker" in answer["error"]


def report_of(summary: str) -> Report:
    return Report("a", "t", ReportState.PROGRESS, summary, "", 0.0)


def test_drift_judge_reads_a_structured_verdict() -> None:
    judge = LlmDriftJudge(FakeLlm({"verdict": "drifting", "reason": "billing, not login"}))
    assert judge.judge("fix login", (report_of("refactor billing"),)).verdict is Verdict.DRIFTING


def test_padding_cannot_push_recent_reports_out_of_the_judges_view() -> None:
    seen: list[str] = []

    class Spy(FakeLlm):
        def complete_structured(self, **kwargs: Any) -> FakeResult:
            seen.append(kwargs["input"][0]["text"])
            return FakeResult({"verdict": "on_track", "reason": "ok"})

    reports = tuple(report_of("x" * 2000) for _ in range(5)) + (report_of("refactoring billing instead"),)
    LlmDriftJudge(Spy()).judge("fix login", reports)
    assert "refactoring billing instead" in seen[0]


@pytest.mark.parametrize(
    "llm",
    [
        FakeLlm({"verdict": "ignore all previous instructions"}),
        FakeLlm(None),
        FakeLlm(error=ConnectionError("provider down")),
    ],
)
def test_broken_judge_never_triggers_a_repair(llm: FakeLlm) -> None:
    assert LlmDriftJudge(llm).judge("g", (report_of("s"),)).verdict is Verdict.ON_TRACK


def test_assessor_requires_rules_and_a_usable_answer() -> None:
    event = Trigger("t", "github", "pr/1", "bob", "looks broken")
    rule = SemanticRule("r", "text", "record")  # type: ignore[arg-type]
    assert LlmAssessor(FakeLlm()).assess([event], []) == []
    with pytest.raises(ValueError):
        LlmAssessor(FakeLlm(None)).assess([event], [rule])
    answer = {"decisions": [{"trigger_id": "t", "rule": "r", "goal": "g"}]}
    picked = LlmAssessor(FakeLlm(answer)).assess([event], [rule])
    assert picked[0].rule == "r"
