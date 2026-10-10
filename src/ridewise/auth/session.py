"""
Session state and identity management for RideWise Assist.

SECURITY PRINCIPLE: Customer identity is established once at login via a
trusted database lookup. It is stored in server-side session state and NEVER
re-read from free-form user messages. Every tool call that touches customer
data uses the session's customer_id, not anything the user typed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SessionRole(str, Enum):
    CUSTOMER = "customer"
    AGENT = "agent"
    NONE = "none"


@dataclass
class SessionState:
    """Immutable-ish session context passed to every tool and workflow step."""

    role: SessionRole = SessionRole.NONE
    # Set once at login; never overwritten by user messages
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    customer_tier: Optional[str] = None
    # Agent identification (display only, not used for data access control)
    agent_id: Optional[str] = None
    # Conversation history: list of {"role": "user"|"assistant", "content": str}
    history: list[dict] = field(default_factory=list)

    @property
    def is_customer(self) -> bool:
        return self.role == SessionRole.CUSTOMER

    @property
    def is_agent(self) -> bool:
        return self.role == SessionRole.AGENT

    @property
    def is_authenticated(self) -> bool:
        if self.role == SessionRole.CUSTOMER:
            return self.customer_id is not None
        if self.role == SessionRole.AGENT:
            return True  # agent sessions are pre-authenticated in this scope
        return False

    def add_message(self, role: str, content: str) -> None:
        """Append a message to the conversation history."""
        self.history.append({"role": role, "content": content})

    def clear_history(self) -> None:
        self.history.clear()

    def to_dict(self) -> dict:
        return {
            "role": self.role.value,
            "customer_id": self.customer_id,
            "customer_name": self.customer_name,
            "customer_tier": self.customer_tier,
            "agent_id": self.agent_id,
        }
