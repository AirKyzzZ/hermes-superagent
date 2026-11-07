from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from .agents import redact
from .connectors import Connector, ConnectorSpec
from .ledger import Item, ItemState, Ledger, LedgerError, SourceState, item_id

logger = logging.getLogger(__name__)

SHRINK_FLOOR = 5
FAILURES_BEFORE_WARNING = 3
STALE_FLOOR_SECONDS = 3600.0
MAX_CONFIRMS_PER_POLL = 20
PROBLEMS_SHOWN = 5
NEW_ITEMS_SHOWN = 20
QUIET_AT_FIRST_SIGHT = frozenset({"gone", "ended"})


@dataclass(frozen=True)
class SourceWarning:
    key: str
    text: str


class PollProblem(RuntimeError):
    pass


Notify = Callable[[str, str], None]


def poll_sources(
    ledger: Ledger,
    specs: Sequence[ConnectorSpec],
    connectors: Mapping[str, Connector],
    budget_seconds: float,
    notify: Notify | None = None,
) -> list[SourceWarning]:
    deadline = time.monotonic() + budget_seconds
    states = {spec.name: ledger.source(spec.name) for spec in specs}
    for spec in sorted(specs, key=lambda spec: states[spec.name].last_attempt or 0.0):
        state = states[spec.name]
        due = state.last_attempt is None or time.time() - state.last_attempt >= spec.every_seconds
        connector = connectors[spec.name]
        if not due or deadline - time.monotonic() < connector.timeout:
            continue
        try:
            poll_one(ledger, spec, connector, state, deadline, notify)
        except Exception:
            logger.exception("polling %s failed outside the connector", spec.name)
    warnings: list[SourceWarning] = []
    for spec in specs:
        warnings += source_health(spec, ledger.source(spec.name), time.time())
    return warnings


def poll_one(
    ledger: Ledger,
    spec: ConnectorSpec,
    connector: Connector,
    state: SourceState,
    deadline: float | None = None,
    notify: Notify | None = None,
) -> None:
    ledger.begin_poll(spec.name)
    try:
        poll = connector.poll(state.cursor)
        new_items = 0
        fresh: list[Item] = []
        for source in poll.items:
            before = ledger.peek(item_id(spec.name, source.external_id))
            item = ledger.record(spec.name, source)
            if item.first_seen == item.last_seen:
                new_items += 1
            if before is None:
                fresh.append(item)
            if spec.owned and item.state is ItemState.NEW:
                try:
                    item = ledger.move(item.id, ItemState.TRIAGED, "rule:owned", "", expected=ItemState.NEW)
                except LedgerError as error:
                    logger.info("left %s as it is: %s", item.id, error)
            announce(ledger, spec, before, item, notify)
            if not spec.track and item.state is ItemState.NEW:
                try:
                    ledger.move(
                        item.id, ItemState.IGNORED, "rule:untracked", "informational source", expected=ItemState.NEW
                    )
                except LedgerError as error:
                    logger.info("left %s as it is: %s", item.id, error)
        if notify is not None and spec.announce_new and spec.channel and fresh and state.last_ok is not None:
            notify(spec.channel, new_items_message(spec.name, fresh))
        if poll.problems:
            extra = len(poll.problems) - PROBLEMS_SHOWN
            raise PollProblem("; ".join(poll.problems[:PROBLEMS_SHOWN]) + (f" (+{extra} more)" if extra > 0 else ""))
        applied = held = False
        if poll.complete:
            previous = state.applied_count
            shrank = previous is not None and previous >= SHRINK_FLOOR and len(poll.items) * 2 <= previous
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
        confirm_vanished(ledger, spec, connector, deadline)


def new_items_message(name: str, items: Sequence[Item]) -> str:
    lines = [f"- {' '.join(redact(item.title).split())[:200]} ({item.handle})" for item in items[:NEW_ITEMS_SHOWN]]
    if len(items) > NEW_ITEMS_SHOWN:
        lines.append(f"- and {len(items) - NEW_ITEMS_SHOWN} more, see superagent ledger list --connector {name}")
    return "\n".join([f"New in {name} since the last poll:", *lines])


def announce(ledger: Ledger, spec: ConnectorSpec, before: Item | None, item: Item, notify: Notify | None) -> None:
    state = item.meta.get("state", "")
    if notify is None or not spec.channel or state not in spec.notify or not item.is_open:
        return
    if before is None or before.meta.get("state") != state:
        return
    if state in QUIET_AT_FIRST_SIGHT and item.last_changed == item.first_seen:
        return
    if not ledger.announce_once(item.id, f"{state}@{item.last_changed}"):
        return
    detail = f": {redact(item.excerpt)[:200]}" if spec.notify_detail and item.excerpt else ""
    hint = f". Resume it with claude --resume {item.external_id} in its folder" if state == "gone" else ""
    notify(spec.channel, f"{item.title} [{spec.name}] is {state}{detail} ({item.handle}){hint}")


def confirm_vanished(ledger: Ledger, spec: ConnectorSpec, connector: Connector, deadline: float | None) -> None:
    failures: list[str] = []
    checked = 0
    for item in ledger.vanished(spec.name, MAX_CONFIRMS_PER_POLL):
        if deadline is not None and deadline - time.monotonic() < connector.timeout:
            break
        checked += 1
        try:
            ledger.confirm(item.id, connector.confirm(item.external_id), f"connector:{spec.name}")
        except Exception as error:
            failures.append(f"{item.handle}: {error}")
    if checked:
        ledger.confirm_round(spec.name, "; ".join(failures)[:300])


def source_health(spec: ConnectorSpec, state: SourceState, now: float) -> list[SourceWarning]:
    name = f"connector {spec.name}"
    warnings: list[SourceWarning] = []
    if state.failures >= FAILURES_BEFORE_WARNING:
        text = f"{name}: {state.failures} failed polls in a row, last: {state.last_error[:200]}"
        warnings.append(SourceWarning(f"{name}: failing", text))
    since_ok = state.last_ok if state.last_ok is not None else state.first_attempt
    if since_ok is not None and now - since_ok > max(3 * spec.every_seconds, STALE_FLOOR_SECONDS):
        text = f"{name}: no successful poll for {(now - since_ok) / 60:.0f} minutes"
        warnings.append(SourceWarning(f"{name}: stale", text))
    if state.held_count is not None:
        text = (
            f"{name}: its snapshot shrank from {state.applied_count} to {state.held_count} items, so nothing was "
            f"marked gone. If that's right, run `superagent ledger accept {spec.name}`"
        )
        warnings.append(SourceWarning(f"{name}: held", text))
    since_new = state.last_new if state.last_new is not None else state.first_attempt
    if spec.quiet_after_seconds and since_new is not None and now - since_new > spec.quiet_after_seconds:
        text = f"{name}: nothing new for {(now - since_new) / 3600:.0f} hours"
        warnings.append(SourceWarning(f"{name}: quiet", text))
    if state.confirm_error:
        text = f"{name}: couldn't check vanished items: {state.confirm_error[:200]}"
        warnings.append(SourceWarning(f"{name}: confirm", text))
    return warnings
