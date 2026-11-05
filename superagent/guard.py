from __future__ import annotations

from .worker import ROLE_ROUTER, ROLE_WATCHDOG, ROLE_WORKER

TOOL_DISPATCH = "superagent_dispatch"
TOOL_STATUS = "superagent_status"
TOOL_REPORT = "superagent_report"
TOOL_TICK = "superagent_tick"

ROUTER_ALLOWED_TOOLS = frozenset(
    {TOOL_DISPATCH, TOOL_STATUS, "memory", "session_search", "skills_list", "skill_view", "clarify"}
)
WATCHDOG_ALLOWED_TOOLS = frozenset({TOOL_TICK, TOOL_STATUS, "skills_list", "skill_view"})
WORKER_BLOCKED_TOOLS = frozenset({TOOL_DISPATCH, TOOL_TICK, TOOL_STATUS, "send_message", "cronjob_manage"})


def blocked_reason(role: str, tool_name: str) -> str | None:
    if role == ROLE_ROUTER and tool_name not in ROUTER_ALLOWED_TOOLS:
        return f"The router dispatches and never executes. {tool_name} is not available here, use superagent_dispatch."
    if role == ROLE_WATCHDOG and tool_name not in WATCHDOG_ALLOWED_TOOLS:
        return f"The watchdog only observes and repairs through superagent_tick. {tool_name} is not available here."
    if role == ROLE_WORKER and tool_name in WORKER_BLOCKED_TOOLS:
        return f"Workers report only to the router through superagent_report. {tool_name} is not available here."
    return None
