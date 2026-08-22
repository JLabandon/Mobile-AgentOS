from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceSpec:
    key: str
    capacity: int = 1


@dataclass(frozen=True)
class ResourceLease:
    lease_id: str
    key: str
    owner: str


class ResourceTable:
    def __init__(self, specs: tuple[ResourceSpec, ...] = ()) -> None:
        self._capacity = {spec.key: max(1, spec.capacity) for spec in specs}
        self._leases: dict[str, ResourceLease] = {}
        self._next_lease = 1

    def ensure(self, key: str, capacity: int = 1) -> None:
        self._capacity.setdefault(key, max(1, capacity))

    def try_acquire(self, owner: str, requirements: tuple[str, ...]) -> tuple[ResourceLease, ...] | None:
        keys = tuple(dict.fromkeys(requirements))
        for key in keys:
            self.ensure(key)
            in_use = sum(lease.key == key for lease in self._leases.values())
            if in_use >= self._capacity[key]:
                return None
        leases = []
        for key in keys:
            lease = ResourceLease(f"L{self._next_lease}", key, owner)
            self._next_lease += 1
            self._leases[lease.lease_id] = lease
            leases.append(lease)
        return tuple(leases)

    def release_owner(self, owner: str) -> tuple[ResourceLease, ...]:
        released = tuple(lease for lease in self._leases.values() if lease.owner == owner)
        for lease in released:
            del self._leases[lease.lease_id]
        return released

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {
            key: {"capacity": capacity, "leased": sum(lease.key == key for lease in self._leases.values())}
            for key, capacity in sorted(self._capacity.items())
        }
