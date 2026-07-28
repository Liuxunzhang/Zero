import asyncio
import json
import os
from types import SimpleNamespace

from web.backend.ai_runtime.context import ContextManager, EvidenceStore, message_groups
from web.backend.ai_runtime.loop import AgentLoop, RunBudget
from web.backend.ai_runtime.models import (
    AssistantMessage,
    TextBlock,
    ToolCallBlock,
    ToolResultMessage,
    TurnSnapshot,
    UserMessage,
)
from web.backend.ai_runtime.providers.anthropic_messages import AnthropicMessagesAdapter
from web.backend.ai_runtime.providers.base import ProviderRequest
from web.backend.ai_runtime.providers.base import ProviderTransientError
from web.backend.ai_runtime.models import ProviderEvent
from web.backend.ai_runtime.providers.google_genai import GoogleGenAIAdapter
from web.backend.ai_runtime.providers.openai_chat import OpenAIChatAdapter
from web.backend.ai_runtime.providers.openai_responses import OpenAIResponsesAdapter
from web.backend.ai_runtime.service import normalize_profile
from web.backend.ai_runtime.storage import (
    ConversationStore,
    CredentialStore,
    EventStore,
    ProviderStateStore,
)
from web.backend.ai_runtime.tools import (
    ResultHandleStore,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
)


class AsyncItems:
    def __init__(self, items):
        self.items = items

    def __aiter__(self):
        self.iterator = iter(self.items)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration:
            raise StopAsyncIteration


def snapshot(protocol, tools=()):
    return TurnSnapshot.create(
        provider=protocol,
        model="test-model",
        protocol=protocol,
        system_prompt="system",
        reasoning_level="off",
        max_output_tokens=128,
        tools=tools,
        context_window=65536,
    )


def collect(adapter, protocol):
    request = ProviderRequest(snapshot(protocol), [UserMessage("hello")])

    async def run():
        return [event async for event in adapter.stream(request)]

    return asyncio.run(run())


def test_openai_chat_contract_text_tool_usage():
    delta_text = SimpleNamespace(content="hello", reasoning_content=None, tool_calls=None)
    tool_fn = SimpleNamespace(name="list_plugins", arguments="{}")
    tool = SimpleNamespace(index=0, id="abc", function=tool_fn)
    delta_tool = SimpleNamespace(content=None, reasoning_content=None, tool_calls=[tool])
    chunks = [
        SimpleNamespace(id="req", usage=None, choices=[SimpleNamespace(delta=delta_text, finish_reason=None)]),
        SimpleNamespace(id="req", usage=None, choices=[SimpleNamespace(delta=delta_tool, finish_reason="tool_calls")]),
        SimpleNamespace(
            id="req",
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
            choices=[],
        ),
    ]

    class Client:
        chat = SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kwargs: asyncio.sleep(0, result=AsyncItems(chunks)))
        )

    events = collect(OpenAIChatAdapter(client=Client()), "openai_chat")
    assert [event.type for event in events].count("text_delta") == 1
    call = next(event for event in events if event.type == "tool_call")
    assert call.data["name"] == "list_plugins"
    assert call.data["complete"] is True
    assert next(event for event in events if event.type == "usage").data["input_tokens"] == 10


def test_deepseek_profiles_use_documented_balanced_defaults():
    flash = normalize_profile({"model": "deepseek-v4-flash"})
    pro = normalize_profile({"model": "deepseek-v4-pro"})

    assert flash["provider"] == pro["provider"] == "deepseek"
    assert flash["credential_id"] == pro["credential_id"] == "deepseek"
    assert flash["context_window"] == pro["context_window"] == 1_000_000
    assert flash["output_token_limit"] == pro["output_token_limit"] == 384_000
    assert flash["max_output_tokens"] == 8_192
    assert flash["reasoning_level"] == "off"
    assert pro["max_output_tokens"] == 16_384
    assert pro["reasoning_level"] == "high"


