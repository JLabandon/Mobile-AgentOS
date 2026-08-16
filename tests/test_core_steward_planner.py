from __future__ import annotations

import json
from pathlib import Path

from mobile_agent_os.agents import AppConfig, SubTask
from mobile_agent_os.benchmark.loaders import load_task_plans
from mobile_agent_os.report import RunReporter
from mobile_agent_os.runtime_requests import AgentRunResult, RuntimeInformationResponse
from mobile_agent_os.steward import StewardAgent
from mobile_agent_os.task_plan import TaskPlan


class FakeLlm:
    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    def raw_chat(self, *, system: str, user: str, max_tokens: int = 900) -> str:
        self.prompts.append((system, user))
        return json.dumps(
            {
                "subtasks": [
                    {
                        "agent_name": "calendar",
                        "instruction": "Create the requested event and request missing email details at runtime.",
                        "max_steps": 8,
                        "expected_visible_terms": ["Investor Check-in"],
                        "not_visible_at_finish": ["Save"],
                    }
                ],
                "edges": [["calendar", "gmail"]],
                "information_flows": [
                    {
                        "from_agent": "gmail",
                        "to_agent": "calendar",
                        "name": "meeting_details",
                        "required": True,
                        "delivery": "on_source_done",
                        "content_contract": {"fields": ["location", "notes"]},
                    }
                ],
                "reason": "Calendar creates event; Gmail provides details.",
            }
        )

    def parse_json_content(self, content: str) -> dict[str, object]:
        return json.loads(content)


class FakeAgent:
    def __init__(self, config: AppConfig, llm: FakeLlm) -> None:
        self.config = config
        self.llm = llm
        self.available_peers = []


def make_agents(llm: FakeLlm) -> dict[str, FakeAgent]:
    return {
        "calendar": FakeAgent(
            AppConfig(
                name="calendar",
                label="Google Calendar",
                package_candidates=["com.google.android.calendar"],
                launch={"mode": "launcher"},
                capabilities=("create_calendar_event",),
                description="Calendar and scheduling app.",
            ),
            llm,
        ),
        "gmail": FakeAgent(
            AppConfig(
                name="gmail",
                label="Gmail",
                package_candidates=["com.google.android.gm"],
                launch={"mode": "launcher"},
                capabilities=("search_email", "retrieve_information"),
                description="Email app for meeting details.",
            ),
            llm,
        ),
    }


def test_dynamic_steward_plan_uses_goal_and_app_profiles_not_hidden_oracle(tmp_path: Path) -> None:
    llm = FakeLlm()
    task = TaskPlan(
        task_id="gmail_event",
        goal="Schedule an Investor Check-in event using meeting details from email.",
        mode="steward_serial",
        success_criteria={"visible_terms": ["SecretHiddenOracle"]},
        environment={"fixture_email": "SecretHiddenOracle is present in Gmail."},
    )
    steward = StewardAgent(make_agents(llm), RunReporter(tmp_path), {task.task_id: task}, mode="steward_serial")  # type: ignore[arg-type]

    plan = steward.plan(task.task_id)

    assert [subtask.agent_name for subtask in plan.subtasks] == ["calendar"]
    assert plan.edges == (("calendar", "gmail"),)
    assert plan.information_flows[0].from_agent == "gmail"
    assert plan.information_flows[0].to_agent == "calendar"
    assert plan.information_flows[0].fields == ("location", "notes")
    _, user_prompt = llm.prompts[0]
    assert "Schedule an Investor Check-in" in user_prompt
    assert "Google Calendar" in user_prompt
    assert "Gmail" in user_prompt
    assert "SecretHiddenOracle" not in user_prompt


def test_planner_prompt_uses_upfront_app_decomposition_for_baseline_and_agentos(tmp_path: Path) -> None:
    task = TaskPlan(
        task_id="gmail_event",
        goal="Schedule an Investor Check-in event using meeting details from email.",
        mode="steward_serial",
    )
    serial_llm = FakeLlm()
    agentos_llm = FakeLlm()

    serial_plan = StewardAgent(make_agents(serial_llm), RunReporter(tmp_path / "serial"), {task.task_id: task}, mode="steward_serial").plan(task.task_id)  # type: ignore[arg-type]
    agentos_plan = StewardAgent(make_agents(agentos_llm), RunReporter(tmp_path / "agentos"), {task.task_id: task}, mode="agentos_parallel").plan(task.task_id)  # type: ignore[arg-type]

    assert serial_plan.edges == agentos_plan.edges
    serial_system, serial_user = serial_llm.prompts[0]
    agentos_system, agentos_user = agentos_llm.prompts[0]
    assert "MobileSteward-style upfront app decomposition" in serial_system
    assert "schedule information-source agents as top-level subtasks" in serial_user
    assert "MobileSteward-style upfront app decomposition" in agentos_system
    assert "provider and requester apps" in agentos_user


