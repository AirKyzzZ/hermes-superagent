from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .briefwriter import BriefWriter, build_pack
from .connectors import ContextPiece
from .ledger import Item, ItemState, Upstream


class PastTaskError(ValueError):
    pass


@dataclass(frozen=True)
class PastTask:
    name: str
    item: Item
    context: tuple[ContextPiece, ...]
    typed: str
    ran: str


def load_past_tasks(path: Path) -> list[PastTask]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PastTaskError(f"can't read {path}: {error}") from error
    if not isinstance(raw, list) or not raw:
        raise PastTaskError(f"{path} must hold a list of past tasks")
    return [_task(number, entry) for number, entry in enumerate(raw, 1)]


def _task(number: int, entry: object) -> PastTask:
    where = f"past task {number}"
    if not isinstance(entry, Mapping):
        raise PastTaskError(f"{where} must be an object")
    for key in ("title", "typed", "ran"):
        if not isinstance(entry.get(key), str) or not str(entry.get(key)).strip():
            raise PastTaskError(f"{where} needs {key}: the item's title, what the owner typed and the brief that ran")
    context = entry.get("context", [])
    if not isinstance(context, list) or not all(_is_piece(piece) for piece in context):
        raise PastTaskError(f"{where} context must be a list of {{label, trusted, text}}")
    meta = entry.get("meta", {})
    if not isinstance(meta, Mapping):
        raise PastTaskError(f"{where} meta must be an object")
    connector = str(entry.get("connector") or "past")
    item = Item(
        id=f"{connector}:past-{number}",
        connector=connector,
        external_id=f"past-{number}",
        handle=f"past-{number}",
        title=str(entry["title"]),
        url=str(entry.get("url") or ""),
        due="",
        upstream=Upstream.OPEN,
        updated="",
        excerpt=str(entry.get("excerpt") or ""),
        meta={str(key): str(value) for key, value in meta.items()},
        state=ItemState.ASSIGNED,
        reason="",
        area="",
        first_seen=0.0,
        last_seen=0.0,
        last_changed=0.0,
        missing_since=None,
        snoozed_until=None,
        ticket_id=None,
    )
    pieces = tuple(ContextPiece(str(p["label"]), bool(p["trusted"]), str(p["text"])) for p in context)
    return PastTask(str(entry.get("name") or f"task {number}"), item, pieces, str(entry["typed"]), str(entry["ran"]))


def _is_piece(piece: object) -> bool:
    return (
        isinstance(piece, Mapping)
        and isinstance(piece.get("label"), str)
        and isinstance(piece.get("trusted"), bool)
        and isinstance(piece.get("text"), str)
    )


def compare(tasks: Sequence[PastTask], writer: BriefWriter, trusted_source: bool = False) -> tuple[str, str]:
    sheet = [
        "# Which brief would you rather hand a worker?",
        "",
        "Each task shows what you typed, then two briefs in random order. Pick A or B and say why. "
        "The key is in a separate file.",
    ]
    key = ["# Key", ""]
    for number, task in enumerate(tasks, 1):
        pack = build_pack(None, task.item, trusted_source=trusted_source, context=task.context)
        fresh = writer.write(pack).text
        new_first = int(hashlib.sha256(task.name.encode()).hexdigest(), 16) % 2 == 0
        first, second = (fresh, task.ran) if new_first else (task.ran, fresh)
        sheet += ["", f"## {number}. {task.name}", "", f"You typed: {task.typed}", ""]
        sheet += [
            "### Brief A",
            "",
            "```text",
            first.strip(),
            "```",
            "",
            "### Brief B",
            "",
            "```text",
            second.strip(),
            "```",
        ]
        sheet += ["", "Better: A / B, because:"]
        key.append(f"- {number}. {task.name}: {'A' if new_first else 'B'} is the new brief")
    return "\n".join(sheet) + "\n", "\n".join(key) + "\n"
