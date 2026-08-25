from __future__ import annotations

import json

from mobile_agent_os.execution import AppAgent, NeedsExpansion
from mobile_agent_os.graph_space import (
    ArtifactDraft,
    ArtifactIdentityCandidate,
    ArtifactSpec,
    GraphFragment,
    GraphSteward,
    WorkSpec,
    WorkStatus,
)
from mobile_agent_os.planner import GraphPlanner
from mobile_agent_os.scheduling import GraphScheduler, ResourceTable
from verification.support import evaluation_registry, project_code_fragment


class ExpansionExecutor:
    def execute(self, context):
        return NeedsExpansion(
            checkpoint=ArtifactDraft("execution_checkpoint", {"value": "form open", "evidence": ["simulated form"]}),
            provider_agent_id="notes",
            required_capability="retrieve_information",
            provider_goal="Retrieve the missing field for the current record.",
            artifact_kind="record_field",
            continuation_goal=f"Continue: {context.work_goal}",
            identity=ArtifactIdentityCandidate("record.field", {"subject": "Alice appointment", "attribute": "location"}),
        )


def _private_operation(task_id: str, operation_id: str) -> GraphFragment:
    return GraphFragment(
        task_id,
        "Record one operation result.",
        (WorkSpec("operation", "calendar", "Complete the assigned operation."),),
        artifacts=(
            ArtifactSpec(
                "receipt",
                "operation_receipt",
                "operation",
                (),
                ArtifactIdentityCandidate(
                    "operation.receipt",
                    {"operation_id": operation_id, "operation_type": "calendar update"},
                ),
            ),
        ),
        terminal_work_ids=("operation",),
    )


def main() -> None:
    registry = evaluation_registry()
    planner = GraphPlanner(registry)
    planned = planner.plan(
        "planned",
        "Find Project Alpha's access code in Notes and enter it into a Calendar record titled Alpha Review.",
    )
    planner_probe = GraphSteward(registry)
    planner_probe.submit_task_fragment(planned)
    planner_probe.validate()

    steward = GraphSteward(registry)
    first = steward.submit_task_fragment(project_code_fragment("reuse-a", "Alpha Review"))
    second = steward.submit_task_fragment(project_code_fragment("reuse-b", "Alpha Handoff"))
    if first.global_id("access_code") != second.global_id("access_code"):
        raise RuntimeError("exact in-flight requests did not converge on one Artifact node")
    provider_id = first.global_id("retrieve")
    artifact_id = first.global_id("access_code")
    steward.assign(provider_id, "AS-L2-PROVIDER")
    steward.start(provider_id, "AS-L2-PROVIDER")
    steward.commit_work(
        provider_id,
        "AS-L2-PROVIDER",
        (
            ArtifactDraft(
                "record_field",
                {"value": "ALPHA-42", "evidence": ["simulated visible Notes record"]},
                artifact_node_id=artifact_id,
            ),
        ),
    )
    third = steward.submit_task_fragment(project_code_fragment("reuse-c", "Alpha Retrospective"))
    if third.global_id("access_code") != artifact_id:
        raise RuntimeError("completed Artifact was not reused by a later task")

    private_a = steward.submit_task_fragment(_private_operation("private-a", "same-operation-id"))
    private_b = steward.submit_task_fragment(_private_operation("private-b", "same-operation-id"))
    if private_a.global_id("receipt") == private_b.global_id("receipt"):
        raise RuntimeError("task-scoped operation receipts were incorrectly merged")

    late_steward = GraphSteward(registry)
    scheduler = GraphScheduler(late_steward, ResourceTable())
    late = late_steward.submit_task_fragment(
        GraphFragment(
            "late",
            "Complete the Alice appointment record.",
            (WorkSpec("appointment", "calendar", "Complete the Alice appointment record"),),
            terminal_work_ids=("appointment",),
        )
    )
    AppAgent("calendar", late_steward, scheduler, ExpansionExecutor()).run_once()
    expanded = late_steward.read()
    continuation = next(item for item in expanded.work_nodes if item.goal.startswith("Continue:"))
    provider = next(item for item in expanded.work_nodes if item.goal == "Retrieve the missing field for the current record.")
    if provider.status not in {WorkStatus.READY, WorkStatus.ASSIGNED} or continuation.status is not WorkStatus.BLOCKED:
        raise RuntimeError("late-bound expansion did not produce the expected executable dependency")
    print(
        json.dumps(
            {
                "planned_work": len(planned.work),
                "planned_artifacts": len(planned.artifacts),
                "late_task": late.task_id,
                "reuse_graph_work": len(steward.read().work_nodes),
                "reuse_graph_artifacts": len(steward.read().artifact_nodes),
                "expanded_graph_work": len(expanded.work_nodes),
                "expanded_graph_artifacts": len(expanded.artifact_nodes),
                "in_flight_artifact": artifact_id,
                "completed_reuse": third.global_id("access_code"),
                "private_artifacts": [private_a.global_id("receipt"), private_b.global_id("receipt")],
                "status": "passed",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
