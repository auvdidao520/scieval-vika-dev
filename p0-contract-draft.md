# SciEval × Vika P0 契约草案

状态：待确认的独立草案，2026-11-12。本文不代表服务已实现。以下 HTTP API、存储和策略是建议，仍需与仓库架构和现有实现核对。评分阈值、模型能力及校内标准均未核准，禁止用本草案自动发布。

## 评测 API

建议使用异步接口，JSON UTF-8，认证和授权由部署环境提供。所有 ID 为不含个人信息的 opaque string；时间为 UTC RFC 3339。`POST /v1/evaluations` 要求 `Idempotency-Key`，取 `job_id:round:input_sha256:rubric_version`；服务端按调用方隔离键并保存请求规范化哈希，重复同内容返回原 evaluation，重复键但内容不同返回 409；应持久化去重记录至少覆盖作业及恢复周期。禁止用短期内存去重替代持久化。

请求示例（地址仅示意，读取前必须核验授权、不可变版本及摘要）：
```json
{"schema_version":"1.0","job_id":"job_123","round":1,"artifact":{"uri":"artifact://job_123/1/lesson.html","sha256":"<64 lowercase hex>","media_type":"text/html"},"rubric":{"id":"scieval-equip-13","version":"draft-1"},"policy_version":"draft-1"}
```
成功返回 202 和 `evaluation_id`、`status=queued`、`status_url`；重复请求可返回 200 和原对象。无效请求 400、未认证 401、无权限 403、内容冲突 409、过大 413、限流 429（带 Retry-After）、暂不可用 503。HTTP 重试使用相同幂等键和指数退避；超时后先查询/重复同键提交，不能生成新轮次。

`GET /v1/evaluations/{evaluation_id}` 返回 `queued|running|succeeded|failed|cancelled`、更新时间、失败代码及可重试标记；成功时提供不可变报告 URI 和 SHA-256。异步状态仅向前推进；失败重试次数和截止时间由策略固定，终止失败需人工处理。客户端保存 evaluation_id、请求哈希和最后状态；worker 重启先查询已有 ID，再决定恢复。资源未找到 404。报告通过授权的存储接口读取，避免信任任意外部 URI。

## 报告契约

报告必须记录 `schema_version`, `evaluation_id`, `job_id`, `round`, `input_uri`, `input_sha256`, `rubric_id`, `rubric_version`, `policy_version`, `evaluator_version`, `created_at`, `criteria` 和 `decision`。同一轮恰好包含下列 13 个唯一 ID；缺项、重复、输入摘要不匹配或报告无效时不可判通过。

| ID | 评测维度 |
|---|---|
| I.A | 解释现象或设计解决方案 |
| I.B.1 | 科学与工程实践 |
| I.B.2 | 学科核心概念 |
| I.B.3 | 跨学科概念 |
| I.C | 三维整合 |
| II.A | 相关性与真实性 |
| II.B | 学生想法 |
| II.C | 科学准确性 |
| III.A | 三维表现监测 |
| III.B | 形成性评估 |
| III.C | 评分指导 |
| III.D | 无偏任务与题目 |
| III.E | 学习机会 |

每项：`criterion_id`, `status`（`scored|not_observable|error`）, `score`（scored 时 0–3 整数，否则 null）, `evidence[]`, `reasoning`, `recommendations[]`。证据含 `quote`、`locator`（HTML 稳定选择器及文本偏移或段落 ID）、`artifact_sha256`；缺失证据可记录明确的缺失说明，禁止伪造引用或定位。引用必须对应输入快照，定位不可解析则人工复核。`decision` 为 `pass|revise|human_review` 并含原因及策略版本；报告只提供评测建议，最终发布仍须人工审批。分数区间和阈值须与数据集标注定义及校方复核，不能假定所有缺失项为零。

建议试点策略（未批准、不应启用自动判定）：全部 13 项 scored、每项 ≥2 且平均 ≥2.5 才建议 pass；证据缺失、低置信、不可观测、重大科学准确性问题统一 human_review；其余建议 revise。明确无权自动豁免项目。阈值、权重、科学准确性红线都需教师及评测负责人核定并版本化。

