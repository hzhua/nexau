# Copyright (c) Nex-AGI. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Regression tests: Executor must wire agent-level max_context_tokens into ContextCompactionMiddleware.

Issue #631: ``Executor.__init__`` called ``_wire_middleware_llm_runtime`` without
``max_context_tokens``, so a ``ContextCompactionMiddleware`` configured without an
explicit ``max_context_tokens`` never inherited the agent-level limit. Regular
compaction was skipped ("max_context_tokens not resolved") and no emergency
compaction strategy was ever created.

The middleware-level inheritance is already covered in ``test_context_compaction.py``
by calling ``set_llm_runtime`` directly; these tests go through ``Executor`` / ``Agent``
so that the wiring call site itself is guarded.
"""

from unittest.mock import Mock, patch

from nexau import Agent, AgentConfig
from nexau.archs.llm.llm_config import LLMConfig
from nexau.archs.main_sub.execution.executor import Executor
from nexau.archs.main_sub.execution.middleware.context_compaction import ContextCompactionMiddleware
from nexau.archs.main_sub.execution.middleware.context_compaction.compact_stratigies.user_model_full_trace_adaptive import (
    UserModelFullTraceAdaptiveCompaction,
)
from nexau.archs.tool.tool_registry import ToolRegistry

# Agent-level limit used by the production report (model context window 262144).
AGENT_MAX_CONTEXT_TOKENS = 262144


def _llm_config() -> LLMConfig:
    return LLMConfig(
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        api_type="openai_chat_completion",
    )


def _make_executor(middleware: ContextCompactionMiddleware, *, max_context_tokens: int = AGENT_MAX_CONTEXT_TOKENS) -> Executor:
    return Executor(
        agent_name="test",
        agent_id="id",
        tool_registry=ToolRegistry(),
        sub_agents={},
        stop_tools=set(),
        openai_client=Mock(),
        llm_config=_llm_config(),
        max_context_tokens=max_context_tokens,
        middlewares=[middleware],
    )


class TestExecutorWiresMaxContextTokens:
    def test_inherits_agent_level_max_context_tokens(self):
        """Middleware without explicit max_context_tokens inherits the Executor's value."""
        middleware = ContextCompactionMiddleware(compaction_strategy="tool_result_compaction")
        assert middleware.max_context_tokens is None
        assert middleware.emergency_compaction_strategy is None

        _make_executor(middleware)

        assert middleware.max_context_tokens == AGENT_MAX_CONTEXT_TOKENS
        assert middleware.emergency_compaction_strategy is not None
        assert isinstance(middleware.emergency_compaction_strategy, UserModelFullTraceAdaptiveCompaction)
        assert middleware.emergency_compaction_strategy.max_context_tokens == AGENT_MAX_CONTEXT_TOKENS

    def test_inherits_default_executor_max_context_tokens(self):
        """Executor's own default (128000) is inherited too when the caller does not override it."""
        middleware = ContextCompactionMiddleware(compaction_strategy="tool_result_compaction")

        Executor(
            agent_name="test",
            agent_id="id",
            tool_registry=ToolRegistry(),
            sub_agents={},
            stop_tools=set(),
            openai_client=Mock(),
            llm_config=_llm_config(),
            middlewares=[middleware],
        )

        assert middleware.max_context_tokens == 128000
        assert middleware.emergency_compaction_strategy is not None

    def test_explicit_middleware_value_wins_over_agent_level(self):
        """An explicit max_context_tokens in the middleware params is never overridden."""
        middleware = ContextCompactionMiddleware(
            compaction_strategy="tool_result_compaction",
            max_context_tokens=64000,
        )
        assert middleware.max_context_tokens == 64000

        _make_executor(middleware)

        assert middleware.max_context_tokens == 64000
        assert middleware.emergency_compaction_strategy is not None
        assert middleware.emergency_compaction_strategy.max_context_tokens == 64000

    def test_legacy_middleware_signature_still_wired(self):
        """Middleware whose set_llm_runtime lacks max_context_tokens keeps working (TypeError fallback)."""
        from nexau.archs.main_sub.execution.hooks import Middleware

        class LegacyMiddleware(Middleware):
            wired_with: tuple[object, ...] | None = None

            @property
            def supports_set_llm_runtime(self) -> bool:
                return True

            def set_llm_runtime(self, llm_config, openai_client, *, session_id=None):
                self.wired_with = (llm_config.model, session_id)

        legacy = LegacyMiddleware()
        Executor(
            agent_name="test",
            agent_id="id",
            tool_registry=ToolRegistry(),
            sub_agents={},
            stop_tools=set(),
            openai_client=Mock(),
            llm_config=_llm_config(),
            max_context_tokens=AGENT_MAX_CONTEXT_TOKENS,
            session_id="sess-1",
            middlewares=[legacy],
        )

        assert legacy.wired_with == ("gpt-4o-mini", "sess-1")


class TestAgentWiresMaxContextTokens:
    def test_agent_config_max_context_tokens_reaches_compaction_middleware(self):
        """AgentConfig.max_context_tokens flows Agent -> Executor -> ContextCompactionMiddleware."""
        middleware = ContextCompactionMiddleware(compaction_strategy="tool_result_compaction")

        with patch("nexau.archs.main_sub.agent.openai") as mock_openai:
            mock_openai.OpenAI.return_value = Mock()
            config = AgentConfig(
                name="test_agent",
                llm_config=_llm_config(),
                max_context_tokens=AGENT_MAX_CONTEXT_TOKENS,
                middlewares=[middleware],
            )
            Agent(config=config)

        assert middleware.max_context_tokens == AGENT_MAX_CONTEXT_TOKENS
        assert middleware.emergency_compaction_strategy is not None
