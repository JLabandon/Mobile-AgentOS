from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .schema import ArtifactIdentityCandidate, ArtifactKey


@dataclass(frozen=True)
class ArtifactSchema:
    schema_id: str
    required_fields: tuple[str, ...]
    field_types: tuple[tuple[str, str], ...]
    description: str = ""
    schema_version: int = 1
    normalizers: tuple[tuple[str, str], ...] = ()
    default_freshness_seconds: float | None = None
    sharing_scope: str = "user:local"

    @classmethod
    def from_config(cls, schema_id: str, value: dict[str, Any]) -> "ArtifactSchema":
        return cls(
            schema_id=schema_id,
            required_fields=tuple(str(item) for item in value.get("required_fields", ())),
            field_types=tuple((str(name), str(kind)) for name, kind in value.get("field_types", {}).items()),
            description=str(value.get("description", "")),
            schema_version=int(value.get("schema_version", 1)),
            normalizers=tuple((str(name), str(rule)) for name, rule in value.get("normalizers", {}).items()),
            default_freshness_seconds=(
                float(value["default_freshness_seconds"])
                if value.get("default_freshness_seconds") is not None
                else None
            ),
            sharing_scope=str(value.get("sharing_scope", "user:local")),
        )

    def canonicalize(self, candidate: ArtifactIdentityCandidate, *, scope_override: str | None = None) -> ArtifactKey:
        if candidate.schema_id != self.schema_id:
            raise ValueError(f"candidate schema does not match {self.schema_id}")
        declared_types = dict(self.field_types)
        unknown = set(candidate.parameters) - set(declared_types)
        missing = set(self.required_fields) - set(candidate.parameters)
        if unknown:
            raise ValueError(f"unknown ArtifactKey fields: {sorted(unknown)}")
        if missing:
            raise ValueError(f"missing ArtifactKey fields: {sorted(missing)}")
        rules = dict(self.normalizers)
        canonical: list[tuple[str, str]] = []
        for name, value in sorted(candidate.parameters.items()):
            normalized = self._normalize(name, value, declared_types[name], rules.get(name, "identity"))
            canonical.append((name, json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(",", ":"))))
        return ArtifactKey(
            self.schema_id,
            self.schema_version,
            tuple(canonical),
            scope_override or candidate.security_scope.strip() or self.sharing_scope,
        )

    def decode_parameters(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, list):
            raise ValueError("Artifact identity parameters must be an array")
        field_types = dict(self.field_types)
        parameters: dict[str, Any] = {}
        for item in raw:
            if not isinstance(item, dict) or "name" not in item or "value" not in item:
                raise ValueError("Artifact identity parameter must contain name and value")
            name = str(item["name"])
            if name in parameters:
                raise ValueError(f"duplicate Artifact identity parameter: {name}")
            value = str(item["value"])
            kind = field_types.get(name)
            if kind == "string" or kind is None:
                parameters[name] = value
                continue
            try:
                parameters[name] = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Artifact identity parameter {name} is not valid JSON scalar text") from exc
        return parameters

    @staticmethod
    def _normalize(name: str, value: Any, field_type: str, rule: str) -> str | int | float | bool:
        if field_type == "string":
            if not isinstance(value, str):
                raise ValueError(f"ArtifactKey field {name} must be a string")
            normalized: str | int | float | bool = value.strip()
            if rule == "casefold":
                normalized = normalized.casefold()
            elif rule not in {"identity", "strip"}:
                raise ValueError(f"unknown canonicalization rule for {name}: {rule}")
            if normalized == "":
                raise ValueError(f"ArtifactKey field {name} cannot be empty")
            return normalized
        if field_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return value
        if field_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if field_type == "boolean" and isinstance(value, bool):
            return value
        raise ValueError(f"ArtifactKey field {name} has invalid type")


@dataclass(frozen=True)
class AppProfile:
    app_id: str
    label: str
    description: str
    capabilities: tuple[str, ...]
    package_candidates: tuple[str, ...]
    long_term_memory: tuple[str, ...] = ()
    default_resources: tuple[str, ...] = ()
    service_capacity: int = 1

    @classmethod
    def from_config(cls, app_id: str, value: dict[str, Any]) -> "AppProfile":
        instance = value.get("instance_policy", {})
        capacity = int(instance.get("max_parallel_instances", 1)) if instance.get("supports_parallel_instances") else 1
        return cls(
            app_id=app_id,
            label=str(value.get("label", app_id)),
            description=str(value.get("description", "")),
            capabilities=tuple(str(item) for item in value.get("capabilities", [])),
            package_candidates=tuple(str(item) for item in value.get("package_candidates", [])),
            long_term_memory=tuple(str(item) for item in value.get("long_term_memory", [])),
            default_resources=tuple(str(item) for item in value.get("default_resources", [])),
            service_capacity=max(1, capacity),
        )

    def prompt_view(self) -> dict[str, Any]:
        return {
            "agent_id": self.app_id,
            "app": self.label,
            "description": self.description,
            "capabilities": list(self.capabilities),
        }


class RegistryTable:
    def __init__(self, profiles: dict[str, AppProfile], artifact_schemas: dict[str, ArtifactSchema] | None = None) -> None:
        self._profiles = dict(profiles)
        self._artifact_schemas = dict(artifact_schemas or {})

    @classmethod
    def from_config(cls, config: dict[str, Any], artifact_config: dict[str, Any] | None = None) -> "RegistryTable":
        return cls(
            {app_id: AppProfile.from_config(app_id, value) for app_id, value in config.items()},
            {
                schema_id: ArtifactSchema.from_config(schema_id, value)
                for schema_id, value in (artifact_config or {}).items()
            },
        )

    def get(self, app_id: str) -> AppProfile:
        return self._profiles[app_id]

    def profiles(self) -> tuple[AppProfile, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))

    def prompt_rows(self) -> list[dict[str, Any]]:
        return [profile.prompt_view() for profile in self.profiles()]

    def providers(self, capability: str) -> tuple[AppProfile, ...]:
        return tuple(profile for profile in self.profiles() if capability in profile.capabilities)

    def artifact_schema(self, schema_id: str) -> ArtifactSchema:
        return self._artifact_schemas[schema_id]

    def decode_artifact_identity(self, schema_id: str, raw_parameters: Any) -> ArtifactIdentityCandidate:
        schema = self.artifact_schema(schema_id)
        return ArtifactIdentityCandidate(schema_id, schema.decode_parameters(raw_parameters))

    def artifact_schemas(self) -> tuple[ArtifactSchema, ...]:
        return tuple(self._artifact_schemas[key] for key in sorted(self._artifact_schemas))

    def artifact_schema_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "schema_id": schema.schema_id,
                "description": schema.description,
                "schema_version": schema.schema_version,
                "required_fields": list(schema.required_fields),
                "field_types": dict(schema.field_types),
                "sharing_scope": schema.sharing_scope,
            }
            for schema in self.artifact_schemas()
        ]
