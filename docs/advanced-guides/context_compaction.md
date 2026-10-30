## Context Compaction

Context Compaction is a middleware that automatically manages conversation history when token limits are approached. Once configured, it monitors token usage after each model call and performs compaction automatically when trigger conditions are met.

### Key Concepts

#### UserRound
A **UserRound** represents a complete execution cycle from a user message to the final model response. A UserRound contains one or more iterations.

#### Iteration
An **iteration** represents one complete round of conversation, bounded by ASSISTANT messages:

```
[USER or FRAMEWORK](optional) -> [ASSISTANT] -> [TOOL results](optional)
```

- Each ASSISTANT message starts a new iteration
- USER or FRAMEWORK messages before the ASSISTANT are part of that iteration
- TOOL results after the ASSISTANT are part of that iteration

**Example:**
```
Messages: [user1, assistant1, tool1, framework1, assistant2, user2, assistant3]

Iterations:
- Iteration 1: [user1, assistant1, tool1]
- Iteration 2: [framework1, assistant2]
- Iteration 3: [user2, assistant3]
```

**How it works:**
1. **Automatic Monitoring**: Tracks token usage after every model call
2. **Smart Triggering**: Automatically compacts when token usage exceeds the threshold (default: 75%)
3. **Safe Execution**: Only compacts when appropriate (e.g., skips if the last assistant message has no tool calls)

### Compaction Strategies

#### 1. Tool Result Compaction (Recommended)

Preserves all conversation structure while compacting old tool results. Fast and cost-free.

**What it preserves:**
- System prompt
- All user messages
- All assistant messages
- Tool results in recent iterations (controlled by `keep_iterations`)

**What it compacts:**
- Old tool result content is replaced with: `"Tool call result has been compacted"`

**Configuration:**

```yaml
middlewares:
  - import: nexau.archs.main_sub.execution.middleware.context_compaction:ContextCompactionMiddleware
    params:
      max_context_tokens: 100000
      auto_compact: true
      threshold: 0.75
      compaction_strategy: "tool_result_compaction"
      keep_iterations: 1  # Number of recent iterations to keep uncompacted
```

**Python Configuration:**
```python
ContextCompactionMiddleware(
    max_context_tokens=100000,
    auto_compact=True,
    threshold=0.75,
    compaction_strategy="tool_result_compaction",
    keep_iterations=1,
)
```

#### 2. LLM Summary Compaction (`llm_summary`)

Summarizes old conversation rounds using an LLM while keeping recent iterations unchanged. Deprecated legacy alias `sliding_window` remains supported and emits a warning.

**How it works:**
- Groups messages into iterations (see Key Concepts above)
- Keeps the most recent N iterations in full
- Summarizes older iterations using a separate LLM call
- Injects the summary into the conversation context

**Configuration:**

```yaml
middlewares:
  - import: nexau.archs.main_sub.execution.middleware.context_compaction:ContextCompactionMiddleware
    params:
      max_context_tokens: 100000
      auto_compact: true
      threshold: 0.75
      compaction_strategy: "llm_summary"
      keep_iterations: 2  # Keep last 2 iterations uncompacted

      # Optional: summary LLM overrides.
      # If omitted, the middleware reuses the agent's llm_config.
      # If provided, this must be a standalone summary LLM config and is used
      # as-is for both regular summary compaction and wrap emergency summarization.
      summary_llm_config:
        model: ${env.SUMMARY_MODEL}
        base_url: ${env.SUMMARY_BASE_URL}
        api_key: ${env.SUMMARY_API_KEY}
        api_type: ${env.SUMMARY_API_TYPE}

      # Optional: Custom summary prompt
      compact_prompt_path: "./prompts/custom_compact_prompt.md"
```

**Python Configuration:**

```python
ContextCompactionMiddleware(
    max_context_tokens=100000,
    auto_compact=True,
    threshold=0.75,
    compaction_strategy="llm_summary",
    keep_iterations=2,
    summary_llm_config={
        "model": "nex-n1",
        "base_url": "https://summary.example.com/v1",
        "api_key": "sk-...",
        "api_type": "openai_chat_completion",
    },
)
```

`wrap_model_call` emergency summarization follows the same rule: by default it reuses the active model runtime configuration, and if `summary_llm_config` is set, that standalone config is used as-is. Legacy flat fields (`summary_model`, `summary_base_url`, `summary_api_key`, `summary_api_type`) remain supported for compatibility.

### Emergency Overflow Fallback (wrap_model_call)

When a provider returns context-overflow errors and `emergency_compact_enabled: true`,
the middleware applies a dedicated emergency fallback flow in `wrap_model_call`:

1. Keep a minimum safety region unchanged:
   - system message (if present)
   - last 1 iteration
   - unresolved tool-use chain
   - last user message
