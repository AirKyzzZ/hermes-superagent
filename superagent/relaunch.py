from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence

from .connectors import Connector, ConnectorError, ConnectorSpec
from .connectors.agents import AgentsConnector
from .ledger import Item, Ledger
from .polling import Notify

logger = logging.getLogger(__name__)

RELAUNCH_ACTOR = "relaunch"
BUDGET = {"nudge": 1, "resume": 1}
AUTO_RESUME_GAVE_UP = frozenset({"stale", "disabled"})


def due_action(item: Item, connector: AgentsConnector, now: float) -> str | None:
    if not item.meta.get("since") or item.last_seen <= item.last_changed:
        return None
    state = item.meta.get("state")
    if state == "limited":
        if item.meta.get("error") == "rate_limit":
            return "nudge" if item.meta.get("auto_resume") in AUTO_RESUME_GAVE_UP else None
        return "nudge" if now - item.last_changed >= connector.retry_after else None
    if state == "gone":
        return "resume"
    return None


def relaunch_sessions(
    ledger: Ledger,
    specs: Sequence[ConnectorSpec],
    connectors: Mapping[str, Connector],
    notify: Notify | None = None,
    now: float | None = None,
) -> None:
    moment = time.time() if now is None else now
    for spec in specs:
        connector = connectors.get(spec.name)
        if not isinstance(connector, AgentsConnector) or not connector.relaunch:
            continue
        for item in ledger.items(open_only=True, connector=spec.name):
            try:
                relaunch_one(ledger, spec, connector, item, moment, notify)
            except Exception:
                logger.exception("relaunching %s failed", item.handle)


def relaunch_one(
    ledger: Ledger, spec: ConnectorSpec, connector: AgentsConnector, item: Item, now: float, notify: Notify | None
) -> None:
    action = due_action(item, connector, now)
    if action is None or not ledger.announce_once(item.id, f"relaunch:{action}@{item.meta.get('since')}"):
        return
    used = sum(
        1
        for event in ledger.history(item.id)
        if event.actor == RELAUNCH_ACTOR and event.note.startswith(f"{action} done")
    )
    if used >= BUDGET[action]:
        done, detail = False, f"it was already given its {action}"
    else:
        try:
            done, detail = connector.act(item.external_id, action, str(item.meta["since"]))
        except ConnectorError as error:
            done, detail = False, str(error)
    ledger.note(item.id, RELAUNCH_ACTOR, f"{action} {'done' if done else 'held'}: {detail}")
    if notify is None or not spec.channel:
        return
    state = item.meta.get("state")
    if done:
        notify(spec.channel, f"{item.title} [{spec.name}] was {state}, so it got a {action}: {detail} ({item.handle})")
    else:
        notify(
            spec.channel, f"{item.title} [{spec.name}] is {state} and needs you, no {action}: {detail} ({item.handle})"
        )
