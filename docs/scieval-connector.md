# SciEval Connector 联调说明

本分支在 Vika 中增加 **入站事件** Connector。它从自建 SciEval 服务轮询不可变评测报告，生成 Connector 0.2 的 `evaluation.completed` 事件。当前版本不声明 `evaluation.submit`，不会向模型增加提交工具；HTML 产物仍由 workspace 编排脚本通过 HTTP 提交。

## 服务端契约

Connector 启动时调用 `GET /health`，要求 2xx JSON 响应和 `Authorization: Bearer <token>` 认证。

事件接口：

```text
GET /v1/events?after=<url-encoded-cursor>&limit=100
```

响应示例：

```json
{
  "events": [{
    "cursor": "42",
    "report": {
      "report_id": "rpt_123",
      "evaluation_id": "eval_123",
      "job_id": "job_123",
      "round": 1,
      "input_sha256": "<64 lowercase hex>",
      "created_at": "2026-11-12T06:00:00Z",
      "criteria": [],
      "decision": {"result": "revise", "reason": "example"}
    }
  }],
  "has_more": false
}
```

- `cursor` 由服务端持久化，按事件顺序单调推进。空字符串表示从头读取；首次生产部署建议从当前末尾开始，避免重放历史报告。
- `report_id` 必须稳定且不可复用。Connector 生成事件 ID `evaluation.completed:<report_id>`，Core 按 `(instance, event_id)` 去重。
- `job_id`、`round`、`input_sha256` 和 `created_at` 必填。报告正文放在事件的 `content.text` JSON 中，下游仍需执行完整的 13 项 schema、当前轮次和产物哈希校验。
- 报告导致事件超过 `VIKA_MAX_EVENT_BYTES` 时，Connector 改发有界 `evaluation.failed`，带 `requires_human_review=true`，不静默丢弃，也不进入自动改稿。
- 服务端必须限制响应大小、事件大小和单页数量。无效事件会记录 `scieval_poll_failed` 并重试，不能让错误游标继续推进。

## Vika 配置

在实例的 `connectors` 中加入 `scieval`，主 IM 保持不变：

```json
{
  "channel": "lark",
  "connectors": ["lark", "scieval"]
}
```

配置 daemon 环境：

```bash
export SCIEVAL_BASE_URL=https://scieval.example.edu
export SCIEVAL_TOKEN_FILE=/srv/scieval/secrets/scieval.token
export SCIEVAL_POLL_INTERVAL_MS=30000
export SCIEVAL_REQUEST_TIMEOUT_MS=20000
chmod 600 /srv/scieval/secrets/scieval.token
```

`SCIEVAL_BASE_URL` 默认要求 HTTPS，仅 loopback 允许 HTTP。URL 不得内嵌用户名、密码、query 或 fragment。token 文件必须是 daemon 用户拥有的普通文件，POSIX 权限 0600，不能是 symlink。token 不写入 `config.json`、事件或日志。

`deployment-config.cjs` 已允许这四个环境字段。修改后重启对应实例，使用 `vika logs <instance>` 检查 `check/start` 和轮询错误。

## 恢复边界

游标保存在 `<dataDir>/scieval/<instance>-cursor.json`，每次向 Core 输出事件后原子更新。协议 0.2 没有通用 inbound ACK/replay 事务：

- 输出后、游标保存前崩溃会重投，稳定 report ID 让 Core 去重。
- Core 接收和 Connector 游标不是一个事务，不能据此声称端到端恰好一次。
- 编排器仍需对 `evaluating` 作业按 `evaluation_id` 主动查询和 reconcile。报告处理按 `report_id` 去重，过期轮次和哈希不匹配不得改状态。
- 多实例必须使用独立 dataDir、token 权限和服务端事件视图，不能共享一个可竞争推进的游标文件。

## 本地验证

```bash
cd vika
npm ci --no-audit --no-fund
node --test packages/daemon/test/scieval-client.test.mjs \
  packages/daemon/test/config.test.mjs \
  packages/updater/test/deployment-config.test.mjs
npm run check
```

测试使用本机临时 HTTP 服务，覆盖认证、事件格式、稳定 ID、游标重启、超大报告降级、preflight 错误和停止时中断请求。尚未连接真实 SciEval 服务，联调前需按上述契约提供测试环境和授权 token。
