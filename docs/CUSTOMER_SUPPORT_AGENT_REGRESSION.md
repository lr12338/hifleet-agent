# Customer Support Agent Regression

本文描述当前 `customer_support` 轻量主链的回归范围、测试矩阵、验收标准和线上排障重点。

## 1. 当前回归范围

当前主链覆盖：

1. `route -> delegate -> check -> finalize`
2. 前置安全拦截
3. 标准客服 Agent 问答
4. `smart_search` 与 ship tools 的标准工具调用
5. 多模态输入的轻量附件提示
6. 最终输出脱敏与链接校验
7. `/stream_run` 调试流事件

## 2. 当前测试入口

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_customer_support_router.py \
  tests/test_customer_support_intent_agent.py \
  tests/test_customer_support_stream_debug.py \
  tests/test_admin_upload_config.py
```

本轮相关最小回归：

```bash
.venv-test/bin/python -m pytest -q \
  tests/test_customer_support_stream_debug.py \
  tests/test_customer_support_intent_agent.py \
  tests/test_customer_support_router.py
```

## 3. 主链验收标准

一次成功客服请求通常应满足：

1. `phase_history` 包含：
   - `route -> delegated -> check -> done`
2. `route_trace.run_id` 与外层 API `run_id` 一致
3. `tool_call_sequence` 来自真实 standard agent tool-calling
4. `check_result` 能反映：
   - `has_answer`
   - `links_ok`
   - `post_guard_applied`
5. 最终 `messages[0].content` 已经过 `sanitize_customer_output(...)`

## 4. 流式调试验收

`/stream_run` 当前验收点：

- 能看到：
  - `message_start`
  - `thinking`
  - `tool_response`
  - `answer`
  - `message_end`
- 事件内容要体现：
  - 前置安全
  - 标准客服 Agent 装配
  - 附件输入分析
  - 后置内容质检
- 不能体现：
  - prompt 原文
  - 隐藏 chain-of-thought
  - 内部路径
  - key / token / env

## 5. 线上排障重点

排查当前 `customer_support` 线上问题时，优先看：

1. `route`
   - 是否明显误分到错误业务类型
2. `tool_call_sequence`
   - 标准 Agent 是否真的调用了预期工具
3. `check_result`
   - 是否因为脱敏、空答、无效链接触发了 post guard
4. `generated_answer` / `messages[0].content`
   - 是否仍夹带搜索包装文本或内部信息
5. `latency_hotspot.total`
   - 是否存在异常慢请求

## 6. 当前已知限制

- 知识库内容不足时，`smart_search` 命中质量会直接下降
- 当知识库没有答案时，当前公网搜索能力仍偏弱
- `agent-browser` 还未接入 `customer_support` 主链

## 7. 当前优化优先级

1. 补知识库内容
2. 补公网搜索匹配能力
3. 再考虑引入 `agent-browser`
4. 不优先恢复复杂 Planner/Harness 主链