def test_multidisplay_planner_uses_upfront_decomposition_without_steward_forwarding(tmp_path: Path) -> None:
    task = TaskPlan(
        task_id="gmail_event",
        goal="Schedule an Investor Check-in event using meeting details from email.",
        mode="agentos_parallel",
    )
    llm = FakeLlm()

    StewardAgent(make_agents(llm), RunReporter(tmp_path), {task.task_id: task}, mode="agentos_parallel").plan(task.task_id)  # type: ignore[arg-type]

    system, user = llm.prompts[0]
    assert "MobileSteward-style upfront app decomposition" in system
    assert "AgentOS runtime will schedule all recruited AppAgents" in system
    assert "information_flows" in system
    assert "provider and requester apps" in user
    assert "do not schedule it as a top-level subtask" not in user


class FakeRunAgent:
    def __init__(self, config: AppConfig, reporter: RunReporter, llm: FakeLlm) -> None:
        self.config = config
        self.reporter = reporter
        self.llm = llm
        self.available_peers = []
        self.received_information: list[RuntimeInformationResponse] = []

    def run(self, subtask, run_dir: Path) -> AgentRunResult:  # noqa: ANN001
        if self.config.name == "keep":
            self.reporter.event(
                "agent_step",
                agent="keep_agent",
                step=1,
                status="finished",
                visible_texts=["Meeting note", "Location: Googleplex", "Room: 301"],
            )
        return AgentRunResult(status="finished")

    def receive_information(self, response: RuntimeInformationResponse) -> None:
        self.received_information.append(response)


def test_steward_serial_forwards_upstream_visible_result_to_downstream_agent(tmp_path: Path) -> None:
    reporter = RunReporter(tmp_path)
    llm = FakeLlm()
    keep = FakeRunAgent(make_agents(llm)["gmail"].config, reporter, llm)
    keep.config = AppConfig(
        name="keep",
        label="Google Keep",
        package_candidates=["com.google.android.keep"],
        launch={"mode": "launcher"},
        capabilities=("retrieve_information",),
        description="Notes app.",
    )
    calendar = FakeRunAgent(make_agents(llm)["calendar"].config, reporter, llm)
    plan = TaskPlan(
        task_id="keep_then_calendar",
        goal="Schedule a meeting using note details.",
        mode="steward_serial",
        subtasks=(
            SubTask(agent_name="keep", instruction="Find note details."),
            SubTask(agent_name="calendar", instruction="Create event using upstream information."),
        ),
        edges=(("keep", "calendar"),),
        information_flows=(),
    )
    steward = StewardAgent({"keep": keep, "calendar": calendar}, reporter, {plan.task_id: plan}, mode="steward_serial")  # type: ignore[arg-type]

    assert steward.run_plan(plan, tmp_path)
    assert calendar.received_information
    assert "Location: Googleplex" in calendar.received_information[0].information


def test_task_loader_parses_information_flows(tmp_path: Path) -> None:
    task_path = tmp_path / "tasks.json"
    task_path.write_text(
        """
{
  "calendar_gmail": {
    "goal": "Create event abc123",
    "edges": [["gmail", "calendar"]],
    "information_flows": [
      {
        "from_agent": "gmail",
        "to_agent": "calendar",
        "name": "meeting_details",
        "required": true,
        "delivery": "on_source_done",
        "content_contract": {"fields": ["location", "agenda"]}
      }
    ]
  }
}
""".strip(),
        encoding="utf-8",
    )

    plans = load_task_plans(task_path, runtime="agentos_parallel")
    flow = plans["calendar_gmail"].information_flows[0]

    assert flow.from_agent == "gmail"
    assert flow.to_agent == "calendar"
    assert flow.name == "meeting_details"
    assert flow.fields == ("location", "agenda")


def test_task_loader_applies_run_variables(tmp_path: Path) -> None:
    task_path = tmp_path / "tasks.json"
    task_path.write_text(
        """
{
  "calendar_gmail": {
    "goal": "Create event {run_id}",
    "success_criteria": {
      "visible_terms": ["Create event {run_id}"]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    plans = load_task_plans(task_path, runtime="agentos_parallel", variables={"run_id": "abc123"})

    assert plans["calendar_gmail"].goal == "Create event abc123"
    assert plans["calendar_gmail"].success_criteria["visible_terms"] == ["Create event abc123"]
