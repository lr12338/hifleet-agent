# 以 `employee_assistant` 为主线简化客服架构：开发日志总结与收敛准备

本文用于承接最近几轮客服链路开发、排障和修复结论，并为下一步“以 `employee_assistant` 为主线收敛架构”做准备。

目标不是重放全部历史讨论，而是把当前已经验证过的事实、暴露出的结构问题、以及下一阶段最值得做的收敛方向压缩成一份可执行文档。

## 1. 当前已经完成的关键修复

### 1.1 知识检索主链从黑盒单工具改为三层受控升级

当前知识主链已经从“优先依赖 `smart_search`”切到：

1. `local_kb_search`
2. `web_search`
3. `web_search_agent_browser`

已确认生效的结果：

- `customer_support` 的知识主链不再默认依赖 `smart_search`
- `knowledge_qa` 的主工具集已是三工具
- `smart_search` 当前仅保留为兼容 facade
- `authority_label` 这类旧 `smart_search -> format_web_result(...)` 崩溃路径不再是主链依赖

### 1.2 多轮历史对新问题的干扰已做轻量压缩

为减少同一 `session_id` 下历史噪声污染当前问题，当前消息窗口已改为：

- 保留最新 `system`
- 压缩长历史为一条 `历史上下文摘要`
- 保留最新用户消息原文

已落地的约束：

- 不再把大量历史原文直接送进 route / planner / agent
- 摘要会去掉 `综合摘要`、`查询1`、工具名、下载 APP 等噪声
- 若当前问题没有明显承接词，历史只作为背景，不主导本轮问题类型

### 1.3 多模态输入格式兼容已按历史版本补回

当前已恢复并增强：

- `content.query.prompt[].type=image/voice/video/text -> messages[].content[]`
- `input.type=multimodal -> messages[].content[]`
- `/stream_run` 回填音频时保留 `format`
- 历史多模态消息会清洗掉过期附件，只保留必要文本上下文
- 最新用户多模态内容完整保留

### 1.4 多模态识别前移，避免标准 agent 直接吃原始附件

当前新增了 direct multimodal perception 层：

- `customer_support` 和 `employee_assistant` 在当前轮有多模态附件时，先做轻量识别
- 音频会优先转成“识别文本 + 用户补充”再进入后续路由
- 图片/视频先产出 perception 摘要，供 route 使用
- 识别失败时稳定返回降级提示，而不是把原始附件直接推给标准 agent

### 1.5 `employee_assistant` 的纯文本 knowledge 崩溃已绕开

线上出现过：

- `employee_assistant`
- `intent_hint=knowledge`
- 纯文本问题
- `standard_agent.invoke(...)`
- LangChain 内部抛 `last_ai_index` 未定义

当前已做的处理不是改 profile 优先级，而是：

- 保留 `payload/header` 中 `employee_assistant` 优先
- 当 `employee_assistant` 命中 `intent_hint=knowledge` 且不是 workspace 文件任务时
- 直接复用当前三层知识链
- 不再走易抛 `last_ai_index` 的标准 delegate 路径

这意味着：`employee_assistant` 已不再只是“文件/Python 助手”，它已经具备稳定承接客服知识问答的主链能力。

## 2. 最近几类线上问题，对架构意味着什么

### 2.1 `authority_label` 报错

表象：

- `/run` 返回 `键不存在: 'authority_label'`

本质：

- 旧兼容链路仍依赖 `smart_search` 的格式化输出字段
- 黑盒单工具把检索、格式化、兼容逻辑绑得太紧

结论：

- 知识检索主链应继续坚持三层显式工具，不要再回到单工具黑盒

### 2.2 新问题被旧历史带偏

表象：

- 用户问 `Hifleet卫星AIS数据情况...`
- 回复却落回注册登录、平台介绍、APP 下载等历史污染内容

本质：

- 同一 `session_id` 下长历史原文直接进入 agent
- 旧回复模板反过来污染新一轮理解和检索

结论：

- 上下文工程必须默认 `current_turn_first`
- 保留 checkpointer，但不要把原始长历史直接送入主链

### 2.3 AMR / 多模态输入导致链路脆弱

表象：

- 多模态输入在不同入口格式不一致
- 历史附件 URL 失效
- 标准 agent 对原始多模态消息鲁棒性差

本质：

- 规范化层、窗口层、路由层、标准 agent 之间缺少清晰边界

