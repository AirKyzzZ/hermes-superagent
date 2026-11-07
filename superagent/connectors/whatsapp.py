from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll
from .process import run_json

KEYS = frozenset({"chats", "wacli", "timeout", "env"})
TEXT_CHARS = 300
_JID = re.compile(r"[0-9A-Za-z._-]{3,64}@(?:s\.whatsapp\.net|g\.us|lid|newsletter)")
_DEFAULT_ENV = ("WACLI_HOME",)


@dataclass(frozen=True)
class WhatsappConnector:
    chats: tuple[str, ...]
    wacli: str
    timeout: float
    env_names: tuple[str, ...]

    def poll(self, cursor: str) -> Poll:
        deadline = time.monotonic() + self.timeout
        items, problems = [], []
        for chat in self.chats:
            try:
                last = self._last_message(chat, deadline)
            except ConnectorError as error:
                problems.append(f"chat {chat}: {error}")
                continue
            if last is not None and last.get("FromMe") is not True:
                items.append(_item(chat, last))
        return Poll(tuple(items), complete=not problems, problems=tuple(problems))

    def confirm(self, external_id: str) -> Upstream:
        if external_id not in self.chats:
            return Upstream.UNKNOWN
        last = self._last_message(external_id, time.monotonic() + self.timeout)
        return Upstream.CLOSED if last is not None and last.get("FromMe") is True else Upstream.OPEN

    def _last_message(self, chat: str, deadline: float) -> Mapping[str, object] | None:
        argv = [self.wacli, "--json", "messages", "list", "--chat", chat, "--limit", "1"]
        result = run_json(argv, self.env_names, deadline - time.monotonic())
        if not isinstance(result, Mapping) or result.get("success") is not True:
            error = result.get("error") if isinstance(result, Mapping) else None
            raise ConnectorError(f"wacli failed: {str(error)[:200]}" if error else "wacli returned an unexpected shape")
        messages = _messages(result.get("data"))
        if messages is None:
            raise ConnectorError("wacli returned messages in an unexpected shape")
        latest = [message for message in messages if isinstance(message, Mapping)]
        return max(latest, key=lambda message: str(message.get("Timestamp", "")), default=None)


def _messages(data: object) -> Sequence[object] | None:
    if isinstance(data, list):
        return data
    nested = data.get("messages") if isinstance(data, Mapping) else None
    return nested if isinstance(nested, list) else None


def _item(chat: str, message: Mapping[str, object]) -> SourceItem:
    name = str(message.get("ChatName") or message.get("SenderName") or chat)
    text = " ".join(str(message.get("DisplayText") or message.get("Text") or "(media)").split())
    meta = {
        "chat": name,
        "sender": str(message.get("SenderName") or message.get("SenderJID") or ""),
        "last_at": str(message.get("Timestamp") or ""),
        "message_id": str(message.get("MsgID") or ""),
    }
    return SourceItem(
        external_id=chat,
        title=f"{name} is waiting for a reply",
        updated=str(message.get("Timestamp") or ""),
        excerpt=text[:TEXT_CHARS],
        meta={key: value for key, value in meta.items() if value},
    )


def build(spec: ConnectorSpec) -> WhatsappConnector:
    where = f"[connectors.{spec.name}]"
    chats = spec.settings.get("chats")
    if (
        not isinstance(chats, list)
        or not chats
        or not all(isinstance(chat, str) and _JID.fullmatch(chat) for chat in chats)
    ):
        raise ConnectorConfigError(
            f'{where} chats must list the chats to watch by JID, such as ["33612345678@s.whatsapp.net"]'
        )
    wacli = spec.settings.get("wacli", "wacli")
    if not isinstance(wacli, str) or not wacli:
        raise ConnectorConfigError(f"{where} wacli must be a command name or path")
    timeout = spec.settings.get("timeout", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds")
    env = spec.settings.get("env", [])
    if not isinstance(env, list) or not all(isinstance(name, str) for name in env):
        raise ConnectorConfigError(f"{where} env must be a list of variable names")
    return WhatsappConnector(tuple(dict.fromkeys(chats)), wacli, float(timeout), (*_DEFAULT_ENV, *env))
