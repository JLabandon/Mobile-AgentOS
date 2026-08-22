from mobile_agent_os.execution import AppAgent, Completed, NeedsExpansion
from mobile_agent_os.graph_space import ArtifactDraft, Edge, GraphSteward, InitialGraph, NodeStatus, WorkSpec
from mobile_agent_os.scheduling import FifoScheduler, ResourceTable

from tests.fakes.runtime import ScriptedExecutor, registry


def _runtime():
    steward = GraphSteward(registry())
    scheduler = FifoScheduler(steward, ResourceTable())
    steward.create_initial_graph(InitialGraph("run", "SOURCE", "SINK", (WorkSpec("A", "calendar", "Create appointment"),), (Edge("SOURCE", "A"), Edge("A", "SINK"))))
    scheduler.schedule("run")
    return steward, scheduler


def test_agent_only_executes_scheduler_assignment_and_commits_result() -> None:
    steward, scheduler = _runtime()
    agent = AppAgent("calendar", steward, scheduler, ScriptedExecutor([Completed((ArtifactDraft("calendar_event", {"value": "E1", "evidence": ["event form"], "id": "E1"}),))]))
    assert agent.run_once()
    snapshot = steward.read("run")
    assert snapshot.node("A").status is NodeStatus.DONE
    assert snapshot.node("SINK").status is NodeStatus.READY


def test_agent_checkpoint_expansion_creates_provider_and_continuation() -> None:
    steward, scheduler = _runtime()
    outcome = NeedsExpansion(
        checkpoint=ArtifactDraft("execution_checkpoint", {"title": "Alice"}),
        provider_agent_id="notes",
        required_capability="search_notes",
        provider_goal="Find location for the appointment",
        provider_artifact_kinds=("information_result",),
        continuation_goal="Continue appointment with returned information",
    )
    agent = AppAgent("calendar", steward, scheduler, ScriptedExecutor([outcome]))
    assert agent.run_once()
    snapshot = steward.read("run")
    assert snapshot.node("A").status is NodeStatus.DONE
    assert snapshot.node("A_provider").status is NodeStatus.ASSIGNED
    assert snapshot.node("A_continuation").status is NodeStatus.BLOCKED


def test_agent_converts_executor_exception_to_failed_node() -> None:
    class ExplodingExecutor:
        def execute(self, context):
            del context
            raise RuntimeError("model transport failed")

    steward, scheduler = _runtime()
    agent = AppAgent("calendar", steward, scheduler, ExplodingExecutor())
    assert agent.run_once()
    snapshot = steward.read("run")
    assert snapshot.node("A").status is NodeStatus.FAILED
    assert "executor error: RuntimeError" in (snapshot.node("A").outcome or "")
