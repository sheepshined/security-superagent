# 深信服 XDR 平台对接剩余问题报告

> 日期：2026-08-13
> 范围：Security SuperAgent 与深信服 XDR（XDR 2.0.20，`https://103.198.125.151:1443`）OpenAPI 对接
> 依据：eolink《深信服XDR平台接口开放列表》(129 接口)、官方三语 SDK 示例、真实平台签名请求实测
> 本文不包含任何密钥、密码或凭证值。

## 一、需向官方申请 / 确认的能力接口（按优先级）

### 1.1 可执行封禁的设备接入 —— 当前写链路阻塞项

- **现象**：`responses/blockiprule/network`（新增网侧封禁）实测返回
  `InternalError: 设备不能为空`（devices 实际必填，文档却标 notNull=0）。
- **根因**：`device/blockdevice/list`（查询封禁设备列表）实测 `item: null`
  —— 当前平台实例没有任何已注册、可联动封禁的设备（AF/EDR 均未开通封禁联动）；
  `bigscreen/devlist` 实测返回 `Unknown.GeneralError: 系统繁忙`；
  `assets/sourcedevice` 只返回字符串设备类型码（af/edr/...），
  而文档示例 devId 为**数值型**，无任何接口给出数值 devId。
- **需要官方确认**：
  1. 如何在 XDR 平台接入/授权一个支持联动封禁的设备（如 AF 防火墙）？
  2. devId 的正确获取接口与字段类型是什么（数值还是字符串）？
  3. `bigscreen/devlist` 的正确调用参数（当前实测系统繁忙）？

### 1.2 新增隔离主机接口（edr.isolate_host）

- 129 个接口中「隔离主机」组只有两个：
  `responses/host/isolate/list`（查询，实测可用）和 `responses/host/unisolate`（解除）。
- **没有「新增隔离」接口**。
- 需要官方确认：是否提供/可开放新增隔离接口？若隔离只能通过 SOAR 剧本或
  平台 Web 端下发，请提供对应的开放接口或剧本触发方式。

### 1.3 账号/身份处置接口（iam.disable_account）

- 129 个接口中**账号/身份类接口数量为 0**（连查询都没有）。
- 需要官方确认：是否有账号禁用/锁定接口或 IAM/AD 联动方案？
  若无，身份处置是否只能由平台联动第三方 IAM 完成？

### 1.4 SOAR 剧本触发执行接口

- `soar/playbooks` 组只有执行历史/统计查询（5 个），无「触发剧本执行」接口。
- 若开放剧本触发，可作为隔离/处置的替代链路。请官方确认是否开放。

## 二、官方 eolink 文档勘误（全部经真实平台签名请求实测）

| # | 位置 | 文档标注 | 实测行为 |
|---|---|---|---|
| 1 | `alerts/list` | GET | **POST + JSON body**（GET 实测 405） |
| 2 | `alerts/:uuid/proof` | POST | **GET** |
| 3 | `incidents/:uuid/proof` | POST | **GET** |
| 4 | 封禁接口族 list/detail/network/endpoint/reblock/unblock | GET | **全部 POST + JSON body**（GET 实测 405） |
| 5 | `blockiprule/list` 的 pageSize | 未注明枚举 | 实测枚举 {10, 20, 50, 100}（与告警列表 5~200 不同） |
| 6 | `blockiprule/network` 的 devices | notNull=0 | 实测必填（设备不能为空）；devId 类型与获取方式未说明 |
| 7 | `host/isolate/list` 的 startTimestamp/endTimestamp | 必填 | 实测不带也能返回（有默认值） |

**网关前缀说明建议**：`/api/xdr/v1/` 为 OpenAPI 网关；`/api/v1.0/openapi/`
为 AI 共创网关，对签名请求返回 401（`please log in`），建议文档明确区分。

**签名/请求体一致性要求建议写入文档**：平台网关对 payload 做字节多重集哈希验签，
请求体必须与签名时序列化完全一致。含中文等非 ASCII 字符时，官方 demo 用
`json.dumps(ensure_ascii=True)` 转义；第三方 HTTP 库若按 UTF-8 原文发送会 401。
（本项目已按此修正并实测通过。）

## 三、数据完整性与字段问题（平台数据侧特性，非本项目缺陷）

1. **举证三域不完整**：WebShell（蚁剑/冰蝎）告警实测只返回 `http`/`networkInfo`
   举证 → 只有 network_flow，无 process_tree（端点进程树）与 identity_event（身份）。
   属平台 NDR/WAF 引擎数据侧特性；需平台接入 EDR 联动后才有端侧举证。
   当前研判会如实报告缺失并保守判定 `needs_review`。
2. **事件举证为空**：`incidents/:uuid/proof` 实测返回空 data，证据在关联告警
   （`incidents/list` 的 alertIds）上。建议官方确认事件举证接口后续是否直接返回聚合证据。
3. **`analysisInfo` 字段无文档**：DSP 告警样例每条都含 `analysisInfo`
   （智能引擎思考过程 JSON），但 OpenAPI 文档未定义其结构。计划用作第二道研判辅助，
   建议官方补充字段说明。
4. **`dsp` 举证类型未列入枚举**：DSP 样例 proofType 均含 `"dsp"`，
   不在文档的 40+ proofType 枚举说明中，建议补充。
5. **平台证书**：自签名、CA:FALSE 且无 SAN，OpenSSL 无法作信任锚；
   生产对接需要平台提供正式证书或至少带 SAN 的证书（当前采用证书指纹 PIN 临时方案）。

## 四、本侧已完成修复（2026-08-13，避免官方重复排查）

1. 响应适配器全部迁移到 `/api/xdr/v1/` + POST + JSON body：
   - `blockiprule/network` 的 `blockIpRule.view` 改为数组、补充 `mode:"in"`；
   - `blockiprule/detail`、`unblock` 的 `ids` 改为原生数组 body；
   - unblock 契约按实测修正：`code=Success` 但 `success=0`（封禁记录不存在）时，
     通过 detail 交叉验证后再判定 rolled_back，杜绝误报。
2. 签名序列化统一 `ensure_ascii=True` 并逐字节发送（修复含中文请求体的 401），
   `platform_xdr.py` 与 `platform_response.py` 同步修正。
3. 契约测试更新为真实平台契约（44 条契约测试、全量后端 130 条测试通过）。
4. 已实测验证（真实平台、只读+受限写）：
   - `alerts/list`（total=8）、`alerts/:uuid/proof`、incidents 聚合举证链路正常；
   - `blockiprule/detail`（dummy id → Success/空 item）、`blockiprule/unblock`
     （dummy id → success=0 + 封禁记录不存在）、`host/isolate/list`（Success/total=0）契约确认；
   - `blockiprule/network` 完整写链路的最终验证被 1.1 节「设备不能为空」阻塞，
     待官方协助接入封禁设备后补验。
