from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .domain import (
    HUMAN_FACING_REPORT_STATES,
    Admission,
    Assignment,
    Mandate,
    Report,
    ReportState,
    Strength,
    Ticket,
    TicketStatus,
    Trigger,
)
from .framing import frame_trigger, frame_untrusted
from .gate import RuleMode, Rules, SemanticRule, SourcePolicy, gate
from .store import Store
from .worker import WorkerBackend

logger = logging.getLogger(__name__)

CHAT_WORD_LIMIT = 100
MAX_SEND_ATTEMPTS = 5
LABEL_WORDS = 6
OFFER_VALID_SECONDS = 3600.0
_ATTACHMENT_TAG = re.compile(r"media:", re.IGNORECASE)
_DIRECTIVE_OPENING = re.compile(r"\[(?=\[)")


class Outcome(StrEnum):
    DISPATCHED = "dispatched"
    PASSED_THROUGH = "passed_through"
    CANDIDATE = "candidate"
    IGNORED = "ignored"
    DUPLICATE = "duplicate"
    ADMITTED = "admitted"


@dataclass(frozen=True)
class Disposition:
    outcome: Outcome
    reason: str
    ticket_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Assessment:
    trigger_id: str
    rule: str
    goal: str


Sender = Callable[[str, str], None]


class Assessor(Protocol):
    def assess(self, batch: Sequence[Trigger], rules: Sequence[SemanticRule]) -> Sequence[Assessment]: ...


class NotAdmittedError(PermissionError):
    pass


