from __future__ import annotations

from mobile_agent_os.adb import AdbClient


def test_parse_logical_displays_from_android36_dumpsys_shape() -> None:
    adb = AdbClient(adb_path="/unused")
    dumpsys_display = """
Logical Displays: size=2
  Display 0:
    mBaseDisplayInfo=DisplayInfo{"Built-in Screen", displayId 0, real 1080 x 2424, type INTERNAL, uniqueId "local:abc", canHostTasks true}
  Display 11:
    mBaseDisplayInfo=DisplayInfo{"vd-exp-0", displayId 11, real 720 x 1280, type VIRTUAL, uniqueId "virtual:pkg,uid,vd-exp-0,9", canHostTasks true}
Display Power Controllers:
"""
    sf_by_name = {
        "Built-in Screen": "4619827259835644672",
        "vd-exp-0": "11529215049183856994",
    }
    task_by_display = {
        11: {"top_activity": "com.google.android.keep/com.google.android.keep.activities.BrowseActivity"},
    }

    displays = adb._parse_logical_displays(dumpsys_display, sf_by_name, task_by_display)

    assert [display.display_id for display in displays] == [0, 11]
    assert displays[0].kind == "internal"
    assert displays[1].kind == "virtual"
    assert displays[1].width == 720
    assert displays[1].height == 1280
    assert displays[1].surfaceflinger_id == "11529215049183856994"
    assert displays[1].top_activity.startswith("com.google.android.keep/")
