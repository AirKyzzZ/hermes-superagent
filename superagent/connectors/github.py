from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll
from .process import run_json

KEYS = frozenset({"queries", "gh", "timeout", "env"})
DEFAULT_QUERIES = {
    "assigned": "is:open assignee:@me",
    "review": "is:open is:pr review-requested:@me",
    "mentioned": "is:open mentions:@me",
}
PAGE_SIZE = 100
SEARCH_CAP = 1000
_ID = re.compile(r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(?P<number>\d+)")
_REPO_FROM_URL = re.compile(r"/repos/([^/]+/[^/]+)$")
_DEFAULT_ENV = ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "GH_HOST")


@dataclass(frozen=True)
class GithubConnector:
    queries: Mapping[str, str]
    gh: str
    timeout: float
    env_names: tuple[str, ...]

    def poll(self, cursor: str) -> Poll:
        found: dict[str, SourceItem] = {}
        matched: dict[str, list[str]] = {}
        for name, query in self.queries.items():
            for entry in self._search(query):
                item = _item(entry)
                found[item.external_id] = item
                matched.setdefault(item.external_id, []).append(name)
        items = tuple(
            SourceItem(
                item.external_id,
                item.title,
                url=item.url,
                updated=item.updated,
                meta={**item.meta, "matched": ",".join(matched[item.external_id])},
            )
            for item in found.values()
        )
        return Poll(items, complete=True)

    def confirm(self, external_id: str) -> Upstream:
        match = _ID.fullmatch(external_id)
        if match is None:
            raise ConnectorError(f"not a GitHub issue id: {external_id[:80]!r}")
        issue = self._api(f"repos/{match['repo']}/issues/{match['number']}")
        state = issue.get("state") if isinstance(issue, Mapping) else None
        if state not in ("open", "closed"):
            raise ConnectorError(f"GitHub returned no state for {external_id}")
        return Upstream.CLOSED if state == "closed" else Upstream.OPEN

    def _search(self, query: str) -> list[Mapping[str, object]]:
        entries: list[Mapping[str, object]] = []
        total: int | None = None
        page = 1
        while total is None or len(entries) < total:
            result = self._api(
                "search/issues", "-X", "GET", "-f", f"q={query}", "-f", f"per_page={PAGE_SIZE}", "-f", f"page={page}"
            )
            if not isinstance(result, Mapping) or not isinstance(result.get("items"), list):
                raise ConnectorError(f"GitHub search for {query!r} returned an unexpected shape")
            if result.get("incomplete_results"):
                raise ConnectorError(f"GitHub says its results for {query!r} are incomplete")
            count = result.get("total_count")
            if not isinstance(count, int) or isinstance(count, bool):
                raise ConnectorError(f"GitHub search for {query!r} returned no total")
            if count > SEARCH_CAP:
                raise ConnectorError(f"{query!r} matches {count} items, over search's {SEARCH_CAP}, so narrow it")
            total = count
            page_items = [entry for entry in result["items"] if isinstance(entry, Mapping)]
            if not page_items:
                break
            entries += page_items
            page += 1
        if len(entries) != total:
            raise ConnectorError(f"GitHub search for {query!r} said {total} items but returned {len(entries)}")
        return entries

    def _api(self, path: str, *args: str) -> object:
        return run_json([self.gh, "api", path, *args], self.env_names, self.timeout)


def _item(entry: Mapping[str, object]) -> SourceItem:
    repo_match = _REPO_FROM_URL.search(str(entry.get("repository_url", "")))
    number = entry.get("number")
    title = entry.get("title")
    if repo_match is None or not isinstance(number, int) or not isinstance(title, str) or not title.strip():
        raise ConnectorError("GitHub returned an item without a repository, number or title")
    repo = repo_match.group(1)
    labels = entry.get("labels")
    label_names = (
        [str(label.get("name")) for label in labels if isinstance(label, Mapping)] if isinstance(labels, list) else []
    )
    user = entry.get("user")
    author = str(user.get("login", "")) if isinstance(user, Mapping) else ""
    meta = {
        "repo": repo,
        "kind": "pr" if entry.get("pull_request") else "issue",
        "author": author,
        "labels": ",".join(label_names),
    }
    return SourceItem(
        external_id=f"{repo}#{number}",
        title=f"{repo}#{number} {title.strip()}",
        url=str(entry.get("html_url", "")),
        updated=str(entry.get("updated_at", "")),
        meta=meta,
    )


def build(spec: ConnectorSpec) -> GithubConnector:
    where = f"[connectors.{spec.name}]"
    queries = spec.settings.get("queries", DEFAULT_QUERIES)
    if (
        not isinstance(queries, Mapping)
        or not queries
        or not all(
            isinstance(name, str) and isinstance(query, str) and query.strip() for name, query in queries.items()
        )
    ):
        raise ConnectorConfigError(
            f'{where} queries must map names to GitHub searches, such as {{ mine = "is:open assignee:@me" }}'
        )
    if any("is:open" not in query for query in queries.values()):
        raise ConnectorConfigError(f"{where} every query needs is:open, since the snapshot is what's open")
    gh = spec.settings.get("gh", "gh")
    if not isinstance(gh, str) or not gh:
        raise ConnectorConfigError(f"{where} gh must be a command name or path")
    timeout = spec.settings.get("timeout", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds")
    env = spec.settings.get("env", [])
    if not isinstance(env, list) or not all(isinstance(name, str) for name in env):
        raise ConnectorConfigError(f"{where} env must be a list of variable names")
    return GithubConnector(dict(queries), gh, float(timeout), (*_DEFAULT_ENV, *env))
