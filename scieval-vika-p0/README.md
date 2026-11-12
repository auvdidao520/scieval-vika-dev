# SciEval × Vika P0 框架

仓库内的独立原型，尚未与 Vika 集成。仅使用 Python 3.10+ 标准库；不连接真实飞书、OpenViking、Runtime 或 SciEval，也不包含真实评分数据和真实评测。契约和未确认策略见上级目录 `p0-contract-draft.md`。阈值和停机参数未确定，框架不自动判断达标、不向外部发布、不发通知。

运行测试：`python3 -m unittest discover -s tests -v`。

仓库根目录的 `.github/workflows/scieval-prototype.yml` 配置 push、pull request 和手动触发，在 Python 3.10–3.13 上运行全部测试，不需要外部服务。本地测试通过不代表远端矩阵已经运行。

`framework.py` 提供 13 项报告校验、固定内容的 SHA-256 快照、SQLite 持久化作业/状态 CAS/事件 outbox、可注入的评测器接口；以此为适配边界，外部调用和人工审批需在目标仓库实现。SQLite 示例仅支持单机，不保证分布式事务、对象锁或与外部服务的原子性。生产实现还需认证、授权、文件隔离、保留策略、幂等提交与回调、重试/恢复 worker、受控制品存储以及人工审批的真实身份校验。

`fixtures/synthetic_scores.json` 是人工构造的两轮测试评分，`simulated_evaluator.py` 实现可替换的 `submit`/`query` 接口。输出带 `synthetic: true`，没有真实证据，不可用于教学评价或发布。此模拟器的幂等记录只在进程内有效，真实数据和评测器应通过同一接口替换；数据到达后需另行确认权限、评分定义及适配方式。

## 本地闭环命令

`local_loop.py` 将手工提供的 HTML 快照、模拟评测请求和报告按作业与轮次存档，逐轮推进 SQLite 状态。每次命令都可重启进程；报告已归档但状态尚未转换时会先校验报告，再恢复转换。第一轮模拟结果要求改稿，第二轮转人工复核：

```sh
python3 local_loop.py --root /tmp/scieval-demo create demo1
python3 local_loop.py --root /tmp/scieval-demo advance demo1 --draft /path/to/draft.html
python3 local_loop.py --root /tmp/scieval-demo status demo1
python3 local_loop.py --root /tmp/scieval-demo advance demo1 --draft /path/to/revised.html
```

改稿文件由调用者提供，原型不调用生成模型；相同内容不作为新一轮。若进程在输入快照写入后中断，可不带 `--draft` 重试 `advance`。快照、请求和报告以独占创建方式保存，重复写入不同内容会报冲突。模拟器只有两轮预设数据；报告的建议和状态均仅用于演示。模拟评测器重启后内存记录会丢失，本地流程使用相同请求重新提交；这不是外部服务持久化幂等或分布式恢复的验证。归档文件可由本机用户修改，尚无生产级防篡改或访问控制。此命令没有接入人工身份认证或外部审批发布，不会通知教师。

快照先写入同目录临时文件并刷盘，再通过硬链接独占创建最终路径，避免写入中断留下半份正式文件；进程被强制终止时可能遗留 `.snapshot-*` 临时文件。该本地实现依赖文件系统支持硬链接，未验证断电后的目录持久性。outbox 提供持久化的 `pending()` 和可重复调用的 `acknowledge(event_id)`；目前没有投递器。实际接入时应在投递成功后确认，消费者仍需按事件 ID 去重，以处理投递成功但确认前崩溃的重放。


## 本地基础适配器

`foundations.py` 提供 SQLite 资源版本读写、从作业状态派生的看板、本地任务队列和处理器调度，以及绑定资源摘要、报告摘要和策略版本的审批与本地发布记录。`Foundations(path)` 使用与 `Store` 相同的数据库；用 `write_resource(job_id, name, bytes, expected_version)` 写入，`read_resource` 读取并验证摘要，`kanban()` 查看作业。队列通过 `enqueue(task_id, kind, payload)` 入队，再由进程显式调用 `run_one({kind: handler})` 执行；重启后队列保留，超时租约可重新领取。处理器可能重复执行，必须自行使用稳定任务 ID 做幂等；没有后台服务、退避或自动重试失败任务；修复失败原因后可用 `retry_failed(task_id)` 显式重新入队。

审批前需把报告存为作业的 `report.json` 资源，调用 `review(job_id, revision, artifact_name, artifact_version, report_sha256, policy_version, credential, decision)`，再用 `publish_local(job_id, revision, credential)` 保存经审批的精确内容。发布前若产物出现新版本则拒绝；状态及 outbox 事件和审批/发布记录在同一 SQLite 事务中提交。审批和发布需要注入授权适配器；演示授权器不提供真实身份认证或授权，报告只检查摘要，不代表评测内容通过验证；本地发布仅写入数据库，不对外发布。调用方应先完成报告验证和人员授权。

这不是 OpenViking 服务、真实 Runtime 或生产审批/发布接入：具体 API 契约和凭据尚未确认。真实接入还需实现认证授权、隔离、可靠调度、外部发布幂等和故障恢复，并在实际环境联调；本地单机 SQLite 不提供分布式事务保障。现有 `local_loop.py` 的磁盘快照不会自动导入资源表，需由调用方明确写入后才能使用这些审批接口。

## P1 本地流程

两轮模拟评测、队列和本地审批发布记录的用法见 [P1 说明](P1说明.md)。
