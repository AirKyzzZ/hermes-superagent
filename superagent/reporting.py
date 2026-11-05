from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .domain import ReportState
from .store import Store
from .worker import ENV_ASSIGNMENT, ENV_DB, ENV_TICKET, ENV_TOKEN


@dataclass(frozen=True)
class WorkerIdentity:
    assignment_id: str
    token: str
    ticket_id: str
    db_path: Path


class NotAWorkerError(RuntimeError):
    pass


def identity_from_env() -> WorkerIdentity:
    try:
        return WorkerIdentity(
            os.environ[ENV_ASSIGNMENT], os.environ[ENV_TOKEN], os.environ[ENV_TICKET], Path(os.environ[ENV_DB])
        )
    except KeyError as error:
        raise NotAWorkerError(f"not running as a superagent worker: {error} is unset") from error


def submit_report(identity: WorkerIdentity, state: ReportState, summary: str, question: str = "") -> dict[str, object]:
    store = Store(identity.db_path)
    store.add_report(identity.assignment_id, identity.token, state, summary, question)
    inputs = [
        {"from": label, "from_allowed_person": trusted, "content": text}
        for label, text, trusted in store.take_inputs(identity.ticket_id)
    ]
    return {"reported": state.value, "inputs": inputs}


def record_tool_activity(identity: WorkerIdentity, tool_name: str, args: object, result: object) -> None:
    payload = json.dumps([tool_name, args, str(result)], sort_keys=True, default=str)
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()[:16]
    Store(identity.db_path).record_activity(identity.assignment_id, identity.token, fingerprint)
