# SciEval × Vika P0 框架

独立 P0 原型，位于 Vika 二开仓库的 `scieval-vika-p0/` 目录。仅使用 Python 3.10+ 标准库；不连接真实飞书、OpenViking、Runtime 或 SciEval，也不包含评分数据和真实评测。契约和未确认策略见[契约草案](../p0-contract-draft.md)。阈值和停机参数未确定，框架不自动判断达标、不发布、不发通知。

运行测试：`python3 -m unittest discover -s tests -v`。

`framework.py` 提供 13 项报告校验、固定内容的 SHA-256 快照、SQLite 持久化作业/状态 CAS/事件 outbox、可注入的评测器接口；以此为适配边界，外部调用和人工审批需在目标仓库实现。SQLite 示例仅支持单机，不保证分布式事务、对象锁或与外部服务的原子性。生产实现还需认证、授权、文件隔离、保留策略、幂等提交与回调、重试/恢复 worker、受控制品存储以及人工审批的真实身份校验。

`fixtures/synthetic_scores.json` 是人工构造的两轮测试评分，`simulated_evaluator.py` 实现可替换的 `submit`/`query` 接口。输出带 `synthetic: true`，没有真实证据，不可用于教学评价或发布。此模拟器的幂等记录只在进程内有效，真实数据和评测器应通过同一接口替换；数据到达后需另行确认权限、评分定义及适配方式。
