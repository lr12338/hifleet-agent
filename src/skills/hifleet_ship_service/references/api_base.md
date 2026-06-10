# API 基址与认证规范

## API 基址

| 域名 | 用途 | 环境变量 |
|------|------|---------|
| `https://api.hifleet.com` | 通用 API（船位/档案/PSC/区域/海峡） | `HIFLEET_API_BASE`（可选，默认 api.hifleet.com） |
| `http://ttseapi.hifleet.com` | 搜索服务（shipSearchText）+ 上传服务 | `HIFLEET_TTSE_BASE`（可选，默认 ttseapi.hifleet.com） |

## 认证

| 环境变量 | 必填 | 适用域名 | 说明 |
|---------|------|---------|------|
| `HIFLEET_API_KEY` | 是 | api.hifleet.com | 通用 API 授权 |
| `HIFLEET_TTSE_KEY` | 是 | ttseapi.hifleet.com | 搜索服务授权 |

### 参数名规则

- **api.hifleet.com**：当前使用 `usertoken` 参数名（值取 `HIFLEET_API_KEY`）
- **ttseapi.hifleet.com**：使用 `usertoken` 参数名（值取 `HIFLEET_TTSE_KEY`）
- **海峡通航**：同时支持 `usertoken` 和 `api_key`，甚至可不带 token
- **注意**：Skills 仓库文档标准为 `api_key`，但当前服务端大部分 API 仅接受 `usertoken`

### 认证方式汇总

| API | 域名 | 参数名 | 传递方式 |
|-----|------|--------|---------|
| shipSearchText | ttseapi | usertoken | Query 参数 |
| position/get | api | usertoken | Query 参数 |
| shiparchive | api | usertoken | Query 参数 |
| pscapi/get | api | usertoken | Query 参数 |
| areas | api | usertoken | Query 参数 |
| gettraffic | api | usertoken | Query 参数 |
| statisticzonetraffic | api | usertoken / api_key | Query 参数 |
| updateShipAisInfo | ttseapi | 无 | POST JSON Body |
| updateShipAisStaticInfo | ttseapi | 无 | POST JSON Body |

## 错误码

| code | message | 含义 | 处理 |
|------|---------|------|------|
| 0 | - | 请求成功 | - |
| 4001 | unauthoried | Token 无权限访问该 API | 检查 Token 权限或联系 HiFleet |
| 4005 | token is empty | 未传 Token 或参数名错误 | 确认使用 `usertoken` 参数名 |
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
