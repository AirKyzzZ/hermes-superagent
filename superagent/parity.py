from __future__ import annotations

import json
import random
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .briefwriter import BriefWriter, Pack, plain
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


PackBuilder = Callable[[PastTask], Pack]


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
        raise PastTaskError(f"{where} context must be a list of {{label, trusted, text}}, with weak optional")
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
    pieces = tuple(
        ContextPiece(str(p["label"]), bool(p["trusted"]), str(p["text"]), bool(p.get("weak", False))) for p in context
    )
    return PastTask(str(entry.get("name") or f"task {number}"), item, pieces, str(entry["typed"]), str(entry["ran"]))


def _is_piece(piece: object) -> bool:
    return (
        isinstance(piece, Mapping)
        and isinstance(piece.get("label"), str)
        and isinstance(piece.get("trusted"), bool)
        and isinstance(piece.get("text"), str)
        and isinstance(piece.get("weak", False), bool)
    )


def fenced(text: str) -> list[str]:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}text", text.strip(), fence]


def balanced_sides(count: int, rng: random.Random) -> list[bool]:
    sides = [index % 2 == 0 for index in range(count)]
    rng.shuffle(sides)
    return sides


def compare(
    tasks: Sequence[PastTask],
    writer: BriefWriter,
    build: PackBuilder,
    rng: random.Random | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, str]:
    sides = balanced_sides(len(tasks), rng or random.SystemRandom())
    sheet = [
        "# Which brief would you rather hand a worker?",
        "",
        "Each task shows what you typed, then two briefs in random order, both in plain text. "
        "Pick A or B and say why. The key is in a separate file.",
    ]
    key = ["# Key", ""]
    for number, (task, new_first) in enumerate(zip(tasks, sides, strict=True), 1):
        if progress is not None:
            progress(f"{number}/{len(tasks)} {task.name}")
        try:
            written = writer.write(build(task))
        except Exception as error:
            key.append(f"- {number}. {task.name}: not compared, the new brief couldn't be written ({error})")
            continue
        fresh = plain(written.draft) if written.draft is not None else written.text
        first, second = (fresh, task.ran) if new_first else (task.ran, fresh)
        sheet += ["", f"## {number}. {task.name}", "", "You typed:", "", *fenced(task.typed), ""]
        sheet += ["### Brief A", "", *fenced(first), "", "### Brief B", "", *fenced(second), ""]
        sheet += ["Better: A / B, because:"]
        verdict = "not ready" if written.failures else "needs approval" if written.needs_approval else "ready"
        lengths = f"{len(fresh.splitlines())} lines new, {len(task.ran.splitlines())} lines past"
        found = f", the lint found: {'; '.join(written.failures)}" if written.failures else ""
        key.append(f"- {number}. {task.name}: {'A' if new_first else 'B'} is new ({verdict}, {lengths}{found})")
    return "\n".join(sheet) + "\n", "\n".join(key) + "\n"
