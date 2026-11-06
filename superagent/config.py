from __future__ import annotations

import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .connectors import ConnectorConfigError, ConnectorSpec, duration, parse_specs
from .gate import Rules, RulesError, parse_rules
from .triage import TriageError, TriageRule, parse_triage
from .watchdog import WatchdogPolicy

CONFIG_FILENAME = "superagent.toml"
DB_FILENAME = "superagent.db"
WORKDIRS_DIRNAME = "workers"
LAST_GOOD_FILENAME = ".superagent.last-good.toml"
_KNOWN_KEYS = {
    "": {
        "sources",
        "semantic",
        "worker",
        "outbound",
        "watchdog",
        "digest",
        "channels",
        "heartbeat",
        "tick",
        "connectors",
        "brief",
        "triage",
        "areas",
        "briefs",
    },
    "sources": {"policy", "allow", "bots", "pass_strangers", "notify", "via_router"},
    "semantic": {"name", "text", "mode"},
    "worker": {"argv", "allow_same_user"},
    "outbound": {"argv"},
    "digest": {"channel"},
    "channels": {"alerts", "brief", "briefs"},
    "briefs": {"notes", "repos", "daily_limit"},
    "brief": {"hour", "timezone", "stale_after"},
    "heartbeat": {"argv"},
    "tick": {"deadline_seconds", "alert_after_seconds"},
}


@dataclass(frozen=True)
class Config:
    rules: Rules
    worker_argv: tuple[str, ...]
    send_argv: tuple[str, ...]
    watchdog: WatchdogPolicy
    allow_same_user: bool = False
    problem: str = ""
    digest_channel: str = ""
    alerts_channel: str = ""
    heartbeat_argv: tuple[str, ...] = ()
    tick_deadline_seconds: float = 600.0
    alert_after_seconds: float = 300.0
    connectors: tuple[ConnectorSpec, ...] = ()
    brief_channel: str = ""
    brief_hour: int = 8
    brief_timezone: str = "UTC"
    stale_after_seconds: float = 3 * 86400.0
    triage: tuple[TriageRule, ...] = ()
    area_channels: Mapping[str, str] = field(default_factory=dict)
    briefs_channel: str = ""
    brief_notes: str = ""
    brief_repos: Mapping[str, str] = field(default_factory=dict)
    briefs_daily_limit: int = 20


class ConfigError(ValueError):
    pass