def test_deepseek_thinking_parameters_and_tool_state_round_trip():
    captured = {}
    tool_fn = SimpleNamespace(name="list_plugins", arguments="{}")
    tool = SimpleNamespace(index=0, id="abc", function=tool_fn)
    chunks = [
        SimpleNamespace(
            id="req",
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    reasoning_content="private reasoning",
                    reasoning_summary=None,
                    tool_calls=[tool],
                ),
                finish_reason="tool_calls",
            )],
        ),
    ]

    class Client:
        class Completions:
            async def create(self, **kwargs):
                captured.update(kwargs)
                return AsyncItems(chunks)

        chat = SimpleNamespace(completions=Completions())

    previous = AssistantMessage(
        message_id="previous",
        content=[ToolCallBlock(tool_call_id="old", name="list_plugins", arguments={})],
    )
    snap = TurnSnapshot.create(
        provider="openai_chat",
        provider_family="deepseek",
        model="deepseek-v4-pro",
        protocol="openai_chat",
        system_prompt="system",
        reasoning_level="high",
        max_output_tokens=16_384,
        tools=(),
        context_window=1_000_000,
        temperature=None,
    )
    request = ProviderRequest(
        snap,
        [previous],
        metadata={"provider_state": {
            "previous": {"reasoning_content": "prior private reasoning"},
        }},
    )

    async def run():
        return [event async for event in OpenAIChatAdapter(client=Client()).stream(request)]

    events = asyncio.run(run())
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert captured["reasoning_effort"] == "high"
    assert "temperature" not in captured
    assert captured["messages"][1]["reasoning_content"] == "prior private reasoning"
    state = next(event for event in events if event.type == "provider_state")
    assert state.data["reasoning_content"] == "private reasoning"


def test_deepseek_flash_disables_thinking_and_uses_temperature():
    captured = {}

    class Client:
        class Completions:
            async def create(self, **kwargs):
                captured.update(kwargs)
                return AsyncItems([])

        chat = SimpleNamespace(completions=Completions())

    snap = TurnSnapshot.create(
        provider="openai_chat",
        provider_family="deepseek",
        model="deepseek-v4-flash",
        protocol="openai_chat",
        system_prompt="system",
        reasoning_level="off",
        max_output_tokens=8_192,
        tools=(),
        context_window=1_000_000,
        temperature=0.1,
    )

    async def run():
        return [event async for event in OpenAIChatAdapter(client=Client()).stream(
            ProviderRequest(snap, [UserMessage("hello")])
        )]

    asyncio.run(run())
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert captured["temperature"] == 0.1
    assert "reasoning_effort" not in captured


def test_openai_responses_contract_summary_and_tool():
    item = SimpleNamespace(type="function_call", id="item", call_id="call", name="run_plugin", arguments="")
    done_item = SimpleNamespace(
        type="function_call",
        id="item",
        call_id="call",
        name="run_plugin",
        arguments='{"plugin_name":"pslist.PsList"}',
    )
    response = SimpleNamespace(
        id="resp",
        usage={"input_tokens": 4, "output_tokens": 5},
        status="completed",
        incomplete_details=None,
    )
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="ok"),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="summary"),
        SimpleNamespace(type="response.output_item.added", item=item),
        SimpleNamespace(type="response.output_item.done", item=done_item, output_index=0),
        SimpleNamespace(type="response.completed", response=response),
    ]

    class Client:
        responses = SimpleNamespace(
            create=lambda **_kwargs: asyncio.sleep(0, result=AsyncItems(events))
        )

    output = collect(OpenAIResponsesAdapter(client=Client()), "openai_responses")
    assert any(event.type == "thinking_summary_delta" for event in output)
    assert next(event for event in output if event.type == "tool_call").data["complete"]
    assert output[-1].data["request_id"] == "resp"


def test_anthropic_contract_tool_and_usage():
    block = SimpleNamespace(type="tool_use", id="toolu_1", name="list_plugins", input={})
    events = [
        SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(id="msg", usage={"input_tokens": 7}),
        ),
        SimpleNamespace(type="content_block_start", index=0, content_block=block),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json="{}"),
        ),
        SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason="tool_use"),
            usage={"output_tokens": 2},
        ),
    ]

    class Client:
        messages = SimpleNamespace(
            create=lambda **_kwargs: asyncio.sleep(0, result=AsyncItems(events))
        )

    output = collect(AnthropicMessagesAdapter(client=Client()), "anthropic_messages")
    assert next(event for event in output if event.type == "tool_call").data["name"] == "list_plugins"
    assert output[-1].data["usage"]["input_tokens"] == 7
    assert output[-1].data["usage"]["output_tokens"] == 2


def test_google_contract_text_tool_and_usage():
    parts = [
        {"text": "visible"},
        {"function_call": {"id": "one", "name": "list_plugins", "args": {}}},
    ]
    chunk = {
        "response_id": "google-request",
        "usage_metadata": {
            "prompt_token_count": 8,
            "candidates_token_count": 3,
            "thoughts_token_count": 1,
        },
        "candidates": [{"finish_reason": "STOP", "content": {"parts": parts}}],
    }

    class Models:
        async def generate_content_stream(self, **_kwargs):
            return AsyncItems([chunk])

    client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    output = collect(GoogleGenAIAdapter(client=client), "google_genai")
    assert next(event for event in output if event.type == "text_delta").data["text"] == "visible"
    assert next(event for event in output if event.type == "tool_call").data["name"] == "list_plugins"
    assert output[-1].data["usage"]["reasoning_tokens"] == 1


