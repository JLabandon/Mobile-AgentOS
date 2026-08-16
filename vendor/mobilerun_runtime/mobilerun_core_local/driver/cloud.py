"""CloudDriver — MobileRun cloud device driver.

This is the async ``DeviceDriver`` used by the agent framework (mobilerun)
to drive cloud-hosted devices step-by-step during a task. It wraps the
``mobilerun_sdk`` SDK (``AsyncMobilerun``) to provide raw device I/O.

Requires the ``cloud`` extra: ``pip install 'mobilerun-core-local[cloud]'``.

``mobilerun_core.connection.cloud`` (in the mobilerun-core package) is a
different thing: the sync, skill-facing ``Connection`` facade. That module
wraps the sync SDK client directly and does not use this driver.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Dict, List, Optional, TypeVar

from mobilerun_core_local.driver.base import DeviceDisconnectedError, DeviceDriver

try:
    from mobilerun_sdk import (
        APIConnectionError,
        APITimeoutError,
        AsyncMobilerun,
        ConflictError,
    )
except ImportError as exc:
    raise ImportError(
        "mobilerun-sdk is not installed - `pip install 'mobilerun-core-local[cloud]'` "
        "or `pip install mobilerun-sdk`"
    ) from exc

logger = logging.getLogger("mobilerun_core_local")

T = TypeVar("T")


class CloudDriver(DeviceDriver):
    """Cloud device I/O via the MobileRun SDK."""

    platform = "Android"

    supported = {
        "tap",
        "swipe",
        "input_text",
        "press_button",
        "start_app",
        "screenshot",
        "get_ui_tree",
        "get_date",
        "get_apps",
        "list_packages",
    }

    supported_buttons = {"back", "home", "enter"}

    _BUTTON_ACTIONS = {
        "back": 1,  # global action: BACK
        "home": 2,  # global action: HOME
        "enter": 66,  # keycode passthrough
    }

    def __init__(
        self,
        device_id: str,
        display_id: int = 0,
        api_key: str | None = None,
        base_url: str = "https://api.mobilerun.com/v1",
        user_id: str | None = None,
        owner_id: str | None = None,
        stealth: bool = False,
    ) -> None:
        self.device_id = device_id
        self.display_id = display_id
        self._stealth = stealth

        if user_id:
            default_headers = {"X-User-ID": user_id}
            if owner_id:
                default_headers["X-Owner-Id"] = owner_id
            self._client = AsyncMobilerun(
                api_key="x",
                base_url=base_url,
                timeout=10.0,
                max_retries=4,
                default_headers=default_headers,
            )
        else:
            self._client = AsyncMobilerun(
                api_key=api_key,
                base_url=base_url,
                timeout=10.0,
                max_retries=4,
            )

    @property
    def _display_kw(self) -> dict:
        """Common keyword arg for display routing."""
        return {"x_device_display_id": self.display_id}

    @property
    def _stealth_extra(self) -> dict | None:
        """Extra body for stealth mode (tap/swipe)."""
        return {"stealth": True} if self._stealth else None

    async def _call(self, coro: Awaitable[T]) -> T:
        """Await an SDK coroutine, translating disconnect errors."""
        try:
            return await coro
        except (ConflictError, APIConnectionError, APITimeoutError) as e:
            raise DeviceDisconnectedError(str(e)) from e

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        pass  # SDK handles connection

    async def ensure_connected(self) -> None:
        pass

    # -- input actions -------------------------------------------------------

    async def tap(self, x: int, y: int) -> None:
        await self._call(
            self._client.devices.actions.tap(
                self.device_id,
                x=x,
                y=y,
                extra_body=self._stealth_extra,
                **self._display_kw,
            )
        )

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: float = 1000,
    ) -> None:
        await self._call(
            self._client.devices.actions.swipe(
                self.device_id,
                start_x=x1,
                start_y=y1,
                end_x=x2,
                end_y=y2,
                duration=duration_ms,
                extra_body=self._stealth_extra,
                **self._display_kw,
            )
        )

    async def input_text(
        self,
        text: str,
        clear: bool = False,
        stealth: bool | None = None,
        wpm: int = 0,
    ) -> bool:
        # Per-call ``stealth`` overrides the constructor default; ``None``
        # (the common case) keeps the driver-level setting.
        extra_body: dict = {}
        if self._stealth if stealth is None else stealth:
            extra_body["stealth"] = True
        if wpm:
            extra_body["wpm"] = wpm
        await self._call(
            self._client.devices.keyboard.write(
                self.device_id,
                text=text,
                clear=clear,
                extra_body=extra_body or None,
                **self._display_kw,
            )
        )
        return True

    async def press_button(self, button: str) -> None:
        button_lower = button.lower()
        if button_lower not in self.supported_buttons:
            raise ValueError(
                f"Button '{button}' not supported. "
                f"Supported: {', '.join(sorted(self.supported_buttons))}"
            )
        if button_lower in ("back", "home"):
            await self.global_action(self._BUTTON_ACTIONS[button_lower])
        else:
            await self._call(
                self._client.devices.keyboard.key(
                    self.device_id,
                    key=self._BUTTON_ACTIONS[button_lower],
                    **self._display_kw,
                )
            )

    async def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: float = 3.0,
    ) -> None:
        raise NotImplementedError("Drag is not supported on cloud devices")

    # -- app management ------------------------------------------------------

    async def start_app(self, package: str, activity: Optional[str] = None) -> str:
        # Omit ``activity`` entirely when not provided (string-or-omitted in
        # the API) instead of sending an explicit null.
        start_kw: dict = dict(self._display_kw)
        if activity:
            start_kw["activity"] = activity
        await self._call(
            self._client.devices.apps.start(
                package,
                device_id=self.device_id,
                **start_kw,
            )
        )
        return f"App started: {package}"

    async def get_apps(self, include_system: bool = True) -> List[Dict[str, Any]]:
        apps = await self._call(
            self._client.devices.apps.list(
                device_id=self.device_id,
                include_system_apps=include_system,
                **self._display_kw,
            )
        )
        return [app.model_dump() for app in apps]

    async def list_packages(self, include_system: bool = False) -> List[str]:
        packages = await self._call(
            self._client.devices.packages.list(
                device_id=self.device_id,
                include_system_packages=include_system,
                **self._display_kw,
            )
        )
        return packages

    # -- state / observation -------------------------------------------------

    async def screenshot(self, hide_overlay: bool = True) -> bytes:
        response = await self._call(
            self._client.devices.state.with_raw_response.screenshot(
                self.device_id, **self._display_kw
            )
        )
        return await self._call(response.read())

    async def get_ui_tree(self) -> Dict[str, Any]:
        response = await self._call(
            self._client.devices.state.ui(self.device_id, **self._display_kw)
        )
        data = response.model_dump()
        self._ensure_screen_bounds(data)
        return data

    @staticmethod
    def _ensure_screen_bounds(data: Dict[str, Any]) -> None:
        """Backfill screen size from the root node when the cloud omits it.

        Cloud state can report ``screen_bounds`` as ``{width: 0, height: 0}``,
        which makes screen-intersection filtering drop every element. Derive
        the real size from the root a11y node's bounds instead.
        """
        ctx = data.get("device_context") or {}
        bounds = ctx.get("screen_bounds") or {}
        if bounds.get("width") and bounds.get("height"):
            return
        root_bounds = (data.get("a11y_tree") or {}).get("boundsInScreen") or {}
        width = root_bounds.get("right", 0) - root_bounds.get("left", 0)
        height = root_bounds.get("bottom", 0) - root_bounds.get("top", 0)
        if width > 0 and height > 0:
            ctx["screen_bounds"] = {"width": width, "height": height}
            data["device_context"] = ctx

    async def get_date(self) -> str:
        return await self._call(
            self._client.devices.state.time(self.device_id, **self._display_kw)
        )

    # -- cloud-specific ------------------------------------------------------

    async def global_action(self, action: int) -> bool:
        """Execute a MobileRun global action (accessibility service).

        Common actions: 1=Back, 2=Home, 3=Recents, 4=Notifications,
        5=QuickSettings, 6=PowerDialog, 9=TakeScreenshot.
        """
        await self._call(
            self._client.devices.actions.global_(
                self.device_id, action=action, **self._display_kw
            )
        )
        return True
