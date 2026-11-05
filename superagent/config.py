from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from .gate import Rules, RulesError, parse_rules
from .watchdog import WatchdogPolicy

CONFIG_FILENAME = "superagent.toml"
DB_FILENAME = "superagent.db"
WORKDIRS_DIRNAME = "workers"


@dataclass(frozen=True)
class Config:
    rules: Rules
    worker_argv: tuple[str, ...]
    send_argv: tuple[str, ...]
    watchdog: WatchdogPolicy
    allow_same_user: bool = False


class ConfigError(ValueError):
    pass


def parse_config(raw: str) -> Config:
    try:
        rules = parse_rules(raw)
        data = tomllib.loads(raw)
    except RulesError as error:
        raise ConfigError(str(error)) from error
    worker_argv = tuple(data.get("worker", {}).get("argv", ()))
    if not worker_argv:
        raise ConfigError("[worker] argv is required: the command that runs one worker")
    if "{brief}" not in " ".join(worker_argv):
        raise ConfigError("[worker] argv must reference {brief}: the file holding the task")
    send_argv = tuple(data.get("outbound", {}).get("argv", ()))
    defaults = WatchdogPolicy()
    section = data.get("watchdog", {})
    known = {field.name for field in fields(WatchdogPolicy)}
    if unknown := set(section) - known:
        raise ConfigError(f"unknown [watchdog] keys: {sorted(unknown)}")
    try:
        policy = WatchdogPolicy(**{key: type(getattr(defaults, key))(value) for key, value in section.items()})
    except (TypeError, ValueError) as error:
        raise ConfigError(f"invalid [watchdog] section: {error}") from error
    allow_same_user = bool(data.get("worker", {}).get("allow_same_user", False))
    return Config(rules, worker_argv, send_argv, policy, allow_same_user)


def load_config(home: Path) -> Config:
    path = home / CONFIG_FILENAME
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    return parse_config(path.read_text(encoding="utf-8"))
