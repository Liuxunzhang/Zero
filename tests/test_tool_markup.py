import asyncio
from types import SimpleNamespace

from web.backend.services.ai_service import AiService
from web.backend.services.tool_markup import (
    DsmlStreamFilter,
    parse_dsml_tool_calls,
    strip_dsml_tool_markup,
)


DSML_RESPONSE = """先继续调查网络连接。
<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="run_plugin">
<｜｜DSML｜｜parameter name="plugin_name" string="true">lsof.Lsof</｜｜DSML｜｜parameter>
<｜｜DSML｜｜parameter name="pid" string="false">343</｜｜DSML｜｜parameter>
</｜｜DSML｜｜invoke>
<｜｜DSML｜｜invoke name="run_plugin">
<｜｜DSML｜｜parameter name="plugin_name" string="true">envars.Envars</｜｜DSML｜｜parameter>
<｜｜DSML｜｜parameter name="pid" string="false">3747</｜｜DSML｜｜parameter>
</｜｜DSML｜｜invoke>
</｜｜DSML｜｜tool_calls>"""


def test_parse_dsml_parallel_tool_calls_and_argument_types():
    assert parse_dsml_tool_calls(DSML_RESPONSE) == [
        {
            "name": "run_plugin",
            "arguments": {"plugin_name": "lsof.Lsof", "pid": 343},
        },
        {
            "name": "run_plugin",
            "arguments": {"plugin_name": "envars.Envars", "pid": 3747},
        },
    ]


def test_stream_filter_hides_dsml_when_markers_cross_chunks():
    stream_filter = DsmlStreamFilter()
    response = DSML_RESPONSE + "\n调查完成。"
    visible = []

    for offset in range(0, len(response), 7):
        chunk = stream_filter.feed(response[offset:offset + 7])
        if chunk:
            visible.append(chunk)
    visible.append(stream_filter.finish())

    assert "".join(visible) == "先继续调查网络连接。\n\n调查完成。"


def test_strip_dsml_markup_cleans_saved_and_truncated_responses():
    assert strip_dsml_tool_markup(DSML_RESPONSE) == "先继续调查网络连接。\n"
    assert strip_dsml_tool_markup(
        "保留这段内容\n<｜｜DSML｜｜tool_calls><｜｜DSML"
    ) == "保留这段内容\n"


def test_chat_stream_executes_textual_dsml_calls_instead_of_displaying_them():
    class FakeCompletions:
        def __init__(self):
            self.call_count = 0

        async def create(self, **_kwargs):
            self.call_count += 1
            text = DSML_RESPONSE if self.call_count == 1 else "最终取证结论。"

            async def chunks():
                for offset in range(0, len(text), 9):
                    delta = SimpleNamespace(
                        content=text[offset:offset + 9],
                        tool_calls=None,
                    )
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

            return chunks()

    completions = FakeCompletions()
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    service = object.__new__(AiService)
    service._history = {}
    service._ai_temperature = 0.1
    service._ai_max_history = 12
    service._ai_memory_enabled = False
    service._resolve_config = lambda: ("https://example.invalid", "key", "model")
    service._get_openai_client = lambda *_args: fake_client
    service._resolve_max_tokens = lambda _model: 4096
    service._build_messages = lambda *_args, **_kwargs: [
        {"role": "user", "content": "调查"}
    ]
    executed = []

    async def execute(tool_name, arguments, engine_id):
        executed.append((tool_name, arguments, engine_id))
        return {"summary": "插件执行完成"}

    async def collect_events():
        return [
            event
            async for event in service.chat_stream(
                "调查",
                tool_executor=execute,
                engine_id="vol3",
            )
        ]

    events = asyncio.run(collect_events())
    visible_text = "".join(
        event["content"] for event in events if event["type"] == "chunk"
    )

    assert "DSML" not in visible_text
    assert visible_text == "先继续调查网络连接。\n最终取证结论。"
    assert executed == [
        ("run_plugin", {"plugin_name": "lsof.Lsof", "pid": 343}, "vol3"),
        ("run_plugin", {"plugin_name": "envars.Envars", "pid": 3747}, "vol3"),
    ]
    assert [event["type"] for event in events].count("tool_call") == 2
    assert [event["type"] for event in events].count("tool_result") == 2
    assert completions.call_count == 2
