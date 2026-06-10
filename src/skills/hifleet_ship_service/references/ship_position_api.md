# 船位查询 API / Ship Position API

按 MMSI 查询船舶实时位置、航速、航向等动态信息。

## 请求

| 项目 | 值 |
|------|-----|
| 请求 URL | `{api_base}/position/position/get/token` |
| 请求方式 | GET |

### Query 参数

| 参数名 | 示例值 | 必选 | 类型 | 说明 |
|--------|--------|------|------|------|
| mmsi | 414726000 | 是 | string | 船舶 MMSI 编号 |
| usertoken | (HIFLEET_API_KEY) | 是 | string | 接口授权 |

### 请求示例

```
GET https://api.hifleet.com/position/position/get/token?mmsi=414726000&usertoken={HIFLEET_API_KEY}
```

## 成功响应

```json
{
  "result": "ok",
  "list": {
    "m": "414726000",           // MMSI
    "n": "YU MING",             // 船名
    "imo": "9613886",           // IMO
    "t": "训练船",               // 船型
    "f": "中国",                 // 船旗
    "lo": 119.668417,           // 经度（十进制度）
    "la": 39.930817,            // 纬度（十进制度）
    "s": 11.5,                  // 航速（节）
    "h": 272,                   // 航首向（度）
    "c": 273,                   // 航迹向（度）
    "d": 10.1,                  // 吃水（米）
    "dest": "KARACHI",          // 目的港
    "eta": "2026-04-14 17:00:00", // 预抵时间
    "ut": 1713483602,           // 更新时间戳
    "st": "机动船在航",          // 航行状态
    "dim": "190/32"             // 船长/船宽（米）
  }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| m | string | MMSI 编号 |
| n | string | 船名 |
| imo | string | IMO 编号 |
| t | string | 船舶类型 |
| f | string | 船旗国 |
| lo | float | 经度（十进制度，东经为正） |
| la | float | 纬度（十进制度，北纬为正） |
| s | float | 对地航速（节） |
| h | int | 航首向（度，0-359） |
| c | int | 航迹向（度，0-359） |
| d | float | 当前吃水（米） |
| dest | string | 目的港代码 |
| eta | string | 预抵时间（UTC） |
| ut | int | 更新时间戳（Unix） |
| st | string | 航行状态 |
| dim | string | 船长/船宽（米） |

## 调用流程

1. 需要 MMSI（若仅有船名，先执行船舶搜索）
2. 构造请求：`GET {api_base}/position/position/get/token?mmsi={mmsi}&usertoken={key}`
3. 解析响应中的位置和动态信息
