import pytest
from concurrent.futures import ThreadPoolExecutor

from mobile_agent_os.graph_space import (
    ArtifactDraft,
    ArtifactIdentityCandidate,
    ArtifactSchema,
    ArtifactSpec,
    ArtifactState,
    GraphFragment,
    GraphSteward,
    RegistryTable,
    ResolutionKind,
    WorkSpec,
    WorkStatus,
)
from mobile_agent_os.graph_space.steward import GraphError

from tests.fakes.runtime import registry


def _shared_fragment(task_id: str, consumer: str) -> GraphFragment:
    return GraphFragment(
        task_id,
        f"Use the project code for {consumer}.",
        (WorkSpec("provider", "notes", "Retrieve Project Alpha code"), WorkSpec("consumer", "calendar", consumer)),
        artifacts=(
            ArtifactSpec(
                "code",
                "project_code",
                "provider",
                ("consumer",),
                ArtifactIdentityCandidate("project.code", {"project": "Project Alpha"}),
            ),
        ),
        terminal_work_ids=("consumer",),
    )


def test_schema_canonicalization_is_stable_and_strict() -> None:
    schema = registry().artifact_schema("project.code")
    first = schema.canonicalize(ArtifactIdentityCandidate("project.code", {"project": " Project Alpha "}))
    second = schema.canonicalize(ArtifactIdentityCandidate("project.code", {"project": "project alpha"}))
    assert first == second
    assert first.fingerprint == second.fingerprint
    with pytest.raises(ValueError, match="missing"):
        schema.canonicalize(ArtifactIdentityCandidate("project.code", {}))
    with pytest.raises(ValueError, match="unknown"):
        schema.canonicalize(ArtifactIdentityCandidate("project.code", {"project": "Alpha", "hint": "notes"}))


def test_two_tasks_reference_the_same_future_artifact_and_provider_work() -> None:
    steward = GraphSteward(registry())
    first = steward.submit_task_fragment(_shared_fragment("task-a", "Create appointment"))
    second = steward.submit_task_fragment(_shared_fragment("task-b", "Update appointment"))
    assert first.global_id("code") == second.global_id("code")
    assert first.global_id("provider") == second.global_id("provider")
    snapshot = steward.read()
    artifact = snapshot.artifact(first.global_id("code"))
    assert artifact.state is ArtifactState.FUTURE
    assert set(artifact.task_memberships) == {"task-a", "task-b"}
    assert len(artifact.consumer_work_ids) == 2


def test_concrete_artifact_releases_all_consumers_and_serves_late_task() -> None:
    steward = GraphSteward(registry())
    first = steward.submit_task_fragment(_shared_fragment("task-a", "Create appointment"))
    second = steward.submit_task_fragment(_shared_fragment("task-b", "Update appointment"))
    provider = first.global_id("provider")
    artifact = first.global_id("code")
    steward.assign(provider, "AS-P")
    steward.start(provider, "AS-P")
    steward.commit_work(
        provider,
        "AS-P",
        (ArtifactDraft("project_code", {"value": "ALPHA-42", "evidence": ["visible note"]}, artifact_node_id=artifact),),
    )
    snapshot = steward.read()
    assert snapshot.work(first.global_id("consumer")).status is WorkStatus.READY
    assert snapshot.work(second.global_id("consumer")).status is WorkStatus.READY
    late = steward.submit_task_fragment(_shared_fragment("task-c", "Send appointment"))
    assert late.global_id("code") == artifact
    assert steward.read().work(late.global_id("consumer")).status is WorkStatus.READY


