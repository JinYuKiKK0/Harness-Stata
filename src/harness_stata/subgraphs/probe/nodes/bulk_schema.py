"""Phase 2: bulk_schema — 跨变量去重后一次拉回所有候选表的 schema。"""

from __future__ import annotations

from typing import Any

from harness_stata.subgraphs.probe.config import ProbeNodeConfig
from harness_stata.subgraphs.probe.pure import parse_bulk_schema_response
from harness_stata.subgraphs.probe.state import ProbeState


async def bulk_schema_phase(state: ProbeState, cfg: ProbeNodeConfig) -> dict[str, Any]:
    plans = list(state.get("plans") or [])
    candidates: list[str] = []
    seen: set[str] = set()
    for plan in plans:
        for code in plan.candidate_table_codes:
            if code and code not in seen:
                seen.add(code)
                candidates.append(code)
    if not candidates:
        return {"schema_dict": {}, "table_names": {}}
    try:
        msg: Any = await cfg.bulk_schema_tool.ainvoke(
            {
                "name": cfg.bulk_schema_tool.name,
                "args": {"table_codes": candidates},
                "id": "probe-bulk-schema",
                "type": "tool_call",
            }
        )
    except Exception:
        return {"schema_dict": {}, "table_names": {}}
    artifact = getattr(msg, "artifact", None)
    payload: object = None
    if isinstance(artifact, dict) and "structured_content" in artifact:
        payload = artifact["structured_content"]
    result = parse_bulk_schema_response(payload)
    return {"schema_dict": result.schema_dict, "table_names": result.table_names}
