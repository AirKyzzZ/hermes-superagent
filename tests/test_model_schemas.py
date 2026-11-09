from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from superagent.briefwriter import BriefWriter, build_pack
from superagent.domain import Trigger
from superagent.gate import RuleMode, SemanticRule
from superagent.judges import LlmAssessor, LlmDriftJudge

from .test_briefwriter import CHALLENGE, GOOD, ScriptedLlm, ledger_with
from .test_plugin import report_of

SOURCE = Path(__file__).resolve().parents[1] / "superagent"
UNSUPPORTED = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "maxItems",
    "uniqueItems",
}


def schemas_sent(tmp_path: Path) -> dict[str, Any]:
    llm = ScriptedLlm(
        {
            "draft": GOOD,
            "challenge": CHALLENGE,
            "revise": GOOD,
            "drift": {"verdict": "on_track", "reason": "fine"},
            "assess": {"decisions": []},
        }
    )
    ledger = ledger_with(tmp_path)
    [item] = ledger.items()
    BriefWriter(llm).write(build_pack(ledger, item))
    LlmDriftJudge(llm).judge("the goal", (report_of("working"),))
    trigger = Trigger("c1", "github", "pr/1", "bot", "ci failed")
    LlmAssessor(llm).assess([trigger], [SemanticRule("r", "text", RuleMode.RECORD)])
    return {call["purpose"]: call["json_schema"] for call in llm.calls}


def nodes(schema: Any) -> Iterator[dict[str, Any]]:
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from nodes(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from nodes(value)


def test_every_schema_sent_to_a_model_meets_strict_structured_outputs(tmp_path: Path) -> None:
    schemas = schemas_sent(tmp_path)
    assert len(schemas) == 5
    for purpose, schema in schemas.items():
        objects = [node for node in nodes(schema) if node.get("type") == "object"]
        assert objects, purpose
        for found in objects:
            assert found.get("additionalProperties") is False, (purpose, found)
            assert set(found["required"]) == set(found["properties"]), (purpose, found)
        for node in nodes(schema):
            assert not UNSUPPORTED & set(node), (purpose, node)


def test_every_model_call_is_covered_by_the_schema_walk() -> None:
    calls = sum(path.read_text(encoding="utf-8").count(".complete_structured(") for path in SOURCE.rglob("*.py"))
    assert calls == 3, "a new model call has to be added to schemas_sent"
