"""AndroidDriver — ADB-based device driver.

Wraps ``async_adbutils.AdbDevice`` + ``PortalClient`` to provide clean device I/O
without event emission, formatting, or element lookup.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET

from async_adbutils import adb

from mobilerun_core_local.driver.base import DeviceDriver
from mobilerun_core_local.transport.android.portal_client import PortalClient

logger = logging.getLogger("mobilerun_core_local")

PORTAL_DEFAULT_TCP_PORT = 8080
_PORTAL_MODES = {"auto", "required", "disabled"}
_ADB_TEXT_CHUNK_SIZE = 200
_ADB_CLEAR_FALLBACK_DELETES = 80
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class AndroidDriver(DeviceDriver):
    """Raw Android device I/O via ADB, with optional Portal enhancement."""

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
        "install_app",
        "stop_app",
        "uninstall_app",
        "press_key_code",
    }

    supported_buttons = {"back", "home", "enter"}

    _BUTTON_KEYCODES = {
        "back": 4,
        "home": 3,
        "enter": 66,
    }

    def __init__(
        self,
        serial: str | None = None,
        use_tcp: bool = False,
        remote_tcp_port: int = PORTAL_DEFAULT_TCP_PORT,
        portal_mode: str = "auto",
    ) -> None:
        if portal_mode not in _PORTAL_MODES:
            valid = ", ".join(sorted(_PORTAL_MODES))
            raise ValueError(f"portal_mode must be one of {valid}")
        self._serial = serial
        self._use_tcp = use_tcp
        self._remote_tcp_port = remote_tcp_port
        self._portal_mode = portal_mode
        self._display_id = os.environ.get("MOBILERUN_ANDROID_DISPLAY_ID", "").strip()
        self._surfaceflinger_display_id = os.environ.get("MOBILERUN_ANDROID_SURFACEFLINGER_ID", "").strip()
        self._default_screenshot = os.environ.get("MOBILERUN_ANDROID_DEFAULT_SCREENSHOT", "").strip() == "1"
        self.device = None
        self.portal: PortalClient | None = None
        self._portal_available = False
        self._portal_keyboard_available = False
        self._connected = False

    @property
    def portal_available(self) -> bool:
        return self._portal_available

    @property
    def effective_supported(self) -> set[str]:
        return set(self.supported)

    # -- lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return

        self.device = await adb.device(serial=self._serial)
        state = await self.device.get_state()
        if state != "device":
            raise ConnectionError(f"Device is not online. State: {state}")

        if self._portal_mode != "disabled":
            portal = PortalClient(self.device, prefer_tcp=self._use_tcp)
            portal_ready = await self._probe_portal(portal)
            if portal_ready:
                self.portal = portal
                self._portal_available = True
                await self._setup_portal_keyboard()
            elif self._portal_mode == "required":
                raise ConnectionError("Portal is not available on this Android device")
            else:
                logger.debug("Portal unavailable; continuing with ADB-only AndroidDriver")

        self._connected = True

    async def ensure_connected(self) -> None:
        if not self._connected:
            await self.connect()

    async def _probe_portal(self, portal: PortalClient) -> bool:
        try:
            await portal.connect()
            result = await asyncio.wait_for(portal.ping(), timeout=5.0)
            return isinstance(result, dict) and result.get("status") == "success"
        except Exception as exc:
            logger.debug("Portal probe failed: %s", exc)
            return False

    async def _setup_portal_keyboard(self) -> None:
        from mobilerun_core_local.driver.android.portal import setup_keyboard  # circular import guard

        try:
            await setup_keyboard(self.device)
            self._portal_keyboard_available = True
        except Exception:
            self._portal_keyboard_available = False
            if self._portal_mode == "required":
                raise
            logger.debug("Portal keyboard setup failed; ADB text fallback will be used")

    def _portal_required(self) -> bool:
        return self._portal_mode == "required"

    # -- input actions -------------------------------------------------------

    async def tap(self, x: int, y: int) -> None:
        await self.ensure_connected()
        if self._display_id:
            await self.device.shell(f"input -d {shlex.quote(self._display_id)} tap {int(x)} {int(y)}")
            return
        await self.device.click(x, y)

    async def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: float = 1000,
    ) -> None:
        await self.ensure_connected()
        if self._display_id:
            await self.device.shell(
                f"input -d {shlex.quote(self._display_id)} swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}"
            )
            await asyncio.sleep(duration_ms / 1000)
            return
        await self.device.swipe(x1, y1, x2, y2, float(duration_ms / 1000))
        await asyncio.sleep(duration_ms / 1000)

    async def input_text(self, text: str, clear: bool = False) -> bool:
        await self.ensure_connected()
        if self.portal is not None and self._portal_keyboard_available:
            try:
                return await self.portal.input_text(text, clear)
            except Exception:
                if self._portal_required():
                    raise
                logger.debug("Portal input_text failed; falling back to ADB", exc_info=True)
                self._portal_keyboard_available = False
        return await self._input_text_adb(text, clear)

    async def press_button(self, button: str) -> None:
        await self.ensure_connected()
        button_lower = button.lower()
        if button_lower not in self.supported_buttons:
            raise ValueError(
                f"Button '{button}' not supported. "
                f"Supported: {', '.join(sorted(self.supported_buttons))}"
            )
        await self.press_key_code(self._BUTTON_KEYCODES[button_lower])

    async def press_key_code(self, key_code: int) -> None:
        await self.ensure_connected()
        if self._display_id:
            await self.device.shell(f"input -d {shlex.quote(self._display_id)} keyevent {int(key_code)}")
            return
        await self.device.keyevent(int(key_code))

    async def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: float = 3.0,
    ) -> None:
        await self.ensure_connected()
        raise NotImplementedError("Drag is not implemented yet")

    # -- app management ------------------------------------------------------

    async def start_app(self, package: str, activity: Optional[str] = None) -> str:
        await self.ensure_connected()
        logger.debug(f"Starting app {package} with activity {activity}")
        if not activity:
            dumpsys_output = await self.device.shell(
                f"cmd package resolve-activity --brief {shlex.quote(package)}"
            )
            component = next(
                (
                    line.strip()
                    for line in reversed(dumpsys_output.splitlines())
                    if "/" in line
                ),
                None,
            )
            if not component:
                detail = dumpsys_output.strip() or "<empty>"
                raise RuntimeError(
                    f"Could not resolve launch activity for {package}: {detail}"
                )
            activity = component.split("/", 1)[1].strip()
            if not activity:
                raise RuntimeError(
                    f"Could not resolve launch activity for {package}: {component}"
                )

        logger.debug(f"Activity: {activity}")
        if self._display_id:
            component = f"{package}/{activity}"
            await self.device.shell(
                f"am start --display {shlex.quote(self._display_id)} -n {shlex.quote(component)}"
            )
        else:
            await self.device.app_start(package, activity)
        logger.debug(f"App started: {package} with activity {activity}")
        return f"App started: {package} with activity {activity}"

    async def install_app(self, path: str, **kwargs) -> str:
        await self.ensure_connected()
        if not os.path.exists(path):
            raise FileNotFoundError(f"APK file not found at {path}")

        reinstall = kwargs.get("reinstall", False)
        grant_permissions = kwargs.get("grant_permissions", True)

        logger.debug(
            f"Installing app: {path} with reinstall: {reinstall} "
            f"and grant_permissions: {grant_permissions}"
        )
        result = await self.device.install(
            path,
            nolaunch=True,
            uninstall=reinstall,
            flags=["-g"] if grant_permissions else [],
            silent=True,
        )
        logger.debug(f"Installed app: {path} with result: {result}")
        return result

    async def stop_app(self, package: str) -> str:
        await self.ensure_connected()
        await self.device.shell(f"am force-stop {shlex.quote(package)}")
        return f"Stopped {package}"

    async def uninstall_app(self, package: str) -> str:
        await self.ensure_connected()
        result = await self.device.shell(f"pm uninstall {shlex.quote(package)}")
        return result.strip() or f"Uninstalled {package}"

    async def get_apps(self, include_system: bool = True) -> List[Dict[str, str]]:
        await self.ensure_connected()
        if self.portal is not None:
            try:
                return await self.portal.get_apps(include_system)
            except Exception:
                if self._portal_required():
                    raise
                logger.debug("Portal get_apps failed; falling back to ADB package list", exc_info=True)
        packages = await self.list_packages(include_system=include_system)
        return [{"package": package, "label": package} for package in packages]

    async def list_packages(self, include_system: bool = False) -> List[str]:
        await self.ensure_connected()
        filter_list = [] if include_system else ["-3"]
        return await self.device.list_packages(filter_list)

    # -- state / observation -------------------------------------------------

    async def screenshot(self, hide_overlay: bool = True) -> bytes:
        await self.ensure_connected()
        if self.portal is not None:
            try:
                return await self.portal.take_screenshot(hide_overlay)
            except Exception:
                if self._portal_required():
                    raise
                logger.debug("Portal screenshot failed; falling back to ADB screencap", exc_info=True)
        if self._default_screenshot:
            return await self.device.screenshot_bytes()
        if self._surfaceflinger_display_id:
            return await self._screencap_display_bytes(self._surfaceflinger_display_id)
        if self._display_id:
            return await self._screencap_display_bytes(self._display_id)
        return await self.device.screenshot_bytes()

    async def get_ui_tree(self) -> Dict[str, Any]:
        await self.ensure_connected()
        if self.portal is not None:
            try:
                return await self.portal.get_state()
            except Exception:
                if self._portal_required():
                    raise
                logger.debug("Portal get_state failed; falling back to uiautomator", exc_info=True)
        return await self._get_ui_tree_adb()

    async def get_date(self) -> str:
        await self.ensure_connected()
        result = await self.device.shell("date")
        return result.strip()

    # -- ADB fallbacks ---------------------------------------------------------

    async def _input_text_adb(self, text: str, clear: bool = False) -> bool:
        encoded_chunks = [
            _adb_input_text_arg(chunk)
            for chunk in _split_text_chunks(text, _ADB_TEXT_CHUNK_SIZE)
        ]
        if clear:
            await self._clear_input_adb()
        if not encoded_chunks:
            return True
        for encoded in encoded_chunks:
            if self._display_id:
                await self.device.shell(f"input -d {shlex.quote(self._display_id)} text {shlex.quote(encoded)}")
                continue
            await self.device.shell(f"input text {shlex.quote(encoded)}")
        return True

    async def _screencap_display_bytes(self, display_id: str) -> bytes:
        cmd = [
            os.environ.get("ADB", os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")),
        ]
        if self._serial:
            cmd.extend(["-s", self._serial])
        cmd.extend(["exec-out", "screencap", "-p", "-d", str(display_id)])
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace") or f"screencap failed for display {display_id}")
        return stdout

    async def _clear_input_adb(self) -> None:
        before_len = await self._focused_text_length()
        key_combo_ok = await self._try_shell(
            "input keycombination KEYCODE_CTRL_LEFT KEYCODE_A"
        )
        if key_combo_ok:
            await self.device.keyevent(67)
        if not key_combo_ok:
            await self._delete_focused_text_adb(before_len)
        after_len = await self._focused_text_length()
        if after_len is not None and after_len > 0:
            await self._delete_focused_text_adb(after_len)
            final_len = await self._focused_text_length()
            if final_len is not None and final_len > 0:
                raise RuntimeError("ADB clear_input failed: focused text remains")

    async def _try_shell(self, cmd: str) -> bool:
        try:
            await self.device.shell(cmd)
            return True
        except Exception:
            logger.debug("ADB shell command failed: %s", cmd, exc_info=True)
            return False

    async def _delete_focused_text_adb(self, text_len: int | None) -> None:
        count = text_len if text_len is not None and text_len > 0 else _ADB_CLEAR_FALLBACK_DELETES
        count = min(max(count, 1), 500)
        await self._try_shell("input keyevent KEYCODE_MOVE_END")
        for chunk in _chunk_keyevents([67] * count, 50):
            await self.device.shell(
                "input keyevent --delay 0 " + " ".join(str(code) for code in chunk)
            )

    async def _focused_text_length(self) -> int | None:
        try:
            state = await self._get_ui_tree_adb()
        except Exception:
            return None
        focused = _find_focused_node(state.get("a11y_tree"))
        if not focused:
            return None
        text = focused.get("text")
        hint = focused.get("hint")
        if isinstance(text, str) and isinstance(hint, str) and text == hint:
            return 0
        return len(text) if isinstance(text, str) else None

    async def _get_ui_tree_adb(self) -> Dict[str, Any]:
        raw = await self.device.shell("uiautomator dump /dev/tty")
        xml_start = raw.find("<?xml")
        if xml_start == -1:
            xml_start = raw.find("<hierarchy")
        if xml_start == -1:
            raise ValueError("uiautomator output did not contain XML")
        xml_end = raw.find("</hierarchy>", xml_start)
        if xml_end == -1:
            raise ValueError("uiautomator output did not contain a complete hierarchy")
        xml_end += len("</hierarchy>")
        root = ET.fromstring(raw[xml_start:xml_end].strip())
        children = [_uiautomator_node_to_dict(child) for child in list(root)]
        a11y_tree: dict[str, Any] | list[dict[str, Any]]
        if len(children) == 1:
            a11y_tree = children[0]
        else:
            a11y_tree = children

        package_name = _first_package(a11y_tree) or ""
        bounds = _first_bounds(a11y_tree)
        device_context: dict[str, Any] = {}
        if bounds is not None:
            device_context["screen_bounds"] = bounds
            device_context["display_metrics"] = {
                "width": bounds["right"] - bounds["left"],
                "height": bounds["bottom"] - bounds["top"],
            }
        return {
            "a11y_tree": a11y_tree,
            "phone_state": {"packageName": package_name},
            "device_context": device_context,
        }


def _split_text_chunks(text: str, max_len: int) -> list[str]:
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]


def _adb_input_text_arg(text: str) -> str:
    unsupported = [ch for ch in text if ord(ch) < 32 or ord(ch) == 127 or ord(ch) > 126]
    if unsupported:
        sample = ", ".join(repr(ch) for ch in unsupported[:3])
        raise ValueError(
            "ADB text input supports printable ASCII only in ADB-only mode; "
            f"unsupported character(s): {sample}"
        )
    if "%s" in text:
        raise ValueError(
            "ADB text input does not support literal %s in ADB-only mode; "
            "Android treats %s as a space escape"
        )
    return text.replace(" ", "%s")


def _chunk_keyevents(codes: list[int], max_len: int) -> list[list[int]]:
    return [codes[i : i + max_len] for i in range(0, len(codes), max_len)]


def _bool_attr(value: str | None) -> bool:
    return value == "true"


def _parse_bounds(value: str | None) -> dict[str, int] | None:
    if not value:
        return None
    match = _BOUNDS_RE.fullmatch(value)
    if not match:
        return None
    left, top, right, bottom = (int(part) for part in match.groups())
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _uiautomator_node_to_dict(element: ET.Element) -> dict[str, Any]:
    attrs = element.attrib
    node: dict[str, Any] = {
        "text": attrs.get("text") or "",
        "resourceId": attrs.get("resource-id") or "",
        "className": attrs.get("class") or "",
        "packageName": attrs.get("package") or "",
        "contentDescription": attrs.get("content-desc") or "",
        "checkable": _bool_attr(attrs.get("checkable")),
        "checked": _bool_attr(attrs.get("checked")),
        "clickable": _bool_attr(attrs.get("clickable")),
        "enabled": _bool_attr(attrs.get("enabled")),
        "focusable": _bool_attr(attrs.get("focusable")),
        "focused": _bool_attr(attrs.get("focused")),
        "scrollable": _bool_attr(attrs.get("scrollable")),
        "longClickable": _bool_attr(attrs.get("long-clickable")),
        "password": _bool_attr(attrs.get("password")),
        "selected": _bool_attr(attrs.get("selected")),
        "hint": attrs.get("hint") or "",
    }
    bounds = _parse_bounds(attrs.get("bounds"))
    if bounds is not None:
        node["boundsInScreen"] = bounds
    children = [_uiautomator_node_to_dict(child) for child in list(element)]
    if children:
        node["children"] = children
    return node


def _walk_nodes(node: Any):
    if isinstance(node, list):
        for child in node:
            yield from _walk_nodes(child)
        return
    if not isinstance(node, dict):
        return
    yield node
    for child in node.get("children") or []:
        yield from _walk_nodes(child)


def _find_focused_node(root: Any) -> dict[str, Any] | None:
    return next((node for node in _walk_nodes(root) if node.get("focused") is True), None)


def _first_package(root: Any) -> str | None:
    for node in _walk_nodes(root):
        package = node.get("packageName")
        if isinstance(package, str) and package:
            return package
    return None


def _first_bounds(root: Any) -> dict[str, int] | None:
    for node in _walk_nodes(root):
        bounds = node.get("boundsInScreen")
        if isinstance(bounds, dict):
            return bounds
    return None
