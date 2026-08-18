from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..android.adb import AdbClient


@dataclass(frozen=True)
class DemoAgent:
    name: str
    app_label: str
    package: str
    display_id: int
    surfaceflinger_id: str | None = None
    description: str = ""
    capabilities: tuple[str, ...] = ()
    long_term_memory: tuple[str, ...] = ()


def capture_agent_screen(adb: AdbClient, agent: DemoAgent, out_path: Path) -> Path:
    if agent.display_id == 0:
        return adb.screenshot(out_path)
    if agent.surfaceflinger_id:
        return adb.screenshot_display(agent.surfaceflinger_id, out_path)
    return adb.screenshot_display(agent.display_id, out_path)


def snap_to_button_center(screenshot: Path, x: int, y: int) -> tuple[int, int, str]:
    image = Image.open(screenshot).convert("RGB")
    width, height = image.size
    pixels = image.load()
    rows: list[tuple[int, int, int, int]] = []
    for yy in range(0, height, 2):
        xs = [
            xx
            for xx in range(0, width, 2)
            if 170 <= pixels[xx, yy][0] <= 235
            and 170 <= pixels[xx, yy][1] <= 235
            and 170 <= pixels[xx, yy][2] <= 235
            and max(pixels[xx, yy]) - min(pixels[xx, yy]) < 12
        ]
        if len(xs) * 2 >= width * 0.35:
            rows.append((yy, min(xs), max(xs), len(xs) * 2))
    candidates: list[tuple[int, int, int, int]] = []
    start = None
    left = width
    right = 0
    previous = -10
    for yy, row_left, row_right, _count in rows:
        if start is None or yy - previous > 4:
            if start is not None and previous - start >= 35:
                candidates.append((left, start, right, previous))
            start = yy
            left = row_left
            right = row_right
        else:
            left = min(left, row_left)
            right = max(right, row_right)
        previous = yy
    if start is not None and previous - start >= 35:
        candidates.append((left, start, right, previous))
    candidates = [
        item for item in candidates
        if item[2] - item[0] >= width * 0.45 and 35 <= item[3] - item[1] <= 140
    ]
    containing = [item for item in candidates if item[0] <= x <= item[2] and item[1] <= y <= item[3]]
    if containing or not candidates:
        return x, y, "unchanged"
    horizontal = [item for item in candidates if item[0] <= x <= item[2]]
    pool = horizontal or candidates
    nearest = min(pool, key=lambda item: abs(((item[1] + item[3]) // 2) - y))
    snapped = ((nearest[0] + nearest[2]) // 2, (nearest[1] + nearest[3]) // 2)
    return snapped[0], snapped[1], f"snapped_to_button:{nearest}"
