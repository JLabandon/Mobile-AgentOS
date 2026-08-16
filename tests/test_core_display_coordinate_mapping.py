from types import SimpleNamespace

from mobile_agent_os.agents import AppStaffAgent


class FakeAdb:
    def display_size(self, display_id: int) -> tuple[int, int]:
        return (1080, 2424)


class FakeReporter:
    def __init__(self) -> None:
        self.events = []

    def event(self, kind: str, **payload: object) -> None:
        self.events.append((kind, payload))


def test_display_zero_uses_uiautomator_coordinates_without_scaling() -> None:
    agent = AppStaffAgent.__new__(AppStaffAgent)
    agent.adb = FakeAdb()

    assert agent._map_node_point_to_display(0, 196, 509, []) == (196, 509)


def test_nonzero_display_can_scale_from_node_coordinate_space() -> None:
    agent = AppStaffAgent.__new__(AppStaffAgent)
    agent.adb = FakeAdb()
    agent.config = SimpleNamespace(name="calendar")
    agent.reporter = FakeReporter()
    node = SimpleNamespace(bounds=SimpleNamespace(right=540, bottom=1212))

    assert agent._map_node_point_to_display(7, 270, 606, [node]) == (540, 1212)
