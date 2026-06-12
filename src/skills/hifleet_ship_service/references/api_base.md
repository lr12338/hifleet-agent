# API 基址与认证规范

## API 基址

| 域名 | 用途 | 环境变量 |
|------|------|---------|
| `https://api.hifleet.com` | 通用 API（船位/档案/PSC/区域/海峡/航程/红海绕航） | `HIFLEET_API_BASE`（可选，默认 api.hifleet.com） |
| `http://ttseapi.hifleet.com` | 搜索服务（shipSearchText）+ 上传服务 | `HIFLEET_TTSE_BASE`（可选，默认 ttseapi.hifleet.com） |

## 认证

| 环境变量 | 必填 | 适用域名 | 说明 |
|---------|------|---------|------|
| `api_key` | 是 | api.hifleet.com | 船位、档案、航程、区域、海峡、红海绕航 |
| `hifleet_key1` | 是 | api.hifleet.com | PSC 全系 |
| `hifleet_key2` | 是 | ttseapi.hifleet.com | 文本搜船、船位上传、静态信息更新 |
| `HIFLEET_API_KEY` | 兼容 | api.hifleet.com | 应与 `api_key` 保持一致 |
| `HIFLEET_TTSE_KEY` | 兼容 | ttseapi.hifleet.com | 应与 `hifleet_key2` 保持一致 |

### 参数名规则

- **api.hifleet.com 公开查询**：同时传 `api_key` 和 `usertoken`，值取 `api_key`。
- **PSC**：同时传 `api_key` 和 `usertoken`，值取 `hifleet_key1`。
- **ttseapi.hifleet.com**：传 `usertoken`，值取 `hifleet_key2`。
- 不要把三类 key 混用；授权错误通常表现为 `code=4001`。

### 认证方式汇总

| API | 域名 | 参数名 | 传递方式 |
|-----|------|--------|---------|
| shipSearchText | ttseapi | usertoken=`hifleet_key2` | Query 参数 |
| position/get | api | api_key/usertoken=`api_key` | Query 参数 |
| shiparchive | api | api_key/usertoken=`api_key` | Query 参数 |
| trajectory/callport/voyage/lastdeparture/getstop | api | api_key/usertoken=`api_key` | Query 参数 |
| pscapi/get | api | api_key/usertoken=`hifleet_key1` | Query 参数 |
| gettraffic / areas | api | api_key/usertoken=`api_key` | Query 参数 |
| statisticzonetraffic | api | api_key/usertoken=`api_key` | Query 参数 |
| getAvoidRedSeaDetail | api | api_key/usertoken=`api_key` | Query 参数 |
| updateShipAisInfo | ttseapi | usertoken=`hifleet_key2` | Query 参数 + JSON Body |
| updateShipAisStaticInfo | ttseapi | usertoken=`hifleet_key2` | Query 参数 + JSON Body |

## 错误码

| code | message | 含义 | 处理 |
|------|---------|------|------|
| 0 | - | 请求成功 | - |
| 4001 | unauthoried | Token 无权限访问该 API | 检查 Token 权限或联系 HiFleet |
| 4005 | token is empty | 未传 Token 或参数名错误 | 确认按接口传 `api_key/usertoken` |
| 500 | - | 服务端错误 | 稍后重试 |

## 请求格式

- **GET 请求**：Token 作为 Query 参数传递
- **POST 请求**：Token 作为 Query 参数，Body 为 JSON（Content-Type: application/json）
- **响应格式**：JSON

## 通用响应结构

```json
{
  "result": "ok",        // 请求结果：ok / error
  "code": 0,            // 错误码（仅出错时出现）
  "message": "...",      // 错误信息（仅出错时出现）
  "num": 1,             // 数据条数（列表类接口）
  "list": [...]          // 数据列表（列表类接口）
}
```