def test_expired_artifact_creates_a_new_generation_and_keeps_history() -> None:
    now = [1000.0]
    steward = GraphSteward(registry(), clock=lambda: now[0])
    first = steward.submit_task_fragment(
        GraphFragment(
            "weather-a",
            "Use a forecast.",
            (WorkSpec("provider", "notes", "Retrieve forecast"), WorkSpec("consumer", "calendar", "Use forecast")),
            artifacts=(
                ArtifactSpec(
                    "forecast",
                    "weather_forecast",
                    "provider",
                    ("consumer",),
                    ArtifactIdentityCandidate("weather.forecast", {"place": "Shenzhen", "date": "2026-08-27"}),
                ),
            ),
            terminal_work_ids=("consumer",),
        )
    )
    provider, artifact = first.global_id("provider"), first.global_id("forecast")
    steward.assign(provider, "AS-W")
    steward.start(provider, "AS-W")
    steward.commit_work(
        provider,
        "AS-W",
        (ArtifactDraft("weather_forecast", {"value": "sunny", "evidence": ["visible forecast"]}, artifact_node_id=artifact),),
    )
    now[0] = 2000.0
    assert steward.sweep_expired() == (artifact,)
    second = steward.submit_task_fragment(
        GraphFragment(
            "weather-b",
            "Use a current forecast.",
            (WorkSpec("provider", "notes", "Retrieve forecast"), WorkSpec("consumer", "calendar", "Use forecast")),
            artifacts=(
                ArtifactSpec(
                    "forecast",
                    "weather_forecast",
                    "provider",
                    ("consumer",),
                    ArtifactIdentityCandidate("weather.forecast", {"place": "Shenzhen", "date": "2026-08-27"}),
                ),
            ),
            terminal_work_ids=("consumer",),
        )
    )
    replacement = second.global_id("forecast")
    assert replacement != artifact
    key = registry().artifact_schema("weather.forecast").canonicalize(
        ArtifactIdentityCandidate("weather.forecast", {"place": "Shenzhen", "date": "2026-08-27"})
    )
    assert [item.node_id for item in steward.artifact_history(key)] == [artifact, replacement]


def test_runtime_resolution_joins_existing_future() -> None:
    steward = GraphSteward(registry())
    first = steward.submit_task_fragment(_shared_fragment("task-a", "Create appointment"))
    consumer_task = steward.submit_task_fragment(
        GraphFragment("task-b", "Independent consumer.", (WorkSpec("consumer", "calendar", "Use code"),), terminal_work_ids=("consumer",))
    )
    result = steward.resolve_artifact(
        ArtifactIdentityCandidate("project.code", {"project": "Project Alpha"}),
        consumer_task.global_id("consumer"),
        "task-b",
        "project_code",
        WorkSpec("provider", "notes", "Retrieve code"),
    )
    assert result.kind is ResolutionKind.JOINED_FUTURE
    assert result.artifact_node_id == first.global_id("code")


def test_invalid_identity_never_falls_back_to_a_shared_key() -> None:
    steward = GraphSteward(registry())
    with pytest.raises(GraphError, match="invalid Artifact identity"):
        steward.submit_task_fragment(
            GraphFragment(
                "bad",
                "Bad identity.",
                (WorkSpec("provider", "notes", "Retrieve code"),),
                artifacts=(
                    ArtifactSpec(
                        "code",
                        "project_code",
                        "provider",
                        (),
                        ArtifactIdentityCandidate("project.code", {}),
                    ),
                ),
                terminal_work_ids=("provider",),
            )
        )
    assert steward.read().tasks == ()


def test_concurrent_task_submission_creates_one_active_generation() -> None:
    steward = GraphSteward(registry())
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = tuple(
            pool.map(
                lambda item: steward.submit_task_fragment(_shared_fragment(*item)),
                (("task-a", "Create appointment"), ("task-b", "Update appointment")),
            )
        )
    artifact_ids = {record.global_id("code") for record in records}
    provider_ids = {record.global_id("provider") for record in records}
    assert len(artifact_ids) == 1
    assert len(provider_ids) == 1


def test_task_scoped_schema_uses_system_task_identity() -> None:
    base = registry()
    table = RegistryTable(
        {profile.app_id: profile for profile in base.profiles()},
        {
            schema.schema_id: schema for schema in base.artifact_schemas()
        }
        | {
            "operation.receipt": ArtifactSchema(
                "operation.receipt",
                ("operation_id",),
                (("operation_id", "string"),),
                sharing_scope="task",
            )
        },
    )
    steward = GraphSteward(table)

    def fragment(task_id: str) -> GraphFragment:
        return GraphFragment(
            task_id,
            "Complete one operation.",
            (WorkSpec("operation", "calendar", "Complete operation"),),
            artifacts=(
                ArtifactSpec(
                    "receipt",
                    "operation_receipt",
                    "operation",
                    (),
                    ArtifactIdentityCandidate("operation.receipt", {"operation_id": "same-id"}),
                ),
            ),
            terminal_work_ids=("operation",),
        )

    first = steward.submit_task_fragment(fragment("task-a"))
    second = steward.submit_task_fragment(fragment("task-b"))
    first_artifact = steward.read().artifact(first.global_id("receipt"))
    second_artifact = steward.read().artifact(second.global_id("receipt"))
    assert first_artifact.node_id != second_artifact.node_id
    assert first_artifact.key.security_scope == "task:task-a"
    assert second_artifact.key.security_scope == "task:task-b"
