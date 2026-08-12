from mobile_agent_os.ipc import IPCLedger
from mobile_agent_os.report import RunReporter
from mobile_agent_os.runtime_requests import RuntimeInformationRequest, RuntimeInformationResponse


def test_ipc_ledger_writes_queryable_events(tmp_path) -> None:
    reporter = RunReporter(tmp_path)
    ledger = IPCLedger(reporter, mode="async_single_display", via="peer")
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
