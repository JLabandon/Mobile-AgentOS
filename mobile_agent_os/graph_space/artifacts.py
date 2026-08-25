from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from threading import RLock
from typing import Any

from .models import ArtifactKey, ArtifactState


@dataclass(frozen=True)
class SharedArtifact:
    key: ArtifactKey
    state: ArtifactState
    producer_graph_id: str
    producer_node_id: str
    consumers: tuple[tuple[str, str], ...] = ()
    payload: dict[str, Any] | None = None
    evidence_refs: tuple[str, ...] = ()
    failure_reason: str | None = None


class ArtifactCatalogError(RuntimeError):
    pass


class SharedArtifactCatalog:
    """Exact-key registry for concrete artifacts and in-flight producer contracts."""

    def __init__(self) -> None:
        self._records: dict[ArtifactKey, SharedArtifact] = {}
        self._lock = RLock()

    def declare_future(self, key: ArtifactKey, producer_graph_id: str, producer_node_id: str) -> tuple[SharedArtifact, bool]:
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                return copy.deepcopy(existing), False
            record = SharedArtifact(key, ArtifactState.FUTURE, producer_graph_id, producer_node_id)
            self._records[key] = record
            return copy.deepcopy(record), True

    def attach_consumer(self, key: ArtifactKey, graph_id: str, node_id: str) -> SharedArtifact:
        with self._lock:
            record = self._require(key)
            consumer = (graph_id, node_id)
            if consumer not in record.consumers:
                record = replace(record, consumers=(*record.consumers, consumer))
                self._records[key] = record
            return copy.deepcopy(record)

    def publish(
        self,
        key: ArtifactKey,
        producer_graph_id: str,
        producer_node_id: str,
        payload: dict[str, Any],
        evidence_refs: tuple[str, ...],
    ) -> SharedArtifact:
        with self._lock:
            record = self._require(key)
            if (record.producer_graph_id, record.producer_node_id) != (producer_graph_id, producer_node_id):
                raise ArtifactCatalogError("shared artifact producer does not match its future contract")
            if record.state is not ArtifactState.FUTURE:
                raise ArtifactCatalogError(f"shared artifact is not publishable: {record.state}")
            record = replace(
                record,
                state=ArtifactState.CONCRETE,
                payload=copy.deepcopy(payload),
                evidence_refs=tuple(evidence_refs),
            )
            self._records[key] = record
            return copy.deepcopy(record)

    def invalidate(self, key: ArtifactKey, reason: str) -> SharedArtifact:
        with self._lock:
            record = self._require(key)
            record = replace(record, state=ArtifactState.INVALIDATED, failure_reason=reason)
            self._records[key] = record
            return copy.deepcopy(record)

    def fail(self, key: ArtifactKey, reason: str) -> SharedArtifact:
        with self._lock:
            record = self._require(key)
            record = replace(record, state=ArtifactState.FAILED, failure_reason=reason)
            self._records[key] = record
            return copy.deepcopy(record)

    def read(self, key: ArtifactKey) -> SharedArtifact | None:
        with self._lock:
            record = self._records.get(key)
            return copy.deepcopy(record) if record is not None else None

    def records(self) -> tuple[SharedArtifact, ...]:
        with self._lock:
            return tuple(copy.deepcopy(record) for record in self._records.values())

    def _require(self, key: ArtifactKey) -> SharedArtifact:
        try:
            return self._records[key]
        except KeyError as exc:
            raise ArtifactCatalogError("unknown shared artifact key") from exc
