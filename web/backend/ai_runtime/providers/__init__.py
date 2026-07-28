"""Provider adapter catalogue."""

from .anthropic_messages import AnthropicMessagesAdapter
from .base import ProviderAdapter, ProviderRequest, ProviderTransientError, ContextOverflowError
from .google_genai import GoogleGenAIAdapter
from .openai_chat import OpenAIChatAdapter
from .openai_responses import OpenAIResponsesAdapter

ADAPTERS = {
    "openai_responses": OpenAIResponsesAdapter,
    "openai_chat": OpenAIChatAdapter,
    "anthropic_messages": AnthropicMessagesAdapter,
    "google_genai": GoogleGenAIAdapter,
}

__all__ = [
    "ADAPTERS",
    "AnthropicMessagesAdapter",
    "ContextOverflowError",
    "GoogleGenAIAdapter",
    "OpenAIChatAdapter",
    "OpenAIResponsesAdapter",
    "ProviderAdapter",
    "ProviderRequest",
    "ProviderTransientError",
]
