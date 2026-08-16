from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobType(StrEnum):
    OBSERVATION = "ObservationJob"
    THINKING = "ThinkingJob"
    ACTION = "ActionJob"
    SETTLE_WAIT = "SettleWaitJob"
    IPC_DELIVERY = "IPCDeliveryJob"


class JobState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED_RESOURCE = "BLOCKED_RESOURCE"
    BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class ResourceRequirement:
    name: str
    scope: str = "global"


@dataclass(frozen=True)
class Job:
    job_type: JobType
    agent_id: str
    phase: str
    display_id: int | None = None
    depends_on: tuple[str, ...] = ()
    resources: tuple[ResourceRequirement, ...] = ()
    job_id: str = field(default_factory=lambda: uuid4().hex[:12])
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JobResult:
    job_id: str
    job_type: JobType
    agent_id: str
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
