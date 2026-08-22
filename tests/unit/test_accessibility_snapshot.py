import base64
import json
from types import SimpleNamespace

import pytest

from mobile_agent_os.android.accessibility import AccessibilitySnapshotClient
from mobile_agent_os.android.adb import AdbError
from mobile_agent_os.android.ui_tree import nodes_from_accessibility_snapshot


class FakeAdb:
    def __init__(self, payload: dict) -> None:
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        self.result = SimpleNamespace(returncode=0, stdout=f"Result: Bundle[{{payload_b64={encoded}}}]", stderr="")

    def shell(self, *args, **kwargs):
        return self.result


def _payload(*, display_id: int = 2, package: str = "test.app") -> dict:
    return {
        "ok": True,
        "display_id": display_id,
        "package": package,
        "nodes": [{
            "text": "Submit",
            "content_desc": "",
            "resource_id": "test:id/submit",
            "class_name": "android.widget.Button",
            "package": package,
            "bounds": [10, 20, 110, 60],
            "clickable": True,
            "enabled": True,
            "editable": False,
            "checkable": False,
            "checked": False,
            "selected": False,
            "focused": False,
        }],
    }


def test_accessibility_snapshot_requires_exact_display_and_package() -> None:
    client = AccessibilitySnapshotClient(FakeAdb(_payload()))
    payload = client.snapshot(2, "test.app")
    nodes = nodes_from_accessibility_snapshot(payload)
    assert nodes[0].label == "Submit"
    assert nodes[0].bounds.center == (60, 40)


def test_accessibility_snapshot_rejects_identity_mismatch() -> None:
    client = AccessibilitySnapshotClient(FakeAdb(_payload(display_id=3)))
    with pytest.raises(AdbError, match="identity mismatch"):
        client.snapshot(2, "test.app")
