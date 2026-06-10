# Coze 客服总结数据库接入说明

> 日期：2026-04-03  
> 用途：说明当前已创建的 Coze 客服总结数据库结构，并指导 Coze agent 基于该数据库完成会话总结、插入、更新和自动上传开发。  
> 工作空间：`7482599602194202678`  
> 数据表：`cs_conv_summary`

---

## 1. 当前状态

数据库已经创建完成，当前使用的是精简版客服总结表：

- 表名：`cs_conv_summary`
- 用途：存储“单次对话”的总结结果，而不是逐条原始消息
- 设计策略：先满足最核心的客服运营和线索沉淀需求，再逐步扩字段

这意味着后续 Coze agent 开发应遵循一个原则：

## 先稳定完成“单次对话结束后自动总结并入库”，不要在当前阶段把表结构做成大而全的消息仓库

---

## 2. 官方 API 结论

已确认 Coze 官方数据库 API 如下：

- 创建数据库：
  - `POST https://api.coze.cn/v1/databases`
- 插入数据：
  - `POST https://api.coze.cn/v1/databases/:database_id/records`
- 更新数据：
  - `PUT https://api.coze.cn/v1/databases/:database_id/records`

当前这张表已经建好，因此后续开发重点不是再讨论建表，而是：

1. 主 Agent 如何生成结构化总结
2. 如何判断单次对话结束
3. 何时调用插入 API
4. 何时调用更新 API

---

## 3. 当前数据库结构

当前表结构如下：

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `conversation_round_id` | string | 是 | 单次对话唯一 ID |
| `session_id` | string | 是 | 会话 ID |
| `source_channel` | string | 否 | 来源渠道 |
| `started_at` | time | 是 | 对话开始时间 |
| `ended_at` | time | 是 | 对话结束时间 |
| `turn_count` | integer | 是 | 对话轮数 |
| `primary_category` | string | 是 | 消息主类别 |
| `summary_content` | string | 是 | 单次对话总结 |
| `contact_name` | string | 否 | 联系人姓名 |
| `contact_phone` | string | 否 | 联系人手机号 |
| `contact_email` | string | 否 | 联系人邮箱 |
| `resolution_status` | string | 是 | 处理结果 |
| `follow_up_needed` | boolean | 是 | 是否需要跟进 |
| `uploaded_at` | time | 是 | 上传时间 |

### 3.1 字段含义解释

#### `conversation_round_id`

这是一条总结记录的唯一业务标识。

建议规则：

- 一个“单次完整对话”对应一个 `conversation_round_id`
- 同一会话里的新任务如果已经明显开始新一轮，也可生成新的 `conversation_round_id`

建议格式：

- `sess_<session_id>_<yyyyMMddHHmmss>`
- 或 `channel_user_timestamp`

#### `session_id`

用于标识整段会话上下文。

如果渠道层已有稳定会话 ID，直接复用。

#### `primary_category`

当前建议只保留一个主类别，避免过早做复杂分类。

推荐枚举值：

- `使用需求`
- `生产需求`
- `商务需求`
- `问题反馈`
- `混合需求`
- `其他`

#### `resolution_status`

推荐枚举值：

- `resolved`
- `partially_resolved`
- `unresolved`
- `lead_captured`
- `handoff_required`

#### `follow_up_needed`

布尔值。

建议规则：

- 需要人工销售回访：`true`
- 用户问题未解决：`true`
- 只是常规 FAQ 已完成：`false`
- 生产查询已完成且无后续动作：`false`

---

## 4. 当前表结构适合存什么

这张表适合存：

- 单次对话的业务总结
- 主需求类别
- 联系方式
- 最终处理结果
- 是否需要后续跟进

这张表不适合直接存：

- 全量原始消息记录
- 多级复杂意图树
- 全量工具调用日志
- 大段结构化业务结果对象

如果后续需要这些能力，应该新增表，而不是继续塞到当前表里。

---