结论：

- 多模态应在进入主 agent 前先被统一标准化、裁剪历史、感知摘要化

### 2.4 `employee_assistant` 与 `customer_support` 两套链路分裂

表象：

- 同样是知识问答，两个 profile 的主链、prompt、排障方式不同
- 文档和代码长期存在“哪个才是主链”的认知分叉

本质：

- 架构中同时存在“外部客服 graph”与“内部员工 graph”
- 但近期修复已经持续把两者往同一套知识/上下文/多模态机制上收敛

结论：

- 继续维持两套并行主链的收益在变低，维护成本在变高

## 3. 当前真实状态：为什么可以考虑以 `employee_assistant` 为主

从现有代码看，`employee_assistant` 已具备三类能力：

1. 稳定知识问答能力
   - `intent_hint=knowledge` 已可走三层知识链
2. 文件/表格/Python/产物工作流
   - `plan -> act -> check -> loop`
3. 多模态前处理能力
   - 当前轮附件先识别再路由

而 `customer_support` 额外提供的，主要是：

- 更强的客户输出 Guard
- 更细的 route / planner / harness 分叉
- 更重的客服专用 trace 结构
- 更浓的外部客服话术约束

这意味着下一步完全可以把架构目标改成：

- `employee_assistant` 作为统一主执行器
- `customer_support` 逐步降为“外部客户输出策略层 / profile policy / response guard”

也就是：

- 执行内核尽量统一
- 面向客户还是面向员工，更多由 profile policy 和最终输出约束决定
- 而不是维护两套逐渐同构却仍分叉的主 graph

## 4. 下一阶段推荐的收敛方向

### 4.1 收敛目标

推荐目标：

- 统一以 `employee_assistant` 为主 graph
- 保留 `customer_support` 作为客户输出策略层，而不是独立复杂执行图

推荐边界：

- 主执行：`employee_assistant`
- 知识链：统一三层 `knowledge_qa`
- 多模态前处理：统一 direct perception
- 文件/沙箱：保留 `employee_assistant` 工作流
- 外部客户安全收口：保留 `customer_support_guard`
- profile 差异主要体现在：
  - prompt 约束
  - tool policy
  - write 权限
  - response sanitation
  - 是否允许进入 sandbox / artifact / employee workspace

### 4.2 优先级最高的简化动作

建议按这个顺序做：

1. 统一主 graph
   - 让 `employee_assistant` 成为默认执行骨架
   - `customer_support` 只保留客户路由/收口差异
2. 统一知识入口
   - 两个 profile 都只走同一套三层知识链
   - 不再并行维护两套知识 route 叙事
3. 统一多模态入口
   - 标准化、历史清洗、感知摘要前置
4. 统一上下文策略
   - 压缩摘要 + latest turn first
5. 最后再决定是否继续保留独立 `customer_support` graph

### 4.3 暂时不要急着动的东西

- 不要先删 `smart_search`
- 不要先改掉所有旧测试
- 不要先重做完整 prompt 体系
- 不要先把所有 `customer_support` trace 结构并到 employee

原因很简单：

- 当前最值钱的是先减少主执行链分叉
- 不是先做大规模清理和重命名

## 5. 文档层面的明确结论

从本次整理起，建议把下面认知作为后续开发基线：

1. `employee_assistant` 不再只是内部文件助手，它已经可以稳定承接知识问答主链。
2. `customer_support` 当前仍有价值，但更适合作为“外部客户输出策略层”，而不是长期并行维护的另一套主执行内核。
3. 三层知识链、上下文压缩、多模态前处理，已经是两条链路的共同基础设施。
4. 下一阶段的最高效客服架构，不应再围绕 `smart_search` 或双 graph 分裂设计，而应围绕“统一执行内核 + 差异化输出策略”设计。

## 6. 当前准备状态

截至本轮整理，已经具备以下前提条件：

- 三层知识链已上线到主逻辑
- `employee_assistant` 的知识问答崩溃路径已被绕开
- 多模态规范化与 direct perception 已接入
- 上下文压缩已接入
- 回归测试已覆盖多模态、知识链和 `employee_assistant` knowledge shortcut

因此，下一步可以直接开始做：

- “统一主 graph，弱化 `customer_support` 独立执行图”的架构收敛设计

而不需要再回头补最基础的知识链、多模态或上下文工程。
