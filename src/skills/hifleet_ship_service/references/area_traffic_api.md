# 区域船舶查询 API / Area Traffic API

按区域 ID、bbox 或 polygon 查询指定区域的当前船舶数量和列表。

## 请求

| 项目 | 值 |
|------|-----|
| 请求 URL | `{api_base}/position/gettraffic/token` |
| 请求方式 | GET |

### Query 参数

| 参数名 | 示例值 | 必选 | 类型 | 说明 |
|--------|--------|------|------|------|
| areaId | 1 | 三选一 | int | 区域 ID（从内置映射表获取） |
| bbox | 120,15,121,17 | 三选一 | string | 矩形范围（左经,下纬,右经,上纬） |
| polygon | POLYGON((...)) | 三选一 | string | 多边形范围（WKT 格式） |
| usertoken | (HIFLEET_API_KEY) | 是 | string | 接口授权 |

> areaId / bbox / polygon 三选一。推荐使用 areaId（最稳定）。

### 请求示例

```
# 按 areaId 查询
GET https://api.hifleet.com/position/gettraffic/token?areaId=1&usertoken={HIFLEET_API_KEY}

# 按 bbox 查询（小范围）
GET https://api.hifleet.com/position/gettraffic/token?bbox=120,15,121,17&usertoken={HIFLEET_API_KEY}
```

## 成功响应

```json
{
  "result": "ok",
  "num": 2167,
  "list": [
    {
      "m": "414726000",
      "n": "YU MING",
      "t": "训练船",
      "f": "中国",
      "lo": 119.66,
      "la": 39.93,
      "s": 11.5,
      "h": 272
    }
  ]
}
```

## 内置区域 ID 映射表

| 区域名称 | areaId | 英文名 |
|---------|--------|--------|
| 红海 | 1 | Red Sea |
| 波斯湾 | 2 | Persian Gulf |
| 亚丁湾 | 3 | Gulf of Aden |
| 地中海 | 4 | Mediterranean |
| 马六甲海峡 | 5 | Malacca Strait |
| 南海 | 6 | South China Sea |
| 印度洋 | 7 | Indian Ocean |
| 北太平洋 | 8 | North Pacific |
| 好望角 | 9 | Cape of Good Hope |
| 加勒比海 | 10 | Caribbean Sea |
| 黑海 | 11 | Black Sea |
| 阿拉伯海 | 12 | Arabian Sea |
| 东非沿海 | 13 | East Africa |
| 西非沿海 | 14 | West Africa |
| 渤海 | 15 | Bohai Sea |

> ⚠️ areas API（/position/areas/token）对当前 Token 无权限，使用内置映射表替代。

## 注意事项

- bbox 范围不宜过大，建议经纬度跨度不超过 5°
- polygon 需使用 WKT 格式
- 返回数据为实时快照，不含历史数据