## 5. Coze agent 如何基于该表开发

后续 Coze agent 的开发目标应明确为：

## 在单次对话结束时，生成一条符合当前表结构的总结记录，并自动写入 `cs_conv_summary`

不要让数据库逻辑侵入主问答逻辑。正确分层应为：

```text
用户对话
  -> 主客服 Agent
    -> 正常回答 / 调工具 / 收集线索
    -> 维护会话状态
  -> 会话结束判定器
    -> 会话总结器
    -> 数据库插入器
```

### 5.1 开发时必须新增三个内部模块

#### 模块 A：会话状态管理器

作用：

- 记录本次会话开始时间
- 记录最后一轮消息时间
- 记录当前轮数
- 记录是否存在待补全字段
- 记录是否存在待确认写操作
- 记录是否已上传总结

建议维护以下内部状态：

```json
{
  "session_id": "sess_xxx",
  "conversation_round_id": "round_xxx",
  "started_at": "2026-04-03T10:00:00+08:00",
  "last_message_at": "2026-04-03T10:08:25+08:00",
  "turn_count": 6,
  "pending_slots": [],
  "pending_confirmation": false,
  "lead_info": {
    "contact_name": "",
    "contact_phone": "",
    "contact_email": ""
  },
  "uploaded": false
}
```

#### 模块 B：会话结束判定器

作用：

- 判断当前单次对话是否结束

#### 模块 C：会话总结器

作用：

- 将本次对话转成数据库所需的结构化记录

---

## 6. 如何判断“单次对话结束”

当前推荐使用混合判定，不要只依赖一句“谢谢”。

### 6.1 满足任一条件可结束

#### 条件 A：用户显式结束

例如：

- “谢谢，没问题了”
- “好了”
- “先这样”
- “不用了”

并且同时满足：

- 当前没有待补全字段
- 当前没有待确认写操作

#### 条件 B：任务完成闭环

例如：

- FAQ 已回答完成
- 船位 / PSC / 档案查询已经返回结果
- 商务线索已提交

并且用户没有继续追问新问题。

#### 条件 C：超时关闭

推荐超时阈值：

- `10~15 分钟`

如果最后一条消息后用户未再发言，则自动结束当前轮次。

#### 条件 D：转人工或异常中止

如果已转人工，或服务异常中止，也应结束当前对话轮次并上传总结。

### 6.2 不应结束的情况

以下情况不应自动结束：

- 还在收集联系方式
- 还在等待用户确认更新操作
- 还在追问缺失的船舶标识信息
- 用户明显在继续同一个任务

---

## 7. 会话总结器应该如何生成记录

总结器输出必须严格对齐当前数据库字段。

### 7.1 推荐生成规则

#### `primary_category`

按主诉求归类：

- 产品功能、FAQ、使用说明 -> `使用需求`
- 船位、档案、PSC、区域、海峡、更新类 -> `生产需求`
- 试用、报价、合作、联系销售 -> `商务需求`
- 故障、异常、投诉、失败反馈 -> `问题反馈`
- 同时包含两个以上主要方向 -> `混合需求`

#### `summary_content`

必须用 1 段自然语言总结单次对话，包含：

- 用户想做什么
- 系统做了什么
- 最终结果如何
- 是否留下联系方式

示例：

`用户咨询 Hifleet 平台功能，并进一步表示希望试用。系统已介绍核心能力，并收集到用户姓名与手机号，建议销售后续跟进。`

#### `resolution_status`

按结果赋值：

- 已解决 -> `resolved`
- 部分解决 -> `partially_resolved`
- 未解决 -> `unresolved`
- 已留资 -> `lead_captured`
- 需要人工 -> `handoff_required`

#### `follow_up_needed`

以下情况设为 `true`：

- 商务机会需要跟进
- 用户问题未解决
- 已转人工
- 需要补充处理

其他情况一般设为 `false`

#### 联系方式字段

仅在用户主动提供时填写：

