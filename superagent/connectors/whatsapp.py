from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll, duration
from .process import run_json

KEYS = frozenset({"chats", "wacli", "sync", "sync_max_age", "activity_max_age", "timeout", "env"})
TEXT_CHARS = 300
WINDOW = 20
SYNC_MODES = ("follow", "once")
_JID = re.compile(r"[0-9A-Za-z._-]{3,64}@(?:s\.whatsapp\.net|g\.us|lid)")
_CALL = re.compile(r"(?i)^whatsapp (audio|video|voice) call\b")
_NOT_SPOKEN = frozenset({"(message)", "poll vote", "poll option added"})
_DEFAULT_ENV = ("WACLI_STORE_DIR", "XDG_STATE_HOME")


@dataclass(frozen=True)
class WhatsappConnector:
    chats: tuple[str, ...]
    wacli: str
    sync: str
    sync_max_age: float
    activity_max_age: float
    timeout: float
    env_names: tuple[str, ...]

    def poll(self, cursor: str) -> Poll:
        deadline = time.monotonic() + self.timeout
        self._check_sync(deadline)
        items, problems = [], []
        for chat in self.chats:
            try:
                last = self._last_spoken(chat, deadline)
            except ConnectorError as error:
                problems.append(f"chat {chat}: {error}")
                continue
            if last is not None and last.get("FromMe") is not True:
                items.append(_item(chat, last))
        return Poll(tuple(items), complete=not problems, problems=tuple(problems))

    def confirm(self, external_id: str) -> Upstream:
        if external_id not in self.chats:
            return Upstream.UNKNOWN
        deadline = time.monotonic() + self.timeout
        self._check_sync(deadline)
        last = self._last_spoken(external_id, deadline)
        if last is None:
            return Upstream.UNKNOWN
        return Upstream.CLOSED if last.get("FromMe") is True else Upstream.OPEN

    def _run(self, args: Sequence[str], deadline: float) -> object:
        result = run_json([self.wacli, "--read-only", "--json", *args], self.env_names, deadline - time.monotonic())
        if not isinstance(result, Mapping) or result.get("success") is not True:
            error = result.get("error") if isinstance(result, Mapping) else None
            raise ConnectorError(f"wacli failed: {str(error)[:200]}" if error else "wacli returned an unexpected shape")
        return result.get("data")

    def _check_sync(self, deadline: float) -> None:
        report = self._run(["doctor"], deadline)
        if not isinstance(report, Mapping):
            raise ConnectorError("wacli doctor returned an unexpected shape")
        if report.get("authenticated") is not True or report.get("session_revoked") is True:
            raise ConnectorError("wacli isn't linked to WhatsApp any more, so new messages can't arrive; link it again")
        store = report.get("store")
        if self.sync == "follow" and report.get("lock_held") is not True:
            raise ConnectorError("wacli sync isn't running, so its store is going stale; start wacli sync --follow")
        if self.sync == "follow" and self.activity_max_age:
            active = store.get("last_activity_at") if isinstance(store, Mapping) else None
            idle = _age(active)
            if idle is None or idle > self.activity_max_age:
                raise ConnectorError(
                    f"wacli sync has heard nothing since {active or 'an unknown time'}, older than activity_max_age, "
                    "so it may be stuck reconnecting"
                )
        if self.sync == "once":
            synced = store.get("last_sync_at") if isinstance(store, Mapping) else None
            age = _age(synced)
            if age is None or age > self.sync_max_age:
                raise ConnectorError(f"wacli last synced at {synced or 'an unknown time'}, older than sync_max_age")

    def _last_spoken(self, chat: str, deadline: float) -> Mapping[str, object] | None:
        data = self._run(["messages", "list", "--chat", chat, "--limit", str(WINDOW)], deadline)
        messages = data.get("messages") if isinstance(data, Mapping) else data
        if messages is None:
            return None
        if not isinstance(messages, list):
            raise ConnectorError("wacli returned messages in an unexpected shape")
        spoken = next((message for message in messages if isinstance(message, Mapping) and _spoken(message)), None)
        page = data.get("page") if isinstance(data, Mapping) else None
        if spoken is None and isinstance(page, Mapping) and page.get("has_more") is True:
            raise ConnectorError(
                f"the last {WINDOW} rows are all reactions, calls or votes, so who spoke last is unknown"
            )
        return spoken


def _spoken(message: Mapping[str, object]) -> bool:
    if str(message.get("ReactionToID") or "").strip() or str(message.get("ReactionEmoji") or "").strip():
        return False
    if message.get("Revoked") is True or message.get("DeletedForMe") is True:
        return False
    text = " ".join(str(message.get("Text") or "").split())
    shown = " ".join(str(message.get("DisplayText") or "").split())
    if not text and _CALL.match(shown):
        return message.get("FromMe") is not True
    if (text or shown).lower() in _NOT_SPOKEN:
        return False
    return bool(text or shown or message.get("MediaType"))


def _age(stamp: object) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return time.time() - moment.timestamp()


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
    wacli, sync = spec.settings.get("wacli", "wacli"), spec.settings.get("sync", "follow")
    if not isinstance(wacli, str) or not wacli:
        raise ConnectorConfigError(f"{where} wacli must be a command name or path")
    if sync not in SYNC_MODES:
        raise ConnectorConfigError(f"{where} sync must be follow (wacli sync --follow runs) or once (a timer runs it)")
    timeout = spec.settings.get("timeout", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds")
    env = spec.settings.get("env", [])
    if not isinstance(env, list) or not all(isinstance(name, str) for name in env):
        raise ConnectorConfigError(f"{where} env must be a list of variable names")
    max_age = duration(spec.settings.get("sync_max_age", "30m"), f"{where} sync_max_age")
    activity = duration(spec.settings.get("activity_max_age", "6h"), f"{where} activity_max_age")
    return WhatsappConnector(
        tuple(dict.fromkeys(chats)), wacli, str(sync), max_age, activity, float(timeout), (*_DEFAULT_ENV, *env)
    )
