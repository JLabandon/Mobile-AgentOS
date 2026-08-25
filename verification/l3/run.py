from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from mobile_agent_os.execution import AppAgent
from mobile_agent_os.graph_space import ArtifactState, GraphSteward, TaskStatus
from mobile_agent_os.planner import GraphPlanner
from mobile_agent_os.scheduling import GraphScheduler, ResourceTable
from verification.support import SleepExecutor, evaluation_registry


GOALS = (
    ("alpha-review", "Find Project Alpha's access code in Notes and enter it into a Calendar record titled Alpha Review."),
    ("alpha-handoff", "Find Project Alpha's access code in Notes and enter it into a Calendar record titled Alpha Handoff."),
)


def main() -> None:
    registry = evaluation_registry()
    planner = GraphPlanner(registry)
    steward = GraphSteward(registry)
    scheduler = GraphScheduler(steward, ResourceTable())
    fragments = []
    for task_id, goal in GOALS:
        fragment = planner.plan(task_id, goal)
        fragments.append(fragment)
        steward.submit_task_fragment(fragment)

    agents = {
        profile.app_id: AppAgent(profile.app_id, steward, scheduler, SleepExecutor())
        for profile in registry.profiles()
    }
    for _ in range(20):
        with ThreadPoolExecutor(max_workers=len(agents)) as pool:
            results = tuple(pool.map(lambda agent: agent.run_once(), agents.values()))
        progressed = any(results)
        snapshot = steward.read()
        if all(task.status is TaskStatus.READY_FOR_EVALUATION for task in snapshot.tasks):
            break
        if not progressed:
            raise RuntimeError("simulation reached a blocked global frontier")
    else:
        raise RuntimeError("simulation exceeded its bounded execution cycles")

    for task_id, _ in GOALS:
        steward.evaluate_task(task_id, success=True, outcome="simulated terminal evidence accepted")
    snapshot = steward.read()
    indexed = [item for item in snapshot.artifact_nodes if item.key is not None]
    shared = [item for item in indexed if len(item.task_memberships) > 1]
    if not shared or any(item.state is not ArtifactState.CONCRETE for item in shared):
        identities = [
            {
                "task_id": fragment.task_id,
                "artifacts": [
                    {
                        "kind": artifact.kind,
                        "schema_id": artifact.identity.schema_id if artifact.identity else None,
                        "parameters": artifact.identity.parameters if artifact.identity else None,
                    }
                    for artifact in fragment.artifacts
                ],
            }
            for fragment in fragments
        ]
        raise RuntimeError(f"full-chain simulation did not reuse one concrete Artifact node: {json.dumps(identities)}")
    print(
        json.dumps(
            {
                "tasks": {task.task_id: task.status for task in snapshot.tasks},
                "work_nodes": len(snapshot.work_nodes),
                "artifact_nodes": len(snapshot.artifact_nodes),
                "shared_artifact_nodes": [item.node_id for item in shared],
                "planned_artifact_identities": [
                    [artifact.identity.parameters if artifact.identity else None for artifact in fragment.artifacts]
                    for fragment in fragments
                ],
                "events": len(steward.events()),
                "status": "passed",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
