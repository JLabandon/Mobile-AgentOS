from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
REMOTE_UI = "/sdcard/window_dump.xml"


class AdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class AndroidDisplayInfo:
    display_id: int
    width: int | None = None
    height: int | None = None
    kind: str = ""
    name: str = ""
    unique_id: str = ""
    can_host_tasks: bool = False
    has_content: bool = False
    top_activity: str = ""
    surfaceflinger_id: str | None = None


class AdbClient:
    def __init__(self, adb_path: str | None = None, device: str | None = None) -> None:
        self.adb_path = adb_path or os.environ.get("ADB", DEFAULT_ADB)
        self.device = device or os.environ.get("ANDROID_SERIAL")

    def _base_cmd(self) -> list[str]:
        cmd = [self.adb_path]
        if self.device:
            cmd.extend(["-s", self.device])
        return cmd

    def run(self, *args: str, check: bool = False, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [*self._base_cmd(), *args],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        if check and proc.returncode != 0:
            raise AdbError(f"adb command failed: {' '.join(args)}\nstdout={proc.stdout}\nstderr={proc.stderr}")
        return proc

    def shell(self, *args: str, check: bool = False, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return self.run("shell", *args, check=check, timeout=timeout)

    def require_device(self) -> str:
        proc = self.run("devices", "-l", check=True)
        devices = []
        for line in proc.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        if not devices:
            raise AdbError(f"no online Android device/emulator found via adb\nadb output:\n{proc.stdout}{proc.stderr}")
        if self.device and self.device not in devices:
            raise AdbError(f"ANDROID_SERIAL={self.device} is not online; online devices: {devices}")
        return self.device or devices[0]

    def package_exists(self, package_name: str) -> bool:
        proc = self.shell("cmd", "package", "path", package_name, timeout=15)
        return proc.returncode == 0 and "package:" in proc.stdout

    def foreground_package(self) -> str | None:
        proc = self.shell("dumpsys", "window", timeout=20)
        text = proc.stdout + proc.stderr
        for pattern in [
            r"mCurrentFocus=Window\{[^ ]+\s+u\d+\s+([^/ ]+)/",
            r"mFocusedApp=ActivityRecord\{[^ ]+\s+u\d+\s+([^/ ]+)/",
        ]:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return None

    def pick_package(self, candidates: list[str]) -> str:
        for package_name in candidates:
            if self.package_exists(package_name):
                return package_name
        raise AdbError(f"none of these packages are installed: {', '.join(candidates)}")

    def launch_package(self, package_name: str) -> subprocess.CompletedProcess[str]:
        return self.shell(
            "monkey",
            "-p",
            package_name,
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
            check=True,
            timeout=30,
        )

    def resolve_activity(self, package_name: str) -> str:
        proc = self.shell("cmd", "package", "resolve-activity", "--brief", package_name, timeout=20)
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            if "/" in line and not line.startswith("priority="):
                return line
        raise AdbError(f"failed to resolve launcher activity for {package_name}: {proc.stdout}{proc.stderr}")

    def launch_package_on_display(self, package_name: str, display_id: int) -> subprocess.CompletedProcess[str]:
        component = self.resolve_activity(package_name)
        return self.shell("am", "start", "--display", str(display_id), "-n", component, check=True, timeout=30)

    def force_stop(self, package_name: str) -> subprocess.CompletedProcess[str]:
        return self.shell("am", "force-stop", package_name, timeout=20)

    def clear_app_data(self, package_name: str) -> subprocess.CompletedProcess[str]:
        return self.shell("pm", "clear", package_name, check=True, timeout=30)

    def launch_shell(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        command = " ".join(shlex.quote(arg) for arg in args)
        return self.shell(command, check=True, timeout=30)

    def dump_ui(self, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        last_error = ""
        for _ in range(3):
            self.shell("rm", "-f", REMOTE_UI, timeout=10)
            dump_proc = self.shell("uiautomator", "dump", REMOTE_UI, timeout=20)
            dump_output = f"{dump_proc.stdout}\n{dump_proc.stderr}"
            if dump_proc.returncode != 0 or "ERROR:" in dump_output:
                last_error = f"failed to dump UI\nstdout={dump_proc.stdout}\nstderr={dump_proc.stderr}"
                self.settle(0.8)
                continue
            proc = self.run("pull", REMOTE_UI, str(out_path), timeout=20)
            if proc.returncode == 0 and out_path.exists():
                return out_path
            last_error = f"failed to pull UI dump\nstdout={proc.stdout}\nstderr={proc.stderr}"
            self.settle(0.8)
        raise AdbError(last_error)
        return out_path

    def screenshot(self, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as handle:
            proc = subprocess.run(
                [*self._base_cmd(), "exec-out", "screencap", "-p"],
                stdout=handle,
                stderr=subprocess.PIPE,
                timeout=20,
            )
        if proc.returncode != 0:
            raise AdbError(f"failed to capture screenshot: {proc.stderr.decode('utf-8', errors='replace')}")
        return out_path

    def screenshot_display(self, display_id: str | int, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as handle:
            proc = subprocess.run(
                [*self._base_cmd(), "exec-out", "screencap", "-p", "-d", str(display_id)],
                stdout=handle,
                stderr=subprocess.PIPE,
                timeout=20,
            )
        if proc.returncode != 0:
            raise AdbError(f"failed to capture display {display_id}: {proc.stderr.decode('utf-8', errors='replace')}")
        return out_path

    def tap(self, x: int, y: int) -> subprocess.CompletedProcess[str]:
        return self.shell("input", "tap", str(x), str(y), check=True)

    def tap_display(self, display_id: int, x: int, y: int) -> subprocess.CompletedProcess[str]:
        return self.shell("input", "-d", str(display_id), "tap", str(x), str(y), check=True)

    def display_size(self, display_id: int) -> tuple[int, int] | None:
        for display in self.list_displays():
            if display.display_id == display_id and display.width and display.height:
                return display.width, display.height
        return None

    def input_text(self, text: str) -> subprocess.CompletedProcess[str]:
        # ADB input text treats spaces specially. %s is the documented escape.
        escaped = re.sub(r"\s+", "%s", text)
        escaped = escaped.replace("'", "").replace('"', "")
        return self.shell("input", "text", escaped, check=True)

    def input_text_display(self, display_id: int, text: str) -> subprocess.CompletedProcess[str]:
        escaped = re.sub(r"\s+", "%s", text)
        escaped = escaped.replace("'", "").replace('"', "")
        return self.shell("input", "-d", str(display_id), "text", escaped, check=True)

    def replace_text(self, text: str, *, max_delete: int = 40) -> None:
        self.shell("input", "keyevent", "KEYCODE_MOVE_END", timeout=10)
        for _ in range(max_delete):
            self.shell("input", "keyevent", "KEYCODE_DEL", timeout=10)
        self.input_text(text)

    def swipe(self, direction: str) -> subprocess.CompletedProcess[str]:
        presets = {
            "up": (540, 1600, 540, 500),
            "down": (540, 500, 540, 1600),
            "left": (900, 1000, 180, 1000),
            "right": (180, 1000, 900, 1000),
        }
        if direction not in presets:
            raise AdbError(f"unsupported swipe direction: {direction}")
        x1, y1, x2, y2 = presets[direction]
        return self.shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), "450", check=True)

    def swipe_display(self, display_id: int, direction: str) -> subprocess.CompletedProcess[str]:
        presets = {
            "up": (360, 1000, 360, 300),
            "down": (360, 300, 360, 1000),
            "left": (620, 640, 100, 640),
            "right": (100, 640, 620, 640),
        }
        if direction not in presets:
            raise AdbError(f"unsupported swipe direction: {direction}")
        x1, y1, x2, y2 = presets[direction]
        return self.shell("input", "-d", str(display_id), "swipe", str(x1), str(y1), str(x2), str(y2), "450", check=True)

    def back(self) -> subprocess.CompletedProcess[str]:
        return self.shell("input", "keyevent", "BACK", check=True)

    def back_display(self, display_id: int) -> subprocess.CompletedProcess[str]:
        return self.shell("input", "-d", str(display_id), "keyevent", "BACK", check=True)

    def list_displays(self) -> list[AndroidDisplayInfo]:
        display_text = self.shell("dumpsys", "display", timeout=30).stdout
        stack_text = self.shell("am", "stack", "list", timeout=30).stdout
        sf_text = self.shell("dumpsys", "SurfaceFlinger", "--display-id", timeout=20).stdout
        sf_by_name = self._parse_surfaceflinger_displays(sf_text)
        task_by_display = self._parse_stack_displays(stack_text)
        infos = self._parse_logical_displays(display_text, sf_by_name, task_by_display)
        if infos:
            return infos
        return self._fallback_parse_display_devices(display_text, sf_by_name, task_by_display)

    def package_display_ids(self) -> dict[str, list[int]]:
        proc = self.shell("dumpsys", "activity", "activities", timeout=30)
        result: dict[str, list[int]] = {}
        current_display: int | None = None
        for line in proc.stdout.splitlines():
            display = re.search(r"Display #(\d+)", line)
            if display:
                current_display = int(display.group(1))
                continue
            package = re.search(r"packageName=([^\s]+)", line)
            if package and current_display is not None:
                result.setdefault(package.group(1), [])
                if current_display not in result[package.group(1)]:
                    result[package.group(1)].append(current_display)
        return result

    def _parse_logical_displays(
        self,
        text: str,
        sf_by_name: dict[str, str],
        task_by_display: dict[int, dict[str, str]],
    ) -> list[AndroidDisplayInfo]:
        logical = text.split("Logical Displays:", 1)
        if len(logical) != 2:
            return []
        section = logical[1].split("Display Power Controllers:", 1)[0]
        infos: list[AndroidDisplayInfo] = []
        for match in re.finditer(r"\n\s*Display\s+(\d+):", section):
            display_id = int(match.group(1))
            next_match = re.search(r"\n\s*Display\s+\d+:", section[match.end() :])
            end = match.end() + next_match.start() if next_match else len(section)
            block = section[match.start() : end]
            base = re.search(r'mBaseDisplayInfo=DisplayInfo\{"([^"]+)",\s*displayId\s+\d+,[^\n]+', block)
            if not base:
                continue
            line = base.group(0)
            name = base.group(1)
            size_match = re.search(r"real\s+(\d+)\s+x\s+(\d+)", line)
            kind_match = re.search(r"type\s+([A-Z]+)", line)
            unique_match = re.search(r'uniqueId\s+"([^"]+)"', line)
            task = task_by_display.get(display_id, {})
            infos.append(
                AndroidDisplayInfo(
                    display_id=display_id,
                    width=int(size_match.group(1)) if size_match else None,
                    height=int(size_match.group(2)) if size_match else None,
                    kind=kind_match.group(1).lower() if kind_match else "",
                    name=name,
                    unique_id=unique_match.group(1) if unique_match else "",
                    can_host_tasks="canHostTasks true" in line or display_id in task_by_display,
                    has_content=bool(task.get("top_activity")),
                    top_activity=str(task.get("top_activity", "")),
                    surfaceflinger_id=sf_by_name.get(name),
                )
            )
        return sorted(infos, key=lambda item: item.display_id)

    def _fallback_parse_display_devices(
        self,
        text: str,
        sf_by_name: dict[str, str],
        task_by_display: dict[int, dict[str, str]],
    ) -> list[AndroidDisplayInfo]:
        infos: list[AndroidDisplayInfo] = []
        for line in text.splitlines():
            if "DisplayDeviceInfo" not in line:
                continue
            name_match = re.search(r'\{"([^"]+)"', line)
            id_match = re.search(r"displayId\s+(\d+)", line)
            if not name_match or not id_match:
                continue
            display_id = int(id_match.group(1))
            size_match = re.search(r"(\d+)\s+x\s+(\d+)", line)
            unique_match = re.search(r'uniqueId="([^"]+)"', line)
            type_match = re.search(r"type\s+([A-Z]+)", line)
            task = task_by_display.get(display_id, {})
            name = name_match.group(1)
            infos.append(
                AndroidDisplayInfo(
                    display_id=display_id,
                    width=int(size_match.group(1)) if size_match else None,
                    height=int(size_match.group(2)) if size_match else None,
                    kind=type_match.group(1).lower() if type_match else "",
                    name=name,
                    unique_id=unique_match.group(1) if unique_match else "",
                    can_host_tasks=display_id in task_by_display,
                    has_content=bool(task.get("top_activity")),
                    top_activity=str(task.get("top_activity", "")),
                    surfaceflinger_id=sf_by_name.get(name),
                )
            )
        return sorted(infos, key=lambda item: item.display_id)

    def _parse_stack_displays(self, text: str) -> dict[int, dict[str, str]]:
        result: dict[int, dict[str, str]] = {}
        current: int | None = None
        for line in text.splitlines():
            root = re.search(r"RootTask .* displayId=(\d+)", line)
            if root:
                current = int(root.group(1))
                result.setdefault(current, {})
                continue
            if current is None:
                continue
            top = re.search(r"topActivity=ComponentInfo\{([^}]+)\}", line)
            if top:
                result.setdefault(current, {})["top_activity"] = top.group(1)
        return result

    def _parse_surfaceflinger_displays(self, text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in text.splitlines():
            match = re.search(r'Display\s+(\d+).*displayName="([^"]+)"', line)
            if match:
                result[match.group(2)] = match.group(1)
        return result

    def is_keyboard_visible(self) -> bool:
        proc = self.shell("dumpsys", "input_method", timeout=10)
        text = proc.stdout + proc.stderr
        return "mInputShown=true" in text or "mImeWindowVis=3" in text

    def settle(self, seconds: float) -> None:
        time.sleep(seconds)
