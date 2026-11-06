from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import CONFIG_FILENAME, parse_config

DOCS = "https://github.com/AirKyzzZ/hermes-superagent"
INSTALL_GUIDE = f"{DOCS}/blob/main/docs/dogfood-install.md"


@dataclass(frozen=True)
class Choices:
    hermes: str = ""
    alerts: str = ""
    brief: str = ""
    agents_channel: str = ""
    timezone: str = "UTC"
    github: bool = False
    agents: bool = False


def detect(hermes: str, *, alerts: str = "", brief: str = "", agents_channel: str = "", timezone: str = "") -> Choices:
    return Choices(
        hermes=hermes,
        alerts=alerts,
        brief=brief,
        agents_channel=agents_channel,
        timezone=timezone or local_timezone(),
        github=shutil.which("gh") is not None,
        agents=shutil.which("claude") is not None or (Path.home() / ".claude").is_dir(),
    )


def local_timezone() -> str:
    candidates = [os.environ.get("TZ", "").lstrip(":")]
    candidates.append(os.path.realpath("/etc/localtime").partition("zoneinfo/")[2])
    for name in candidates:
        try:
            if name:
                ZoneInfo(name)
                return name
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return "UTC"


def starter_config(choices: Choices) -> str:
    blocks = [f"# Every key is described in {DOCS}#configuration"]
    if choices.hermes:
        argv = [choices.hermes, "send", "-q", "--to", "{channel}", "--", "{text}"]
        blocks.append(f"[outbound]\nargv = {json.dumps(argv)}")
    channels = [
        f"{key} = {json.dumps(value)}" for key, value in (("alerts", choices.alerts), ("brief", choices.brief)) if value
    ]
    if channels:
        blocks.append("\n".join(["[channels]", *channels]))
    if choices.brief:
        blocks.append(f"[brief]\nhour = 8\ntimezone = {json.dumps(choices.timezone)}")
    if choices.github:
        blocks.append('[connectors.github]\ntype = "github"\nevery = "10m"')
    if choices.agents:
        agents = ["[connectors.agents]", 'type = "agents"', 'every = "90s"']
        if choices.agents_channel:
            agents.append(f"channel = {json.dumps(choices.agents_channel)}")
        blocks.append("\n".join(agents))
    text = "\n\n".join(blocks) + "\n"
    parse_config(text)
    return text


def write_starter(home: Path, text: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / CONFIG_FILENAME
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)
    return path


def next_steps(home: Path, choices: Choices, cli: str) -> str:
    base = f"{cli} --home {home}"
    found = [name for name, on in (("github", choices.github), ("agents", choices.agents)) if on]
    lines = [f"Wrote {home / CONFIG_FILENAME} with {', '.join(found) or 'no'} connectors.", "Next:"]
    if choices.agents:
        lines += [
            "- Install the Claude Code hook on this machine, after the owner says yes:",
            f"    mkdir -p ~/.superagent && {cli} agents --hook-script > ~/.superagent/hook.py",
            f"    {cli} agents --hook-settings",
            "  then merge the printed hooks block into ~/.claude/settings.json, next to any hooks already there.",
        ]
    if not choices.alerts:
        lines.append("- Add an alerts channel under [channels], so failures reach someone.")
    lines += [
        f"- Run `{base} tick` every 2 minutes, for example with the systemd timer in {INSTALL_GUIDE}",
        f"- Check everything with `{base} doctor`.",
    ]
    return "\n".join(lines)
