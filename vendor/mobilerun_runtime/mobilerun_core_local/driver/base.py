"""DeviceDriver — raw device I/O interface.

Subclasses implement the actual communication (ADB, iOS HTTP, cloud SDK, etc.).
Unsupported methods are detected via the ``supported`` set, not introspection.

Two ways a verb can be "missing":
- ``NotImplementedError`` — not built yet (TODO). May appear in a later release.
- ``PlatformUnsupportedError`` — the platform fundamentally cannot offer it; will
  never be added. Mark the verb with ``@unsupported("reason")`` so callers get
  a clear, structured error instead of a misleading TODO.
"""

from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable, Dict, List, Optional


class DeviceDisconnectedError(Exception):
    """Raised when the device is no longer reachable."""

    pass


class PlatformUnsupportedError(NotImplementedError):
    """Raised when a verb is impossible on this driver's platform.

    Subclasses ``NotImplementedError`` so legacy ``except NotImplementedError``
    callers still catch it; new callers can branch on ``PlatformUnsupportedError``
    to distinguish "won't ever work" from "not built yet".
    """

    pass


def unsupported(reason: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator: replace a verb body with one that raises ``PlatformUnsupportedError``.

    Use on driver methods that the platform fundamentally cannot offer
    (e.g. ``install_app`` on iOS Portal HTTP — there is no IPA install verb).
    The decorated method's body is never executed; ``...`` is enough::

        @unsupported("ios-portal cannot install local apps")
        async def install_app(self, path: str, **kwargs) -> str: ...
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(self: "DeviceDriver", *args: Any, **kwargs: Any) -> Any:
            raise PlatformUnsupportedError(
                f"{type(self).__name__}.{fn.__name__}: {reason}"
            )

        return wrapper

    return decorator


class DeviceDriver:
    """Base class for all device drivers.

    Every method raises ``NotImplementedError`` by default.
    Concrete drivers override the methods they support and declare them
    in the ``supported`` class-level set.

    ``platform`` identifies the device type (e.g. "Android", "iOS"). Subclasses
    must override; the base default is empty so a forgotten declaration on a
    new driver doesn't silently mis-identify itself as Android.
    """

    platform: str = ""
    supported: set[str] = set()
    supported_buttons: set[str] = set()

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        """Establish connection to the device."""
        raise NotImplementedError

    async def ensure_connected(self) -> None:
        """Connect if not already connected."""
        raise NotImplementedError

    # -- input actions -------------------------------------------------------

    async def tap(self, x: int, y: int) -> None:
        """Tap at absolute pixel coordinates."""
        raise NotImplementedError

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: float = 1000,
    ) -> None:
        """Swipe from (x1, y1) to (x2, y2)."""
        raise NotImplementedError

    async def input_text(
        self,
        text: str,
        clear: bool = False,
        stealth: bool = False,
        wpm: int = 0,
    ) -> bool:
        """Type *text* into the currently focused field.

        Returns ``True`` on success, ``False`` on failure.
        """
        raise NotImplementedError

    async def press_button(self, button: str) -> None:
        """Press a named button (e.g. back, home, enter).

        Raises ``ValueError`` if *button* is not in ``supported_buttons``.
        """
        raise NotImplementedError

    async def press_key_code(self, key_code: int) -> None:
        """Press a platform-native integer key code."""
        raise NotImplementedError

    async def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: float = 3.0,
    ) -> None:
        """Drag from (x1, y1) to (x2, y2)."""
        raise NotImplementedError

    # -- app management ------------------------------------------------------

    async def start_app(self, package: str, activity: Optional[str] = None) -> str:
        """Launch an application.

        Returns a human-readable result string.
        """
        raise NotImplementedError

    async def install_app(self, path: str, **kwargs) -> str:
        """Install an APK/IPA at *path*."""
        raise NotImplementedError

    async def stop_app(self, package: str) -> str:
        """Stop a running application."""
        raise NotImplementedError

    async def uninstall_app(self, package: str) -> str:
        """Uninstall an application."""
        raise NotImplementedError

    async def get_apps(self, include_system: bool = True) -> List[Dict[str, str]]:
        """Return installed apps as ``[{"package": …, "label": …}, …]``."""
        raise NotImplementedError

    async def list_packages(self, include_system: bool = False) -> List[str]:
        """Return installed package names."""
        raise NotImplementedError

    # -- state / observation -------------------------------------------------

    async def screenshot(self, hide_overlay: bool = True) -> bytes:
        """Capture the current screen.

        Returns raw PNG bytes.
        """
        raise NotImplementedError

    async def input_coordinate_size(
        self,
        screenshot_width: int,
        screenshot_height: int,
    ) -> tuple[int, int]:
        """Return the coordinate size expected by input methods.

        Most backends accept screenshot pixel coordinates, so the default input
        coordinate size matches the captured screenshot. Backends whose input
        layer uses a different coordinate system, such as XCTest points on iOS,
        override this method.
        """
        return screenshot_width, screenshot_height

    async def get_ui_tree(self) -> Dict[str, Any]:
        """Return the raw UI / accessibility tree from the device."""
        raise NotImplementedError

    async def get_date(self) -> str:
        """Return the device's current date/time as a string."""
        raise NotImplementedError