def parse_config(raw: str) -> Config:
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML: {error}") from error
    _reject_unknown_keys(data)
    try:
        rules = parse_rules(raw)
    except RulesError as error:
        raise ConfigError(str(error)) from error
    worker_argv = tuple(data.get("worker", {}).get("argv", ()))
    if "worker" in data and not worker_argv:
        raise ConfigError("[worker] argv is required: the command that runs one worker")
    if worker_argv and "{brief}" not in " ".join(worker_argv):
        raise ConfigError("[worker] argv must reference {brief}: the file holding the task")
    send_argv = tuple(data.get("outbound", {}).get("argv", ()))
    defaults = WatchdogPolicy()
    section = data.get("watchdog", {})
    known = {entry.name for entry in fields(WatchdogPolicy)}
    if unknown := set(section) - known:
        raise ConfigError(f"unknown [watchdog] keys: {sorted(unknown)}")
    try:
        policy = WatchdogPolicy(**{key: type(getattr(defaults, key))(value) for key, value in section.items()})
    except (TypeError, ValueError) as error:
        raise ConfigError(f"invalid [watchdog] section: {error}") from error
    allow_same_user = bool(data.get("worker", {}).get("allow_same_user", False))
    digest_channel = str(data.get("digest", {}).get("channel", ""))
    tick_section = _table(data, "tick")
    deadline = _number(tick_section, "tick", "deadline_seconds", 600.0, minimum=1.0)
    alert_after = _number(tick_section, "tick", "alert_after_seconds", 300.0, minimum=0.0)
    channels = _table(data, "channels")
    alerts_channel, brief_channel = channels.get("alerts", ""), channels.get("brief", "")
    if not isinstance(alerts_channel, str) or not isinstance(brief_channel, str):
        raise ConfigError("[channels] values must be strings such as discord:123")
    brief = _table(data, "brief")
    if brief and not brief_channel:
        raise ConfigError("[brief] is set but [channels] brief isn't, so no brief would ever be sent")
    hour = brief.get("hour", 8)
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ConfigError("[brief] hour must be a whole hour from 0 to 23")
    timezone = brief.get("timezone", "UTC")
    try:
        ZoneInfo(str(timezone))
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(f"[brief] timezone must be a zone such as Europe/Berlin, got {timezone!r}") from error
    try:
        stale_after = duration(brief.get("stale_after", "3d"), "[brief] stale_after")
    except ConnectorConfigError as error:
        raise ConfigError(str(error)) from error
    try:
        connectors = parse_specs(data.get("connectors", {}))
    except ConnectorConfigError as error:
        raise ConfigError(str(error)) from error
    try:
        triage, area_channels = parse_triage(data.get("triage", []), data.get("areas", {}))
    except TriageError as error:
        raise ConfigError(str(error)) from error
    briefs_channel = channels.get("briefs", "")
    if not isinstance(briefs_channel, str):
        raise ConfigError("[channels] values must be strings such as discord:123")
    briefs = _table(data, "briefs")
    notes, repos, limit = briefs.get("notes", ""), briefs.get("repos", {}), briefs.get("daily_limit", 20)
    if not isinstance(notes, str):
        raise ConfigError("[briefs] notes must be a string")
    if not isinstance(repos, dict) or not all(isinstance(path, str) for path in repos.values()):
        raise ConfigError('[briefs] repos maps owner/repo to a path, such as { "your-org/app" = "~/code/app" }')
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ConfigError("[briefs] daily_limit must be a whole number, 0 or more")
    heartbeat = _table(data, "heartbeat").get("argv", [])
    if not isinstance(heartbeat, list) or not all(isinstance(part, str) for part in heartbeat):
        raise ConfigError("[heartbeat] argv must be a list of strings")
    return Config(
        rules,
        worker_argv,
        send_argv,
        policy,
        allow_same_user,
        digest_channel=digest_channel,
        alerts_channel=alerts_channel,
        heartbeat_argv=tuple(heartbeat),
        tick_deadline_seconds=deadline,
        alert_after_seconds=alert_after,
        connectors=connectors,
        brief_channel=brief_channel,
        brief_hour=hour,
        brief_timezone=str(timezone),
        stale_after_seconds=stale_after,
        triage=triage,
        area_channels=area_channels,
        briefs_channel=briefs_channel,
        brief_notes=notes,
        brief_repos=dict(repos),
        briefs_daily_limit=limit,
    )


def _table(data: dict[str, object], name: str) -> dict[str, object]:
    section = data.get(name, {})
    if not isinstance(section, dict):
        raise ConfigError(f"[{name}] must be a table")
    return section


def _number(section: dict[str, object], name: str, key: str, default: float, *, minimum: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ConfigError(f"[{name}] {key} must be a finite number of at least {minimum:g}")
    return float(value)


def _reject_unknown_keys(data: dict[str, object]) -> None:
    problems = [f"unknown top-level keys: {sorted(unknown)}" for unknown in [set(data) - _KNOWN_KEYS[""]] if unknown]
    for name, body in dict(data.get("sources") or {}).items():  # type: ignore[call-overload]
        if unknown := set(body) - _KNOWN_KEYS["sources"]:
            problems.append(f"unknown keys in [sources.{name}]: {sorted(unknown)}")
    for rule in data.get("semantic") or []:  # type: ignore[attr-defined]
        if unknown := set(rule) - _KNOWN_KEYS["semantic"]:
            problems.append(f"unknown keys in [[semantic]]: {sorted(unknown)}")
    for section in ("worker", "outbound", "digest", "channels", "heartbeat", "tick", "brief"):
        body = data.get(section) or {}
        if not isinstance(body, dict):
            problems.append(f"[{section}] must be a table")
        elif unknown := set(body) - _KNOWN_KEYS[section]:
            problems.append(f"unknown keys in [{section}]: {sorted(unknown)}")
    if problems:
        raise ConfigError("; ".join(problems))


def load_config(home: Path) -> Config:
    path = home / CONFIG_FILENAME
    last_good = home / LAST_GOOD_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
        config = parse_config(raw)
    except (OSError, ConfigError) as error:
        if not last_good.is_file():
            raise ConfigError(f"{path}: {error}") from error
        fallback = parse_config(last_good.read_text(encoding="utf-8"))
        return replace(fallback, problem=f"{path}: {error}. Running on the last good config until it's fixed")
    if not last_good.is_file() or last_good.read_text(encoding="utf-8") != raw:
        descriptor = os.open(last_good, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(raw)
    return config
