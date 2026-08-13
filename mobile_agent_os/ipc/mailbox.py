from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque

from ..runtime_requests import RuntimeInformationRequest, RuntimeInformationResponse, RuntimeOperationRequest, RuntimeOperationResponse


@dataclass(frozen=True)
class MailboxMessage:
    kind: str
    payload: RuntimeInformationRequest | RuntimeInformationResponse | RuntimeOperationRequest | RuntimeOperationResponse
    created_at: str

    @property
    def to_agent(self) -> str:
        return self.payload.to_agent


class AgentMailbox:
    def __init__(self) -> None:
        self._queues: dict[str, Deque[MailboxMessage]] = defaultdict(deque)

    def enqueue_request(self, request: RuntimeInformationRequest) -> None:
        self._queues[request.to_agent].append(
            MailboxMessage(
                kind="RuntimeInformationRequest",
                payload=request,
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
        )

    def enqueue_message(self, message: MailboxMessage) -> None:
        self._queues[message.to_agent].append(message)

    def enqueue_response(self, response: RuntimeInformationResponse) -> None:
        self._queues[response.to_agent].append(
            MailboxMessage(
                kind="RuntimeInformationResponse",
                payload=response,
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
        )

    def enqueue_operation_request(self, request: RuntimeOperationRequest) -> None:
        self._queues[request.to_agent].append(
            MailboxMessage(
                kind="RuntimeOperationRequest",
                payload=request,
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
        )

    def enqueue_operation_response(self, response: RuntimeOperationResponse) -> None:
        self._queues[response.to_agent].append(
            MailboxMessage(
                kind="RuntimeOperationResponse",
                payload=response,
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
        )

    def dequeue(self, agent_name: str) -> MailboxMessage | None:
        queue = self._queues.get(agent_name)
        if not queue:
            return None
        return queue.popleft()

    def has_messages(self, agent_name: str) -> bool:
        return bool(self._queues.get(agent_name))

    def pending_count(self, agent_name: str | None = None) -> int:
        if agent_name is not None:
            return len(self._queues.get(agent_name, ()))
        return sum(len(queue) for queue in self._queues.values())

    def pending_summary(self) -> dict[str, int]:
        return {agent: len(queue) for agent, queue in self._queues.items() if queue}
