"""Phase 1: Planning Agent — variables → (target_database, candidate_tables)。"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage

from harness_stata.clients.llm import get_chat_model
from harness_stata.state import VariableDefinition
from harness_stata.subgraphs.probe.config import ProbeNodeConfig
from harness_stata.subgraphs.probe.pure import ensure_manifest, ensure_report
from harness_stata.subgraphs.probe.schemas import PlanningOutput, VariablePlan
from harness_stata.subgraphs.probe.state import ProbeState


async def planning_agent(state: ProbeState, cfg: ProbeNodeConfig) -> dict[str, Any]:
    """Plan candidate tables for every variable in the empirical spec.

    队列为 ``empirical_spec.variables``;空队列直接返回 shaped report/manifest,
    路由层会落到 END。
    """
    spec = state["empirical_spec"]
    queue: list[VariableDefinition] = list(spec["variables"])

    if not queue:
        return {
            "pending_variables": [],
            "planning_queue": [],
            "plans": [],
            "schema_dict": {},
            "pending_hard_fallbacks": [],
            "probe_report": ensure_report(state.get("probe_report")),
            "download_manifest": ensure_manifest(state.get("download_manifest")),
        }

    db_block = state.get("available_databases", "")
    var_lines = [
        f"- name=`{v['name']}`, contract={v['contract_type']}, role={v['role']},"
        f" description={v['description']}"
        for v in queue
    ]
    human = HumanMessage(
        content=(
            f"Variables awaiting candidate-table planning ({len(queue)} total):\n"
            + "\n".join(var_lines)
            + f"\n\nSample scope: {spec['sample_scope']}"
            + f"\nTime range: {spec['time_range_start']} to {spec['time_range_end']}"
            + f"\nData frequency: {spec['data_frequency']}"
            + f"\n\nPurchased databases:\n{db_block}"
        )
    )
    agent = create_agent(
        model=get_chat_model(),
        tools=list(cfg.planning_tools),
        system_prompt=cfg.planning_system_prompt,
        middleware=[
            ToolCallLimitMiddleware(
                run_limit=cfg.planning_agent_max_calls,
                exit_behavior="end",
            ),
        ],
        response_format=ToolStrategy(PlanningOutput),
    )
    result: dict[str, Any] = await agent.ainvoke({"messages": [human]})
    planning = result.get("structured_response")
    plans: list[VariablePlan] = list(planning.plans) if isinstance(planning, PlanningOutput) else []
    return {
        "pending_variables": queue,
        "planning_queue": queue,
        "plans": plans,
        "schema_dict": {},
        "pending_hard_fallbacks": [],
        "messages": result.get("messages", []),
    }
