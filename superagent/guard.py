from __future__ import annotations

from .worker import ROLE_ROUTER, ROLE_WATCHDOG, ROLE_WORKER

TOOL_DISPATCH = "superagent_dispatch"
TOOL_STATUS = "superagent_status"
TOOL_REPORT = "superagent_report"
TOOL_TICK = "superagent_tick"
TOOL_FORWARD = "superagent_forward"

# Hermes defers plugin tools behind these bridges by default, and runs this hook again for each tool they call.
TOOL_SEARCH_BRIDGES = frozenset({"tool_search", "tool_describe", "tool_call"})
ROUTER_ALLOWED_TOOLS = TOOL_SEARCH_BRIDGES | {
    TOOL_DISPATCH,
    TOOL_FORWARD,
    TOOL_STATUS,
    "memory",
    "session_search",
    "skills_list",
    "skill_view",
    "clarify",
}
WATCHDOG_ALLOWED_TOOLS = TOOL_SEARCH_BRIDGES | {TOOL_TICK, TOOL_STATUS, "skills_list", "skill_view"}
WORKER_BLOCKED_TOOLS = frozenset(
    {TOOL_DISPATCH, TOOL_FORWARD, TOOL_TICK, TOOL_STATUS, "send_message", "cronjob_manage"}
)
WORKER_HOST_TOOLS = frozenset(
    {"terminal", "process_manage", "execute_code", "read_file", "write_file", "patch", "search_files", "computer_use"}
)


def blocked_reason(role: str, tool_name: str, *, worker_sandboxed: bool = True) -> str | None:
    if role == ROLE_ROUTER and tool_name not in ROUTER_ALLOWED_TOOLS:
        return f"The router dispatches and never executes. {tool_name} is not available here, use superagent_dispatch."
    if role == ROLE_WATCHDOG and tool_name not in WATCHDOG_ALLOWED_TOOLS:
        return f"The watchdog only observes and repairs through superagent_tick. {tool_name} is not available here."
    if role == ROLE_WORKER and tool_name in WORKER_BLOCKED_TOOLS:
        return f"Workers report only to the router through superagent_report. {tool_name} is not available here."
    if role == ROLE_WORKER and tool_name in WORKER_HOST_TOOLS and not worker_sandboxed:
        return (
            f"{tool_name} would run as the same OS user as the router, and could rewrite its config and database. "
            "Give the worker profile a sandboxed terminal backend such as docker, or set allow_same_user in "
            "[worker] to accept that risk."
        )
    return None
