from subprocess import CompletedProcess

from agent_ipc_mvp.adb import AdbClient
from agent_ipc_mvp.agents import AppConfig, AppStaffAgent, SubTask


class FakeReporter:
    def event(self, *args, **kwargs):
        pass


class FakeLlm:
    pass


class FakeAdb(AdbClient):
    def __init__(self):
        super().__init__()
        self.launched_args = None

    def pick_package(self, candidates):
        return "com.google.android.calendar"

    def force_stop(self, package_name):
        return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def launch_shell(self, args):
        self.launched_args = args
        return CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def foreground_package(self):
        return "com.google.android.calendar"

    def settle(self, seconds):
        pass


def test_staff_agent_launch_adds_package_to_intent_and_keeps_spaced_extra() -> None:
    adb = FakeAdb()
    config = AppConfig(
        name="calendar",
        label="Calendar",
        package_candidates=["com.google.android.calendar"],
        launch={
            "mode": "intent",
            "args": [
                "am",
                "start",
                "-a",
                "android.intent.action.INSERT",
                "--es",
                "title",
                "Agent IPC MVP Meeting",
            ],
        },
    )
    agent = AppStaffAgent(config=config, adb=adb, llm=FakeLlm(), reporter=FakeReporter())
    agent.launch(SubTask(agent_name="calendar", instruction="test"))
    assert adb.launched_args[:4] == ["am", "start", "-p", "com.google.android.calendar"]
    assert "Agent IPC MVP Meeting" in adb.launched_args


def test_staff_agent_launch_appends_task_specific_args() -> None:
    adb = FakeAdb()
    config = AppConfig(
        name="calendar",
        label="Calendar",
        package_candidates=["com.google.android.calendar"],
        launch={
            "mode": "intent",
            "args": [
                "am",
                "start",
                "-a",
                "android.intent.action.INSERT",
            ],
        },
    )
    task = SubTask(
        agent_name="calendar",
        instruction="test",
        launch_args=("--es", "title", "Agent IPC MVP Meeting"),
    )
    agent = AppStaffAgent(config=config, adb=adb, llm=FakeLlm(), reporter=FakeReporter())
    agent.launch(task)
    assert adb.launched_args[-3:] == ["--es", "title", "Agent IPC MVP Meeting"]
