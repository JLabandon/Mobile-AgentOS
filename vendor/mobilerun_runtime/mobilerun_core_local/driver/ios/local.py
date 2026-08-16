"""iOS driver for the droidrun portal HTTP contract (``mobilerun-ios --local``).

``mobilerun-ios --local <udid>`` serves the same HTTP API as the Android
Portal app, backed by the device's WDA agent. All coordinates (tree bounds,
tap/swipe input, screen_bounds) are logical iOS points.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import httpx

from mobilerun_core_local.driver.base import DeviceDriver, unsupported
from mobilerun_core_local.driver.ios.http import (
    IOS_PORTAL_DEFAULT_PORT,
    IOS_PORTAL_SCAN_RANGE,
    IOSPortalDriver,
    _infer_ios_point_size,
)
from mobilerun_core_local.driver.portal_http import (
    PortalHttpDriver,
    validate_portal_url,
)

logger = logging.getLogger("mobilerun_core_local")

# mobilerun-ios --local defaults to 127.0.0.1:8080 and advances the port per
# attached device (8080, 8081, ...).
IOS_LOCAL_PORTAL_DEFAULT_PORT = 8080
IOS_LOCAL_PORTAL_SCAN_RANGE = 10

# The Android key codes pkg/iosportal's injectKey actually implements:
# home/enter are hardware button presses, delete a backspace, back the
# system edge-swipe back gesture, app_switch the app-switcher macro.
_IOS_KEY_CODES = {
    "home": 3,
    "back": 4,
    "enter": 66,
    "delete": 67,
    "app_switch": 187,
}


class IOSPortalHttpDriver(PortalHttpDriver):
    """iOS device driver for a ``mobilerun-ios --local`` portal HTTP server.

    Tokenless on loopback; pass ``token`` when the server was started with
    ``--local-token`` (mandatory for non-loopback binds).
    """

    platform = "iOS"
    _server_label = "iOS local portal"
    _auth_guidance = (
        ". Set MOBILERUN_DEVICE_TOKEN (or device.auth_token in config.yaml) "
        "to the --local-token value the server was started with"
    )
    _KEY_CODES = _IOS_KEY_CODES

    supported_buttons = set(_IOS_KEY_CODES)
    supported = PortalHttpDriver.supported | {"get_date", "install_app"}

    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(url, token, timeout=timeout, transport=transport)
        self._input_coordinate_sizes: dict[tuple[int, int], tuple[int, int]] = {}

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
                "Could not read iOS local input coordinate size from /state_full: %s",
                exc,
            )

        width, height = _infer_ios_point_size(screenshot_width, screenshot_height)
        self._input_coordinate_sizes[key] = (width, height)
        return width, height

    async def get_date(self) -> str:
        await self.ensure_connected()
        millis = self._unwrap_response(await self._http.get("/time"))
        return (
            datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc)
            .astimezone()
            .isoformat()
        )

    async def install_app(self, path: str, **kwargs: Any) -> str:
        # The Go server fetches URLs / reads local paths on its own host.
        await self._post("/install", {"urls": [path]})
        return f"Installed {path}"

    @unsupported("the portal HTTP API exposes no uninstall verb")
    async def uninstall_app(self, package: str) -> str: ...


class _NonClosingAsyncTransport(httpx.AsyncBaseTransport):
    """Let a temporary detection client borrow a caller-owned transport."""

    def __init__(self, transport: httpx.AsyncBaseTransport) -> None:
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._transport.handle_async_request(request)

    async def aclose(self) -> None:
        pass


def _is_portal_http_pong(response: httpx.Response) -> bool:
    if response.status_code != 200:
        return False
    try:
        body = response.json()
    except Exception:
        return False
    return (
        isinstance(body, dict)
        and body.get("status") == "success"
        and body.get("result") == "pong"
    )


def _is_ios_portal_http_version(response: httpx.Response) -> bool:
    if response.status_code != 200:
        return False
    try:
        body = response.json()
    except Exception:
        return False
    return (
        isinstance(body, dict)
        and body.get("status") == "success"
        and isinstance(body.get("result"), str)
        and body["result"].startswith("iosportal(")
    )


async def detect_ios_portal_kind(
    url: str,
    *,
    token: str | None = None,
    timeout: float = 2.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Literal["portal-http", "legacy"]:
    """Which contract does the iOS portal at ``url`` speak?

    ``GET /ping`` plus the iOS-specific ``GET /version`` result identifies a
    portal-HTTP server (``mobilerun-ios --local``). Both iOS and Android portal
    servers answer the same ping, so ping alone is only a reachability check.
    Otherwise ``GET /device/date`` identifies the legacy ios-portal app.
    """
    base = validate_portal_url(url)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        headers=headers,
    ) as client:
        try:
            if _is_portal_http_pong(await client.get(f"{base}/ping")):
                version = await client.get(f"{base}/version")
                if _is_ios_portal_http_version(version):
                    return "portal-http"
        except Exception:
            pass
        try:
            resp = await client.get(f"{base}/device/date")
            if resp.status_code == 200 and "date" in resp.json():
                return "legacy"
        except Exception:
            pass
    raise ConnectionError(
        f"No iOS portal answered at {base}: neither GET /ping plus GET /version "
        "(mobilerun-ios --local) nor GET /device/date (ios-portal app) "
        "responded as expected. Start one with `mobilerun-ios --local <udid>` "
        "or launch the Portal app."
    )


async def create_ios_driver(
    url: str,
    *,
    token: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DeviceDriver:
    """Return the right iOS driver for ``url``. The driver is not connected."""
    detection_transport = (
        _NonClosingAsyncTransport(transport) if transport is not None else None
    )
    kind = await detect_ios_portal_kind(
        url,
        token=token,
        transport=detection_transport,
    )
    if kind == "portal-http":
        return IOSPortalHttpDriver(url, token, transport=transport)
    return IOSPortalDriver(url, transport=transport)


async def discover_ios_device(
    host: str = "127.0.0.1",
    *,
    timeout: float = 1.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Find a running iOS portal: the --local ladder first, then the legacy app."""

    async def _probe_local(client: httpx.AsyncClient, port: int) -> Optional[str]:
        url = f"http://{host}:{port}"
        try:
            if _is_portal_http_pong(await client.get(f"{url}/ping")):
                version = await client.get(f"{url}/version")
                if _is_ios_portal_http_version(version):
                    return url
        except Exception:
            pass
        return None

    async def _probe_legacy(client: httpx.AsyncClient, port: int) -> Optional[str]:
        url = f"http://{host}:{port}"
        try:
            resp = await client.get(f"{url}/device/date")
            if resp.status_code == 200 and "date" in resp.json():
                return url
        except Exception:
            pass
        return None

    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
        for probe, start, span in (
            (_probe_local, IOS_LOCAL_PORTAL_DEFAULT_PORT, IOS_LOCAL_PORTAL_SCAN_RANGE),
            (_probe_legacy, IOS_PORTAL_DEFAULT_PORT, IOS_PORTAL_SCAN_RANGE),
        ):
            found = await probe(client, start)
            if found:
                logger.info("iOS portal found at %s", found)
                return found
            results = await asyncio.gather(
                *[probe(client, p) for p in range(start + 1, start + span)]
            )
            for found in results:
                if found is not None:
                    logger.info("iOS portal found at %s", found)
                    return found

    raise ConnectionError(
        f"Could not find an iOS portal on {host} (scanned "
        f"{IOS_LOCAL_PORTAL_DEFAULT_PORT}-"
        f"{IOS_LOCAL_PORTAL_DEFAULT_PORT + IOS_LOCAL_PORTAL_SCAN_RANGE - 1} for "
        f"mobilerun-ios --local and {IOS_PORTAL_DEFAULT_PORT}-"
        f"{IOS_PORTAL_DEFAULT_PORT + IOS_PORTAL_SCAN_RANGE - 1} for the Portal "
        "app). Run `mobilerun-ios --local <udid>` or start the Portal app "
        "with iproxy forwarding."
    )
