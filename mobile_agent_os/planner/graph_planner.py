from __future__ import annotations

import json
from typing import Any

from ..graph_space.registry import RegistryTable
from ..graph_space.schema import ArtifactSpec, ControlEdgeSpec, GraphFragment, WorkSpec
from ..model_clients.base import TextModelClient
from ..model_clients.factory import create_text_model_client


WORK_SCHEMA = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string"},
        "agent_id": {"type": "string"},
        "goal": {"type": "string"},
    },
    "required": ["node_id", "agent_id", "goal"],
}

PLANNER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "work": {"type": "array", "items": WORK_SCHEMA},
        "control_edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_work_id": {"type": "string"},
                    "to_work_id": {"type": "string"},
                },
                "required": ["from_work_id", "to_work_id"],
            },
        },
        "artifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "local_id": {"type": "string"},
                    "kind": {"type": "string"},
                    "producer_work_id": {"type": "string"},
                    "consumer_work_ids": {"type": "array", "items": {"type": "string"}},
                    "identity": {
                        "type": "object",
                        "properties": {
                            "schema_id": {"type": "string"},
                            "parameters": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "value": {"type": "string"},
                                    },
                                    "required": ["name", "value"],
                                },
                            },
                        },
                        "required": ["schema_id", "parameters"],
                    },
                    "freshness_requirement_seconds": {"type": "number"},
                },
                "required": ["local_id", "kind", "producer_work_id", "consumer_work_ids"],
            },
        },
        "terminal_work_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["work", "control_edges", "artifacts", "terminal_work_ids"],
}


class GraphPlanner:
    """Converts a User Goal and Registry view into a coarse graph fragment."""

    def __init__(self, registry: RegistryTable, client: TextModelClient | None = None) -> None:
        self.registry = registry
        self.client = client or create_text_model_client()

    def plan(self, task_id: str, user_goal: str) -> GraphFragment:
        system = (
            "Create a coarse dependency graph for mobile AppAgents and return JSON only. "
            "WORK units are continuous app-level jobs rather than UI actions. "
            "Represent a data dependency as one Artifact with one producer and one or more consumers. "
            "An Artifact provider communicates through that Artifact instead of a duplicate control edge. "
            "Use control edges only for ordering without data handoff. "
            "Use only agents, capabilities, Artifact schemas, and facts present in the input. "
            "Every Artifact that matches an available Artifact schema and has all required fields established by the User Goal must include that identity. "
            "Leave identity absent only when no schema applies or a required field remains unknown. "
            "Encode identity parameters as name/value pairs; use JSON scalar text for number or boolean values. "
            "Leave information that becomes necessary only inside an app for runtime expansion."
        )
        user = json.dumps(
            {
                "user_goal": user_goal,
                "registry": self.registry.prompt_rows(),
                "artifact_schemas": self.registry.artifact_schema_rows(),
            },
            ensure_ascii=False,
        )
        value = self.client.parse_json_content(
            self.client.generate_text(system=system, user=user, max_tokens=1800, json_schema=PLANNER_JSON_SCHEMA)
        )
        return self._build(task_id, user_goal, value)

    def _build(self, task_id: str, user_goal: str, value: dict[str, Any]) -> GraphFragment:
        raw_work = value.get("work")
        if not isinstance(raw_work, list) or not raw_work:
            raise ValueError("planner returned no WORK units")
        work = tuple(
            WorkSpec(str(item["node_id"]), str(item["agent_id"]), str(item["goal"]))
            for item in raw_work
        )
        ids = {item.node_id for item in work}
        if len(ids) != len(work):
            raise ValueError("planner returned duplicate WORK ids")
        valid_agents = {profile.app_id for profile in self.registry.profiles()}
        if any(item.agent_id not in valid_agents for item in work):
            raise ValueError("planner selected an unknown AppAgent")

        control_edges = tuple(
            ControlEdgeSpec(str(item["from_work_id"]), str(item["to_work_id"]))
            for item in value.get("control_edges", [])
        )
        artifacts = []
        for item in value.get("artifacts", []):
            raw_identity = item.get("identity")
            identity = None
            if isinstance(raw_identity, dict):
                identity = self.registry.decode_artifact_identity(
                    str(raw_identity["schema_id"]),
                    raw_identity.get("parameters"),
                )
            freshness = item.get("freshness_requirement_seconds")
            artifacts.append(
                ArtifactSpec(
                    local_id=str(item["local_id"]),
                    kind=str(item["kind"]),
                    producer_work_id=str(item["producer_work_id"]),
                    consumer_work_ids=tuple(str(value) for value in item.get("consumer_work_ids", [])),
                    identity=identity,
                    freshness_requirement_seconds=float(freshness) if freshness is not None else None,
                )
            )
        return GraphFragment(
            task_id=task_id,
            user_goal=user_goal,
            work=work,
            control_edges=control_edges,
            artifacts=tuple(artifacts),
            terminal_work_ids=tuple(str(item) for item in value.get("terminal_work_ids", [])),
        )
