from __future__ import annotations

from mobile_agent_os.evaluator import evaluate_hidden_success
from mobile_agent_os.report import RunReporter
from mobile_agent_os.task_plan import TaskPlan


def test_hidden_evaluator_uses_final_visible_texts_by_agent(tmp_path) -> None:
    reporter = RunReporter(tmp_path)
    reporter.event("agent_step", agent="calendar_agent", visible_texts=["Save", "Draft"])
    reporter.event("agent_step", agent="calendar_agent", visible_texts=["Investor Check-in", "Googleplex"])
    reporter.event("agent_step", agent="gmail_agent", visible_texts=["roadmap review"])
    plan = TaskPlan(
        task_id="calendar_gmail",
        goal="Schedule an event from email.",
        mode="agentos_parallel",
        success_criteria={
            "visible_terms": ["Investor Check-in", "Googleplex", "roadmap"],
            "not_visible_at_finish": ["Save"],
        },
    )

    result = evaluate_hidden_success(plan, reporter)

    assert result.passed
    assert result.missing_terms == ()
    assert result.present_forbidden_terms == ()


def test_hidden_evaluator_reports_missing_terms(tmp_path) -> None:
    reporter = RunReporter(tmp_path)
    reporter.event("agent_step", agent="calendar_agent", visible_texts=["Investor Check-in"])
    plan = TaskPlan(
        task_id="calendar_gmail",
        goal="Schedule an event from email.",
        mode="agentos_parallel",
        success_criteria={"visible_terms": ["Investor Check-in", "Googleplex"]},
    )

    result = evaluate_hidden_success(plan, reporter)

    assert not result.passed
    assert result.missing_terms == ("Googleplex",)


def test_hidden_evaluator_ignores_observe_only_texts(tmp_path) -> None:
    reporter = RunReporter(tmp_path)
    reporter.event("display_observe", agent="calendar_agent", visible_texts=["Investor Check-in", "Googleplex", "roadmap"])
    plan = TaskPlan(
        task_id="calendar_gmail",
        goal="Schedule an event from email.",
        mode="agentos_parallel",
        success_criteria={"visible_terms": ["Investor Check-in", "Googleplex"]},
    )

    result = evaluate_hidden_success(plan, reporter)

    assert not result.passed
    assert result.missing_terms == ("Investor Check-in", "Googleplex")
