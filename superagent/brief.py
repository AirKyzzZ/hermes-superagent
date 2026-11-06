from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .agents import ATTENTION_STATES, redact
from .connectors import ConnectorSpec
from .ledger import Item, ItemState, Ledger
from .polling import source_health
from .router import chat_safe

UNTRACKED_REASON = "informational source"
REOPENED_NOTE = "reopened at the source"
TITLE_LIMIT = 120
_ATTENTION = {state.value for state in ATTENTION_STATES}


@dataclass(frozen=True)
class BriefSettings:
    stale_after: float
    timezone: str


@dataclass(frozen=True)
class Brief:
    text: str
    shown: tuple[str, ...]


def build_brief(
    ledger: Ledger,
    specs: Sequence[ConnectorSpec],
    since: float,
    now: float,
    settings: BriefSettings,
    live_tickets: Collection[str] = (),
) -> Brief:
    untracked = {spec.name for spec in specs if not spec.track}
    day = datetime.fromtimestamp(now, ZoneInfo(settings.timezone)).strftime("%a %d %b")
    problems = [warning.text for spec in specs for warning in source_health(spec, ledger.source(spec.name), now)]
    open_items = [item for item in ledger.items(open_only=True) if item.connector not in untracked]
    counts = {spec.name: sum(1 for item in open_items if item.connector == spec.name) for spec in specs}
    shown: dict[str, Item] = {}

    def pick(rows: Sequence[Item]) -> list[Item]:
        chosen = [item for item in rows if item.id not in shown]
        shown.update((item.id, item) for item in chosen)
        return chosen

    sections = [
        (
            "Sources",
            [f"! {problem}" for problem in problems]
            + [f"{name}: {count} open" for name, count in counts.items() if name not in untracked],
        ),
    ]
    waiting = pick(
        [item for item in open_items if item.state is ItemState.WAITING or item.meta.get("state") in _ATTENTION]
    )
    sections.append(("Waiting on you", [_line(item, item.meta.get("state", "")) for item in waiting]))
    vanished = pick([item for item in open_items if item.missing_since is not None])
    sections.append(("Gone from their source without a close", [_line(item, "") for item in vanished]))
    reopened = pick(
        [
            item
            for item, _ in ledger.events_since(since, now, to_state=ItemState.NEW, note=REOPENED_NOTE)
            if item.is_open
        ]
    )
    sections.append(("Reopened at the source", [_line(item, "") for item in reopened]))
    fresh = pick(
        sorted((item for item in open_items if item.state is ItemState.NEW and item.first_seen > since), key=_by_source)
    )
    sections.append(("New since the last brief", [_line(item, "") for item in fresh]))
    due = pick(
        sorted(
            (item for item in open_items if _due(item, now, settings.stale_after, live_tickets)),
            key=lambda item: item.last_changed,
        )
    )
    sections.append(
        (
            f"Also open, each shown at least every {settings.stale_after / 86400:g} days",
            [_line(item, f"{item.state.value}, {(now - item.last_changed) / 86400:.0f}d") for item in due],
        )
    )
    ignored = [
        (item, event)
        for item, event in ledger.events_since(since, now, to_state=ItemState.IGNORED)
        if event.note != UNTRACKED_REASON and item.state is ItemState.IGNORED
    ]
    sections.append(
        ("Ignored since the last brief", [_line(item, f"because {_flat(event.note)}") for item, event in ignored])
    )
    today = [item for item in ledger.items() if item.connector in untracked and item.last_changed > since]
    sections.append(("Today", [_flat(redact(item.title))[:TITLE_LIMIT] for item in today]))
    lines = [f"Brief for {day}: {len(shown)} items. Shadow mode, so nothing below was acted on."]
    for title, rows in sections:
        if rows:
            lines += ["", f"{title} ({len(rows)})", *rows]
    return Brief(chat_safe("\n".join(lines)), tuple(shown))


def _due(item: Item, now: float, stale_after: float, live_tickets: Collection[str]) -> bool:
    if item.snoozed_until is not None and item.snoozed_until > now:
        return False
    if item.state is ItemState.ASSIGNED and item.ticket_id in live_tickets:
        return False
    return item.last_briefed is None or now - item.last_briefed > stale_after


def _by_source(item: Item) -> tuple[str, float]:
    return item.connector, item.first_seen


def _flat(text: str) -> str:
    return " ".join(text.split())


def _line(item: Item, note: str) -> str:
    suffix = f" [{_flat(note)}]" if note else ""
    where = f"{item.connector} ({item.area})" if item.area else item.connector
    return f"{item.handle} {where}: {_flat(redact(item.title))[:TITLE_LIMIT]}{suffix}"
