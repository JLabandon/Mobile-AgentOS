from __future__ import annotations

import time
from dataclasses import dataclass

from mobile_agent_os.execution import Completed, ExecutionContext
from mobile_agent_os.graph_space import (
    AppProfile,
    ArtifactDraft,
    ArtifactIdentityCandidate,
    ArtifactSchema,
    ArtifactSpec,
    GraphFragment,
    RegistryTable,
    WorkSpec,
)


def evaluation_registry() -> RegistryTable:
    return RegistryTable(
        {
            "notes": AppProfile(
                "notes",
                "Notes",
                "Personal note-taking app for browsing and retrieving stored records.",
                ("search_notes", "retrieve_information"),
                ("edu.agentos.mocknotes",),
            ),
            "calendar": AppProfile(
                "calendar",
                "Calendar",
                "Calendar app for creating and updating event records.",
                ("create_event", "update_event"),
                ("edu.agentos.mockworkflow",),
            ),
        },
        {
            "record.field": ArtifactSchema(
                "record.field",
                ("subject", "attribute"),
                (("subject", "string"), ("attribute", "string")),
                description="A semantic attribute value about a named subject. Provider app and destination record are provenance and consumer context.",
                normalizers=(("subject", "casefold"), ("attribute", "casefold")),
            ),
            "operation.receipt": ArtifactSchema(
                "operation.receipt",
                ("operation_id", "operation_type"),
                (("operation_id", "string"), ("operation_type", "string")),
                description="A receipt produced by one identified side-effecting operation.",
                normalizers=(("operation_id", "identity"), ("operation_type", "casefold")),
                sharing_scope="task",
            ),
        },
    )


def project_code_fragment(task_id: str, record_title: str, *, security_scope: str = "user:local") -> GraphFragment:
    return GraphFragment(
        task_id,
        f"Enter Project Alpha's access code into the {record_title} calendar record.",
        (
            WorkSpec("retrieve", "notes", "Retrieve Project Alpha's access code."),
            WorkSpec("record", "calendar", f"Enter the retrieved code into the {record_title} calendar record."),
        ),
        artifacts=(
            ArtifactSpec(
                "access_code",
                "record_field",
                "retrieve",
                ("record",),
                ArtifactIdentityCandidate(
                    "record.field",
                    {"subject": "Project Alpha", "attribute": "access code"},
                    security_scope,
                ),
            ),
        ),
        terminal_work_ids=("record",),
    )


@dataclass
class SleepExecutor:
    delay_seconds: float = 0.01

    def execute(self, context: ExecutionContext) -> Completed:
        time.sleep(self.delay_seconds)
        work = context.snapshot.work(context.assignment.work_id)
        drafts = tuple(
            ArtifactDraft(
                context.snapshot.artifact(artifact_id).kind,
                {
                    "value": {"result": f"completed by {context.profile.app_id}"},
                    "evidence": [f"simulated visible state for {context.profile.label}"],
                },
                (f"simulation://{context.assignment.work_id}",),
                artifact_id,
            )
            for artifact_id in work.output_artifact_ids
        )
        return Completed(drafts)
