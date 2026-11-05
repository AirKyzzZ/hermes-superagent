from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .agents import ATTENTION_STATES, redact
from .connectors import ConnectorSpec
from .ledger import Item, ItemState, Ledger
from .polling import source_health

UNTRACKED_REASON = "informational source"
TITLE_LIMIT = 120
_ATTENTION = {state.value for state in ATTENTION_STATES}


@dataclass(frozen=True)
class BriefSettings:
    stale_after: float
    timezone: str


def excused(item: Item, now: float, stale_after: float, sessions: set[str]) -> bool:
    if item.snoozed_until is not None and item.snoozed_until > now:
        return True
    fresh = now - item.last_changed <= stale_after
    if item.connector in sessions:
        return fresh and item.meta.get("state") not in _ATTENTION and item.state is not ItemState.NEW
    return fresh and item.state in (ItemState.TRIAGED, ItemState.ASSIGNED)


def build_brief(
    ledger: Ledger, specs: Sequence[ConnectorSpec], since: float, now: float, settings: BriefSettings
) -> str:
    sessions = {spec.name for spec in specs if spec.type == "agents"}
    untracked = {spec.name for spec in specs if not spec.track}
    day = datetime.fromtimestamp(now, ZoneInfo(settings.timezone)).strftime("%a %d %b")
    lines = [f"Brief for {day}. Shadow mode: nothing below was acted on."]
    problems = [warning.text for spec in specs for warning in source_health(spec, ledger.source(spec.name), now)]
    open_items = [item for item in ledger.items(open_only=True) if item.connector not in untracked]
    counts = {spec.name: sum(1 for item in open_items if item.connector == spec.name) for spec in specs}
    lines += _section(
        "Sources",
        [f"! {problem}" for problem in problems]
        + [f"{name}: {count} open" for name, count in counts.items() if name not in untracked],
    )
    shown: set[str] = set()

    def pick(rows: list[Item]) -> list[Item]:
        chosen = [item for item in rows if item.id not in shown]
        shown.update(item.id for item in chosen)
        return chosen

    waiting = pick(
        [item for item in open_items if item.state is ItemState.WAITING or item.meta.get("state") in _ATTENTION]
    )
    lines += _section("Waiting on you", [_line(item, item.meta.get("state", "")) for item in waiting])
    vanished = pick([item for item in open_items if item.missing_since is not None])
    lines += _section("Gone from their source without a close", [_line(item, "") for item in vanished])
    new = pick([item for item in open_items if item.state is ItemState.NEW and item.connector not in sessions])
    lines += _section("New, no owner yet", [_line(item, "") for item in sorted(new, key=lambda item: item.connector)])
    stale = pick(
        [
            item
            for item in open_items
            if not excused(item, now, settings.stale_after, sessions) and not _snoozed(item, now)
        ]
    )
    lines += _section(
        "Untouched for a while", [_line(item, f"{(now - item.last_changed) / 86400:.0f}d") for item in stale]
    )
    ignored = [
        (item, event)
        for item, event in ledger.ignored_since(since)
        if event.note != UNTRACKED_REASON and item.state is ItemState.IGNORED
    ]
    lines += _section("Ignored since the last brief", [_line(item, f"because {event.note}") for item, event in ignored])
    today = [item for item in ledger.items() if item.connector in untracked and item.first_seen > since]
    lines += _section("Today", [f"{item.title[:TITLE_LIMIT]}" for item in today])
    return "\n".join(lines)


def _snoozed(item: Item, now: float) -> bool:
    return item.snoozed_until is not None and item.snoozed_until > now


def _section(title: str, rows: list[str]) -> list[str]:
    return ["", f"{title} ({len(rows)})", *rows] if rows else []


def _line(item: Item, note: str) -> str:
    suffix = f" [{note}]" if note else ""
    return f"{item.handle} {item.connector}: {redact(item.title)[:TITLE_LIMIT]}{suffix}"
