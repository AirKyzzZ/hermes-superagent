from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from superagent.domain import Assignment, Ticket
from superagent.gate import Rules, parse_rules
from superagent.router import Router
from superagent.store import Store

RULES = """
[sources.chat]
policy = "explicit_only"
allow = ["alice"]

[sources.github]
policy = "candidate"
allow = ["alice"]
bots = ["ci-bot"]

[sources.muted]
policy = "ignore"

[[semantic]]
name = "ci-watch"
text = "Investigate a failing main branch."
mode = "act"

[[semantic]]
name = "silent-fix"
text = "Reproduce reported bugs quietly."
"""


@dataclass
class FakeBackend:
    alive: bool = True
    started: list[tuple[str, str, str]] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)

    def start(self, ticket: Ticket, assignment: Assignment, token: str) -> int:
        self.started.append((ticket.id, assignment.id, token))
        return 4242

    def is_alive(self, assignment: Assignment) -> bool:
        return self.alive

    def stop(self, assignment: Assignment) -> None:
        self.stopped.append(assignment.id)


@pytest.fixture
def rules() -> Rules:
    return parse_rules(RULES)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "superagent.db")


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def router(store: Store, rules: Rules, backend: FakeBackend) -> Router:
    return Router(store, rules, backend)
