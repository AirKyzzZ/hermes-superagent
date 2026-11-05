from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from ..ledger import SourceItem, Upstream

DEFAULT_EVERY_SECONDS = 300.0
DEFAULT_NOTIFY = ("waiting", "stuck", "limited", "gone")
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)([smhd])$")
_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
COMMON_KEYS = frozenset({"type", "every", "track", "quiet_after", "owned", "channel", "notify"})


class ConnectorError(RuntimeError):
    pass


class ConnectorConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Poll:
    items: tuple[SourceItem, ...]
    complete: bool
    cursor: str = ""
    problems: tuple[str, ...] = ()


class Connector(Protocol):
    @property
    def timeout(self) -> float: ...

    def poll(self, cursor: str) -> Poll: ...

    def confirm(self, external_id: str) -> Upstream: ...


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    type: str
    every_seconds: float = DEFAULT_EVERY_SECONDS
    track: bool = True
    quiet_after_seconds: float = 0.0
    settings: Mapping[str, object] = field(default_factory=dict)
    owned: bool = False
    channel: str = ""
    notify: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ConnectorType:
    keys: frozenset[str]
    build: Callable[[ConnectorSpec], Connector]


def connector_types() -> dict[str, ConnectorType]:
    from . import agents, command

    return {
        "command": ConnectorType(command.KEYS, command.build),
        "agents": ConnectorType(agents.KEYS, agents.build),
    }


def build_connector(spec: ConnectorSpec) -> Connector:
    return connector_types()[spec.type].build(spec)


def parse_specs(section: object) -> tuple[ConnectorSpec, ...]:
    if not isinstance(section, Mapping):
        raise ConnectorConfigError("[connectors] must be a table of named connectors")
    types = connector_types()
    specs = []
    for name, body in section.items():
        if not isinstance(body, Mapping):
            raise ConnectorConfigError(f"[connectors.{name}] must be a table")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
            raise ConnectorConfigError(f"connector names use lowercase letters, digits, - and _, got {name!r}")
        kind = body.get("type")
        if kind not in types:
            raise ConnectorConfigError(f"[connectors.{name}] type must be one of {sorted(types)}, got {kind!r}")
        if unknown := set(body) - COMMON_KEYS - types[kind].keys:
            raise ConnectorConfigError(f"unknown keys in [connectors.{name}]: {sorted(unknown)}")
        track, owned = body.get("track", True), body.get("owned", kind == "agents")
        if not isinstance(track, bool) or not isinstance(owned, bool):
            raise ConnectorConfigError(f"[connectors.{name}] track and owned must be true or false")
        channel = body.get("channel", "")
        notify = body.get("notify", list(DEFAULT_NOTIFY) if kind == "agents" else [])
        if not isinstance(channel, str) or not isinstance(notify, list) or not all(isinstance(s, str) for s in notify):
            raise ConnectorConfigError(f"[connectors.{name}] channel must be a string and notify a list of states")
        spec = ConnectorSpec(
            name=name,
            type=kind,
            every_seconds=duration(body.get("every", DEFAULT_EVERY_SECONDS), f"[connectors.{name}] every"),
            track=track,
            quiet_after_seconds=duration(body.get("quiet_after", 0), f"[connectors.{name}] quiet_after"),
            settings={key: value for key, value in body.items() if key not in COMMON_KEYS},
            owned=owned,
            channel=channel,
            notify=frozenset(notify),
        )
        types[kind].build(spec)
        specs.append(spec)
    return tuple(specs)


def duration(value: object, where: str) -> float:
    if isinstance(value, bool):
        raise ConnectorConfigError(f"{where} must be a duration such as 5m or 2h")
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str) and (match := _DURATION.match(value.strip())):
        seconds = float(match.group(1)) * _UNITS[match.group(2)]
    else:
        raise ConnectorConfigError(f"{where} must be a duration such as 5m or 2h, got {value!r}")
    if not math.isfinite(seconds) or seconds < 0:
        raise ConnectorConfigError(f"{where} must be a finite, non-negative duration")
    return seconds
