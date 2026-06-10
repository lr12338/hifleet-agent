# 海峡通航统计 API / Strait Traffic API

查询四大咽喉航道的通航统计数据，支持按方向和船型分类。

## 请求

| 项目 | 值 |
|------|-----|
| 请求 URL | `{api_base}/position/statisticzonetraffic` |
| 请求方式 | POST |

### Query 参数

| 参数名 | 示例值 | 必选 | 类型 | 说明 |
|--------|--------|------|------|------|
| oid | 24480 | 是 | string | 海峡 OID（从映射表获取） |
| startdate | 2026-05-07 | 是 | string | 开始日期（yyyy-MM-dd） |
| enddate | 2026-05-07 | 是 | string | 结束日期（yyyy-MM-dd） |
| i18n | zh | 否 | string | 语言（zh/en），默认 zh |
| usertoken | (HIFLEET_API_KEY) | 否 | string | 接口授权（无 token 仅查近 1 周） |

### 请求示例

```
POST https://api.hifleet.com/position/statisticzonetraffic?oid=24480&startdate=2026-05-07&enddate=2026-05-07&i18n=zh&usertoken={HIFLEET_API_KEY}
```

## 成功响应

```json
{
  "passdata": [
    {
      "passdate": "2026-05-07",
      "passdirection": [
        {
          "direction": "东",
          "total": 5,
          "shiptypecount": [
            {"shiptype": "石油化学品船", "count": 3},
            {"shiptype": "散货船", "count": 2}
          ],
          "ships": [
            {
              "mmsi": 414726000,
              "imonumber": 9613886,
              "shipname": "YU MING",
              "type": "训练船",
              "dwt": 0,
              "flagname": "中国"
            }
          ]
        },
        {
          "direction": "西",
          "total": 3,
          "shiptypecount": [...],
          "ships": [...]
        }
      ]
    }
  ]
}
```

## 海峡 OID 映射表

| 海峡名称 | OID | 英文名 |
|---------|-----|--------|
| 霍尔木兹海峡 | 24480 | Strait of Hormuz |
| 曼德海峡 | 24471 | Bab el-Mandeb Strait |
| 苏伊士运河 | 24474 | Suez Canal |
| 好望角 | 24476 | Cape of Good Hope |
| 龙目海峡 | 24482 | Lombok Strait |

## 霍尔木兹海峡特殊说明

霍尔木兹海峡方向需特殊映射：
- **东行 = 出湾**（波斯湾 → 阿曼湾）
- **西行 = 进湾**（阿曼湾 → 波斯湾）

其他海峡直接使用 API 返回的原始方向名称（如"东南"、"西北"等）。

## 注意事项

- 不同海峡的方向命名不同（霍尔木兹用"东/西"，曼德用"东南/西北"）
- 无 usertoken 时仅可查询近 1 周数据
- 日期范围不宜超过 30 天
- 返回数据包含每日每方向的船舶数量和船型分布
