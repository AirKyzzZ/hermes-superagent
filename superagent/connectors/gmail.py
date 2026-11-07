from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll
from .process import run_json

KEYS = frozenset({"account", "query", "gog", "timeout", "env"})
DEFAULT_QUERY = "in:inbox"
VOLATILE_LABELS = frozenset({"UNREAD"})
PAGE_SIZE = 500
THREAD_URL = "https://mail.google.com/mail/u/?authuser={account}#all/{id}"
_SAFE_ID = re.compile(r"[A-Za-z0-9]{1,64}")
_DEFAULT_ENV = ("GOG_HOME", "GOG_TIMEZONE", "DBUS_SESSION_BUS_ADDRESS")


@dataclass(frozen=True)
class GmailConnector:
    account: str
    query: str
    gog: str
    timeout: float
    env_names: tuple[str, ...]

    def poll(self, cursor: str) -> Poll:
        argv = [*self._base(), "gmail", "search", self.query, "--all", "--count", "--max", str(PAGE_SIZE)]
        result = run_json(argv, self.env_names, self.timeout)
        threads = result.get("threads") if isinstance(result, Mapping) else None
        if not isinstance(result, Mapping) or not isinstance(threads, list):
            raise ConnectorError("gog returned an unexpected shape for the search")
        problems = []
        if result.get("nextPageToken"):
            problems.append("gog stopped before the last page, so the inbox can't be trusted to be whole")
        total = result.get("totalMatches")
        items: dict[str, SourceItem] = {}
        for entry in threads:
            try:
                item = self._thread(entry)
            except ConnectorError as error:
                problems.append(str(error))
                continue
            items[item.external_id] = item
        unreadable = sum(1 for problem in problems if problem.startswith("thread"))
        if not isinstance(total, int) or isinstance(total, bool):
            problems.append("gog gave no exact count of the threads, so the inbox can't be checked for completeness")
        elif total != len(items) + unreadable:
            problems.append(f"gog counted {total} threads but returned {len(items)} different ones")
        return Poll(tuple(items.values()), complete=not problems, problems=tuple(problems))

    def confirm(self, external_id: str) -> Upstream:
        if not _SAFE_ID.fullmatch(external_id):
            raise ConnectorError(f"refusing to pass the thread id {external_id[:80]!r} to gog")
        try:
            result = run_json([*self._base(), "gmail", "thread", "get", external_id], self.env_names, self.timeout)
        except ConnectorError as error:
            if "not found" in str(error).lower() or "404" in str(error):
                return Upstream.UNKNOWN
            raise
        thread = result.get("thread") if isinstance(result, Mapping) else None
        messages = thread.get("messages") if isinstance(thread, Mapping) else None
        if not isinstance(messages, list) or not messages:
            raise ConnectorError(f"gog returned no messages for thread {external_id}")
        labels = {
            label
            for message in messages
            if isinstance(message, Mapping) and isinstance(message.get("labelIds"), list)
            for label in message["labelIds"]
        }
        return Upstream.OPEN if "INBOX" in labels else Upstream.CLOSED

    def _base(self) -> list[str]:
        return [self.gog, "--readonly", "--json", "--no-input", "--account", self.account]

    def _thread(self, entry: object) -> SourceItem:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str):
            raise ConnectorError("thread without an id in gog's search")
        identifier = str(entry["id"])
        if not _SAFE_ID.fullmatch(identifier):
            raise ConnectorError(f"thread with an unusable id {identifier[:40]!r} in gog's search")
        subject = " ".join(str(entry.get("subject") or "(no subject)").split())
        labels = entry.get("labels")
        meta = {
            "account": self.account,
            "from": str(entry.get("from") or ""),
            "labels": ",".join(str(label) for label in labels if label not in VOLATILE_LABELS)
            if isinstance(labels, list)
            else "",
            "messages": str(entry.get("messageCount") or ""),
        }
        return SourceItem(
            external_id=identifier,
            title=subject,
            url=THREAD_URL.format(account=self.account, id=identifier),
            updated=str(entry.get("internalDateIso") or entry.get("date") or ""),
            meta={key: value for key, value in meta.items() if value},
        )


def build(spec: ConnectorSpec) -> GmailConnector:
    where = f"[connectors.{spec.name}]"
    account, query = spec.settings.get("account"), spec.settings.get("query", DEFAULT_QUERY)
    if not isinstance(account, str) or "@" not in account:
        raise ConnectorConfigError(f"{where} account must be the mailbox's address, one block per account")
    if not isinstance(query, str) or "in:inbox" not in query.lower().split():
        raise ConnectorConfigError(
            f"{where} query must include in:inbox, since a thread closes when it leaves the inbox"
        )
    gog = spec.settings.get("gog", "gog")
    if not isinstance(gog, str) or not gog:
        raise ConnectorConfigError(f"{where} gog must be a command name or path")
    timeout = spec.settings.get("timeout", 180)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds")
    env = spec.settings.get("env", [])
    if not isinstance(env, list) or not all(isinstance(name, str) for name in env):
        raise ConnectorConfigError(f"{where} env must be a list of variable names")
    return GmailConnector(account, query, gog, float(timeout), (*_DEFAULT_ENV, *env))