def test_legacy_conversation_migration_and_backup(tmp_path):
    root = tmp_path / "conversations"
    root.mkdir()
    (root / "index.json").write_text(json.dumps([{
        "id": "legacy",
        "title": "old",
        "engine": "vol3",
        "message_count": 1,
    }]), "utf-8")
    (root / "legacy.json").write_text(json.dumps([
        {"role": "user", "content": "question"},
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "toolName": "run_plugin",
            "toolArgs": {"plugin_name": "pslist.PsList"},
            "toolSummary": "summary",
        },
    ]), "utf-8")

    store = ConversationStore(root)
    messages = store.messages("legacy")
    assert (root / "legacy.json.bak-v1").exists()
    assert len(messages) == 3
    assert isinstance(messages[1], AssistantMessage)
    assert isinstance(messages[2], ToolResultMessage)
    assert messages[2].details["legacy_summary_only"] is True


def test_compaction_keeps_raw_history_pairs_and_is_repeatable(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    meta = store.create(engine_id="vol3", image_id="image")
    store.append_message(meta["id"], UserMessage("A" * 7000))
    store.append_message(meta["id"], AssistantMessage(content=[
        ToolCallBlock(tool_call_id="call", name="run_plugin", arguments={})
    ]))
    store.append_message(meta["id"], ToolResultMessage(
        "call",
        "B" * 5000,
        details={"plugin": "linux.pslist.PsList", "tool_call_id": "call"},
    ))
    store.append_message(meta["id"], UserMessage("recent"))
    store.append_message(meta["id"], AssistantMessage(content=[TextBlock(text="answer")]))
    original_ids = [message.message_id for message in store.messages(meta["id"])]
    manager = ContextManager(store, default_reserve=1024, default_recent=800)

    first = asyncio.run(manager.compact(meta["id"], 4096))
    second = asyncio.run(manager.compact(meta["id"], 4096))

    assert first.compacted and second.compacted
    assert len(store.checkpoints(meta["id"])) == 1
    assert [message.message_id for message in store.messages(meta["id"])] == original_ids
    for group in message_groups(first.messages):
        calls = {
            block.tool_call_id
            for message in group
            if isinstance(message, AssistantMessage)
            for block in message.content
            if isinstance(block, ToolCallBlock)
        }
        results = {
            message.tool_call_id for message in group if isinstance(message, ToolResultMessage)
        }
        assert not calls or calls == results


def test_tool_registry_validation_truncation_and_result_query(tmp_path):
    invoked = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="echo",
        description="echo",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        executor=lambda arguments, _context: invoked.append(arguments) or arguments,
    ))
    context = ToolExecutionContext(
        run_id="run",
        conversation_id="conv",
        engine_id="vol3",
        image_id="image",
        tool_call_id="call",
    )
    invalid = asyncio.run(registry.execute("echo", {}, context))
    truncated = asyncio.run(registry.execute("echo", {"value": 1}, context, call_complete=False))
    valid = asyncio.run(registry.execute("echo", {"value": 2}, context))
    assert invalid.is_error and truncated.is_error
    assert not valid.is_error
    assert invoked == [{"value": 2}]

    results = ResultHandleStore(tmp_path / "results")
    handle = results.save(
        "image", "conv", plugin="pslist", columns=["PID", "Name"],
        rows=[(2, "b"), (1, "a"), (3, "other")],
    )
    page = results.query(
        "image", "conv", handle["result_id"],
        filter_text='Name -contain "a"', sort_column="PID", columns=["Name"], page_size=1,
    )
    assert page["rows"] == [["a"]]
    assert page["total"] == 1


def test_credentials_are_mode_0600_and_evidence_requires_source(tmp_path):
    credentials = CredentialStore(tmp_path / "credentials.json")
    credentials.set("profile", "secret")
    assert os.stat(credentials.path).st_mode & 0o777 == 0o600
    assert credentials.masked("profile") == {
        "has_api_key": True,
        "api_key_mask": "••••••••cret",
    }

    evidence = EvidenceStore(tmp_path / "evidence")
    try:
        evidence.add("image", "conv", "unsupported", {})
    except ValueError:
        pass
    else:
        raise AssertionError("evidence without provenance must be rejected")
    evidence.add("image", "conv", "fact", {"result_id": "res_1"})
    assert evidence.list("image", "conv")[0]["content"] == "fact"


