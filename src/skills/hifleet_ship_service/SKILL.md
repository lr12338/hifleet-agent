---
name: hifleet-ship-service
version: 1.0.0
description: >
  HiFleet 船舶智能服务：船舶搜索/船位查询/档案查询/PSC检查/区域船舶/海峡通航统计/船位上传/静态信息更新。
  需按接口配置 api_key、hifleet_key1、hifleet_key2；兼容别名 HIFLEET_API_KEY=api_key、HIFLEET_TTSE_KEY=hifleet_key2。
  执行前须 read_file 对应分册。勿伪造数据。
metadata:
  openclaw:
    homepage: https://www.hifleet.com
    requires:
      anyBins:
        - python
        - python3
---

# HiFleet 船舶智能服务

## 能力一览

| 路由 | 能力 | 工具名 | API 端点 | 需 Token | 方法 |
|------|------|--------|---------|----------|------|
| A | 船舶搜索 | ship_search | `/position/shipSearchText` | hifleet_key2 | GET |
| B | 船位查询 | get_ship_position | `/position/position/get/token` | api_key | GET |
| C | 船舶档案 | get_ship_archive | `/shiparchive/getShipArchiveWithEnginAndCompany` | api_key | GET |
| D | PSC 检查记录 | get_psc_records | `/pscapi/get` | hifleet_key1 | GET |
| E | 区域船舶数量 | get_area_traffic | `/position/gettraffic/token` | api_key | GET |
| F | 海峡通航统计 | get_strait_traffic | `/position/statisticzonetraffic` | api_key | POST |
| G | 船位上传 | upload_ship_position | `/position/updateShipAisInfo` | hifleet_key2 | POST |
| H | 静态信息更新 | update_ship_static_info | `/position/updateShipAisStaticInfo` | hifleet_key2 | POST |

## 必读分册

执行任意工作流前：**read_file `references/api_base.md`**（认证、基址、错误码）。

按需读取：
- 船舶搜索 → **`references/ship_search_api.md`**
- 船位查询 → **`references/ship_position_api.md`**
- 船舶档案 → **`references/ship_archive_api.md`**
- PSC 检查 → **`references/psc_api.md`**
- 区域船舶 → **`references/area_traffic_api.md`**
- 海峡通航 → **`references/strait_traffic_api.md`**
- 船位上传 → **`references/ship_position_upload_api.md`** + **`references/coord_conversion.md`**
- 静态信息更新 → **`references/ship_static_update_api.md`**

## 路由决策

1. **判断意图类型**：
   - 包含"更新/上传/修改" → 更新类（G/H）
   - 包含"查询/搜索/在哪里/多少船/通航" → 查询类（A-F）

2. **判断查询对象**：
   - 具体船名/MMSI/IMO → A/B/C/D
   - 区域名称 → E
   - 海峡名称 → F

## 输出处理（最高优先级 - 必须遵守）

- ✅ **直接透传**：工具返回的有效结果**直接原样返回给用户，一字不改**
- ✅ 保留原始换行、空格、标点，不做任何修改
- ❌ **禁止**重新组织格式、添加Markdown标题、修改换行或调整内容顺序
- ❌ **禁止**对返回内容进行摘要或删减
- ❌ **禁止**添加"您好"、"已为您查询到"等额外客套话
- ❌ **禁止**将HTML的<a>标签转换为纯文本URL后丢失格式
- ❌ **禁止**用用户提供的期望值替换工具真实返回值（如：工具返回更新时间07:34:12，不得改为用户要求的15:30:00）
- ❌ **禁止**编造工具未返回的任何数据字段
- 如果工具返回错误信息，直接将错误信息告知用户

## Workflow

### Workflow 1: 船舶搜索（路由 A）

用户给出船名或关键字，搜索获取 MMSI/IMO。

1. 调用 `ship_search` 工具，参数：keyword="YU MING"
2. 返回匹配船舶列表（MMSI、IMO、船名、船型、船旗）
3. 用户仅给船名时，此步骤为前置步骤，获取 MMSI 后进入 B/C/D

### Workflow 2: 船位查询（路由 B）

1. 需要 MMSI（若仅有船名，先执行 Workflow 1）
2. 调用 `get_ship_position` 工具，参数：mmsi="414726000"
3. 返回实时位置、航速、航向、状态等

### Workflow 3: 船舶档案（路由 C）

1. 需要 MMSI 或 IMO
2. 调用 `get_ship_archive` 工具，参数：mmsi="414726000" 或 imo="9613886"
3. 返回船舶详细参数（尺寸、载重吨、建造年份等）

### Workflow 4: PSC 检查记录（路由 D）

