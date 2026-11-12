# SciEval × Vika P0 框架

独立 P0 原型，位于 Vika 二开仓库的 `scieval-vika-p0/` 目录。仅使用 Python 3.10+ 标准库，不包含评分数据和真实评分模型。契约和未确认策略见[契约草案](../p0-contract-draft.md)。阈值和停机参数未确定，框架不自动判断达标、不发布、不发通知。

运行测试：`python3 -m unittest discover -s tests -v`。

`framework.py` 提供 13 项报告校验、固定内容的 SHA-256 快照、SQLite 持久化作业/状态 CAS/事件 outbox、可注入的评测器接口；以此为适配边界，外部调用和人工审批需在目标仓库实现。SQLite 示例仅支持单机，不保证分布式事务、对象锁或与外部服务的原子性。生产实现还需认证、授权、文件隔离、保留策略、幂等提交与回调、重试/恢复 worker、受控制品存储以及人工审批的真实身份校验。

`fixtures/synthetic_scores.json` 是人工构造的两轮测试评分，`simulated_evaluator.py` 实现可替换的 `submit`/`query` 接口。输出带 `synthetic: true`，没有真实证据，不可用于教学评价或发布。此模拟器的幂等记录只在进程内有效。

`http_evaluator.py` 是真实服务的 HTTP 适配器：提交时重新计算文件 SHA-256 并上传 HTML 的 base64 内容，要求 `Idempotency-Key` 和 Bearer token；查询时只接受同源报告 URL，并校验报告摘要。它不会自动重试，调用方在超时后必须用同一幂等键恢复。服务端仍需自己持久化幂等记录。

`ov_store.py` 用实例的 `ov` CLI 把每个作业写成 `viking://…/<job_id>/state.json`，状态转换和 outbox 在同一 JSON 文档中更新，写入后回读核对。它通过本机 flock 保证单作业单写者，适用于单主机 P0；OV content/write 没有通用 CAS 承诺，不能把该适配器直接用于多主机并发。运行时需设置正确的 `OPENVIKING_CLI_CONFIG_FILE`，并先用 `ov health` 验证连接。

真实飞书、Runtime、SciEval 和 OV 联调仍需部署环境。测试中的 HTTP 服务和 OV runner 都是本机测试替身，不代表已完成外部服务验收。

## 下一阶段工作流原型

`contracts.py` 和 `category_rubrics.yaml` 保存未确认的分类、指标与方案契约。`OvStore.create_workflow` 创建 v2 状态，`worker_loop.py` 提供 task 关联、快照、提交意图、幂等提交、报告校验/去重及按当前评测 ID 查 task 的原型。`scieval-role.md` 为独立实例的自定义 role 草案，SciEval connector 规则只在启用该 connector 时注入。

这些函数尚未接入 Vika 的 task_create/task_route、事件 inbox、人工确认界面或自动改稿；不要在生产实例启用该 role。报告引用定位、停滞/回归/预算判断、分类低置信处理、任务创建后崩溃对账等仍待实现。OV 测试使用本机替身，SciEval 测试使用模拟器；不能据此声称真实 Worker 双轮联调已通过。现有 OV 写锁仅在同机所有写入者使用同一锁目录时有效，不支持多机 CAS。类别映射和轮次仅为路线图草案，正式策略需要业务确认。

### 报告恢复与投递意图

`reconcile_report` 对仍在 evaluating 的作业查询当前 evaluation ID，等待中保持原状；完成后要求服务返回稳定 `report_id`，并复用完整报告校验。`apply_report` 在同一次 OV 文档更新中保存报告、状态及面向绑定 task 的投递意图；`pending_report_deliveries` 可在进程重启后取回，调用方只有在确认目标系统已持久化路由后才可调用 `acknowledge_report_delivery`。重复报告不会产生第二条意图；相同 ID 的不同正文会被拒绝。

这是待接线的单机接口，不会自行调用 Vika `task_route`。该接口要求可信 inbox 事件引用，不能把这里生成的 event_id 当作 Vika 的 trigger_id。发送与确认之间发生崩溃时会重试，因此目标侧必须按稳定事件标识去重或先对账；否则不能宣称恰好一次。服务失败和未提供稳定 report_id 时不会推进作业，需由调用方记录和处理错误。已有历史作业在引入该功能前处理的报告不会自动补出投递意图。

### 可信事件投递对账原型

`trusted_delivery.deliver_pending` 规定可信网关的查找和路由边界，校验已接收报告与持久投递意图后确认。当前没有生产网关或调度接线，不能仅靠此函数接收真实事件；调用方必须从 Core 账本读取并验证指定 connector 实例，按任务和已接受序号幂等路由。查询得到报告但找不到已接受事件时保持待处理，不伪造 trigger ID。详情见 `docs/n4-trusted-delivery-t61.html`。