def test_provider_state_is_private_and_provider_scoped(tmp_path):
    states = ProviderStateStore(tmp_path / "provider_state")
    states.set(
        "conversation",
        "message",
        provider_family="deepseek",
        model="deepseek-v4-pro",
        state={"reasoning_content": "private"},
    )
    assert os.stat(states._path("conversation")).st_mode & 0o777 == 0o600
    assert states.for_messages(
        "conversation",
        ["message"],
        provider_family="deepseek",
        model="deepseek-v4-pro",
    ) == {"message": {"reasoning_content": "private"}}
    assert states.for_messages(
        "conversation",
        ["message"],
        provider_family="openai",
        model="gpt-5",
    ) == {}


def test_event_store_after_seq_is_idempotent(tmp_path):
    events = EventStore(tmp_path)
    for seq in range(1, 4):
        events.append("run", {"seq": seq, "type": "text_delta"})
    assert [event["seq"] for event in events.read("run", after_seq=1)] == [2, 3]


def test_append_only_entries_form_parent_chain(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    meta = store.create()
    first = store.append_message(meta["id"], UserMessage("one"))
    second = store.append_message(meta["id"], AssistantMessage(content=[TextBlock(text="two")]))
    assert first["parent_id"] is None
    assert second["parent_id"] == first["entry_id"]


def test_agent_loop_state_machine_executes_tool_then_finishes(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    meta = store.create(engine_id="vol3", image_id="image")
    store.append_message(meta["id"], UserMessage("investigate"))
    registry = ToolRegistry()
    invoked = []
    registry.register(ToolDefinition(
        "echo",
        "echo",
        {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        lambda arguments, _context: invoked.append(arguments["value"]) or arguments,
    ))

    class Adapter:
        def __init__(self, events):
            self.events = events

        async def stream(self, _request):
            for item in self.events:
                yield item

        async def close(self):
            return None

    adapters = [
        Adapter([
            ProviderEvent("tool_call", {
                "tool_call_id": "call",
                "name": "echo",
                "arguments": {"value": 7},
                "complete": True,
            }),
            ProviderEvent("message_end", {"stop_reason": "tool_use"}),
        ]),
        Adapter([
            ProviderEvent("text_delta", {"text": "done"}),
            ProviderEvent("usage", {"input_tokens": 10, "output_tokens": 2}),
            ProviderEvent("message_end", {"stop_reason": "stop"}),
        ]),
    ]
    loop = AgentLoop(
        conversations=store,
        context=ContextManager(store),
        tools=registry,
        adapter_factory=lambda _snapshot: adapters.pop(0),
        sleep=lambda _delay: asyncio.sleep(0),
    )
    emitted = []

    async def emit(kind, data):
        emitted.append((kind, data))

    result = asyncio.run(loop.run(
        run_id="run",
        conversation_id=meta["id"],
        engine_id="vol3",
        image_id="image",
        snapshot_factory=lambda disabled: snapshot(
            "openai_chat",
            () if disabled else tuple(registry.declarations()),
        ),
        emit=emit,
        cancel_event=asyncio.Event(),
        budget=RunBudget(max_turns=4, max_tool_calls=2, max_seconds=60),
    ))
    assert result["status"] == "completed"
    assert invoked == [7]
    assert [kind for kind, _data in emitted][:3] == ["run_start", "turn_start", "context"]
    assert "tool_start" in [kind for kind, _data in emitted]
    assert any(
        isinstance(message, ToolResultMessage) and not message.is_error
        for message in store.messages(meta["id"])
    )


def test_agent_loop_retries_transient_provider_without_replaying_tools(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    meta = store.create()
    store.append_message(meta["id"], UserMessage("hello"))

    class Adapter:
        def __init__(self, fail=False):
            self.fail = fail

        async def stream(self, _request):
            if self.fail:
                raise ProviderTransientError("temporary")
            yield ProviderEvent("text_delta", {"text": "ok"})
            yield ProviderEvent("message_end", {"stop_reason": "stop"})

        async def close(self):
            return None

    adapters = [Adapter(True), Adapter(False)]
    loop = AgentLoop(
        conversations=store,
        context=ContextManager(store),
        tools=ToolRegistry(),
        adapter_factory=lambda _snapshot: adapters.pop(0),
        sleep=lambda _delay: asyncio.sleep(0),
    )
    emitted = []

    async def emit(kind, data):
        emitted.append((kind, data))

    asyncio.run(loop.run(
        run_id="run",
        conversation_id=meta["id"],
        engine_id="vol3",
        image_id="",
        snapshot_factory=lambda _disabled: snapshot("openai_chat"),
        emit=emit,
        cancel_event=asyncio.Event(),
        budget=RunBudget(),
        agent_mode=False,
    ))
    retry = next(data for kind, data in emitted if kind == "retry")
    assert retry["attempt"] == 1
    assert retry["delay_seconds"] == 2
