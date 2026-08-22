from __future__ import annotations

import json
from typing import Any

from ..graph_space.models import Edge, WorkSpec
from ..graph_space.registry import RegistryTable
from ..graph_space.steward import InitialGraph
from ..model_clients.base import TextModelClient
from ..model_clients.factory import create_text_model_client


PLANNER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "work": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "goal": {"type": "string"},
                    "expected_artifact_kinds": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["node_id", "agent_id", "goal"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_node_id": {"type": "string"},
                    "to_node_id": {"type": "string"},
                    "artifact_kinds": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["from_node_id", "to_node_id"],
            },
        },
    },
    "required": ["work", "edges"],
}


class GraphPlanner:
    """Creates only the initial coarse graph. Runtime discovery belongs to AppAgents."""

    def __init__(self, registry: RegistryTable, client: TextModelClient | None = None) -> None:
        self.registry = registry
        self.client = client or create_text_model_client()

    def plan(self, graph_id: str, user_goal: str) -> InitialGraph:
        system = (
            "You create a coarse dependency graph for mobile AppAgents. Return JSON only. "
            "Create independent app-level work units, not UI clicks. Add dependencies only when one work unit needs another's result. "
            "Use only Registry agents and user-goal facts. Add a dependency only when the user goal explicitly establishes that one work unit requires another result; shared domain, app descriptions, capabilities, or a possible workflow relation are insufficient. "
            "Leave application-specific missing fields that become visible during execution for runtime discovery."
        )
        user = json.dumps({"user_goal": user_goal, "registry": self.registry.prompt_rows(), "format": {"work": [{"node_id": "N1", "agent_id": "registry agent id", "goal": "app-level work", "expected_artifact_kinds": ["optional type"]}], "edges": [{"from_node_id": "N1", "to_node_id": "N2", "artifact_kinds": ["optional type"]}]}}, ensure_ascii=False)
        value = self.client.parse_json_content(
            self.client.generate_text(system=system, user=user, max_tokens=1600, json_schema=PLANNER_JSON_SCHEMA)
        )
        return self._build(graph_id, value)

    def _build(self, graph_id: str, value: dict[str, Any]) -> InitialGraph:
        raw_work = value.get("work")
        raw_edges = value.get("edges", [])
        if not isinstance(raw_work, list) or not raw_work:
            raise ValueError("planner returned no work units")
        work = tuple(
            WorkSpec(
                node_id=str(item["node_id"]),
                agent_id=str(item["agent_id"]),
                goal=str(item["goal"]),
                expected_artifact_kinds=tuple(str(kind) for kind in item.get("expected_artifact_kinds", [])),
            )
            for item in raw_work
        )
        ids = {item.node_id for item in work}
        if len(ids) != len(work):
            raise ValueError("planner returned duplicate work node ids")
        edges = [
            Edge(str(item["from_node_id"]), str(item["to_node_id"]), tuple(str(kind) for kind in item.get("artifact_kinds", [])))
            for item in raw_edges
        ]
        for edge in edges:
            if edge.from_node_id not in ids or edge.to_node_id not in ids:
                raise ValueError("planner edge refers to unknown work node")
        incoming = {edge.to_node_id for edge in edges}
        outgoing = {edge.from_node_id for edge in edges}
        source_id, sink_id = "SOURCE", "SINK"
        edges.extend(Edge(source_id, node_id) for node_id in sorted(ids - incoming))
        edges.extend(Edge(node_id, sink_id) for node_id in sorted(ids - outgoing))
        return InitialGraph(graph_id, source_id, sink_id, work, tuple(edges))
