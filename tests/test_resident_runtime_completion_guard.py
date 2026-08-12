from __future__ import annotations

from mobile_agent_os.agents import AppStaffAgent, SubTask, normalized_match_text
from mobile_agent_os.runtime_requests import RuntimeInformationResponse


def test_runtime_request_instruction_requires_peer_response_before_completion() -> None:
    agent = AppStaffAgent.__new__(AppStaffAgent)
    subtask = SubTask(
        agent_name="calendar",
        instruction="Create an event. Before saving, request the missing details with REQUEST_INFORMATION.",
    )

    assert agent._requires_runtime_response(subtask)


def test_plain_instruction_does_not_require_peer_response() -> None:
    agent = AppStaffAgent.__new__(AppStaffAgent)
    subtask = SubTask(agent_name="calendar", instruction="Create a simple event with the provided title.")

    assert not agent._requires_runtime_response(subtask)


def test_salient_received_information_preserves_time_values() -> None:
    agent = AppStaffAgent.__new__(AppStaffAgent)
    agent.received_information = [
        RuntimeInformationResponse(
            request_id="req_1",
            from_agent="keep_agent",
            to_agent="clock_agent",
            status="success",
            information="Location: Googleplex; 4:00 PM",
            source_app="Google Keep",
            confidence="high",
        )
    ]

    assert agent._salient_received_information_terms() == ["Googleplex", "4:00 PM"]


def test_salient_received_information_handles_nested_key_value() -> None:
    agent = AppStaffAgent.__new__(AppStaffAgent)
    agent.received_information = [
        RuntimeInformationResponse(
            request_id="req_1",
            from_agent="keep_agent",
            to_agent="calendar_agent",
            status="success",
            information="Title: Agent IPC MVP Meeting; Location: Googleplex; Description: Location: Googleplex",
            source_app="Google Keep",
            confidence="high",
        )
    ]

    assert agent._salient_received_information_terms() == ["Agent IPC MVP Meeting", "Googleplex"]


def test_salient_received_information_ignores_negative_absence_notes() -> None:
    agent = AppStaffAgent.__new__(AppStaffAgent)
    agent.received_information = [
        RuntimeInformationResponse(
            request_id="req_1",
            from_agent="keep_agent",
            to_agent="calendar_agent",
            status="success",
            information="Title: Agent IPC MVP Meeting; Location: Googleplex; Time and description not found in note",
            source_app="Google Keep",
            confidence="medium",
        )
    ]

    assert agent._salient_received_information_terms() == ["Agent IPC MVP Meeting", "Googleplex"]


def test_salient_received_information_ignores_email_metadata_date() -> None:
    agent = AppStaffAgent.__new__(AppStaffAgent)
    agent.received_information = [
        RuntimeInformationResponse(
            request_id="req_1",
            from_agent="gmail_agent",
            to_agent="calendar_agent",
            status="success",
            information="Title: Investor Check-in; Date: Aug 11; Location: Googleplex; Agenda: roadmap review",
            source_app="Gmail",
            confidence="medium",
        )
    ]

    assert agent._salient_received_information_terms() == ["Investor Check-in", "Googleplex"]


def test_salient_received_information_ignores_supporting_address() -> None:
    agent = AppStaffAgent.__new__(AppStaffAgent)
    agent.received_information = [
        RuntimeInformationResponse(
            request_id="req_1",
            from_agent="maps_agent",
            to_agent="calendar_agent",
            status="success",
            information="Place name: Googleplex; Address: Amphitheatre Parkway, Mountain View, CA",
            source_app="Google Maps",
            confidence="high",
        )
    ]

    assert agent._salient_received_information_terms() == ["Googleplex"]


def test_salient_received_information_ignores_decision_support_sentence() -> None:
    agent = AppStaffAgent.__new__(AppStaffAgent)
    agent.received_information = [
        RuntimeInformationResponse(
            request_id="req_1",
            from_agent="maps_agent",
            to_agent="calendar_agent",
            status="success",
            information="Yes, Googleplex is a real searchable place.",
            source_app="Google Maps",
            confidence="high",
        )
    ]

    assert agent._salient_received_information_terms() == []


def test_normalized_match_text_ignores_punctuation() -> None:
    assert normalized_match_text("Wake-up time: 4:00 PM")
    assert "wake up time" in normalized_match_text("Wake-up time: 4:00 PM")
