"""Provider-neutral, persistent AI agent runtime.

The package deliberately owns no Volatility implementation details.  Providers,
the agent state machine, context selection, tools, conversations and active runs
are separate modules and are wired together by :mod:`service`.
"""

from .models import (
    AssistantMessage,
    Message,
    ProviderEvent,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from .service import AgentRuntime, get_runtime

__all__ = [
    "AgentRuntime",
    "AssistantMessage",
    "Message",
    "ProviderEvent",
    "ToolResultMessage",
    "Usage",
    "UserMessage",
    "get_runtime",
]