- `contact_name`
- `contact_phone`
- `contact_email`

不要编造，不要猜测。

---

## 8. 推荐的插入数据格式

当前表对应的推荐插入对象如下：

```json
{
  "conversation_round_id": "sess_7482_20260403100825",
  "session_id": "sess_7482",
  "source_channel": "websdk",
  "started_at": "2026-04-03T10:00:00+08:00",
  "ended_at": "2026-04-03T10:08:25+08:00",
  "turn_count": "6",
  "primary_category": "商务需求",
  "summary_content": "用户先咨询 Hifleet 平台功能，后续表达试用意向。系统已完成产品能力介绍，并收集到姓名和手机号，建议销售继续跟进。",
  "contact_name": "张三",
  "contact_phone": "13800138000",
  "contact_email": "",
  "resolution_status": "lead_captured",
  "follow_up_needed": "true",
  "uploaded_at": "2026-04-03T10:08:26+08:00"
}
```

注意：

- Coze 插入接口的 `insert_rows` 里，值按官方文档要求以字符串形式传入最稳
- `integer / boolean / time` 最终由系统按表结构转换

---

## 9. 插入 API 开发指导

### 9.1 开发原则

只在“单次对话结束”时插入一条记录。

不要：

- 每轮都插入
- 会话未结束就插入最终总结

### 9.2 请求格式

```bash
curl --location --request POST 'https://api.coze.cn/v1/databases/:database_id/records' \
  --header 'Authorization: Bearer ${COZE_ACCESS_TOKEN}' \
  --header 'Content-Type: application/json' \
  --data-raw '{
    "insert_rows": [
      {
        "conversation_round_id": "sess_7482_20260403100825",
        "session_id": "sess_7482",
        "source_channel": "websdk",
        "started_at": "2026-04-03T10:00:00+08:00",
        "ended_at": "2026-04-03T10:08:25+08:00",
        "turn_count": "6",
        "primary_category": "商务需求",
        "summary_content": "用户先咨询 Hifleet 平台功能，后续表达试用意向。系统已完成产品能力介绍，并收集到姓名和手机号，建议销售继续跟进。",
        "contact_name": "张三",
        "contact_phone": "13800138000",
        "contact_email": "",
        "resolution_status": "lead_captured",
        "follow_up_needed": "true",
        "uploaded_at": "2026-04-03T10:08:26+08:00"
      }
    ],
    "is_async": false
  }'
```

### 9.3 插入时机

插入应发生在：

1. 主 Agent 完成最后一轮回复
2. 会话结束判定器返回 `true`
3. 会话总结器输出结构化对象
4. 调用数据库插入接口

### 9.4 插入成功判断

成功标准：

- `code = 0`
- 同步模式下 `data.affected_rows = 1`

失败时：

- 记录失败原因
- 不要阻断主客服回复
- 可进入重试队列或延迟补写

---

## 10. 更新 API 开发指导

当前精简表虽然字段不多，但仍然建议保留更新能力。

### 10.1 哪些情况用更新

适用场景：

- 初次入库后，后续补到了联系方式
- 初次判断为 `partially_resolved`，后续人工处理完成
- 会话被超时关闭后，补充了最终结果

### 10.2 更新条件建议

按 `conversation_round_id` 更新最合适。

原因：

- 它是一条单次对话总结的唯一业务标识

### 10.3 更新示例

```bash
curl --location --request PUT 'https://api.coze.cn/v1/databases/:database_id/records' \
  --header 'Authorization: Bearer ${COZE_ACCESS_TOKEN}' \
  --header 'Content-Type: application/json' \
  --data-raw '{
    "update_fields": [
      { "field_name": "contact_phone", "value": "13800138000" },
      { "field_name": "resolution_status", "value": "lead_captured" },
      { "field_name": "follow_up_needed", "value": "true" }
    ],
    "filter": {
      "logic": "and",
      "conditions": [
        {
          "left": "conversation_round_id",
          "operation": "equal",
          "right": "sess_7482_20260403100825"
        }
      ]
    },
    "is_async": false
  }'
```

