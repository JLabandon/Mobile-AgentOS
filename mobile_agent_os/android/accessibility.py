from __future__ import annotations

import base64
import json
import re
from typing import Any

from .adb import AdbClient, AdbError


DEFAULT_SNAPSHOT_URI = "content://edu.agentos.accessibilitybridge.snapshot"


class AccessibilitySnapshotClient:
    """Reads one package-scoped UI tree from one logical Android display."""

    def __init__(self, adb: AdbClient, uri: str = DEFAULT_SNAPSHOT_URI) -> None:
        self.adb = adb
        self.uri = uri

    def snapshot(self, display_id: int, package: str, *, limit: int = 160) -> dict[str, Any]:
        result = self.adb.shell(
            "content",
            "call",
            "--uri",
            self.uri,
            "--method",
            "snapshot",
            "--extra",
            f"display_id:i:{display_id}",
            "--extra",
            f"package:s:{package}",
            "--extra",
            f"limit:i:{limit}",
            timeout=20,
        )
        if result.returncode:
            raise AdbError(
                f"accessibility snapshot command failed for display {display_id}, package {package}: "
                f"{result.stdout}{result.stderr}"
            )
        match = re.search(r"payload_b64=([A-Za-z0-9+/=]+)", result.stdout)
        if not match:
            raise AdbError(
                f"accessibility bridge returned no snapshot payload for display {display_id}, package {package}: "
                f"{result.stdout}{result.stderr}"
            )
        try:
            payload = json.loads(base64.b64decode(match.group(1)))
        except (ValueError, json.JSONDecodeError) as exc:
            raise AdbError("accessibility bridge returned an invalid snapshot payload") from exc
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            reason = payload.get("error") if isinstance(payload, dict) else "invalid_payload"
            raise AdbError(
                f"accessibility snapshot unavailable for display {display_id}, package {package}: {reason}"
            )
        if payload.get("display_id") != display_id or payload.get("package") != package:
            raise AdbError(
                f"accessibility snapshot identity mismatch: requested display={display_id} package={package}, "
                f"received display={payload.get('display_id')} package={payload.get('package')}"
            )
        return payload
