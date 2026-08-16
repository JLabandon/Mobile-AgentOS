"""Generic visual remote driver.

This driver talks to a public screenshot/action server contract. The server is
responsible for any platform-specific capture, input, orientation, calibration,
and transport details.
"""

from __future__ import annotations

import logging
import struct
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx
from mobilerun_core_local.driver.base import DeviceDriver

logger = logging.getLogger("mobilerun_core_local")

VISUAL_REMOTE_CONNECTION = "visual-remote"
VISUAL_REMOTE_DEFAULT_URL = "http://localhost:8090"
SCREENSHOT_COORDINATE_SPACE = "screenshot_pixels"


def validate_visual_remote_url(url: str) -> str:
    """Validate and normalize a visual remote server base URL."""
    normalized = url.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "visual-remote device must be a server base URL, "
            "e.g. http://localhost:8090"
        )
    return normalized


class VisualRemoteDriver(DeviceDriver):
    """Device driver for compatible visual remote servers."""

    platform = "VisualRemote"

    def __init__(
        self,
        url: str = VISUAL_REMOTE_DEFAULT_URL,
        device_id: str | None = "auto",
    ) -> None:
        self.url = validate_visual_remote_url(url)
        self.requested_device_id = device_id or "auto"
        self.device_id: str | None = None
        self.device_name: str | None = None
        self.capabilities: dict[str, Any] = {}
        self.supported: set[str] = set()
        self.supported_buttons: set[str] = set()
        self._client: httpx.AsyncClient | None = None
        self._connected = False
        self._screen_size: tuple[int, int] | None = None

    async def connect(self) -> None:
        self._client = httpx.AsyncClient(base_url=self.url, timeout=30.0)
        try:
            devices = await self._fetch_devices()
            selected = self._select_device(devices)
            self._configure_device(selected)
        except Exception:
            await self._client.aclose()
            self._client = None
            raise

        self._connected = True
        logger.info(
            "Connected to visual remote device %s at %s",
            self.device_id,
            self.url,
        )

    async def ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    async def tap(self, x: int, y: int) -> None:
        await self._post_action(
            {
                "action": "tap",
                "x": int(x),
                "y": int(y),
                **await self._screen_context(),
            }
        )

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: float = 1000,
    ) -> None:
        await self._post_action(
            {
                "action": "swipe",
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "duration_ms": int(duration_ms),
                **await self._screen_context(),
            }
        )

    async def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: float = 3.0,
    ) -> None:
        await self.swipe(x1, y1, x2, y2, duration_ms=duration * 1000)

    async def input_text(
        self,
        text: str,
        clear: bool = False,
        stealth: bool = False,
        wpm: int = 0,
    ) -> bool:
        await self._post_action(
            {
                "action": "type_text",
                "text": text,
                "clear": bool(clear),
            }
        )
        return True

    async def press_button(self, button: str) -> None:
        await self.ensure_connected()
        button_lower = button.lower()
        if button_lower not in self.supported_buttons:
            raise ValueError(
                f"Button '{button}' not supported. "
                f"Supported: {', '.join(sorted(self.supported_buttons))}"
            )
        await self._post_action({"action": "press_button", "button": button_lower})

    async def start_app(self, package: str, activity: Optional[str] = None) -> str:
        if "start_app" not in self.supported:
            return (
                "Failed to launch app: visual remote server does not support app launch"
            )

        payload: dict[str, Any] = {"action": "open_app", "package": package}
        if activity:
            payload["activity"] = activity
        await self._post_action(payload)
        return f"Launched {package}"

    async def screenshot(self, hide_overlay: bool = True) -> bytes:
        await self.ensure_connected()
        resp = await self._client.get(f"/devices/{self._quoted_device_id()}/screenshot")
        resp.raise_for_status()
        image = resp.content
        self._screen_size = _image_dimensions(image)
        return image

    async def get_date(self) -> str:
        return ""

    async def get_ui_tree(self) -> Dict[str, Any]:
        raise NotImplementedError(
            "visual-remote is screenshot-only and does not expose an accessibility tree"
        )

    async def get_apps(self, include_system: bool = True) -> List[Dict[str, str]]:
        return []

    async def list_packages(self, include_system: bool = False) -> List[str]:
        return []

    async def _fetch_devices(self) -> list[dict[str, Any]]:
        resp = await self._client.get("/devices")
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, list):
            return body
        if isinstance(body, dict) and isinstance(body.get("devices"), list):
            return body["devices"]
        raise ValueError(
            "visual-remote /devices must return a list or {devices: [...]}"
        )

    def _select_device(self, devices: list[dict[str, Any]]) -> dict[str, Any]:
        if not devices:
            raise ConnectionError("No visual remote devices reported by server.")

        requested = self.requested_device_id
        if requested and requested != "auto":
            for device in devices:
                if device.get("id") == requested:
                    return device
            raise ConnectionError(
                f"visual-remote device '{requested}' not found. "
                f"Available: {', '.join(str(d.get('id')) for d in devices)}"
            )

        if len(devices) == 1:
            return devices[0]

        ids = ", ".join(str(device.get("id")) for device in devices)
        raise ConnectionError(
            "Multiple visual remote devices are available. "
            f"Set device.device_id or --device-id. Available: {ids}"
        )

    def _configure_device(self, device: dict[str, Any]) -> None:
        device_id = device.get("id")
        if not device_id:
            raise ValueError("visual-remote device entry is missing id.")
        if not device.get("ready", True):
            raise ConnectionError(f"visual-remote device '{device_id}' is not ready.")

        capabilities = device.get("capabilities") or {}
        if not capabilities.get("screenshot", False):
            raise ConnectionError(
                f"visual-remote device '{device_id}' does not support screenshots."
            )

        self.device_id = str(device_id)
        self.device_name = device.get("name") or self.device_id
        self.platform = _normalize_platform(device.get("platform"))
        self.capabilities = dict(capabilities)
        self.supported = _capabilities_to_supported(self.capabilities)
        self.supported_buttons = _capabilities_to_buttons(self.capabilities)

    async def _post_action(self, payload: dict[str, Any]) -> None:
        await self.ensure_connected()
        resp = await self._client.post(
            f"/devices/{self._quoted_device_id()}/actions",
            json=payload,
        )
        resp.raise_for_status()

    async def _screen_context(self) -> dict[str, Any]:
        width, height = await self._get_screen_size()
        return {
            "coordinate_space": SCREENSHOT_COORDINATE_SPACE,
            "screen": {"width": width, "height": height},
        }

    async def _get_screen_size(self) -> tuple[int, int]:
        if self._screen_size is None:
            await self.screenshot()
        if self._screen_size is None:
            raise RuntimeError("Could not determine visual remote screen size.")
        return self._screen_size

    def _quoted_device_id(self) -> str:
        if self.device_id is None:
            raise RuntimeError("visual-remote device is not selected.")
        return quote(self.device_id, safe="")


