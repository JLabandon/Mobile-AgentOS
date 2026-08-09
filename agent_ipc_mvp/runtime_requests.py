from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import uuid4


@dataclass(frozen=True)
class RuntimeInformationRequest:
    request_id: str
    from_agent: str
    to_agent: str
    need: str
    context: str
    purpose: str
    resume_instruction: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        from_agent: str,
        to_agent: str,
        need: str,
        context: str,
        purpose: str,
        resume_instruction: str,
    ) -> "RuntimeInformationRequest":
        return cls(
            request_id=f"req_{uuid4().hex[:8]}",
            from_agent=from_agent,
            to_agent=to_agent,
            need=need,
            context=context,
            purpose=purpose,
            resume_instruction=resume_instruction,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )


@dataclass(frozen=True)
class RuntimeInformationResponse:
    request_id: str
    from_agent: str
    to_agent: str
    status: Literal["success", "failed"]
    information: str
    source_app: str
    confidence: str
    evidence: str = ""
    limitations: str = ""


@dataclass(frozen=True)
class AgentRunResult:
    status: Literal["finished", "waiting", "failed"]
    request: RuntimeInformationRequest | None = None
    message: str = ""
