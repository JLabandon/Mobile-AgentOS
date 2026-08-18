from mobile_agent_os.message_layer import AgentMailbox
from mobile_agent_os.message_layer.messages import RuntimeInformationRequest, RuntimeInformationResponse


def test_mailbox_routes_by_target_agent() -> None:
    mailbox = AgentMailbox()
    request = RuntimeInformationRequest.create(
        from_agent="calendar_agent",
        to_agent="keep_agent",
        need="meeting location",
        context="event creation",
        purpose="fill event",
        resume_instruction="continue after answer",
    )
    mailbox.enqueue_request(request)

    assert mailbox.dequeue("calendar_agent") is None
    message = mailbox.dequeue("keep_agent")
    assert message is not None
    assert message.kind == "RuntimeInformationRequest"
    assert message.payload == request


def test_mailbox_delivers_response() -> None:
    mailbox = AgentMailbox()
    response = RuntimeInformationResponse(
        request_id="req_1",
        from_agent="keep_agent",
        to_agent="calendar_agent",
        status="success",
        information="Googleplex",
        source_app="Google Keep",
        confidence="high",
    )
    mailbox.enqueue_response(response)

    message = mailbox.dequeue("calendar_agent")
    assert message is not None
    assert message.kind == "RuntimeInformationResponse"
    assert message.payload.information == "Googleplex"
