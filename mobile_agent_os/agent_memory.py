from __future__ import annotations

import json
from pathlib import Path


class AgentMemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, app_name: str) -> Path:
        return self.root / "memory" / f"{app_name}.json"

    def load(self, app_name: str) -> list[str]:
        path = self.path_for(app_name)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = raw.get("lessons", []) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        return [str(item).strip() for item in items if str(item).strip()][-20:]

    def save(self, app_name: str, lessons: list[str]) -> Path:
        path = self.path_for(app_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"lessons": lessons[-20:]}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path
