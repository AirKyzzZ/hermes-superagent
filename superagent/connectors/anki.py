from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll
from .process import run_json

KEYS = frozenset({"url", "curl", "timeout", "env"})
DEFAULT_URL = "http://127.0.0.1:8765"
API_VERSION = 6
DUE_QUERY = "is:due"
CURL_COULD_NOT_CONNECT = "exited 7:"
_SAFE_URL = re.compile(r"https?://[A-Za-z0-9.:/_-]+")


class _AnkiNotOpen(Exception):
    pass


@dataclass(frozen=True)
class AnkiConnector:
    url: str
    curl: tuple[str, ...]
    timeout: float
    env_names: tuple[str, ...]
    clock: Callable[[], float] = time.time

    def poll(self, cursor: str) -> Poll:
        deadline = time.monotonic() + self.timeout
        try:
            # not getDeckStats: AnkiConnect creates the decks it's asked about, so it isn't read-only
            cards = self._call("findCards", {"query": DUE_QUERY}, deadline)
            if not isinstance(cards, list):
                raise ConnectorError("AnkiConnect returned an unusable card list")
            decks = self._call("getDecks", {"cards": cards}, deadline) if cards else {}
            reviewed = self._call("getNumCardsReviewedToday", {}, deadline)
        except _AnkiNotOpen:
            return Poll((), complete=False)
        return Poll((_reading(datetime.fromtimestamp(self.clock()), len(cards), decks, reviewed),), complete=False)

    def confirm(self, external_id: str) -> Upstream:
        return Upstream.UNKNOWN

    def _call(self, action: str, params: Mapping[str, object], deadline: float) -> object:
        left = deadline - time.monotonic()
        request = json.dumps({"action": action, "version": API_VERSION, "params": params})
        argv = [*self.curl, "-sS", "--max-time", f"{max(left, 1):.0f}", "--data-binary", "@-", self.url]
        try:
            reply = run_json(argv, self.env_names, left, stdin=request)
        except ConnectorError as error:
            if CURL_COULD_NOT_CONNECT in str(error):
                raise _AnkiNotOpen from error
            raise
        if not isinstance(reply, Mapping) or "result" not in reply or "error" not in reply:
            raise ConnectorError(f"AnkiConnect gave an unexpected reply to {action}")
        if reply["error"]:
            message = str(reply["error"])
            if "collection" in message.lower():
                raise _AnkiNotOpen
            raise ConnectorError(f"AnkiConnect {action}: {message[:200]}")
        return reply["result"]


def _reading(day: datetime, due: int, decks: object, reviewed: object) -> SourceItem:
    if not isinstance(decks, Mapping) or not all(isinstance(cards, list) for cards in decks.values()):
        raise ConnectorError("AnkiConnect returned unusable decks")
    if isinstance(reviewed, bool) or not isinstance(reviewed, int):
        raise ConnectorError("AnkiConnect returned an unusable reviewed count")
    by_top_deck: Counter[str] = Counter()
    for name, cards in decks.items():
        by_top_deck[str(name).split("::", 1)[0]] += len(cards)
    ranked = sorted(by_top_deck.items(), key=lambda deck: (-deck[1], deck[0]))
    meta = {
        "due": str(due),
        "reviewed": str(reviewed),
        "decks": ", ".join(f"{name} {count}" for name, count in ranked),
    }
    summary = f"{due} due" if due else "nothing due"
    return SourceItem(
        external_id=day.date().isoformat(),
        title=f"Anki {day:%a %d %b}: {summary}, {reviewed} reviewed",
        meta={key: value for key, value in meta.items() if value},
    )


def build(spec: ConnectorSpec) -> AnkiConnector:
    where = f"[connectors.{spec.name}]"
    url = spec.settings.get("url", DEFAULT_URL)
    if not isinstance(url, str) or not _SAFE_URL.fullmatch(url):
        raise ConnectorConfigError(f"{where} url must be AnkiConnect's http address, such as {DEFAULT_URL}")
    configured = spec.settings.get("curl", "curl")
    curl = [configured] if isinstance(configured, str) else configured
    if not isinstance(curl, list) or not curl or not all(isinstance(part, str) and part for part in curl):
        raise ConnectorConfigError(f"{where} curl must be a command name, a path, or a list of arguments")
    timeout = spec.settings.get("timeout", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds")
    env = spec.settings.get("env", [])
    if not isinstance(env, list) or not all(isinstance(name, str) for name in env):
        raise ConnectorConfigError(f"{where} env must be a list of variable names")
    return AnkiConnector(url, tuple(curl), float(timeout), tuple(env))