---

## 11. Coze agent 具体开发要求

下面这部分是给 Coze agent 的实施要求。

### 11.1 主 Agent 不直接操作数据库结构

主 Agent 的职责是：

- 处理用户对话
- 维护会话状态
- 生成会话总结输入

不要让主 Agent 在对话时“边回复边写库”。

### 11.2 增加一个“总结上传器”能力

建议新增一个内部工具或工作流节点：

- `conversation_summary_uploader`

职责：

1. 接收本次对话状态
2. 判断是否结束
3. 生成数据库记录对象
4. 调用 Coze 插入 API

### 11.3 推荐内部输入结构

```json
{
  "session_id": "sess_xxx",
  "conversation_round_id": "round_xxx",
  "source_channel": "websdk",
  "started_at": "2026-04-03T10:00:00+08:00",
  "ended_at": "2026-04-03T10:08:25+08:00",
  "turn_count": 6,
  "messages": [],
  "lead_info": {
    "contact_name": "",
    "contact_phone": "",
    "contact_email": ""
  },
  "task_status": "completed",
  "uploaded": false
}
```

### 11.4 推荐内部输出结构

```json
{
  "should_upload": true,
  "insert_row": {
    "conversation_round_id": "sess_xxx_round_01",
    "session_id": "sess_xxx",
    "source_channel": "websdk",
    "started_at": "2026-04-03T10:00:00+08:00",
    "ended_at": "2026-04-03T10:08:25+08:00",
    "turn_count": "6",
    "primary_category": "生产需求",
    "summary_content": "用户查询指定船舶的实时船位，系统已成功返回查询结果，本次对话已闭环。",
    "contact_name": "",
    "contact_phone": "",
    "contact_email": "",
    "resolution_status": "resolved",
    "follow_up_needed": "false",
    "uploaded_at": "2026-04-03T10:08:26+08:00"
  }
}
```

---

## 12. 推荐测试用例

### 用例 1：FAQ 完成后自动总结

输入：

- 用户问功能
- Agent 正常回答
- 用户表示“好的，明白了”

预期：

- `primary_category = 使用需求`
- `resolution_status = resolved`
- 自动插入 1 条记录

### 用例 2：生产查询闭环

输入：

- 用户查询船位
- 下游生产工作流返回结果
- 用户未继续追问

预期：

- `primary_category = 生产需求`
- `follow_up_needed = false`

### 用例 3：商务线索

输入：

- 用户要求试用或报价
- 已收集手机号

预期：

- `primary_category = 商务需求`
- `contact_phone` 被正确写入
- `resolution_status = lead_captured`
- `follow_up_needed = true`

### 用例 4：问题反馈

输入：

- 用户反馈产品异常
- 当前未解决

预期：

- `primary_category = 问题反馈`
- `resolution_status = unresolved`
- `follow_up_needed = true`

### 用例 5：超时关闭

输入：

- 用户完成一轮查询后 15 分钟未再发消息

预期：

- 系统自动结束并上传总结

---

## 13. 当前最合理的迭代策略

基于当前已经落地的精简表，推荐按以下顺序迭代：

### Phase 1

完成：

- 会话结束判定
- 会话总结生成
- 自动插入

### Phase 2

完成：

- 插入失败重试
- 更新能力
- 去重机制

### Phase 3

如果后续需要更细分析，再扩字段或新增表，例如：

- 原始消息表
- 商务线索表
- 问题反馈专项表

---

## 14. 最终建议

当前数据库已经够用，重点不再是继续改表，而是把上传链路做好：

1. 用当前精简表先跑通会话总结
2. 用“结束判定器 + 总结器 + 上传器”实现自动上传
3. 只在单次对话结束时插入一条
4. 后续需要补数据时再用更新接口

这是当前阶段最稳、最容易落地的实现方式。