1. 需要 IMO（若仅有 MMSI，先通过 Workflow 1/3 获取 IMO）
2. 调用 `get_psc_records` 工具，参数：imo="9613886"
3. 返回 PSC 检查记录和滞留情况

### Workflow 5: 区域船舶查询（路由 E）

1. 用户给出区域名称或坐标范围
2. 区域名称 → 内置映射表获取 areaId
3. 坐标范围 → 构造 bbox（`左经,下纬,右经,上纬`）或 polygon
4. 调用 `get_area_traffic` 工具，参数：area_name="红海" 或 area_id="1" 或 bbox="120,15,121,17"
5. 返回区域船舶数量

### Workflow 6: 海峡通航统计（路由 F）

1. 用户给出海峡名称 + 日期范围
2. 海峡名称 → OID 映射表
3. 调用 `get_strait_traffic` 工具，参数：strait_name="霍尔木兹海峡" startdate="2026-05-07" enddate="2026-05-07"
4. 返回通航统计数据（按方向、船型分类）
5. 霍尔木兹海峡特殊：东行=出湾（波斯湾→阿曼湾），西行=进湾（阿曼湾→波斯湾）

### Workflow 7: 船位上传（路由 G）

1. 用户当前请求或当前附件明确提供：MMSI/IMO/船名至少其一 + 经度 + 纬度 + 更新时间
2. 经纬度格式转换（度分秒 → 十进制度），工具内部自动处理
3. 需要唯一 MMSI；若仅有 IMO，先执行 Workflow 1 搜索，唯一匹配后可继续更新；若仅有船名，先执行 Workflow 1 搜索候选，即使唯一匹配也要请用户确认 MMSI 后再更新，多候选或无匹配时要求用户确认 MMSI
4. 调用 `upload_ship_position` 工具，参数：mmsi="414726000" lon="116.875" lat="22.125" updatetime="2026-06-30 10:16:00"，可选传 speed/heading/course/draft/navstatus/destination/eta
5. 返回上传结果
6. 仅当本轮输入字段完整且船舶标识明确时直接执行；不得用历史 MMSI 或当前系统时间补值

### Workflow 8: 静态信息更新（路由 H）

1. 用户提供：船名/MMSI + 更新字段
2. 需要 MMSI（若仅有船名，先执行 Workflow 1）
3. 调用 `update_ship_static_info` 工具，参数：mmsi="636025653" destination="LUOYUAN" eta="2026-05-09 20:00:00"
4. 返回更新结果
5. **直接执行无需确认**
6. 若更新船舶类型，`ship_type` 与 `minotype` 代表同一个业务字段，写入时必须同步为同一个标准船型目录值

## 澄清策略

只做**最小必要澄清**，不替代下游工作流的大规模参数编排。

**澄清场景**：
- 船位查询/档案查询/PSC查询：优先补充船名/MMSI/IMO之一
- 区域船舶查询：补充区域名称
- 海峡通航统计：补充海峡名称、时间范围
- 船位上传：补充目标船舶标识、经度、纬度、更新时间；航速、船首向、航迹向、吃水、航行状态、目的港、ETA 可选补充
- 静态更新：补充目标船舶标识和待更新字段

**原则**：
- 只做一层轻量澄清
- 一旦具备基本请求形式，直接交给工具
- 不要复制下游工作流的字段推理逻辑

## Notes

- **经纬度格式**：用户输入可能是度分秒（116°52.5′E），工具内部自动转换为十进制度（116.875）
- **MMSI 补全**：用户仅给船名时，必须先搜索获取 MMSI，再执行后续查询/更新
- **IMO 补全**：PSC 查询需要 IMO，可通过搜索或档案接口获取
- **日期**：当前年份为 2026 年，用户未指定年份时默认 2026
- **霍尔木兹海峡**：东行=出湾（波斯湾→阿曼湾），西行=进湾（阿曼湾→波斯湾）
- **海峡通航**：无需 api_key 也可查询，但仅限近 1 周数据
- **更新操作**：仅更新用户当前请求或当前附件明确提供的参数，不设置默认值
- **更新缺失数据**：船位上传缺少可唯一解析的船舶标识、经度、纬度、更新时间任一项时，提示用户补充
- **船名更新规则**：用户只给船名时必须先搜索候选船舶；即使唯一匹配，也要返回船名/MMSI/IMO 请用户确认后再更新；无匹配或多候选时要求用户确认 MMSI，不按中文名/简称/船队名前缀猜测更新
- **船型更新规则**：`ship_type` 和 `minotype` 均表示船舶类型；用户提供船型时同时写入 `type` 与 `minotype`，目录外或冲突值不直接更新
- **写操作规则**：仅在本轮字段完整且目标船舶明确时执行；不得默认复用历史 MMSI，不得编造更新时间或状态修正结论
