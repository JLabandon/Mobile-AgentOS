from mobile_agent_os.message_layer import IPCLedger
from mobile_agent_os.report import RunReporter
from mobile_agent_os.message_layer.messages import RuntimeInformationRequest, RuntimeInformationResponse


def test_ipc_ledger_writes_queryable_events(tmp_path) -> None:
    reporter = RunReporter(tmp_path)
    ledger = IPCLedger(reporter, mode="agentos_parallel", via="peer")
    request = RuntimeInformationRequest.create(
        from_agent="calendar_agent",
        to_agent="keep_agent",
        need="meeting location",
        context="event creation",
        purpose="fill event",
        resume_instruction="continue",
    )
    response = RuntimeInformationResponse(
        request_id=request.request_id,
        from_agent="keep_agent",
        to_agent="calendar_agent",
        status="success",
        information="Googleplex",
        source_app="Google Keep",
        confidence="high",
    )

    ledger.request_created(request)
    ledger.request_routed(request)
    ledger.request_received(request)
    ledger.response_created(request, response)
    ledger.response_delivered(request, response)

    events = reporter.query_ipc_ledger(request_id=request.request_id)
    assert [event["status"] for event in events] == ["created", "routed", "received", "success", "delivered"]
    assert reporter.ipc_ledger_path.exists()


def test_ipc_ledger_keeps_long_payloads_out_of_main_ledger(tmp_path) -> None:
    reporter = RunReporter(tmp_path)
    long_text = "meeting detail " * 80

    reporter.ipc_event(
        request_id="req_long",
        message_kind="RuntimeInformationResponse",
        status="delivered",
        from_agent="gmail_agent",
        to_agent="calendar_agent",
        response_summary=long_text,
        evidence=long_text,
    )

    event = reporter.query_ipc_ledger(request_id="req_long")[0]
    assert len(event["response_summary"]) < len(long_text)
    assert event["response_summary"].endswith("…")
    assert event["payload_ref"]
    assert event["evidence_ref"]
    assert "meeting detail" in (tmp_path / "ipc_payloads" / "req_long_response.txt").read_text(encoding="utf-8")