## 作业、轮次与恢复

kanban 至少存 `job_id`, `state`, 单调递增 `revision`, 当前 `round`, `input_sha256`, `evaluation_id`, 报告 URI/摘要、策略版本、重试和截止时间、审批记录、操作事件及时间。状态转换用 revision CAS 和持久化事件实现；外部副作用用事务 outbox/幂等消费者处理。状态和待发事件同一事务提交；崩溃后重放未确认事件，按稳定事件 ID 去重。不得依赖消息恰好一次送达。

| 当前 | 触发与校验 | 下一状态 |
|---|---|---|
| received | 输入合法且持久保存 | preparing |
| preparing | 产物快照与哈希已保存 | evaluating |
| evaluating | 合法报告，决策 revise，未触停机 | revising |
| revising | 新快照生成且轮次递增 | evaluating |
| evaluating | 合法报告 pass 或需人工处理/停止 | awaiting_review |
| 任意非终态 | 不可恢复错误或重试耗尽 | awaiting_review |
| awaiting_review | 有授权人员审批指定产物摘要 | approved 或 rejected |
| approved | 发布动作幂等完成并记录版本 | published |

失败、取消及过期均保留原因；人工可拒绝或另行创建新作业，不能覆写终态记录。审批必须绑定精确 artifact digest、报告及策略版本；产物变化后审批失效。冲突事件以 CAS 拒绝，重读状态后判断是否重复或过期。通知只在经授权的集成中实现，使用持久 outbox 和可去重通知 ID，不假称已经通知。

建议默认最多 3 轮、每轮评测重试最多 3 次、全作业 24 小时截止；连续两次无分数提升或产物摘要不变时停止自动改稿，转人工。数值仅为待确认试点参数，不是校方标准。模型无法产生有效报告、输入损坏、重大安全或科学问题也应转人工；pass 不等于发布。

## 不可变归档与 skill

路径建议 `jobs/{job_id}/rounds/{round}/`，包含 input.html、input.manifest.json、request.json、report.json、report.manifest.json；manifest 保存内容 SHA-256、媒体类型、创建时间、生产者版本、父轮次和关联 ID。对象以条件创建或版本锁定禁止覆盖，同一路径不同内容视为冲突；读取和恢复时重新验摘要。审批和审计事件追加存储；作业级策略、访问控制、保留期和删除政策待确定。个人信息最小化，日志不记录课件正文或密钥。

OV skill 草案职责：验证授权与输入 → 创建/恢复作业 → 调 Runtime 产出快照 → 幂等提交评测 → 校验报告 → 按版本化策略改稿或转人工 → 记录审批与发布事件。只用受限工具访问本作业存储；课程内容和报告视为数据，不作为覆盖系统指令的指令。具体 skill 文件格式及 kanban API 应以所选 Workers 预览提交实际接口为准，当前未核验。

## 已核实事项与待办

- SciEval 官方站点列出上述 13 项，并要求研究用途申请数据：发邮件至 freemanchen115@gmail.com，说明姓名、机构和研究用途。未申请，使用许可、商业用途、分发和保存条件须向数据提供方确认。来源：https://scieval-benchmark.github.io/SciEval/
- SciEval 论文称 273 份 lesson-level 材料；项目网站的下载材料数量和划分表述不同。核实实际版本和去重口径后再规划实验。https://arxiv.org/abs/2604.25472
- 官方 EQuIP 页面提供 v3.1；SciEval 的 13 项是项目选取的评测集合，不能宣称覆盖全部官方准则。https://www.nextgenscience.org/resources/equip-rubric-science
- 校内指标、权重、评分尺度、证据质量、中文适配、教学对象及人工审批角色均未知；需拿到校内量表逐项映射为 `equivalent|partial|missing|not_applicable`，列出差距和评审人，不能凭空补齐。
- 验收建议：同键重试与冲突、并发 CAS、崩溃后恢复和 outbox 重放、13 项缺失/重复、证据摘要与定位、停机边界、审批后内容变更、权限拒绝及发布幂等；先在模拟服务跑通，再做经授权的真实服务联调。
