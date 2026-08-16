from pathlib import Path

from mobile_agent_os.report import RunReporter


def test_ui_actions_count_finished_primitive_actions(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    reporter.event("agent_step", action={"action": "click"}, status="finished")
    reporter.event("agent_step", action={"action": "input"}, status="ready")
    reporter.event("agent_step", action={"action": "FINISH"}, status="finished")
    reporter.event("agent_step", action={"action": "REQUEST_INFORMATION"}, status="waiting")
    reporter.state_event("payment_agent", "ACTING", action="click")

    metrics = reporter.metrics(task="x", runtime="test", success=True)

    assert metrics["adb_ui_actions"] == 3
