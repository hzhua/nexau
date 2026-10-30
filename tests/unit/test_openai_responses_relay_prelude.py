"""中转站在流首插自己的事件时，Responses 流式不能崩。

背景：NorthGate（反代 Codex）会在 ``response.created`` **之前**先发
``codex.rate_limits`` / ``codex.response.metadata``。OpenAI SDK 的
``client.responses.stream()`` 辅助方法带严格累加状态机，遇到这种顺序直接
``RuntimeError: Expected to have received `response.created` before ...``，
整轮对话失败（2026-09-14 首次出现、10-08 复发）。

我们只用自己的 aggregator 拼最终结果，所以改用 ``responses.create(stream=True)``。
本用例钉住这个行为：**流首有陌生事件时，整轮仍然要正常拿到文本**。

⚠️ 这个用例必须走真实 HTTP + 真实 OpenAI 客户端 —— 直接喂事件给 aggregator 的测法
抓不到它，因为崩溃发生在 SDK 的传输/累加层，根本到不了 aggregator。
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from openai import AsyncOpenAI, OpenAI

from nexau.archs.main_sub.execution.llm_caller import (
    call_llm_with_openai_responses,
    call_llm_with_openai_responses_async,
)
from nexau.archs.tracer.context import reset_current_span, set_current_span
from nexau.archs.tracer.core import BaseTracer, Span, SpanType

_RESP = {
    "id": "resp_relay",
    "object": "response",
    "created_at": 1759900000,
    "status": "in_progress",
    "model": "gpt-5.6-sol",
    "output": [],
    "parallel_tool_calls": False,
    "tool_choice": "auto",
    "tools": [],
}
_MSG = {"id": "msg_1", "type": "message", "role": "assistant", "status": "in_progress", "content": []}
_PART = {"type": "output_text", "text": "", "annotations": []}
_DONE_MSG = {**_MSG, "status": "completed", "content": [{**_PART, "text": "OK"}]}

# 中转站自己插的两条（真实样本：NorthGate 2026-09-14）
RELAY_PRELUDE = [
    ("codex.rate_limits", {"type": "codex.rate_limits", "plan_type": "self_serve_business_prolite"}),
    ("codex.response.metadata", {"type": "codex.response.metadata", "headers": {"x-models-etag": 'W/"abc"'}}),
]
STANDARD_BODY = [
    ("response.created", {"type": "response.created", "sequence_number": 0, "response": _RESP}),
    ("response.in_progress", {"type": "response.in_progress", "sequence_number": 1, "response": _RESP}),
    ("response.output_item.added", {"type": "response.output_item.added", "sequence_number": 2, "output_index": 0, "item": _MSG}),
    (
        "response.content_part.added",
        {
            "type": "response.content_part.added",
            "sequence_number": 3,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "part": _PART,
        },
    ),
    (
        "response.output_text.delta",
        {
            "type": "response.output_text.delta",
            "sequence_number": 4,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "delta": "OK",
            "logprobs": [],
        },
    ),
    (
        "response.output_text.done",
        {
            "type": "response.output_text.done",
            "sequence_number": 5,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "text": "OK",
            "logprobs": [],
        },
    ),
    (
        "response.content_part.done",
        {
            "type": "response.content_part.done",
            "sequence_number": 6,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "part": {**_PART, "text": "OK"},
        },
    ),
    ("response.output_item.done", {"type": "response.output_item.done", "sequence_number": 7, "output_index": 0, "item": _DONE_MSG}),
    (
        "response.completed",
        {"type": "response.completed", "sequence_number": 8, "response": {**_RESP, "status": "completed", "output": [_DONE_MSG]}},
    ),
]


def _make_server(events):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length", 0) or 0))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for name, data in events:
                self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()

        def log_message(self, *args):  # 静音
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def relay_server():
    """流首带中转站事件的假 Responses 端点。"""
    server = _make_server(RELAY_PRELUDE + STANDARD_BODY)
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


@pytest.fixture
def clean_server():
    """负对照：同一条流，只去掉中转站那两条。"""
    server = _make_server(STANDARD_BODY)
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()


def _kwargs():
    return {"model": "gpt-5.6-sol", "input": "Reply with exactly: OK", "stream": True}


def _text_of(response) -> str:
    return response.render_text()


def test_relay_prelude_does_not_break_sync_stream(relay_server):
    client = OpenAI(base_url=relay_server, api_key="test", max_retries=0)
    response = call_llm_with_openai_responses(client, _kwargs())
    assert _text_of(response) == "OK"


def test_clean_stream_still_works_sync(clean_server):
    """负对照：没有中转站事件时同样要通过（否则上面那条绿说明不了问题）。"""
    client = OpenAI(base_url=clean_server, api_key="test", max_retries=0)
    response = call_llm_with_openai_responses(client, _kwargs())
    assert _text_of(response) == "OK"


def test_relay_prelude_does_not_break_async_stream(relay_server):
    """异步路径同样要扛住。用 asyncio.run 包一层（本仓约定，见
    tests/unit/test_anthropic_layer4_replay.py 的同款注释），不依赖 async 插件。"""

    async def _run():
        client = AsyncOpenAI(base_url=relay_server, api_key="test", max_retries=0)
        return await call_llm_with_openai_responses_async(client, _kwargs())

    assert _text_of(asyncio.run(_run())) == "OK"


# ---------------------------------------------------------------------------
# 带 tracer 的那两条分支
#
# llm_caller 里 `if should_trace and tracer is not None:` 把同步/异步各劈成两个
# **独立的调用点**（共 4 处）。上面三条用例不传 tracer，只走到 else 那半 ——
# 带 trace 的那半可以单独回退成 responses.stream() 而不被任何用例发现。
# ---------------------------------------------------------------------------


class _RecordingTracer(BaseTracer):
    """只记 span 名，够判断「确实走了带 trace 那条分支」。"""

    def __init__(self) -> None:
        self.start_calls: list[str] = []

    def start_span(
        self,
        name: str,
        span_type: SpanType,
        inputs: dict | None = None,
        parent_span: Span | None = None,
        attributes: dict | None = None,
    ) -> Span:
        self.start_calls.append(name)
        return Span(
            id="span",
            name=name,
            type=span_type,
            parent_id=getattr(parent_span, "id", None),
            inputs=inputs or {},
            attributes=attributes or {},
        )

    def end_span(self, span: Span, outputs=None, error=None, attributes=None) -> None:
        pass


def test_relay_prelude_does_not_break_traced_sync_stream(relay_server):
    tracer = _RecordingTracer()
    token = set_current_span(Span(id="parent", name="parent", type=SpanType.AGENT))
    try:
        client = OpenAI(base_url=relay_server, api_key="test", max_retries=0)
        response = call_llm_with_openai_responses(client, _kwargs(), tracer=tracer)
    finally:
        reset_current_span(token)

    assert _text_of(response) == "OK"
    # 没这句的话，tracer 没生效（走回了 else 分支）也会绿 —— 等于没测到这条分支
    assert "OpenAI responses.stream" in tracer.start_calls, f"没走带 trace 的分支: {tracer.start_calls}"


def test_relay_prelude_does_not_break_traced_async_stream(relay_server):
    tracer = _RecordingTracer()

    async def _run():
        client = AsyncOpenAI(base_url=relay_server, api_key="test", max_retries=0)
        return await call_llm_with_openai_responses_async(client, _kwargs(), tracer=tracer)

    token = set_current_span(Span(id="parent", name="parent", type=SpanType.AGENT))
    try:
        response = asyncio.run(_run())
    finally:
        reset_current_span(token)

    assert _text_of(response) == "OK"
    assert "OpenAI responses.stream (async)" in tracer.start_calls, f"没走带 trace 的分支: {tracer.start_calls}"
