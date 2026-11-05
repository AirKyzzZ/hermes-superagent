from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence

from .connectors import Connector, ConnectorSpec
from .ledger import ItemState, Ledger, LedgerError, SourceState

logger = logging.getLogger(__name__)

SHRINK_FLOOR = 5
FAILURES_BEFORE_WARNING = 3
STALE_FLOOR_SECONDS = 3600.0
MAX_CONFIRMS_PER_POLL = 20


def poll_sources(ledger: Ledger, specs: Sequence[ConnectorSpec], connectors: Mapping[str, Connector]) -> list[str]:
    warnings: list[str] = []
    for spec in specs:
        state = ledger.source(spec.name)
        if state.last_attempt is None or time.time() - state.last_attempt >= spec.every_seconds:
            poll_one(ledger, spec, connectors[spec.name], state)
        warnings += source_health(spec, ledger.source(spec.name), time.time())
    return warnings


def poll_one(ledger: Ledger, spec: ConnectorSpec, connector: Connector, state: SourceState) -> None:
    ledger.begin_poll(spec.name)
    try:
        poll = connector.poll(state.cursor)
        new_items = 0
        for source in poll.items:
            item = ledger.record(spec.name, source)
            if item.first_seen == item.last_seen:
                new_items += 1
            if not spec.track and item.state is ItemState.NEW:
                ledger.move(
                    item.id, ItemState.IGNORED, "rule:untracked", "informational source", expected=ItemState.NEW
                )
        applied = held = False
        if poll.complete:
            previous = state.applied_count
            shrank = previous is not None and previous >= SHRINK_FLOOR and len(poll.items) * 2 < previous
            if shrank and not state.accept_next:
                held = True
            else:
                ledger.mark_missing(spec.name, [source.external_id for source in poll.items])
                applied = True
    except Exception as error:
        logger.warning("polling %s failed: %s", spec.name, error)
        ledger.poll_failed(spec.name, str(error) or type(error).__name__)
        return
    ledger.poll_succeeded(
        spec.name, count=len(poll.items), new_items=new_items, cursor=poll.cursor, applied=applied, held=held
    )
    if applied:
        confirm_vanished(ledger, spec, connector)


def confirm_vanished(ledger: Ledger, spec: ConnectorSpec, connector: Connector) -> None:
    vanished = ledger.items(open_only=True, connector=spec.name, missing=True)[:MAX_CONFIRMS_PER_POLL]
    for item in vanished:
        try:
            ledger.confirm(item.id, connector.confirm(item.external_id), f"connector:{spec.name}")
        except (LedgerError, Exception) as error:
            logger.warning("confirming %s failed: %s", item.id, error)


def source_health(spec: ConnectorSpec, state: SourceState, now: float) -> list[str]:
    name = f"connector {spec.name}"
    warnings: list[str] = []
    if state.failures >= FAILURES_BEFORE_WARNING:
        warnings.append(f"{name}: {state.failures} failed polls in a row, last: {state.last_error[:300]}")
    since_ok = state.last_ok if state.last_ok is not None else state.first_attempt
    stale_after = max(3 * spec.every_seconds, STALE_FLOOR_SECONDS)
    if since_ok is not None and now - since_ok > stale_after:
        warnings.append(f"{name}: no successful poll for {(now - since_ok) / 60:.0f} minutes")
    if state.held_count is not None:
        warnings.append(
            f"{name}: its snapshot shrank from {state.applied_count} to {state.held_count} items, so nothing was "
            f"marked gone. If that's right, run `superagent ledger accept {spec.name}`"
        )
    since_new = state.last_new if state.last_new is not None else state.first_attempt
    if spec.quiet_after_seconds and since_new is not None and now - since_new > spec.quiet_after_seconds:
        warnings.append(f"{name}: nothing new for {(now - since_new) / 3600:.0f} hours")
    return warnings
