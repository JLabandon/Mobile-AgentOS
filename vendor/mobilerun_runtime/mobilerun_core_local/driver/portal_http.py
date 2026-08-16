"""Portal-contract HTTP driver base.

The droidrun portal HTTP API is served by the Android Portal app and by
``mobilerun-ios --local``. This base class holds the shared client; platform
subclasses declare their key-code map, platform name, and token policy.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from mobilerun_core_local.driver.base import DeviceDriver

logger = logging.getLogger("mobilerun_core_local")


def validate_portal_url(url: str) -> str:
    """Validate and normalize a portal HTTP base URL."""
    normalized = url.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "Device must be the portal base URL, e.g. http://127.0.0.1:8080"
        )
    return normalized


class PortalHttpDriver(DeviceDriver):
    """Driver for devices speaking the droidrun portal HTTP contract."""

    platform = ""
    # Human-readable server name used in connect/auth error messages.
    _server_label = "Portal HTTP"
    # Appended verbatim to the 401/403 PermissionError message; subclasses
    # override with platform-specific guidance on how to fix the token.
    _auth_guidance: str = ""
    # Subclasses map button names to portal key codes and set
    # ``supported_buttons = set(_KEY_CODES)``.
    _KEY_CODES: dict[str, int] = {}

    supported = {
        "tap",
        "swipe",
        "input_text",
        "press_button",
        "press_key_code",
        "start_app",
        "stop_app",
        "screenshot",
        "get_ui_tree",
        "get_apps",
        "list_packages",
    }

    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = validate_portal_url(url)
        self.token = token or None
        self.timeout = timeout
        self.transport = transport
        self._client: httpx.AsyncClient | None = None
        self._connected = False

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        self._client = httpx.AsyncClient(
            base_url=self.url,
            timeout=self.timeout,
            headers=headers,
            transport=self.transport,
        )
        try:
            response = await self._client.get("/version")
            if response.status_code in {401, 403}:
                raise PermissionError(
                    f"{self._server_label} authentication failed; "
                    "check the bearer token" + self._auth_guidance
                )
            response.raise_for_status()
        except PermissionError:
            await self._client.aclose()
            self._client = None
            raise
        except Exception as exc:
            await self._client.aclose()
            self._client = None
            raise ConnectionError(
                f"Could not connect to {self._server_label} at {self.url}"
            ) from exc
        self._connected = True
        logger.info("Connected to %s at %s", self._server_label, self.url)

    async def ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise ConnectionError(f"{type(self).__name__} is not connected")
        return self._client

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _unwrap_json(data: Any) -> Any:
        if isinstance(data, dict):
            status = data.get("status")
            if status == "error":
                message = data.get("message") or data.get("error") or data
                raise RuntimeError(f"Portal returned error: {message}")
            inner_key = "result" if "result" in data else "data" if "data" in data else None
            if inner_key is not None:
                inner = data[inner_key]
                if isinstance(inner, str):
                    try:
                        return json.loads(inner)
                    except json.JSONDecodeError:
                        return inner
                return inner
        return data

    @classmethod
    def _unwrap_response(cls, response: httpx.Response) -> Any:
        response.raise_for_status()
        if not response.content:
            return None
        try:
            return cls._unwrap_json(response.json())
        except json.JSONDecodeError:
            return response.content

    async def _post(self, path: str, payload: dict[str, Any]) -> Any:
        await self.ensure_connected()
        return self._unwrap_response(await self._http.post(path, json=payload))

    # -- input actions -----------------------------------------------------

    async def tap(self, x: int, y: int) -> None:
        await self._post("/tap", {"x": int(x), "y": int(y)})

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: float = 1000,
    ) -> None:
        await self._post(
            "/swipe",
            {
                "startX": int(x1),
                "startY": int(y1),
                "endX": int(x2),
                "endY": int(y2),
                "duration": float(duration_ms),
            },
        )

    async def input_text(
        self,
        text: str,
        clear: bool = False,
        stealth: bool = False,
        wpm: int = 0,
    ) -> bool:
        encoded = base64.b64encode(text.encode()).decode("ascii")
        await self._post("/keyboard/input", {"base64_text": encoded, "clear": clear})
        return True

    async def press_button(self, button: str) -> None:
        button_lower = button.lower()
        if button_lower not in self.supported_buttons:
            raise ValueError(
                f"Button '{button}' not supported. "
                f"Supported: {', '.join(sorted(self.supported_buttons))}"
            )
        await self.press_key_code(type(self)._KEY_CODES[button_lower])

    async def press_key_code(self, key_code: int) -> None:
        await self._post("/keyboard/key", {"key_code": int(key_code)})

    # -- app management ----------------------------------------------------

    async def start_app(self, package: str, activity: Optional[str] = None) -> str:
        payload = {"package": package}
        if activity:
            payload["activity"] = activity
        await self._post("/app", payload)
        return f"Started {package}"

    async def stop_app(self, package: str) -> str:
        await self._post("/app/stop", {"package": package})
        return f"Stopped {package}"

    async def get_apps(self, include_system: bool = True) -> List[Dict[str, str]]:
        await self.ensure_connected()
        data = self._unwrap_response(await self._http.get("/packages"))
        if isinstance(data, dict) and "packages" in data:
            data = data["packages"]
        if not isinstance(data, list):
            return []

        apps: list[dict[str, str]] = []
        for app in data:
            if not isinstance(app, dict):
                continue
            if not include_system and app.get("isSystemApp", False):
                continue
            package = app.get("packageName") or app.get("package") or ""
            label = app.get("label") or package
            apps.append({"package": package, "label": label})
        return apps

    async def list_packages(self, include_system: bool = False) -> List[str]:
        apps = await self.get_apps(include_system=include_system)
        return [app["package"] for app in apps if app.get("package")]

    # -- state / observation ----------------------------------------------

    async def screenshot(self, hide_overlay: bool = True) -> bytes:
        await self.ensure_connected()
        params = {"hideOverlay": "true" if hide_overlay else "false"}
        response = await self._http.get("/screenshot", params=params)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "image/" in content_type or response.content.startswith(b"\x89PNG"):
            return response.content

        data = self._unwrap_response(response)
        if isinstance(data, str):
            return base64.b64decode(data)
        raise ValueError(f"Invalid screenshot response from {self._server_label}")

    async def get_ui_tree(self) -> Dict[str, Any]:
        await self.ensure_connected()
        data = self._unwrap_response(await self._http.get("/state_full"))
        return data if isinstance(data, dict) else {}
