from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import CONFIG_FILENAME, LAST_GOOD_FILENAME, parse_config

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
    gitlab: bool = False
    agents: bool = False


def detect(hermes: str, *, alerts: str = "", brief: str = "", agents_channel: str = "", timezone: str = "") -> Choices:
    return Choices(
        hermes=hermes,
        alerts=alerts,
        brief=brief,
        agents_channel=agents_channel,
        timezone=timezone or local_timezone(),
        github=which("gh") is not None,
        gitlab=which("glab") is not None,
        agents=bool(agents_channel) or which("claude") is not None or (Path.home() / ".claude").is_dir(),
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
        blocks.append(f"[outbound]\nargv = {json.dumps(argv, ensure_ascii=False)}")
    channels = [
        f"{key} = {json.dumps(value, ensure_ascii=False)}"
        for key, value in (("alerts", choices.alerts), ("brief", choices.brief))
        if value
    ]
    if channels:
        blocks.append("\n".join(["[channels]", *channels]))
    if choices.brief:
        blocks.append(f"[brief]\nhour = 8\ntimezone = {json.dumps(choices.timezone, ensure_ascii=False)}")
    if choices.github:
        blocks.append('[connectors.github]\ntype = "github"\nevery = "10m"')
    if choices.gitlab:
        blocks.append('[connectors.gitlab]\ntype = "gitlab"\nevery = "10m"')
    if choices.agents:
        agents = ["[connectors.agents]", 'type = "agents"', 'every = "90s"']
        if choices.agents_channel:
            agents.append(f"channel = {json.dumps(choices.agents_channel, ensure_ascii=False)}")
        blocks.append("\n".join(agents))
    text = "\n\n".join(blocks) + "\n"
    parse_config(text)
    return text


class SetupError(ValueError):
    pass


def check_choices(home: Path, choices: Choices) -> None:
    if (home / CONFIG_FILENAME).exists():
        raise FileExistsError(home / CONFIG_FILENAME)
    if (home / LAST_GOOD_FILENAME).is_file() and not (home / CONFIG_FILENAME).is_file():
        raise SetupError(
            f"{CONFIG_FILENAME} is missing but the last good copy is still in {home}. "
            f"Restore it with: cp {home / LAST_GOOD_FILENAME} {home / CONFIG_FILENAME}"
        )
    if not choices.hermes and (choices.alerts or choices.brief or choices.agents_channel):
        raise SetupError(
            "no hermes to send through, so those channels would get nothing. Run it as hermes superagent setup, "
            "or pass --hermes with the path to hermes"
        )


def write_starter(home: Path, text: str) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / CONFIG_FILENAME
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)
    return path


def next_steps(home: Path, choices: Choices, cli: str) -> str:
    base = f"{cli} --home {home}"
    detected = (("github", choices.github), ("gitlab", choices.gitlab), ("agents", choices.agents))
    found = [name for name, on in detected if on]
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
