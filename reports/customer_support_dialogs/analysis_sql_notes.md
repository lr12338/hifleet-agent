# SQL 查询记录

本次分析脚本只执行 `SELECT`，不包含 `INSERT`、`UPDATE`、`DELETE`、DDL 或锁表操作。数据库密码仅从 `.env` 读取，未写入报告。

## 1. 查询 api_calls 明细

用途：抽取用户输入、agent 回复、渠道、route、模型、延迟与状态。

```sql
SELECT id, run_id, session_id, user_id, source_channel, route, intent_hint,
       request_json, response_json, http_status_code, status, latency_ms, created_at
FROM observability.api_calls
WHERE created_at >= %(start)s
ORDER BY created_at DESC
LIMIT %(limit)s;
```

## 2. 查询工具调用

用途：通过 `run_id` 关联每轮对话的工具链、参数、返回、状态与错误摘要。

```sql
SELECT run_id, session_id, tool_name, tool_args, tool_result, status, code,
       message, retriable, attempt, latency_ms, source, layer_trace, created_at
FROM observability.tool_invocations
WHERE run_id = ANY(%(run_ids)s)
ORDER BY created_at ASC;
```

## 3. 查询 agent 错误

用途：通过 `run_id` 关联错误类型、错误信息、节点与堆栈摘要。

```sql
SELECT run_id, session_id, route, error_code, error_message, stack_trace,
       error_category, node_name, attempt, created_at
FROM observability.agent_errors
WHERE run_id = ANY(%(run_ids)s)
ORDER BY created_at ASC;
```

## 4. 渠道统计

用途：统计分析窗口内各渠道对话数、会话数和最近时间。

```sql
SELECT source_channel AS channel, count(*) AS api_calls,
       count(DISTINCT session_id) AS sessions, max(created_at) AS last_at
FROM observability.api_calls
WHERE created_at >= %(start)s
GROUP BY source_channel
ORDER BY api_calls DESC;
```

## 5. checkpoint 抽样

用途：抽样理解 LangGraph checkpoint 的可解析字段，不还原敏感完整上下文。

```sql
SELECT thread_id, checkpoint_id, checkpoint, metadata
FROM memory.checkpoints
ORDER BY checkpoint_id DESC
LIMIT %(limit)s;
```
