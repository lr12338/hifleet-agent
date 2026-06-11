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

在进入 employee loop 之前，请求会先经过统一模型路由层：

- 纯文本消息默认走文本模型 `doubao-seed-2-0-pro-260215`
- 图片/音频/视频消息默认走多模态模型 `doubao-seed-2-0-lite-260428`
- 运行态路由结果会回写到 `llm_route`，用于调试页和 `/run` 返回体对齐

随后才进入以下 employee 执行流程：

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
HIFLEET_PY_SANDBOX_IMAGE=hifleet/python-sandbox:3.11
HIFLEET_PY_SANDBOX_IMAGE_CANDIDATES=hifleet/python-sandbox:3.11,swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim
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
PYTHONPATH=src .venv/bin/python scripts/test_llm_config.py
bash scripts/prepare_employee_sandbox_image.sh
cd frontend && npm run build
```

模型路由专项验证：

1. `PUT /admin/config/llm` 设置 `text_model/multimodal_model/thinking_type`。
2. 发一条纯文本 `/run` 请求，检查返回体中的 `llm_route.model=文本模型`。
3. 发一条包含 `image_url` 或 `input_audio` 的 `/run` 请求，检查返回体中的 `llm_route.model=多模态模型`。
4. 如需追查实际模型名，查看最终 AI message 的 `response_metadata.model_name`。

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

模型路由与返回模型名不一致

- 先看 `/run` 返回体中的 `llm_route`，确认应用层解析结果。
- 再看最终 AI message 的 `response_metadata.model_name`，确认上游实际返回模型。
- 若只在并发更新 `/admin/config/llm` 与并发请求时出现一次性不一致，优先按运行态竞态处理，不要立即修改核心路由代码。
- 若顺序复现也稳定失败，再分别用 OpenAI SDK 直连、`ChatOpenAI` 直连、`bind_tools` 最小样本拆分验证。
