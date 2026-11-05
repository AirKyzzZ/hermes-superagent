from __future__ import annotations

import tomllib
from dataclasses import dataclass
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
    try:
        policy = WatchdogPolicy(
            stall_after_seconds=float(section.get("stall_after_seconds", defaults.stall_after_seconds)),
            repeat_limit=int(section.get("repeat_limit", defaults.repeat_limit)),
            repeated_call_limit=int(section.get("repeated_call_limit", defaults.repeated_call_limit)),
            nudge_budget=int(section.get("nudge_budget", defaults.nudge_budget)),
            restart_budget=int(section.get("restart_budget", defaults.restart_budget)),
        )
    except (TypeError, ValueError) as error:
        raise ConfigError(f"invalid [watchdog] section: {error}") from error
    return Config(rules, worker_argv, send_argv, policy)


def load_config(home: Path) -> Config:
    path = home / CONFIG_FILENAME
    if not path.is_file():
        raise ConfigError(f"config not found: {path}")
    return parse_config(path.read_text(encoding="utf-8"))
