from __future__ import annotations

import glob
import hashlib
import os
import re
import stat
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll

KEYS = frozenset({"paths", "timeout"})
HASH_CHARS = 8
_WILDCARD = re.compile(r"[*?[]")
_TASK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[(.)\]\s+(\S.*)$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*?)(?:\s+#+)?\s*$")
_DUE = re.compile(r"(?:📅️?\s*|\bdue:)(\d{4}-\d{2}-\d{2})")
# Obsidian Tasks appends ✅ and a date when a task is ticked, so dates stay out of the id
_DATED = re.compile(r"(?:[📅⏳🛫✅➕❌]️?\s*|\bdue:)\d{4}-\d{2}-\d{2}")
_WIKILINK = re.compile(r"!?\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]*)[^)]*\)")
_MARKS = {" ": Upstream.OPEN, "x": Upstream.CLOSED, "X": Upstream.CLOSED}


@dataclass(frozen=True)
class _Task:
    key: str
    mark: str
    title: str
    due: str
    heading: str


@dataclass(frozen=True)
class MarkdownConnector:
    paths: tuple[str, ...]
    timeout: float

    def poll(self, cursor: str) -> Poll:
        deadline = time.monotonic() + self.timeout
        items: list[SourceItem] = []
        for relative, path in self._files(deadline).items():
            text = self._read(path, deadline)
            if "[ ]" in text:
                items += (_item(relative, task) for task in _tasks(text) if task.mark == " ")
        return Poll(tuple(items), complete=True)

    def confirm(self, external_id: str) -> Upstream:
        deadline = time.monotonic() + self.timeout
        relative, _, key = external_id.rpartition("#")
        path = self._files(deadline).get(relative)
        if path is None:
            return Upstream.UNKNOWN
        for task in _tasks(self._read(path, deadline)):
            if task.key == key:
                return _MARKS.get(task.mark, Upstream.UNKNOWN)
        return Upstream.UNKNOWN

    def _files(self, deadline: float) -> dict[str, Path]:
        found: dict[str, Path] = {}
        seen: set[Path] = set()
        for configured in self.paths:
            try:
                for relative, path in _expand(configured):
                    self._in_time(deadline, path)
                    if path in seen:
                        continue
                    seen.add(path)
                    if relative in found:
                        raise ConnectorError(
                            f"{found[relative]} and {path} both read as {relative}, so their tasks would share ids; "
                            "list them in separate markdown connectors"
                        )
                    found[relative] = path
            except OSError as error:
                raise ConnectorError(f"can't read {error.filename or configured}: {error.strerror or error}") from error
        return found

    def _read(self, path: Path, deadline: float) -> str:
        self._in_time(deadline, path)
        try:
            return path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as error:
            raise ConnectorError(f"can't read {path}: {error.strerror or error}") from error

    def _in_time(self, deadline: float, path: Path) -> None:
        if time.monotonic() > deadline:
            raise ConnectorError(f"reading the notes took longer than {self.timeout:g}s, stopped at {path}")


def _expand(configured: str) -> Iterator[tuple[str, Path]]:
    parts = Path(configured).parts
    wild = next((index for index, part in enumerate(parts) if _WILDCARD.search(part)), None)
    if wild is not None:
        base = Path(*parts[:wild])
        matches = [Path(match) for match in sorted(glob.glob(configured, recursive=True)) if os.path.isfile(match)]
        if not matches:
            raise ConnectorError(f"{configured} matched no files")
        for match in matches:
            yield match.relative_to(base).as_posix(), match
        return
    root = Path(configured)
    mode = root.stat().st_mode
    if stat.S_ISREG(mode):
        yield root.name, root
    elif stat.S_ISDIR(mode):
        for directory, subdirectories, names in os.walk(root, onerror=_fail):
            subdirectories[:] = sorted(name for name in subdirectories if not name.startswith("."))
            for name in sorted(names):
                if name.endswith(".md"):
                    path = Path(directory, name)
                    yield path.relative_to(root).as_posix(), path
    else:
        raise ConnectorError(f"{configured} isn't a file or a directory")


def _fail(error: OSError) -> NoReturn:
    raise error


def _tasks(text: str) -> Iterator[_Task]:
    seen: Counter[str] = Counter()
    fence, heading = "", ""
    for line in text.splitlines():
        if fenced := _FENCE.match(line):
            marker, rest = fenced.groups()
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not rest.strip():
                fence = ""
            continue
        if fence:
            continue
        if titled := _HEADING.match(line):
            heading = " ".join(titled.group(1).split())
        elif task := _TASK.match(line):
            mark, raw = task.groups()
            title = _readable(_DATED.sub("", raw)) or " ".join(raw.split())
            digest = hashlib.sha256(title.casefold().encode()).hexdigest()[:HASH_CHARS]
            seen[digest] += 1
            due = _DUE.search(raw)
            key = digest if seen[digest] == 1 else f"{digest}~{seen[digest]}"
            yield _Task(key, mark, title, due.group(1) if due else "", heading)


def _readable(text: str) -> str:
    text = _WIKILINK.sub(lambda link: link.group(2) or link.group(1), text)
    return " ".join(_LINK.sub(_link, text).split())


def _link(link: re.Match[str]) -> str:
    label, url = " ".join(link.group(1).split()), link.group(2)
    return f"{label} ({url})" if label and label != url else url


def _item(relative: str, task: _Task) -> SourceItem:
    where = PurePosixPath(relative)
    meta = {"file": relative, "heading": task.heading, "project": where.parent.name or where.stem}
    return SourceItem(
        external_id=f"{relative}#{task.key}",
        title=task.title,
        due=task.due,
        meta={key: value for key, value in meta.items() if value},
    )


def build(spec: ConnectorSpec) -> MarkdownConnector:
    where = f"[connectors.{spec.name}]"
    paths = spec.settings.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(path, str) and path.strip() for path in paths):
        raise ConnectorConfigError(f"{where} paths must be a list of Markdown files, directories or globs")
    expanded = tuple(os.path.normpath(os.path.expanduser(path)) for path in paths)
    if relative := [path for path in expanded if not os.path.isabs(path)]:
        raise ConnectorConfigError(f"{where} paths must be absolute or start with ~, got {relative[0]!r}")
    timeout = spec.settings.get("timeout", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds")
    return MarkdownConnector(expanded, float(timeout))
