from mobile_agent_os.execution import AppAgent, Completed, NeedsExpansion
from mobile_agent_os.graph_space import ArtifactDraft, GraphFragment, GraphSteward, WorkSpec, WorkStatus
from mobile_agent_os.scheduling import GraphScheduler, ResourceTable

from tests.fakes.runtime import ScriptedExecutor, registry


def _runtime():
    steward = GraphSteward(registry())
    scheduler = GraphScheduler(steward, ResourceTable())
    task = steward.submit_task_fragment(
        GraphFragment("run", "Create appointment.", (WorkSpec("appointment", "calendar", "Create appointment"),), terminal_work_ids=("appointment",))
    )
    return steward, scheduler, task.global_id("appointment")


def test_agent_executes_assignment_and_commits_result() -> None:
    steward, scheduler, work_id = _runtime()
    agent = AppAgent("calendar", steward, scheduler, ScriptedExecutor([Completed()]))
    assert agent.run_once()
    assert steward.read().work(work_id).status is WorkStatus.DONE


def test_agent_expansion_creates_provider_and_continuation() -> None:
    steward, scheduler, work_id = _runtime()
    outcome = NeedsExpansion(
        checkpoint=ArtifactDraft("execution_checkpoint", {"value": "form open", "evidence": ["visible form"]}),
        provider_agent_id="notes",
        required_capability="search_notes",
        provider_goal="Find appointment location",
        artifact_kind="information_result",
        continuation_goal="Continue appointment with returned information",
    )
    agent = AppAgent("calendar", steward, scheduler, ScriptedExecutor([outcome]))
    assert agent.run_once()
    snapshot = steward.read()
    assert snapshot.work(work_id).status is WorkStatus.DONE
    assert next(item for item in snapshot.work_nodes if item.goal == "Find appointment location").status is WorkStatus.ASSIGNED
    assert next(item for item in snapshot.work_nodes if item.goal.startswith("Continue appointment")).status is WorkStatus.BLOCKED


def test_agent_converts_executor_exception_to_failed_work() -> None:
    class ExplodingExecutor:
        def execute(self, context):
            del context
            raise RuntimeError("model transport failed")

    steward, scheduler, work_id = _runtime()
    agent = AppAgent("calendar", steward, scheduler, ExplodingExecutor())
    assert agent.run_once()
    assert steward.read().work(work_id).status is WorkStatus.FAILED
    assert "executor error: RuntimeError" in (steward.read().work(work_id).outcome or "")
