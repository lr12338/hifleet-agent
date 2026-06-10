# 船舶搜索 API / Ship Search API

按船名或关键字搜索船舶，获取 MMSI、IMO 等标识信息。通常作为其他查询的前置步骤。

## 请求

| 项目 | 值 |
|------|-----|
| 请求 URL | `{ttse_base}/position/shipSearchText` |
| 请求方式 | GET |

### Query 参数

| 参数名 | 示例值 | 必选 | 类型 | 说明 |
|--------|--------|------|------|------|
| keyword | YU MING | 是 | string | 搜索关键字（船名、MMSI 等） |
| usertoken | (HIFLEET_TTSE_KEY) | 是 | string | 接口授权 |

### 请求示例

```
GET http://ttseapi.hifleet.com/position/shipSearchText?keyword=YU%20MING&usertoken={HIFLEET_TTSE_KEY}
```

## 成功响应

```json
{
  "result": "ok",
  "num": 3,
  "list": [
    {
      "m": "414726000",       // MMSI
      "n": "YU MING",         // 船名
      "imo": "9613886",       // IMO
      "t": "训练船",           // 船型
      "f": "中国",             // 船旗
      "lo": 119.66,           // 经度（十进制度）
      "la": 39.93             // 纬度（十进制度）
    }
  ]
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
| lo | float | 经度 |
| la | float | 纬度 |

## 调用流程

1. 用户给出船名或关键字
2. 构造请求：`GET {ttse_base}/position/shipSearchText?keyword={keyword}&usertoken={key}`
3. 解析响应：从 list 中提取 MMSI/IMO
4. 若匹配多条，列出供用户选择
5. 后续查询使用选定的 MMSI/IMO

## 错误处理

| 错误码 | 原因 | 处理 |
|--------|------|------|
| 4001 | Token 无权限 | 检查 HIFLEET_TTSE_KEY 是否正确 |
| num=0 | 无匹配结果 | 提示用户检查船名拼写或尝试英文船名 |
