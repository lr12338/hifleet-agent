# 船舶位置更新（上传）API / Ship Position Upload API

更新船舶动态位置信息（经纬度、航速、航向等）。

## 请求

| 项目 | 值 |
|------|-----|
| 请求 URL | `{ttse_base}/position/updateShipAisInfo` |
| 请求方式 | POST |

### 请求头

| 参数名 | 值 |
|--------|-----|
| Content-Type | application/json |

### 请求 Body（JSON）

| API字段名 | 必选 | 类型 | 说明 |
|-----------|------|------|------|
| name | 是 | string | 船名或 MMSI，通常传 MMSI，例如 `"LEO I"` 或 `"353738000"` |
| mmsi | 否 | string | 船舶 MMSI，例如 `"353738000"` |
| lon | 是 | float | 经度，十进制度，例如 `122` |
| lat | 是 | float | 纬度，十进制度，例如 `31` |
| updatetime | 是 | string | 更新时间，格式 `yyyy-MM-dd HH:mm:ss`，例如 `"2025-03-31 09:52:13"` |
| speed | 否 | float | 航速（节），例如 `7.1` |
| heading | 否 | float | 船首向（度），例如 `135.0` |
| course | 否 | float | 航迹向（度），例如 `254.1` |
| draught | 否 | float | 吃水（米），例如 `4.2` |
| destination | 否 | string | 目的港，例如 `"DA LIAN"` |
| eta | 否 | string | 预抵时间，例如 `"2025-03-27 14:00"` |
| status | 否 | string | 航行状态（中文文本，见下方状态值列表）|
| wechatgroup | 否 | string | 分组ID，控制权限用 |
| checkFly | 否 | string | 值为"0"时不做飞点校验，其他做校验 |
| bindCheck | 否 | string | 是否做绑定船队校验，"0"不校验，默认校验 |

### status 航行状态可选值（中文文本，直接传入）

```
在航 | 失控 | 帆船在航 | 搁浅 | 操纵能力受限 | 机动船在航
系泊 | 锚泊 | 停泊 | 未知 | 未定义 | 正在捕鱼作业
限于吃水 | 高速船留用 | 地效翼船留用 | 待定义
```

### 请求示例

```json
{
    "wechatgroup": "51817237583@chatroom",
    "destination": "DA LIAN",
    "eta": "2025-03-27 14:00",
    "draught": 4.2,
    "heading": 135.0,
    "name": "LEO I",
    "mmsi": "353738000",
    "updatetime": "2025-03-31 09:52:13",
    "lon": 122,
    "lat": 31,
    "speed": 7.1,
    "course": 254.1
}
```

## 工具参数名 → API字段名映射

Agent / skill 调用工具时必须传工具参数，不直接传 API body。工具内部负责字段映射、经纬度转换和 `checkFly/bindCheck` 默认值。

| 工具参数名 | → API字段名 | 说明 |
|-----------|------------|------|
| mmsi | name + mmsi | 同时传name和mmsi字段 |
| lon | lon | 经度（经coord_utils转换）|
| lat | lat | 纬度（经coord_utils转换）|
| speed | speed | 航速 |
| heading | heading | 船首向 |
| course | course | 航迹向 |
| draft | draught | 吃水 ⚠️ 字段名为draught |
| updatetime | updatetime | 更新时间 |
| navstatus | status | 航行状态（中文文本）⚠️ 不是数字代码 |
| destination | destination | 目的港 |
| eta | eta | 预抵时间 |
| ship_name | name | 船名（如需更新）|
| wechatgroup | wechatgroup | 微信群组 |

### 工具调用参数示例

```json
{
  "mmsi": "353738000",
  "lon": "122",
  "lat": "31",
  "updatetime": "2025-03-31 09:52:13",
  "speed": "7.1",
  "heading": "135.0",
  "course": "254.1",
  "draft": "4.2",
  "destination": "DA LIAN",
  "eta": "2025-03-27 14:00:00",
  "navstatus": "机动船在航",
  "ship_name": "LEO I"
}
```

### 解析和格式化规则

- `lon/lat` 最终优先使用十进制度；输入为 `116°19.746′ E`、`29°49.007′ N` 时可先解析为 `116.3291`、`29.816783`。
- `updatetime` 必须来自用户文本或附件，不能用当前系统时间补齐。
- `speed/heading/course/draft` 可从 `0 kn`、`163°`、`1.6 m` 等展示文本提取数值。
- `船艏/航迹向: A / B` 解析为 `heading=A`、`course=B`。
- `eta` 为可选字段，最终建议归一为 `yyyy-MM-dd HH:mm:ss`；无法归一或残缺时丢弃，不阻断船位更新。
- `目的港/ETA: -- / --`、`/ETA`、`ETA`、`N/A`、`未知` 等占位符表示未提供，不传 `destination/eta`。

## 成功响应

纯文本，示例：

```
更新成功！
MMSI: 777777771
...
```

## 失败响应

- `"该群组未绑定账号，请联系管理员绑定账号"` → 需要添加 bindCheck="0"
- `"经度或纬度为空"` → lon/lat为必填字段

## 注意事项

1. **lon/lat 为必填**：即使只更新航速/吃水等，也必须提供经纬度
2. **bindCheck="0"**：建议始终传入，跳过群组绑定检查
3. **checkFly="0"**：建议始终传入，跳过飞点校验
4. **status 传中文文本**：如"机动船在航"、"锚泊"等，不要传数字代码
5. **经纬度转换**：工具内部自动将度分秒/度分格式转为十进制度