class Router:
    def __init__(self, store: Store, rules: Rules, backend: WorkerBackend) -> None:
        self.store = store
        self.rules = rules
        self.backend = backend

    def ingest(self, trigger: Trigger, *, auto_dispatch: bool = True) -> Disposition:
        decision = gate(trigger, self.rules)
        if not self.store.record_trigger(trigger, decision.admission, decision.reason, self.rules.version):
            return Disposition(Outcome.DUPLICATE, "trigger already recorded")
        subscribers = [
            ticket_id
            for ticket_id in self.store.subscribers(trigger.thread)
            if self.store.get_ticket(ticket_id).mandate.source == trigger.source
        ]
        if subscribers and self._may_reach_workers(trigger):
            return self._pass_through(trigger, subscribers)
        if decision.admission is Admission.IGNORE:
            return Disposition(Outcome.IGNORED, decision.reason)
        if decision.admission is Admission.CANDIDATE:
            return Disposition(Outcome.CANDIDATE, decision.reason)
        if not auto_dispatch:
            return Disposition(Outcome.ADMITTED, decision.reason)
        self.store.mark_assessed(trigger.id, "dispatched")
        ticket = self._open_ticket(trigger, trigger.text, Strength.EXPLICIT)
        return Disposition(Outcome.DISPATCHED, decision.reason, (ticket.id,))

    def dispatch(self, trigger_id: str, goal: str) -> Ticket:
        trigger = self._admitted_trigger(trigger_id)
        if self.store.claim_offered_trigger(trigger_id, "dispatched", OFFER_VALID_SECONDS):
            return self._open_ticket(trigger, goal, Strength.EXPLICIT)
        existing = self.store.ticket_for_trigger(trigger_id)
        if existing is not None and self.store.assessment_of(trigger_id) == "dispatched":
            return existing
        raise NotAdmittedError(f"trigger {trigger_id} was already used or is not an offered request")

    def forward(self, trigger_id: str, ticket_id: str) -> Ticket:
        trigger, ticket = self._claim_for_ticket(trigger_id, ticket_id, f"forwarded to {ticket_id}")
        self.store.add_input(ticket_id, f"{trigger.source}:{trigger.actor}", frame_trigger(trigger), trusted=True)
        self._resume_if_waiting(ticket)
        return self.store.get_ticket(ticket_id)

    def cancel_from(self, trigger_id: str, ticket_id: str) -> Ticket:
        trigger, _ = self._claim_for_ticket(trigger_id, ticket_id, f"cancelled {ticket_id}")
        return self.cancel(ticket_id, trigger.actor or trigger.source)

    def cancel(self, ticket_id: str, by: str) -> Ticket:
        ticket = self.store.get_ticket(ticket_id)
        if ticket.status is TicketStatus.ENDED:
            raise ValueError(f"ticket {ticket_id} has already ended")
        self.store.end_ticket(ticket_id)
        label = task_label(ticket.id, ticket.goal)
        self.store.queue_message(ticket.id, ticket.channel, chat_safe(shorten(f"Cancelled {label} by {by}")))
        for assignment in self.store.running_assignments():
            if assignment.ticket_id == ticket_id and self.store.end_assignment(assignment.id):
                self.backend.stop(assignment)
        return self.store.get_ticket(ticket_id)

    def _claim_for_ticket(self, trigger_id: str, ticket_id: str, assessment: str) -> tuple[Trigger, Ticket]:
        trigger = self._admitted_trigger(trigger_id)
        ticket = self.store.get_ticket(ticket_id)
        if ticket.status is TicketStatus.ENDED:
            raise ValueError(f"ticket {ticket_id} has ended, dispatch a new one")
        same_person = (
            bool(trigger.actor) and trigger.source == ticket.mandate.source and trigger.actor == ticket.mandate.actor
        )
        if trigger.channel != ticket.channel and not same_person:
            raise NotAdmittedError(f"trigger {trigger_id} comes from another conversation and another person")
        if not self.store.claim_offered_trigger(trigger_id, assessment, OFFER_VALID_SECONDS):
            raise NotAdmittedError(f"trigger {trigger_id} was already used or is not an offered request")
        return trigger, ticket

    def assess_candidates(self, assessor: Assessor) -> list[Ticket]:
        batch = self.store.pending_candidates()
        active_rules = list(self.rules.semantic)
        if not batch:
            return []
        assessments = {item.trigger_id: item for item in assessor.assess(batch, active_rules)}
        modes = {rule.name: rule.mode for rule in active_rules}
        tickets: list[Ticket] = []
        for trigger in batch:
            assessment = assessments.get(trigger.id)
            if assessment is None:
                self.store.mark_assessed(trigger.id, "quiet")
                continue
            if modes.get(assessment.rule) is not RuleMode.ACT:
                self.store.mark_assessed(trigger.id, f"recorded by {assessment.rule}: {assessment.goal}")
                continue
            self.store.mark_assessed(trigger.id, f"accepted by {assessment.rule}")
            tickets.append(self._open_ticket(trigger, assessment.goal, Strength.INFERRED))
        return tickets

    def relay(self, send: Sender | None = None) -> list[tuple[str, str, str]]:
        self.store.move_to_outbox(_render_report, _render_escalation)
        delivered: list[tuple[str, str, str]] = []
        for seq, ticket_id, channel, text in self.store.pending_outbox():
            if send is not None:
                try:
                    send(channel, text)
                except (subprocess.SubprocessError, OSError) as error:
                    gave_up = self.store.record_send_failure(seq, MAX_SEND_ATTEMPTS)
                    logger.warning("send to %s failed (gave up: %s): %s", channel, gave_up, error)
                    continue
            self.store.mark_sent(seq)
            delivered.append((ticket_id, channel, text))
        return delivered

    def status(self) -> list[dict[str, str]]:
        lines = []
        for ticket in self.store.live_tickets():
            reports = self.store.ticket_reports(ticket.id)
            lines.append(
                {
                    "ticket": ticket.id,
                    "goal": shorten(ticket.goal),
                    "status": ticket.status.value,
                    "state": reports[-1].state.value if reports else "starting",
                    "question": reports[-1].question if reports and ticket.status is TicketStatus.WAITING else "",
                }
            )
        return lines

    def is_allowed(self, trigger: Trigger) -> bool:
        rules = self.rules.sources.get(trigger.source)
        return rules is not None and trigger.actor in rules.allow

    def start_worker(self, ticket: Ticket) -> Assignment | None:
        created = self.store.create_assignment(ticket.id)
        if created is None:
            return None
        return self._launch(ticket, *created)

    def _launch(self, ticket: Ticket, assignment: Assignment, token: str) -> Assignment:
        pending = self.store.pending_inputs(ticket.id)
        reports = self.store.ticket_reports(ticket.id)
        parts = [text for _, _, text, _ in pending]
        if reports:
            last = reports[-1]
            history = f"{last.summary}\nIt asked: {last.question}" if last.question else last.summary
            parts.insert(0, frame_untrusted("what the last worker reported", history, keep_tail=True))
        pid = self.backend.start(ticket, assignment, token, "\n".join(parts))
        self.store.set_assignment_pid(assignment.id, pid)
        self.store.mark_inputs_delivered([seq for seq, _, _, _ in pending])
        return self.store.get_assignment(assignment.id)

    def _admitted_trigger(self, trigger_id: str) -> Trigger:
        trigger = self.store.get_trigger(trigger_id)
        if trigger is None:
            raise NotAdmittedError(f"unknown trigger: {trigger_id}")
        if self.store.admission_of(trigger_id) is not Admission.ACT:
            raise NotAdmittedError(f"trigger {trigger_id} was not admitted by the gate")
        if gate(trigger, self.rules).admission is not Admission.ACT:
            raise NotAdmittedError(f"trigger {trigger_id} is no longer allowed by the current rules")
        return trigger

    def _open_ticket(self, trigger: Trigger, goal: str, strength: Strength) -> Ticket:
        mandate = Mandate(self.rules.version, goal, strength, trigger.actor, trigger.source, trigger.id)
        source = self.rules.sources.get(trigger.source)
        channel = source.notify if source is not None and source.notify else trigger.channel
        ticket, created = self.store.create_ticket(goal, mandate, channel, trigger.thread)
        if created:
            self.start_worker(ticket)
        return ticket

    def _pass_through(self, trigger: Trigger, subscribers: list[str]) -> Disposition:
        framed = frame_trigger(trigger)
        trusted = self.is_allowed(trigger)
        for ticket_id in subscribers:
            self.store.add_input(ticket_id, f"{trigger.source}:{trigger.actor}", framed, trusted=trusted)
            if trusted:
                self._resume_if_waiting(self.store.get_ticket(ticket_id))
        self.store.mark_assessed(trigger.id, "passed through")
        return Disposition(Outcome.PASSED_THROUGH, "thread has a live subscriber", tuple(subscribers))

    def _resume_if_waiting(self, ticket: Ticket) -> None:
        resumed = self.store.resume_waiting_ticket(ticket.id)
        if resumed is not None:
            self._launch(ticket, *resumed)

    def _may_reach_workers(self, trigger: Trigger) -> bool:
        rules = self.rules.sources.get(trigger.source)
        if rules is None or rules.policy is SourcePolicy.IGNORE or trigger.is_bot:
            return False
        return self.is_allowed(trigger) or rules.pass_strangers


def chat_safe(text: str) -> str:
    return _DIRECTIVE_OPENING.sub("[ ", _ATTACHMENT_TAG.sub("media ", text))


def shorten(text: str, limit: int = CHAT_WORD_LIMIT) -> str:
    words = text.split()
    return text if len(words) <= limit else " ".join(words[:limit]) + " ..."


def task_label(ticket_id: str, goal: str) -> str:
    return f"[{shorten(goal, LABEL_WORDS)} · {ticket_id}]"


def _render_report(report: Report, goal: str) -> str | None:
    if report.state not in HUMAN_FACING_REPORT_STATES:
        return None
    text = report.question if report.question else report.summary
    prefix = "Done" if report.state is ReportState.READY else report.state.value.capitalize()
    return chat_safe(shorten(f"{prefix} {task_label(report.ticket_id, goal)}: {text}"))


def _render_escalation(reason: str, ticket_id: str, goal: str) -> str:
    return chat_safe(shorten(f"Needs you {task_label(ticket_id, goal)}: {reason}"))