def _normalize_platform(platform: Any) -> str:
    value = str(platform or "").lower()
    if value == "ios":
        return "iOS"
    if value == "android":
        return "Android"
    return "VisualRemote"


def _capabilities_to_supported(capabilities: dict[str, Any]) -> set[str]:
    supported = {"screenshot", "get_date"}
    if capabilities.get("tap"):
        supported.add("tap")
    if capabilities.get("swipe"):
        supported.update({"swipe", "drag"})
    if capabilities.get("type_text"):
        supported.update({"input_text", "direct_text_input"})
    if capabilities.get("open_app"):
        supported.add("start_app")
    if _capabilities_to_buttons(capabilities):
        supported.add("press_button")
    return supported


def _capabilities_to_buttons(capabilities: dict[str, Any]) -> set[str]:
    buttons = capabilities.get("press_button")
    if isinstance(buttons, list):
        return {str(button).lower() for button in buttons}
    if buttons is True:
        return {"back", "home", "enter"}
    return set()


# ---------------------------------------------------------------------------
# Image dimension helpers
#
# Copied (behavior-preserving) from mobilerun.tools.helpers.images.
# image_dimensions is kept private here (``_image_dimensions``) since this
# package only needs it internally for screen-size inference from raw
# screenshot bytes; it is not part of this package's public surface.
# ---------------------------------------------------------------------------


def _image_dimensions(image: bytes) -> tuple[int, int]:
    """Return ``(width, height)`` for PNG or JPEG bytes."""
    if image.startswith(b"\x89PNG\r\n\x1a\n") and len(image) >= 24:
        width, height = struct.unpack(">II", image[16:24])
        return int(width), int(height)

    if image.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(image)

    raise ValueError("Unsupported screenshot image format. Expected PNG or JPEG.")


def _jpeg_dimensions(image: bytes) -> tuple[int, int]:
    offset = 2
    length = len(image)
    while offset + 9 < length:
        if image[offset] != 0xFF:
            offset += 1
            continue

        while offset < length and image[offset] == 0xFF:
            offset += 1
        if offset >= length:
            break

        marker = image[offset]
        offset += 1

        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            break
        if offset + 2 > length:
            break

        segment_length = int.from_bytes(image[offset : offset + 2], "big")
        if segment_length < 2:
            raise ValueError("Invalid JPEG segment length.")

        if _is_start_of_frame(marker):
            if offset + 7 > length:
                break
            height = int.from_bytes(image[offset + 3 : offset + 5], "big")
            width = int.from_bytes(image[offset + 5 : offset + 7], "big")
            return int(width), int(height)

        offset += segment_length

    raise ValueError("Could not read JPEG dimensions.")


def _is_start_of_frame(marker: int) -> bool:
    return marker in {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
