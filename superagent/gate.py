from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .domain import Admission, Strength, Trigger


class SourcePolicy(StrEnum):
    EXPLICIT_ONLY = "explicit_only"
    CANDIDATE = "candidate"
    IGNORE = "ignore"


class RuleMode(StrEnum):
    RECORD = "record"
    ACT = "act"


@dataclass(frozen=True)
class SourceRules:
    policy: SourcePolicy
    allow: frozenset[str]
    bots: frozenset[str]
    pass_strangers: bool = False
    notify: str = ""
    via_router: bool = False


@dataclass(frozen=True)
class SemanticRule:
    name: str
    text: str
    mode: RuleMode


@dataclass(frozen=True)
class Rules:
    version: str
    sources: dict[str, SourceRules]
    semantic: tuple[SemanticRule, ...]


@dataclass(frozen=True)
class GateDecision:
    admission: Admission
    strength: Strength | None
    reason: str


class RulesError(ValueError):
    pass


def parse_rules(raw: str) -> Rules:
    try:
        data = tomllib.loads(raw)
        sources = {
            name: SourceRules(
                policy=SourcePolicy(body["policy"]),
                allow=_names(body, f"[sources.{name}]", "allow"),
                bots=_names(body, f"[sources.{name}]", "bots"),
                pass_strangers=_flag(body, f"[sources.{name}]", "pass_strangers"),
                notify=_text(body, f"[sources.{name}]", "notify", ""),
                via_router=_flag(body, f"[sources.{name}]", "via_router"),
            )
            for name, body in data.get("sources", {}).items()
        }
        semantic = tuple(
            SemanticRule(
                name=_text(rule, "[[semantic]]", "name"),
                text=_text(rule, "[[semantic]]", "text"),
                mode=RuleMode(_text(rule, "[[semantic]]", "mode", RuleMode.RECORD)),
            )
            for rule in data.get("semantic", [])
        )
    except (tomllib.TOMLDecodeError, KeyError, ValueError, TypeError, AttributeError) as error:
        raise RulesError(f"invalid rules file: {error}") from error
    version = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return Rules(version=version, sources=sources, semantic=semantic)


def _names(body: dict[str, object], where: str, key: str) -> frozenset[str]:
    value = body.get(key, [])
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise RulesError(f'{where} {key} must be a list of names, such as ["alice"]')
    return frozenset(value)


def _flag(body: dict[str, object], where: str, key: str) -> bool:
    value = body.get(key, False)
    if not isinstance(value, bool):
        raise RulesError(f"{where} {key} must be true or false")
    return value


def _text(body: dict[str, object], where: str, key: str, default: str | None = None) -> str:
    value = body[key] if default is None else body.get(key, default)
    if not isinstance(value, str):
        raise RulesError(f"{where} {key} must be a string")
    return value


def load_rules(path: Path) -> Rules:
    if not path.is_file():
        raise RulesError(f"rules file not found: {path}")
    return parse_rules(path.read_text(encoding="utf-8"))


def gate(trigger: Trigger, rules: Rules) -> GateDecision:
    source = rules.sources.get(trigger.source)
    if source is None:
        return GateDecision(Admission.IGNORE, None, "source has no rules")
    if source.policy is SourcePolicy.IGNORE:
        return GateDecision(Admission.IGNORE, None, "source is set to ignore")
    if trigger.is_bot:
        if trigger.actor in source.bots and source.policy is SourcePolicy.CANDIDATE:
            return GateDecision(Admission.CANDIDATE, Strength.INFERRED, "listed bot on a candidate source")
        return GateDecision(Admission.IGNORE, None, "bot is not listed")
    allowed = trigger.actor in source.allow
    if allowed and trigger.addressed:
        return GateDecision(Admission.ACT, Strength.EXPLICIT, "allowed person addressed the agent")
    if source.policy is SourcePolicy.CANDIDATE and allowed:
        return GateDecision(Admission.CANDIDATE, Strength.INFERRED, "allowed person, agent not addressed")
    if allowed:
        return GateDecision(Admission.IGNORE, None, "agent not addressed on an explicit-only source")
    return GateDecision(Admission.IGNORE, None, "actor is not on the allowed list")
