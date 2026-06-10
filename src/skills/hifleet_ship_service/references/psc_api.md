# PSC 检查记录 API / PSC Inspection API

按 IMO 查询船舶港口国监督检查记录和滞留情况。

## 请求

| 项目 | 值 |
|------|-----|
| 请求 URL | `{api_base}/pscapi/get` |
| 请求方式 | GET |

### Query 参数

| 参数名 | 示例值 | 必选 | 类型 | 说明 |
|--------|--------|------|------|------|
| imo | 9613886 | 是 | string | 船舶 IMO 编号 |
| usertoken | (HIFLEET_API_KEY) | 是 | string | 接口授权 |

> ⚠️ PSC 查询需要 IMO 编号，不支持 MMSI 查询。若仅有 MMSI/船名，需先通过船舶搜索或档案查询获取 IMO。

### 请求示例

```
GET https://api.hifleet.com/pscapi/get?imo=9613886&usertoken={HIFLEET_API_KEY}
```

## 成功响应

```json
{
  "status": "ok",
  "data": [
    {
      "imo": "9613886",
      "shipname": "YU MING",
      "inspectdate": "2025-01-15",
      "port": "Shanghai",
      "country": "China",
      "authority": "MOT",
      "result": "无缺陷",
      "defects": [],
      "detained": false,
      "detention_days": 0,
      "inspection_type": "初次检查"
    }
  ]
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| imo | string | IMO 编号 |
| shipname | string | 船名 |
| inspectdate | string | 检查日期 |
| port | string | 检查港口 |
| country | string | 检查国家 |
| authority | string | 检查机构 |
| result | string | 检查结果 |
| defects | array | 缺陷列表 |
| detained | boolean | 是否被滞留 |
| detention_days | int | 滞留天数 |
| inspection_type | string | 检查类型 |

## 调用流程

1. 需要 IMO（若仅有 MMSI/船名，先通过船舶搜索或档案查询获取 IMO）
2. 构造请求
3. 解析响应中的检查记录

## 注意事项

- 部分 PSC 记录可能为空（船舶未被检查过）
- 返回数据按检查日期倒序排列
- IMO 为 7 位数字
