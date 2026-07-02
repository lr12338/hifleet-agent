# 船舶静态信息更新 API / Ship Static Info Update API

更新船舶档案信息（船名、IMO、船型、目的港等）。

## 请求

| 项目 | 值 |
|------|-----|
| 请求 URL | `{ttse_base}/position/updateShipAisStaticInfo` |
| 请求方式 | POST |

### 请求头

| 参数名 | 值 |
|--------|-----|
| Content-Type | application/json |

### 请求 Body（JSON）

| API字段名 | 必选 | 类型 | 说明 |
|-----------|------|------|------|
| mmsi | 是 | string | 船舶 MMSI |
| bindCheck | 是 | string | 是否检查绑定微信，"0"=不检查，"1"=检查。建议传"0" |
| name | 否 | string | 船名（必须为英文船名）|
| imonumber | 否 | string | IMO 编号 |
| callsign | 否 | string | 呼号 |
| type | 否 | string | 船型描述（如"散货船"、"渔船"）|
| minotype | 否 | string | 船舶子类型（船舶详情显示的是该字段；本地工具要求与 type 同步）|
| width | 否 | string | **船宽**（米）⚠️ API文档描述误写为"船长"，实际为船宽 |
| length | 否 | string | **船长**（米）⚠️ API文档描述误写为"船宽"，实际为船长 |
| dwt | 否 | string | 载重吨 |
| buildyear | 否 | string | 建造年份，格式 yyyy |
| wechatgroup | 否 | string | 分组ID，控制权限用，可为空 |
| destination | 否 | string | 目的港 |
| eta | 否 | string | 预抵时间 |
| draught | 否 | double | 吃水（米）|

> ⚠️ **字段名映射是关键**：工具参数名必须映射到API字段名，否则API不识别该字段，静默丢弃。
> ⚠️ **width/length 描述勘误**：API官方文档中 width 标注为"船长"、length 标注为"船宽"，这是描述错误。根据标准海事惯例和请求示例验证（width=12.3, length=30.5），实际含义为 width=船宽、length=船长。

### 请求示例

```json
{
  "bindCheck": "0",
  "destination": "-",
  "eta": "2021-05-22 00:00:00",
  "draught": "1.0",
  "name": "00888 02",
  "imonumber": "0",
  "callsign": "0",
  "type": "渔船",
  "minotype": "渔船",
  "width": "12.3",
  "length": "30.5",
  "dwt": "12000",
  "buildyear": "2018",
  "mmsi": "100000278"
}
```

## 工具参数名 → API字段名映射

| 工具参数名 | → API字段名 | 说明 |
|-----------|------------|------|
| ship_name | name | 船名（英文）|
| imo | imonumber | IMO编号 |
| ship_type | type | 船型；若传船舶类型，本地工具会同步写入 type 和 minotype |
| built_year | buildyear | 建造年份 |
| draft | draught | 吃水 ⚠️ 与船位上传API不同 |
| callsign | callsign | 呼号 |
| minotype | minotype | MINO船型代码；本地工具不建议单独更新，默认与 ship_type 同步 |

> 本地工具额外规则：船舶类型仅支持标准目录值；目录外输入不会直接写入，会要求用户确认后再更新。
| wechatgroup | wechatgroup | 微信群组 |
| destination | destination | 目的港 |
| eta | eta | 预抵时间 |
| width | width | 船宽 |
| length | length | 船长 |
| dwt | dwt | 载重吨 |
| flag | flag | 船旗国（API文档未列出但实测可用）|

## 成功响应

```
更新成功！
```

或 JSON 格式：

```json
{"data": "更新成功！"}
```

## 失败响应

```json
{"status": 404, "error": "Not Found", "message": "No message available"}
```

> 如果出现 404，请确认 URL 路径是否正确：`/position/updateShipAisStaticInfo`（不是 updateShipStaticInfo）

## 调用流程

1. 用户提供 MMSI + 至少一个可更新的静态字段
2. 构造请求 Body：仅包含用户提供的字段 + bindCheck
3. 发送 POST 请求
4. 解析响应（支持纯文本和JSON两种格式）
