"""Android Portal HTTP-only driver.

This backend talks to a running Mobilerun Portal HTTP server with an
explicit bearer token. It deliberately does not use ADB: setup, token
retrieval, port forwarding, and APK installation belong to the ADB-backed
driver or external provisioning.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from mobilerun_core_local.driver.base import unsupported
from mobilerun_core_local.driver.portal_http import PortalHttpDriver

_ANDROID_KEY_CODES = {
    "back": 4,
    "home": 3,
    "menu": 82,
    "enter": 66,
    "delete": 67,
    "escape": 111,
    "tab": 61,
    "space": 62,
    "search": 84,
    "page_up": 92,
    "page_down": 93,
    "volume_up": 24,
    "volume_down": 25,
    "power": 26,
    "media_play_pause": 85,
    "media_next": 87,
    "media_prev": 88,
}


def validate_android_portal_url(url: str) -> str:
    """Validate and normalize an Android Portal HTTP base URL."""
    normalized = url.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "Android HTTP device must be the portal base URL, "
            "e.g. http://127.0.0.1:8080"
        )
    return normalized


class AndroidPortalHttpDriver(PortalHttpDriver):
    """Android device driver for Portal HTTP without ADB."""

    platform = "Android"
    _server_label = "Android Portal HTTP"
    _KEY_CODES = _ANDROID_KEY_CODES

    supported_buttons = set(_ANDROID_KEY_CODES)

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not token:
            raise ValueError("Android Portal HTTP requires a non-empty auth token")
        super().__init__(
            validate_android_portal_url(url),
            token,
            timeout=timeout,
            transport=transport,
        )

    # -- platform-impossible verbs ----------------------------------------

    @unsupported("Android Portal HTTP cannot install APKs without ADB")
    async def install_app(self, path: str, **kwargs: Any) -> str: ...

    @unsupported("Android Portal HTTP cannot uninstall apps without ADB")
    async def uninstall_app(self, package: str) -> str: ...
