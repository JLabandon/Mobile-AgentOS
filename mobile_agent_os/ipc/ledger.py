from __future__ import annotations

from ..report import RunReporter
from ..runtime_requests import (
    RuntimeInformationRequest,
    RuntimeInformationResponse,
    RuntimeOperationRequest,
    RuntimeOperationResponse,
)


class IPCLedger:
    def __init__(self, reporter: RunReporter, *, mode: str, via: str) -> None:
        self.reporter = reporter
        self.mode = mode
        self.via = via

    def request_created(self, request: RuntimeInformationRequest, *, evidence_ref: str = "") -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationRequest",
            status="created",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.need,
            evidence_ref=evidence_ref,
            policy_decision="not_checked",
        )

    def request_routed(self, request: RuntimeInformationRequest) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationRequest",
            status="routed",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.need,
            policy_decision="not_checked",
        )

    def request_queued(self, request: RuntimeInformationRequest) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationRequest",
            status="queued",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.need,
            policy_decision="not_checked",
        )

    def request_accepted(self, request: RuntimeInformationRequest) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationRequest",
            status="accepted",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.need,
            policy_decision="not_checked",
        )

    def request_rejected(self, request: RuntimeInformationRequest, *, reason: str = "") -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationRequest",
            status="rejected",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.need,
            response_summary=reason,
            policy_decision="not_checked",
        )

    def request_timed_out(self, request: RuntimeInformationRequest) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationRequest",
            status="timed_out",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.need,
            policy_decision="not_checked",
        )

    def request_received(self, request: RuntimeInformationRequest) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationRequest",
            status="received",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.need,
            policy_decision="not_checked",
        )

    def response_created(self, request: RuntimeInformationRequest, response: RuntimeInformationResponse, *, evidence_ref: str = "") -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationResponse",
            status=response.status,
            from_agent=response.from_agent,
            to_agent=response.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.need,
            response_summary=response.information or response.limitations,
            evidence=response.evidence,
            evidence_ref=evidence_ref,
            policy_decision="not_checked",
        )

    def response_delivered(self, request: RuntimeInformationRequest, response: RuntimeInformationResponse) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeInformationResponse",
            status="delivered",
            from_agent=response.from_agent,
            to_agent=response.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.need,
            response_summary=response.information,
            evidence=response.evidence,
            policy_decision="not_checked",
        )

    def operation_request_created(self, request: RuntimeOperationRequest, *, evidence_ref: str = "") -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationRequest",
            status="created",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.operation,
            evidence_ref=evidence_ref,
            policy_decision="not_checked",
        )

    def operation_request_routed(self, request: RuntimeOperationRequest) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationRequest",
            status="routed",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.operation,
            policy_decision="not_checked",
        )

    def operation_request_queued(self, request: RuntimeOperationRequest) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationRequest",
            status="queued",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.operation,
            policy_decision="not_checked",
        )

    def operation_request_accepted(self, request: RuntimeOperationRequest) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationRequest",
            status="accepted",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.operation,
            policy_decision="not_checked",
        )

    def operation_request_rejected(self, request: RuntimeOperationRequest, *, reason: str = "") -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationRequest",
            status="rejected",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.operation,
            response_summary=reason,
            policy_decision="not_checked",
        )

    def operation_request_timed_out(self, request: RuntimeOperationRequest) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationRequest",
            status="timed_out",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.operation,
            policy_decision="not_checked",
        )

    def operation_request_received(self, request: RuntimeOperationRequest) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationRequest",
            status="received",
            from_agent=request.from_agent,
            to_agent=request.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.operation,
            policy_decision="not_checked",
        )

    def operation_response_created(
        self,
        request: RuntimeOperationRequest,
        response: RuntimeOperationResponse,
        *,
        evidence_ref: str = "",
    ) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationResponse",
            status=response.status,
            from_agent=response.from_agent,
            to_agent=response.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.operation,
            response_summary=response.result or response.limitations,
            evidence=response.evidence,
            evidence_ref=evidence_ref,
            policy_decision="not_checked",
        )

    def operation_response_delivered(self, request: RuntimeOperationRequest, response: RuntimeOperationResponse) -> None:
        self.reporter.ipc_event(
            request_id=request.request_id,
            message_kind="RuntimeOperationResponse",
            status="delivered",
            from_agent=response.from_agent,
            to_agent=response.to_agent,
            mode=self.mode,
            via=self.via,
            request_summary=request.operation,
            response_summary=response.result,
            evidence=response.evidence,
            policy_decision="not_checked",
        )
