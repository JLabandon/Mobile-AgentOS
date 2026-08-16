"""iOS Portal HTTP driver."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from mobilerun_core_local.driver.base import DeviceDriver, unsupported

logger = logging.getLogger("mobilerun_core_local")

SYSTEM_APP_LABELS = {
    "ai.mobilerun.mobilerun-ios-portal": "Mobilerun Portal",
    "com.apple.Bridge": "Watch",
    "com.apple.DocumentsApp": "Files",
    "com.apple.Fitness": "Fitness",
    "com.apple.Health": "Health",
    "com.apple.Maps": "Maps",
    "com.apple.MobileAddressBook": "Contacts",
    "com.apple.MobileSMS": "Messages",
    "com.apple.Passbook": "Wallet",
    "com.apple.Passwords": "Passwords",
    "com.apple.Preferences": "Settings",
    "com.apple.PreviewShell": "Freeform",
    "com.apple.mobilecal": "Calendar",
    "com.apple.mobilesafari": "Safari",
    "com.apple.mobileslideshow": "Photos",
    "com.apple.news": "News",
    "com.apple.reminders": "Reminders",
    "com.apple.shortcuts": "Shortcuts",
    "com.apple.webapp": "Web App",
}

IOS_PORTAL_DEFAULT_PORT = 6643
IOS_PORTAL_SCAN_RANGE = 10
IOS_STATE_TIMEOUT_SECONDS = 4.0
IOS_STATE_HTTP_TIMEOUT_SECONDS = 6.0


def validate_ios_portal_url(url: str) -> str:
    """Validate and normalize an iOS portal base URL."""
    normalized = url.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "iOS device must be the portal base URL, e.g. http://127.0.0.1:6643"
        )
    return normalized


async def discover_ios_portal(
    host: str = "127.0.0.1",
    start_port: int = IOS_PORTAL_DEFAULT_PORT,
    scan_range: int = IOS_PORTAL_SCAN_RANGE,
    timeout: float = 1.0,
) -> str:
    """Find an already-running iOS portal by scanning a small localhost range."""

    async def _probe(client: httpx.AsyncClient, port: int) -> Optional[str]:
        url = f"http://{host}:{port}"
        try:
            resp = await client.get(f"{url}/device/date")
            if resp.status_code == 200 and "date" in resp.json():
                return url
        except Exception:
            pass
        return None

    async with httpx.AsyncClient(timeout=timeout) as client:
        result = await _probe(client, start_port)
        if result:
            logger.info("iOS portal found at %s", result)
            return result

        results = await asyncio.gather(
            *[_probe(client, p) for p in range(start_port + 1, start_port + scan_range)]
        )
        for result in results:
            if result is not None:
                logger.info("iOS portal found at %s", result)
                return result

    raise ConnectionError(
        f"Could not find iOS portal on {host} "
        f"(scanned ports {start_port}-{start_port + scan_range - 1}). "
        "Make sure the Mobilerun Portal app is running and iproxy is forwarding the port."
    )


def _humanize_bundle_identifier(bundle_id: str) -> str:
    mapped = SYSTEM_APP_LABELS.get(bundle_id)
    if mapped:
        return mapped

    last_segment = bundle_id.rsplit(".", 1)[-1]
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)|\d+", last_segment)
    if words:
        return " ".join(words)
    return last_segment or bundle_id


def _looks_like_ios_point_size(width: int, height: int) -> bool:
    short_side, long_side = sorted((width, height))
    aspect_ratio = long_side / short_side if short_side else 0
    is_phone = (
        250 <= short_side <= 500
        and 480 <= long_side <= 1000
        and 1.45 <= aspect_ratio <= 2.5
    )
    is_tablet = (
        700 <= short_side <= 1100
        and 900 <= long_side <= 1500
        and 1.2 <= aspect_ratio <= 1.6
    )
    return is_phone or is_tablet


def _infer_ios_point_size(pixel_width: int, pixel_height: int) -> tuple[int, int]:
    """Best-effort fallback when portal screen bounds are temporarily unavailable."""
    if _looks_like_ios_point_size(pixel_width, pixel_height):
        return pixel_width, pixel_height

    for scale in (3, 2):
        if pixel_width % scale or pixel_height % scale:
            continue
        point_width = pixel_width // scale
        point_height = pixel_height // scale
        if _looks_like_ios_point_size(point_width, point_height):
            return point_width, point_height
    return pixel_width, pixel_height


_IOS_RECT_RE = re.compile(
    r"\{\{\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\}\s*,\s*"
    r"\{\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\}\s*\}"
)
_IOS_ATTR_RE = re.compile(
    r"\b(identifier|label|value|placeholderValue):\s*" r"(\"[^\"]*\"|'[^']*'|[^,]+)"
)
_IOS_MEMORY_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]+")
_IOS_LINE_MARKERS = ("\u2192", "\u21b3", "-")


def _clean_ios_attr_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_ios_accessibility_tree(raw_tree: str) -> list[dict[str, Any]]:
    """Parse XCTest debugDescription text into a minimal node tree.

    ios-portal currently returns accessibility as text. The core helpers need
    dict/list nodes, so this parser extracts the stable pieces agents use:
    type, label/value/identifier/placeholder, bounds, and indentation nesting.
    """
    roots: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []

    for raw_line in raw_tree.splitlines():
        if not raw_line.strip():
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = _IOS_MEMORY_ADDRESS_RE.sub("", raw_line.strip())
        while line and line[0] in _IOS_LINE_MARKERS:
            line = line[1:].strip()

        if line.startswith("Attributes:"):
            continue
        if not line or (line.endswith(":") and "," not in line):
            continue
        if "," not in line and not _IOS_RECT_RE.search(line):
            continue

        type_name = line.split(",", 1)[0].strip()
        if not type_name:
            continue

        node: dict[str, Any] = {"className": type_name, "type": type_name}
        attrs = {
            key: _clean_ios_attr_value(value)
            for key, value in _IOS_ATTR_RE.findall(line)
        }
        identifier = attrs.get("identifier")
        label = attrs.get("label")
        value = attrs.get("value")
        placeholder = attrs.get("placeholderValue")

        if identifier:
            node["resourceId"] = identifier
            node["accessibilityIdentifier"] = identifier
        if label:
            node["text"] = label
            node["contentDescription"] = label
        elif value:
            node["text"] = value
        elif placeholder:
            node["text"] = placeholder
        if value:
            node["value"] = value
        if placeholder:
            node["placeholderValue"] = placeholder

        rect_match = _IOS_RECT_RE.search(line)
        if rect_match:
            x, y, width, height = (float(part) for part in rect_match.groups())
            node["bounds"] = {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }

        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            stack[-1][1].setdefault("children", []).append(node)
        else:
            roots.append(node)
        stack.append((indent, node))

    return roots


def _normalize_ios_state(state: dict[str, Any]) -> dict[str, Any]:
    raw_tree = state.get("a11y_tree")
    if isinstance(raw_tree, str):
        state = dict(state)
        state["raw_a11y_tree"] = raw_tree
        state["a11y_tree"] = _parse_ios_accessibility_tree(raw_tree)
    return state


class IOSPortalDriver(DeviceDriver):
    """iOS device driver communicating via the iOS portal HTTP API."""

    platform = "iOS"

    supported = {
        "tap",
        "swipe",
        "input_text",
        "press_button",
        "start_app",
        "screenshot",
        "get_ui_tree",
        "get_date",
    }

    supported_buttons = {"home"}

    def __init__(
        self,
        url: str,
        bundle_identifiers: Optional[List[str]] = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = validate_ios_portal_url(url)
        self.bundle_identifiers = bundle_identifiers or []
        self.transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._connected = False
        self._input_coordinate_sizes: dict[tuple[int, int], tuple[int, int]] = {}

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        self._client = httpx.AsyncClient(
            base_url=self.url,
            timeout=30.0,
            transport=self.transport,
        )
        try:
            resp = await self._client.get("/device/date")
            resp.raise_for_status()
        except Exception as exc:
            await self._client.aclose()
            self._client = None
            raise ConnectionError(
                f"Could not connect to iOS portal at {self.url}. "
                "Make sure the Mobilerun Portal app is running on the device "
                "and the URL/port is correct."
            ) from exc
        self._connected = True
        logger.info("Connected to iOS device at %s", self.url)

    async def ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise ConnectionError("IOSPortalDriver is not connected")
        return self._client

    # -- input actions -----------------------------------------------------

    async def tap(self, x: int, y: int) -> None:
        await self.ensure_connected()
        ios_rect = f"{{{{{x},{y}}},{{{1},{1}}}}}"
        resp = await self._http.post(
            "/gestures/tap",
            json={"rect": ios_rect, "count": 1, "longPress": False},
        )
        resp.raise_for_status()

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: float = 1000,
    ) -> None:
        await self.ensure_connected()
        resp = await self._http.post(
            "/gestures/swipe",
            json={
                "x1": float(x1),
                "y1": float(y1),
                "x2": float(x2),
                "y2": float(y2),
                "durationMs": float(duration_ms),
            },
        )
        resp.raise_for_status()

    async def input_text(
        self,
        text: str,
        clear: bool = False,
        stealth: bool = False,
        wpm: int = 0,
    ) -> bool:
        await self.ensure_connected()
        resp = await self._http.post(
            "/inputs/type", json={"text": text, "clear": clear}
        )
        resp.raise_for_status()
        return True

    async def press_button(self, button: str) -> None:
        await self.ensure_connected()
        button_lower = button.lower()
        if button_lower not in self.supported_buttons:
            raise ValueError(
                f"Button '{button}' not supported on iOS. "
                f"Supported: {', '.join(sorted(self.supported_buttons))}"
            )
        resp = await self._http.post("/inputs/key", json={"key": 1})
        resp.raise_for_status()

    # -- app management ----------------------------------------------------

    async def start_app(self, package: str, activity: Optional[str] = None) -> str:
        await self.ensure_connected()
        resp = await self._http.post(
            "/inputs/launch",
            json={"bundleIdentifier": package},
        )
        resp.raise_for_status()
        return f"Launched {package}"

    async def get_apps(self, include_system: bool = True) -> List[Dict[str, str]]:
        all_ids: set[str] = set(self.bundle_identifiers)
        if include_system:
            all_ids.update(SYSTEM_APP_LABELS)
        return [
            {"package": bundle_id, "label": _humanize_bundle_identifier(bundle_id)}
            for bundle_id in sorted(all_ids)
        ]

    async def list_packages(self, include_system: bool = False) -> List[str]:
        apps = await self.get_apps(include_system)
        return [app["package"] for app in apps]

    # -- state / observation ----------------------------------------------

    async def screenshot(self, hide_overlay: bool = True) -> bytes:
        await self.ensure_connected()
        resp = await self._http.get("/vision/screenshot")
        resp.raise_for_status()
        return resp.content

    async def input_coordinate_size(
        self,
        screenshot_width: int,
        screenshot_height: int,
    ) -> tuple[int, int]:
        key = (screenshot_width, screenshot_height)
        cached = self._input_coordinate_sizes.get(key)
        if cached is not None:
            return cached

        try:
            state = await self.get_ui_tree()
            bounds = (state.get("device_context") or {}).get("screen_bounds") or {}
            width = int(round(float(bounds.get("width", 0))))
            height = int(round(float(bounds.get("height", 0))))
            if width > 0 and height > 0:
                self._input_coordinate_sizes[key] = (width, height)
                return width, height
        except Exception as exc:
            logger.debug(
                "Could not read iOS input coordinate size from /state: %s", exc
            )

        width, height = _infer_ios_point_size(screenshot_width, screenshot_height)
        self._input_coordinate_sizes[key] = (width, height)
        return width, height

    async def get_ui_tree(self) -> Dict[str, Any]:
        await self.ensure_connected()
        resp = await self._http.get(
            "/state",
            params={"timeout": str(IOS_STATE_TIMEOUT_SECONDS)},
            timeout=IOS_STATE_HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except Exception as exc:
            raise ValueError(f"Invalid response from /state: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Invalid response from /state: expected JSON object")
        return _normalize_ios_state(data)

    async def get_date(self) -> str:
        await self.ensure_connected()
        resp = await self._http.get("/device/date")
        if resp.status_code == 200:
            return resp.json().get("date", "")
        return ""

    # -- platform-impossible verbs ----------------------------------------

    @unsupported("ios-portal cannot install local apps")
    async def install_app(self, path: str, **kwargs: Any) -> str: ...

    @unsupported("ios-portal does not expose an uninstall verb")
    async def uninstall_app(self, package: str) -> str: ...

    @unsupported("ios-portal does not expose a force-stop verb")
    async def stop_app(self, package: str) -> str: ...

    @unsupported("iOS has no integer keycode model — use press_button instead")
    async def press_key_code(self, key_code: int) -> None: ...


IOSDriver = IOSPortalDriver

__all__ = [
    "IOSPortalDriver",
    "IOSDriver",
    "SYSTEM_APP_LABELS",
    "discover_ios_portal",
    "validate_ios_portal_url",
]
