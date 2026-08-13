from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .report import RunReporter
from .task_plan import TaskPlan


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool
    message: str
    visible_terms: tuple[str, ...]
    missing_terms: tuple[str, ...]
    forbidden_terms: tuple[str, ...]
    present_forbidden_terms: tuple[str, ...]


def evaluate_hidden_success(plan: TaskPlan, reporter: RunReporter) -> EvaluationResult:
    criteria = plan.success_criteria or {}
    visible_terms = tuple(str(term) for term in criteria.get("visible_terms", []) if str(term).strip())
    forbidden_terms = tuple(str(term) for term in criteria.get("not_visible_at_finish", []) if str(term).strip())
    if not visible_terms and not forbidden_terms:
        return EvaluationResult(
            passed=True,
            message="no hidden success criteria configured",
            visible_terms=(),
            missing_terms=(),
            forbidden_terms=(),
            present_forbidden_terms=(),
        )

    final_texts = _final_visible_texts_by_agent(reporter.events)
    haystack = "\n".join(text for texts in final_texts.values() for text in texts).lower()
    normalized_final_texts = {text.strip().lower() for texts in final_texts.values() for text in texts}
    missing = tuple(term for term in visible_terms if term.lower() not in haystack)
    present_forbidden = tuple(term for term in forbidden_terms if term.lower() in normalized_final_texts)
    passed = not missing and not present_forbidden
    parts = []
    if missing:
        parts.append(f"missing visible terms: {list(missing)}")
    if present_forbidden:
        parts.append(f"forbidden terms visible at finish: {list(present_forbidden)}")
    message = "; ".join(parts) if parts else "hidden success criteria verified"
    return EvaluationResult(
        passed=passed,
        message=message,
        visible_terms=visible_terms,
        missing_terms=missing,
        forbidden_terms=forbidden_terms,
        present_forbidden_terms=present_forbidden,
    )


def record_hidden_evaluation(plan: TaskPlan, reporter: RunReporter) -> EvaluationResult:
    result = evaluate_hidden_success(plan, reporter)
    reporter.event(
        "hidden_evaluation",
        task=plan.task_id,
        passed=result.passed,
        message=result.message,
        visible_terms=list(result.visible_terms),
        missing_terms=list(result.missing_terms),
        forbidden_terms=list(result.forbidden_terms),
        present_forbidden_terms=list(result.present_forbidden_terms),
    )
    return result


def _final_visible_texts_by_agent(events: list[dict[str, Any]]) -> dict[str, list[str]]:
    final_texts: dict[str, list[str]] = {}
    for event in events:
        if event.get("kind") not in {"agent_step", "post_action_completion_check"}:
            continue
        agent = event.get("agent")
        if not isinstance(agent, str):
            continue
        texts = event.get("visible_texts")
        if isinstance(texts, list):
            final_texts[agent] = [str(text) for text in texts]
    return final_texts
