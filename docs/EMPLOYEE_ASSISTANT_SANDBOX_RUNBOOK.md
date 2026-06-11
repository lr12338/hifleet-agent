# Employee Assistant 沙盒与闭环 Runbook

本文只覆盖当前 `employee_assistant` 的真实落地能力：表格探测、Docker sibling container 执行、自愈 loop、observability 对齐和发布前测试。

## 1. 适用场景

- 读取 CSV/XLS/XLSX，分析字段、缺失值、样例数据。
- 处理公开 OSS/HTTP 文件链接，先下载再进入分析闭环。
- 生成报价汇总、内部统计表、临时分析产物。
- 在管理台 `Chat Debug` 中以 `agent_profile=employee_assistant` 调试多轮复杂任务。

不适用：

- 外部客服会话。
- 未鉴权来源的用户上传执行。
- 需要联网、系统命令、宿主机路径扫描的脚本。

## 2. 运行时流程

1. `/run` 或 `/stream_run` 进入 `src/main.py`，解析 `agent_profile/source_channel`。
2. `src/agents/agent.py` 在 `employee_assistant` 下识别是否为“表格分析/产物任务”。
3. 命中后进入 `route -> plan -> act -> check -> loop/finalize/fail`。
4. 如果输入是公开 URL，`plan` 先调用 `download_public_file_to_artifact`。
5. `plan` 调用 `inspect_tabular_file` 返回 schema。
6. `act` 基于 schema 生成 Python。
7. `check` 调用 `run_sandboxed_python`：
   - 先 AST 扫描。
   - 再启动 Docker sibling container。
   - 校验 `exit_code` 与 `expected_artifact`。
7. 所有工具调用与错误通过 observability 落库。

## 3. 关键环境变量

```bash
HIFLEET_AGENT_ARTIFACT_DIR=/workspace/artifacts
HIFLEET_PY_SANDBOX_IMAGE=python:3.11-slim
HIFLEET_PY_SANDBOX_IMAGE_CANDIDATES=python:3.11-slim,registry.example.com/hifleet/python:3.11-slim
HIFLEET_PY_SANDBOX_AUTO_PULL=1
HIFLEET_PY_SANDBOX_VOLUME=coze_ai_shared-artifacts
HIFLEET_PY_SANDBOX_VOLUME_MOUNT=/workspace/artifacts
HIFLEET_PY_SANDBOX_MEM_LIMIT=512m
HIFLEET_PY_SANDBOX_CPU_QUOTA=50000
HIFLEET_PY_SANDBOX_TIMEOUT_SEC=20
HIFLEET_PY_SANDBOX_MAX_CODE_CHARS=12000
HIFLEET_PY_SANDBOX_STDIO_CHARS=8000
HIFLEET_PY_SANDBOX_USER=1000:1000
HIFLEET_EMPLOYEE_MAX_LOOPS=4
HIFLEET_PUBLIC_FILE_TIMEOUT_SEC=30
HIFLEET_PUBLIC_FILE_MAX_MB=100
```

## 4. 观测对齐

`observability.tool_invocations`：

- `run_id`
- `session_id`
- `tool_name`
- `status`
- `code`
- `attempt`
- `tool_result.exit_code/stdout/stderr/artifact_check/input_file_path`
- `layer_trace.phase/security_blocked`

`observability.agent_errors`：

- `run_id`
- `session_id`
- `error_code`
- `node_name`
- `attempt`

关键错误码：

- `ERR_SANDBOX_SECURITY`
- `PYTHON_SANDBOX_NONZERO`
- `PYTHON_SANDBOX_ARTIFACT_CHECK_FAILED`
- `PYTHON_SANDBOX_DOCKER_FAILED`

## 5. 发布前测试

```bash
PYTHONPATH=src .venv/bin/python scripts/test_agent_profiles.py
PYTHONPATH=src .venv/bin/python scripts/test_employee_workspace.py
PYTHONPATH=src .venv/bin/python scripts/test_employee_agent_loop.py
bash scripts/prepare_employee_sandbox_image.sh
cd frontend && npm run build
```

## 6. 排障

`ERR_SANDBOX_SECURITY`

- 说明脚本命中了 AST 白名单/黑名单规则。
- 优先检查 import、`eval/exec`、双下划线访问。

`PYTHON_SANDBOX_DOCKER_FAILED`

- 优先检查 Docker socket 权限。
- 容器内路径与共享卷是否和 `docker-compose.dev.yml` 一致。
- 优先执行 `bash scripts/prepare_employee_sandbox_image.sh` 预热镜像。

产物校验失败

- 检查模型要求输出的文件名与 `expected_artifact` 是否一致。
- 检查文件是否实际写入 `ARTIFACT_DIR`。