2. Split the remaining trace into two fixed segments (50/50 by token accumulation).
3. Summarize both segments with the emergency prompt.
4. Merge the two summaries with the same emergency prompt into one compact context.
5. Rebuild messages as: `system + merged summary + safety region`.
6. Run a token gate check; if still over limit, fail fast instead of retrying.

Emergency prompt path:

`nexau/archs/main_sub/execution/middleware/context_compaction/prompts/emergency_compact_prompt.md`

### Trigger Strategy

The middleware automatically monitors token usage after each model call. When usage exceeds the configured threshold percentage, compaction is triggered automatically.

**YAML Configuration:**
```yaml
threshold: 0.75  # Trigger at 75% of max_context_tokens
```

**Python Configuration:**
```python
ContextCompactionMiddleware(
    max_context_tokens=100000,
    threshold=0.75,
)
```

**Safety checks:**
- Compaction is automatically skipped if the last assistant message has no tool calls

### Futile-Compaction Backoff

A strategy can only trim what it is allowed to touch. `tool_result` keeps the last
`keep_iterations` iterations intact, so once every older tool result has already been
compacted, the untouchable remainder (system prompt, user/assistant turns, the recent
iterations) may still sit above `threshold`. Before this backoff existed the trigger then
fired on **every** step, and every firing persisted a full-history REPLACE snapshot even
when the strategy changed nothing — one production run did that 642 times in 47 minutes.

The middleware now classifies each regular compaction (before/after token figures are
always taken with the same local estimator, so they are comparable):

- **changed nothing** → no REPLACE is persisted, the messages are left as they were, a
  `COMPACTION_FINISHED` event is still emitted (its after-figures equal its before-figures);
- **changed something, but usage is still at/above `threshold` and less than 10 % of the
  input was reclaimed** → the REPLACE is persisted as usual, but the pass is treated as
  marginal: another whole-history snapshot for a sliver of gain is not worth it;
- **anything else** (usage dropped under `threshold`, or a meaningful share was reclaimed)
  → business as usual, no backoff.

The first two outcomes mute the trigger until `compaction_retry_after_messages` (default
`6`) new messages have been appended — i.e. until there is new material to work on. The
mute is lifted early by a shrinking history (undo / clear) and by usage reaching
`max_context_tokens` (a retry is always cheaper than an overflowing call). Emergency
(`wrap_model_call`) compaction is not affected.

```yaml
middlewares:
  - import: nexau.archs.main_sub.execution.middleware.context_compaction:ContextCompactionMiddleware
    params:
      compaction_strategy: tool_result_compaction
      threshold: 0.75
      compaction_retry_after_messages: 6  # 0 disables the mute (a no-op still persists no REPLACE)
```

While muted, usage may drift between `threshold` and `max_context_tokens`; that headroom
is what the mute spends. If you see the mute message repeatedly for the same run, the
strategy cannot reach the threshold on its own — raise `max_context_tokens`, lower
`keep_iterations`, or switch to `llm_summary`.

### Testing Guide

Use this checklist to verify `before_model` / `after_model` compaction and wrap emergency fallback.

#### 1) Unit tests (fast, deterministic)

```bash
uv run pytest tests/unit/test_context_compaction.py -q
uv run pytest tests/unit/test_executor.py tests/unit/test_sse_client.py -q
```

What these tests cover:
- regular compaction trigger paths (`before_model`, `after_model`)
- wrap fallback retry on provider overflow
- failure path when emergency compaction still exceeds context limit
- token gate counting with tools schema (`tools=...` included)
- event emission and parsing:
  - `COMPACTION_STARTED`
  - `COMPACTION_FINISHED`

#### 2) Integration test (agent-level flow)

```bash
uv run pytest tests/integration/test_wrap_emergency_compaction_integration.py -q
```

Scenario in this integration test:
- agent has a large-output tool (`big_blob_writer`)
- multiple large user messages are sent across rounds
- provider overflow is simulated
- emergency wrap compaction is expected to trigger

Key assertions:
- at least one overflow happened
- wrap fallback emitted `COMPACTION_STARTED` (`phase=wrap_model_call`, `mode=emergency`)
- wrap fallback emitted `COMPACTION_FINISHED` with `success=True`

#### 3) Manual smoke test (real model/provider)

Recommended config:
- `auto_compact: true`
- `emergency_compact_enabled: true`
- relatively small `max_context_tokens` for easier reproduction

Run several rounds where each round:
1. sends a large user message
2. triggers one large tool result

Expected behavior:
- first provider overflow is caught in `wrap_model_call`
- emergency compaction runs
- one retry is attempted with compacted messages
- event stream/log contains paired compaction events (`STARTED` then `FINISHED`)
