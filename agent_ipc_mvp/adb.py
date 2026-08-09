from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path


DEFAULT_ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
REMOTE_UI = "/sdcard/window_dump.xml"


class AdbError(RuntimeError):
    pass


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

    def force_stop(self, package_name: str) -> subprocess.CompletedProcess[str]:
        return self.shell("am", "force-stop", package_name, timeout=20)

    def launch_shell(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        command = " ".join(shlex.quote(arg) for arg in args)
        return self.shell(command, check=True, timeout=30)

    def dump_ui(self, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.shell("uiautomator", "dump", REMOTE_UI, timeout=20)
        proc = self.run("pull", REMOTE_UI, str(out_path), timeout=20)
        if proc.returncode != 0 or not out_path.exists():
            raise AdbError(f"failed to pull UI dump\nstdout={proc.stdout}\nstderr={proc.stderr}")
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

    def tap(self, x: int, y: int) -> subprocess.CompletedProcess[str]:
        return self.shell("input", "tap", str(x), str(y), check=True)

    def input_text(self, text: str) -> subprocess.CompletedProcess[str]:
        # ADB input text treats spaces specially. %s is the documented escape.
        escaped = re.sub(r"\s+", "%s", text)
        escaped = escaped.replace("'", "").replace('"', "")
        return self.shell("input", "text", escaped, check=True)

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

    def back(self) -> subprocess.CompletedProcess[str]:
        return self.shell("input", "keyevent", "BACK", check=True)

    def settle(self, seconds: float) -> None:
        time.sleep(seconds)
