from __future__ import annotations

from mobile_agent_os.actions import AgentAction
from mobile_agent_os.display import DisplaySlot
from mobile_agent_os.guards import ActionGuard, risk_level_for_action
from mobile_agent_os.resources import ResourceManager, ResourceSpec
from mobile_agent_os.snapshots import ObservationSnapshot, PendingAction


def test_resource_manager_acquire_release_and_conflict() -> None:
    manager = ResourceManager([ResourceSpec("display_input:1", capacity=1)])
    manager.acquire("calendar_agent", ["display_input:1"], reason="click")

    ok, reason = manager.can_acquire("gmail_agent", ["display_input:1"])
    assert not ok
    assert "calendar_agent" in reason

    manager.release_agent("calendar_agent", reason="done")
    ok, _ = manager.can_acquire("gmail_agent", ["display_input:1"])
    assert ok


def test_action_guard_fast_pass_and_owner_failure() -> None:
    snapshot = ObservationSnapshot.create(
        agent="calendar_agent",
        display_id=3,
        app_package="com.example.calendar",
        visible_text="Add title",
    )
    action = AgentAction(action="click", target_text="Add title")
    pending = PendingAction(
        action_id="act_1",
        decision_id="dec_1",
        agent="calendar_agent",
        action=action,
        snapshot_id=snapshot.snapshot_id,
        risk_level=risk_level_for_action(action),
    )
    guard = ActionGuard()

    passed = guard.check(pending, snapshot, DisplaySlot(display_id=3, owner_agent="calendar_agent", app_package="com.example.calendar"))
    assert passed.passed
    assert passed.mode == "fast"

    failed = guard.check(pending, snapshot, DisplaySlot(display_id=3, owner_agent="gmail_agent", app_package="com.example.calendar"))
    assert not failed.passed
    assert failed.reason == "display_owner_changed"


def test_high_risk_action_requires_fresh_snapshot() -> None:
    snapshot = ObservationSnapshot.create(
        agent="mail_agent",
        display_id=1,
        app_package="com.example.mail",
        visible_text="Send",
    )
    action = AgentAction(action="click", target_text="Send", reason="send the email")
    pending = PendingAction(
        action_id="act_2",
        decision_id="dec_2",
        agent="mail_agent",
        action=action,
        snapshot_id=snapshot.snapshot_id,
        risk_level=risk_level_for_action(action),
    )
    fresh = ActionGuard().check(pending, snapshot, DisplaySlot(display_id=1, owner_agent="mail_agent", app_package="com.example.mail"))

    assert fresh.passed
    assert fresh.mode == "full_observe"

    stale = ActionGuard(max_snapshot_age_ms=-1).check(pending, snapshot, DisplaySlot(display_id=1, owner_agent="mail_agent", app_package="com.example.mail"))
    assert not stale.passed
    assert stale.mode == "full_observe_required"
