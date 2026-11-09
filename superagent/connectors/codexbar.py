from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll
from .process import CommandFailed, run_json

KEYS = frozenset({"providers", "codexbar", "timeout", "env"})
WINDOWS = ("primary", "secondary", "tertiary")
GROUP_SELECTORS = frozenset({"all", "both"})
_SAFE_PROVIDER = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


@dataclass(frozen=True)
class CodexbarConnector:
    providers: tuple[str, ...]
    command: tuple[str, ...]
    timeout: float
    env_names: tuple[str, ...]

    def poll(self, cursor: str) -> Poll:
        deadline = time.monotonic() + self.timeout
        items: dict[str, SourceItem] = {}
        problems: list[str] = []
        for provider in self.providers or ("",):
            try:
                entries = self._usage(provider, deadline)
            except ConnectorError as error:
                problems.append(f"{provider or 'codexbar'}: {error}")
                continue
            for entry in entries:
                try:
                    item = _reading(entry)
                except ConnectorError as error:
                    problems.append(str(error))
                    continue
                if item is not None:
                    items[item.external_id] = item
        return Poll(tuple(items.values()), complete=False, problems=tuple(problems))

    def confirm(self, external_id: str) -> Upstream:
        return Upstream.UNKNOWN

    def _usage(self, provider: str, deadline: float) -> list[object]:
        selector = ["--provider", provider] if provider else []
        argv = [*self.command, "usage", "--format", "json", "--no-color", *selector]
        try:
            result = run_json(argv, self.env_names, deadline - time.monotonic())
        except CommandFailed as failure:
            result = _printed_despite(failure)
        if not isinstance(result, list):
            raise ConnectorError("codexbar returned an unexpected shape")
        if not result:
            raise ConnectorError("codexbar reported no provider")
        return result


def _printed_despite(failure: CommandFailed) -> object:
    # codexbar exits 1 when any one provider errors, and still prints every reading
    try:
        return json.loads(failure.stdout)
    except ValueError:
        raise failure from None


def _reading(entry: object) -> SourceItem | None:
    provider = entry.get("provider") if isinstance(entry, Mapping) else None
    if not isinstance(entry, Mapping) or not isinstance(provider, str) or not provider:
        raise ConnectorError("codexbar returned a reading without a provider")
    error = entry.get("error")
    if isinstance(error, Mapping):
        raise ConnectorError(f"{provider}: {str(error.get('message') or error.get('kind') or 'failed')[:200]}")
    usage = entry.get("usage")
    if not isinstance(usage, Mapping):
        raise ConnectorError(f"{provider}: no usage in codexbar's reading")
    labels = entry.get("rateWindowLabels")
    named = labels if isinstance(labels, Mapping) else {}
    parts: list[str] = []
    meta = {
        "provider": provider,
        "source": str(entry.get("source") or ""),
        "plan": str(usage.get("loginMethod") or ""),
    }
    for slot in WINDOWS:
        window = usage.get(slot)
        if not isinstance(window, Mapping) or not _is_number(window.get("usedPercent")):
            continue
        label, used = str(named.get(slot) or slot), f"{window['usedPercent']:g}"
        parts.append(f"{label} {used}%")
        meta |= {
            f"{slot}_label": label,
            f"{slot}_used": used,
            f"{slot}_resets_at": str(window.get("resetsAt") or ""),
        }
    meta["extra"] = _extras(usage.get("extraRateWindows"))
    meta["cost"] = cost = _cost(usage.get("providerCost"))
    if not parts and not cost:
        return None
    return SourceItem(
        external_id=provider,
        title=f"{provider}: {', '.join(parts) or cost}",
        meta={key: value for key, value in meta.items() if value},
    )


def _extras(windows: object) -> str:
    if not isinstance(windows, list):
        return ""
    readings = []
    for extra in windows:
        window = extra.get("window") if isinstance(extra, Mapping) else None
        if isinstance(extra, Mapping) and isinstance(window, Mapping) and _is_number(window.get("usedPercent")):
            readings.append(f"{extra.get('title') or extra.get('id') or 'extra'} {window['usedPercent']:g}%")
    return ", ".join(readings)


def _cost(cost: object) -> str:
    if not isinstance(cost, Mapping) or not _is_number(cost.get("used")) or not _is_number(cost.get("limit")):
        return ""
    return f"{cost['used']:g}/{cost['limit']:g} {cost.get('currencyCode') or ''}".strip()


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def build(spec: ConnectorSpec) -> CodexbarConnector:
    where = f"[connectors.{spec.name}]"
    providers = spec.settings.get("providers", [])
    if not isinstance(providers, list) or not all(
        isinstance(name, str) and _SAFE_PROVIDER.fullmatch(name) for name in providers
    ):
        raise ConnectorConfigError(f"{where} providers must be a list of CodexBar provider names")
    if GROUP_SELECTORS.intersection(providers):
        raise ConnectorConfigError(
            f"{where} name the providers one by one, or leave providers out for the enabled ones"
        )
    configured = spec.settings.get("codexbar", "codexbar")
    command = [configured] if isinstance(configured, str) else configured
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise ConnectorConfigError(f"{where} codexbar must be a command name, a path, or a list of arguments")
    timeout = spec.settings.get("timeout", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds")
    env = spec.settings.get("env", [])
    if not isinstance(env, list) or not all(isinstance(name, str) for name in env):
        raise ConnectorConfigError(f"{where} env must be a list of variable names")
    return CodexbarConnector(tuple(providers), tuple(command), float(timeout), tuple(env))
