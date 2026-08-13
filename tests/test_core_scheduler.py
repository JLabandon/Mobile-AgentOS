from pathlib import Path

from mobile_agent_os.ipc import AgentMailbox
from mobile_agent_os.runtime_requests import RuntimeInformationRequest, RuntimeInformationResponse


class FakeAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.states = ["READY"]
        self.received = []

    def wait_for_peer(self) -> RuntimeInformationRequest:
        self.states.append("RUNNING")
        request = RuntimeInformationRequest.create(
            from_agent=self.name,
            to_agent="keep_agent",
            need="meeting location",
            context="calendar",
            purpose="finish event",
            resume_instruction="resume",
        )
        self.states.append("WAIT_PEER")
        return request

    def receive_information(self, response: RuntimeInformationResponse) -> None:
        self.received.append(response)
        self.states.append("READY")
        self.states.append("DONE")


def test_ready_wait_peer_ready_done_flow(tmp_path: Path) -> None:
    mailbox = AgentMailbox()
    calendar = FakeAgent("calendar_agent")
    request = calendar.wait_for_peer()
    mailbox.enqueue_request(request)
    assert mailbox.dequeue("keep_agent").payload == request

    response = RuntimeInformationResponse(
        request_id=request.request_id,
        from_agent="keep_agent",
        to_agent="calendar_agent",
        status="success",
        information="Googleplex",
        source_app="Google Keep",
        confidence="high",
    )
    mailbox.enqueue_response(response)
    calendar.receive_information(mailbox.dequeue("calendar_agent").payload)

    assert calendar.states == ["READY", "RUNNING", "WAIT_PEER", "READY", "DONE"]
