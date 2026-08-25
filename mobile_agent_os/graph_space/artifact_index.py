from __future__ import annotations

from collections.abc import Iterable

from .schema import ArtifactKey, ArtifactNode, ArtifactState, ReusePolicy


class ArtifactIndexError(RuntimeError):
    pass


class ArtifactIndex:
    """Derived exact-key index. ArtifactNode remains the authoritative record."""

    def __init__(self) -> None:
        self._active: dict[str, str] = {}
        self._history: dict[str, list[str]] = {}

    def active(self, key: ArtifactKey) -> str | None:
        return self._active.get(key.fingerprint)

    def history(self, key: ArtifactKey) -> tuple[str, ...]:
        return tuple(self._history.get(key.fingerprint, ()))

    def register(self, key: ArtifactKey, node_id: str) -> None:
        fingerprint = key.fingerprint
        existing = self._active.get(fingerprint)
        if existing is not None and existing != node_id:
            raise ArtifactIndexError("artifact key already has an active generation")
        self._active[fingerprint] = node_id
        history = self._history.setdefault(fingerprint, [])
        if node_id not in history:
            history.append(node_id)

    def retire(self, key: ArtifactKey, node_id: str) -> None:
        if self._active.get(key.fingerprint) == node_id:
            del self._active[key.fingerprint]

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            "active": dict(self._active),
            "history": {key: list(values) for key, values in self._history.items()},
        }

    def rebuild(self, artifacts: Iterable[ArtifactNode]) -> None:
        self._active.clear()
        self._history.clear()
        for artifact in sorted(artifacts, key=lambda item: (item.created_order, item.generation)):
            if artifact.key is None or artifact.reuse_policy is not ReusePolicy.INDEXED:
                continue
            fingerprint = artifact.key.fingerprint
            self._history.setdefault(fingerprint, []).append(artifact.node_id)
            if artifact.state in {ArtifactState.FUTURE, ArtifactState.CONCRETE}:
                if fingerprint in self._active:
                    raise ArtifactIndexError("multiple active generations for one ArtifactKey")
                self._active[fingerprint] = artifact.node_id
