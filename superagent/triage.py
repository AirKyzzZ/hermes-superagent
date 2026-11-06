from __future__ import annotations

import fnmatch
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .ledger import Item, ItemState, Ledger, LedgerError
from .polling import Notify

logger = logging.getLogger(__name__)

FIELDS = ("connector", "title", "url", "due", "excerpt")
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
            candidates = [value, *(part.strip() for part in value.split(","))] if "," in value else [value]
            lowered = [pattern.lower() for pattern in patterns]
            if not any(
                fnmatch.fnmatchcase(candidate.lower(), pattern) for candidate in candidates for pattern in lowered
            ):
                return False
        return True


def field_value(item: Item, key: str) -> str | None:
    if key == "id":
        return item.external_id
    if key in FIELDS:
        return str(getattr(item, key))
    value = item.meta.get(key)
    return None if value is None else str(value)


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
    parsed = []
    for number, body in enumerate(rules, 1):
        where = f"triage rule {number}"
        if not isinstance(body, Mapping) or set(body) - {"name", "match", "area", "ignore"}:
            raise TriageError(f"{where} takes name, match, and either area or ignore")
        name = body.get("name", str(number))
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise TriageError(f"{where} name must use lowercase letters, digits, - and _")
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
            ledger.move(item.id, ItemState.TRIAGED, actor, "", expected=ItemState.NEW, area=rule.area)
        except LedgerError as error:
            logger.info("left %s as it is: %s", item.handle, error)
            continue
        channel = channels.get(rule.area, "")
        if notify is not None and channel and ledger.announce_once(item.id, f"area:{rule.area}@{item.last_changed}"):
            title = " ".join(item.title.split())[:200]
            notify(channel, f"New in {rule.area}: {title} [{item.connector}] ({item.handle})")
