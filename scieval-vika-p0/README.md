# SciEval × Vika P0 框架

独立 P0 原型，位于 Vika 二开仓库的 `scieval-vika-p0/` 目录。仅使用 Python 3.10+ 标准库，不包含评分数据和真实评分模型。契约和未确认策略见[契约草案](../p0-contract-draft.md)。阈值和停机参数未确定，框架不自动判断达标、不发布、不发通知。

运行测试：`python3 -m unittest discover -s tests -v`。

`framework.py` 提供 13 项报告校验、固定内容的 SHA-256 快照、SQLite 持久化作业/状态 CAS/事件 outbox、可注入的评测器接口；以此为适配边界，外部调用和人工审批需在目标仓库实现。SQLite 示例仅支持单机，不保证分布式事务、对象锁或与外部服务的原子性。生产实现还需认证、授权、文件隔离、保留策略、幂等提交与回调、重试/恢复 worker、受控制品存储以及人工审批的真实身份校验。

`fixtures/synthetic_scores.json` 是人工构造的两轮测试评分，`simulated_evaluator.py` 实现可替换的 `submit`/`query` 接口。输出带 `synthetic: true`，没有真实证据，不可用于教学评价或发布。此模拟器的幂等记录只在进程内有效。

`http_evaluator.py` 是真实服务的 HTTP 适配器：提交时重新计算文件 SHA-256 并上传 HTML 的 base64 内容，要求 `Idempotency-Key` 和 Bearer token；查询时只接受同源报告 URL，并校验报告摘要。它不会自动重试，调用方在超时后必须用同一幂等键恢复。服务端仍需自己持久化幂等记录。

`ov_store.py` 用实例的 `ov` CLI 把每个作业写成 `viking://…/<job_id>/state.json`，状态转换和 outbox 在同一 JSON 文档中更新，写入后回读核对。它通过本机 flock 保证单作业单写者，适用于单主机 P0；OV content/write 没有通用 CAS 承诺，不能把该适配器直接用于多主机并发。运行时需设置正确的 `OPENVIKING_CLI_CONFIG_FILE`，并先用 `ov health` 验证连接。

真实飞书、Runtime、SciEval 和 OV 联调仍需部署环境。测试中的 HTTP 服务和 OV runner 都是本机测试替身，不代表已完成外部服务验收。
