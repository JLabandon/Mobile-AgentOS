from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol


class TraceSink(Protocol):
    def event(self, kind: str, **payload: object) -> None:
        ...


@dataclass(frozen=True)
class ResourceSpec:
    name: str
    capacity: int = 1


@dataclass(frozen=True)
class ResourceLease:
    resource: str
    owner_agent: str
    reason: str
    acquired_at: float
    step: int | None = None


class ResourceManager:
    def __init__(self, specs: list[ResourceSpec] | None = None, reporter: TraceSink | None = None) -> None:
        self.specs = {spec.name: spec for spec in specs or []}
        self.reporter = reporter
        self._leases: dict[str, list[ResourceLease]] = {}

    def can_acquire(self, agent: str, resources: list[str]) -> tuple[bool, str]:
        for resource in resources:
            spec = self.specs.get(resource, ResourceSpec(resource))
            leases = self._leases.get(resource, [])
            foreign_leases = [lease for lease in leases if lease.owner_agent != agent]
            if len(foreign_leases) >= spec.capacity:
                owner = foreign_leases[0].owner_agent
                return False, f"{resource} held by {owner}"
        return True, ""

    def acquire(self, agent: str, resources: list[str], reason: str, step: int | None = None) -> list[ResourceLease]:
        ok, message = self.can_acquire(agent, resources)
        if not ok:
            if self.reporter:
                self.reporter.event("resource_blocked", agent=agent, resources=resources, reason=message)
            raise RuntimeError(message)
        acquired: list[ResourceLease] = []
        now = time.monotonic()
        for resource in resources:
            existing = [lease for lease in self._leases.get(resource, []) if lease.owner_agent == agent]
            if existing:
                acquired.extend(existing)
                continue
            lease = ResourceLease(resource=resource, owner_agent=agent, reason=reason, acquired_at=now, step=step)
            self._leases.setdefault(resource, []).append(lease)
            acquired.append(lease)
        if self.reporter:
            self.reporter.event("resource_acquire", agent=agent, resources=resources, reason=reason, step=step)
        return acquired

    def release_agent(self, agent: str, reason: str) -> None:
        released: list[str] = []
        for resource, leases in list(self._leases.items()):
            kept = [lease for lease in leases if lease.owner_agent != agent]
            if len(kept) != len(leases):
                released.append(resource)
            if kept:
                self._leases[resource] = kept
            else:
                del self._leases[resource]
        if released and self.reporter:
            self.reporter.event("resource_release", agent=agent, resources=sorted(released), reason=reason)

    def snapshot(self) -> dict[str, list[dict[str, object]]]:
        return {
            resource: [
                {
                    "resource": lease.resource,
                    "owner_agent": lease.owner_agent,
                    "reason": lease.reason,
                    "acquired_at": lease.acquired_at,
                    "step": lease.step,
                }
                for lease in leases
            ]
            for resource, leases in sorted(self._leases.items())
        }


def display_slot_resource(display_id: int) -> str:
    return f"display_slot:{display_id}"


def display_observation_resource(display_id: int) -> str:
    return f"display_observation:{display_id}"


def display_input_resource(display_id: int) -> str:
    return f"display_input:{display_id}"
