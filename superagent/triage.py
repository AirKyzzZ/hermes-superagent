from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache

from .agents import redact
from .ledger import Item, ItemState, Ledger, LedgerError
from .polling import Notify

logger = logging.getLogger(__name__)

FIELDS = ("connector", "title", "url", "due", "excerpt")
LIST_KEYS = frozenset({"labels", "matched", "tags"})
RESERVED_NAMES = frozenset({"untracked", "owned"})
LINES_PER_MESSAGE = 20
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


class TriageError(ValueError):
    pass


@dataclass(frozen=True)
class TriageRule:
    name: str
    match: Mapping[str, tuple[str, ...]]
    area: str = ""
    ignore: str = ""

    def matches(self, item: Item) -> bool:
        for key, patterns in self.match.items():
            value = field_value(item, key)
            if value is None:
                return False
            candidates = [part.strip() for part in value.split(",")] if key in LIST_KEYS else [value]
            if not any(glob(pattern).fullmatch(candidate) for candidate in candidates for pattern in patterns):
                return False
        return True


@lru_cache(maxsize=512)
def glob(pattern: str) -> re.Pattern[str]:
    parts = (".*" if char == "*" else "." if char == "?" else re.escape(char) for char in pattern)
    return re.compile("".join(parts), re.IGNORECASE | re.DOTALL)


def field_value(item: Item, key: str) -> str | None:
    if key == "id":
        return item.external_id
    value = getattr(item, key) if key in FIELDS else item.meta.get(key)
    return str(value) if value else None


def parse_triage(rules: object, areas: object) -> tuple[tuple[TriageRule, ...], dict[str, str]]:
    if not isinstance(areas, Mapping):
        raise TriageError("[areas] must be a table of named areas")
    channels: dict[str, str] = {}
    for name, body in areas.items():
        if not _NAME.fullmatch(str(name)) or not isinstance(body, Mapping) or set(body) - {"channel"}:
            raise TriageError(f"[areas.{name}] takes a lowercase name and only a channel")
        channel = body.get("channel", "")
        if not isinstance(channel, str):
            raise TriageError(f"[areas.{name}] channel must be a string such as discord:123")
        channels[str(name)] = channel
    if not isinstance(rules, list):
        raise TriageError("[[triage]] must be a list of rules")
    parsed: list[TriageRule] = []
    for number, body in enumerate(rules, 1):
        where = f"triage rule {number}"
        if not isinstance(body, Mapping) or set(body) - {"name", "match", "area", "ignore"}:
            raise TriageError(f"{where} takes name, match, and either area or ignore")
        name = body.get("name", str(number))
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise TriageError(f"{where} name must use lowercase letters, digits, - and _")
        if name in RESERVED_NAMES:
            raise TriageError(f"{where} can't be named {name}, the plugin's own rules use that name")
        if any(rule.name == name for rule in parsed):
            raise TriageError(f"{where} reuses the name {name}, and each rule needs its own")
        match = body.get("match")
        if not isinstance(match, Mapping) or not match:
            raise TriageError(f'{where} needs a match table, such as {{ connector = "github", repo = "acme/*" }}')
        patterns: dict[str, tuple[str, ...]] = {}
        for key, value in match.items():
            values = [value] if isinstance(value, str) else value
            if not isinstance(values, list) or not values or not all(isinstance(v, str) and v for v in values):
                raise TriageError(f"{where} match.{key} must be a pattern or a list of patterns")
            patterns[str(key)] = tuple(values)
        area, ignore = body.get("area", ""), body.get("ignore", "")
        if not isinstance(area, str) or not isinstance(ignore, str) or bool(area) == bool(ignore.strip()):
            raise TriageError(f"{where} needs exactly one of area or ignore, and ignore needs its reason")
        if area and not _NAME.fullmatch(area):
            raise TriageError(f"{where} area must use lowercase letters, digits, - and _")
        if area and channels and area not in channels:
            raise TriageError(f"{where} files items under {area}, which isn't one of the [areas]")
        parsed.append(TriageRule(name, patterns, area, ignore.strip()))
    return tuple(parsed), channels


def triage_new(
    ledger: Ledger, rules: Sequence[TriageRule], channels: Mapping[str, str], notify: Notify | None = None
) -> None:
    if not rules:
        return
    filed: dict[str, list[tuple[Item, str]]] = {}
    for item in ledger.items(open_only=True):
        if item.state is not ItemState.NEW:
            continue
        rule = next((rule for rule in rules if rule.matches(item)), None)
        if rule is None:
            continue
        actor = f"rule:{rule.name}"
        try:
            if rule.ignore:
                ledger.move(item.id, ItemState.IGNORED, actor, rule.ignore, expected=ItemState.NEW)
                continue
            moved = ledger.move(item.id, ItemState.TRIAGED, actor, "", expected=ItemState.NEW, area=rule.area)
        except LedgerError as error:
            logger.info("left %s as it is: %s", item.handle, error)
            continue
        key = f"area:{rule.area}@{moved.last_changed}"
        if channels.get(rule.area) and not ledger.announced(item.id, key):
            filed.setdefault(rule.area, []).append((moved, key))
    if notify is None:
        return
    for area, entries in filed.items():
        lines = [
            f"- {' '.join(redact(item.title).split())[:200]} [{item.connector}] ({item.handle})"
            for item, _ in entries[:LINES_PER_MESSAGE]
        ]
        if len(entries) > LINES_PER_MESSAGE:
            lines.append(f"- and {len(entries) - LINES_PER_MESSAGE} more, see superagent ledger list")
        notify(channels[area], "\n".join([f"New in {area}:", *lines]))
        for item, key in entries:
            ledger.announce_once(item.id, key)
