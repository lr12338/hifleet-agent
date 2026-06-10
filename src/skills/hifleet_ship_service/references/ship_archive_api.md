# 船舶档案 API / Ship Archive API

按 MMSI 或 IMO 查询船舶详细参数（尺寸、载重吨、建造年份、船东等）。

## 请求

| 项目 | 值 |
|------|-----|
| 请求 URL | `{api_base}/shiparchive/getShipArchiveWithEnginAndCompany` |
| 请求方式 | GET |

### Query 参数

| 参数名 | 示例值 | 必选 | 类型 | 说明 |
|--------|--------|------|------|------|
| mmsi | 414726000 | 二选一 | string | 船舶 MMSI 编号 |
| imo | 9613886 | 二选一 | string | 船舶 IMO 编号 |
| usertoken | (HIFLEET_API_KEY) | 是 | string | 接口授权 |

> mmsi 和 imo 二选一，同时提供时以 mmsi 为准。

### 请求示例

```
GET https://api.hifleet.com/shiparchive/getShipArchiveWithEnginAndCompany?mmsi=414726000&usertoken={HIFLEET_API_KEY}
```

## 成功响应

```json
{
  "status": "ok",
  "data": [
    {
      "mmsi": "414726000",
      "name": "YU MING",
      "imo": "9613886",
      "type": "训练船",
      "flag": "中国",
      "length": 190,
      "width": 32,
      "dwt": 0,
      "gt": 15180,
      "nt": 4554,
      "built_year": 2012,
      "builder": "...",
      "owner": "...",
      "callsign": "BXSQ",
      "speed_max": 16.5,
      "draught_design": 7.8
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| mmsi | string | MMSI 编号 |
| name | string | 船名（英文） |
| imo | string | IMO 编号 |
| type | string | 船舶类型 |
| flag | string | 船旗国 |
| length | int | 船长（米） |
| width | int | 船宽（米） |
| dwt | int | 载重吨（DWT） |
| gt | int | 总吨位（GT） |
| nt | int | 净吨位（NT） |
| built_year | int | 建造年份 |
| builder | string | 建造厂 |
| owner | string | 船东 |
| callsign | string | 呼号 |
| speed_max | float | 最大航速（节） |
| draught_design | float | 设计吃水（米） |

## 调用流程

1. 需要 MMSI 或 IMO（若仅有船名，先执行船舶搜索）
2. 构造请求
3. 解析响应中的船舶参数
