# N1 分类、方案和任务关联契约草稿

开发版本 `0.2.0-n1.draft.1`。本 PR 保留 t55 的 `eab9ef0`，基于 PR #3 的 `28ca37f` 继续开发。PR 指向 main，包含尚未合并的 P0 / Connector 依赖，因此审查时应将继承代码与 N1 增量分开看。main 由仓库负责人审查合并，不直接改写。

## 已实现与边界

- `load_rubrics()` 校验五类集合、版本、指标白名单、重复指标、II.C 始终保留、完整评分与教师审批要求。配置是 JSON 格式的 YAML 子集，无第三方解析器。
- `validate_category()` 严格校验类别对象；未知字段、版本错配、空学科或年级、非有限置信度、布尔假冒数值均拒绝。
- `category_gate()` 对**已经结构化的候选分类**做确定性检查；它不从任意自然语言 IM 推断类别。没有候选时返回一个明确问题；other、显式需确认、低置信及未确认策略均返回 `needs_confirmation`。
- 低置信阈值必须由调用方显式提供，且 `policy_confirmed=True`。默认永远不返回 ready；测试阈值 0.8 只用于边界测试，不是生产标准。该参数仅为调用协议，不能验证真实教师身份或业务审批。
- `validate_plan()` 检查轮次、上一报告引用、指标、动作和保留项；每个目标至少有一项动作。调用方掌握当前报告时必须传 `expected_report_id`，才能拒绝同轮次中的错误报告引用。单独的非空 report ID 检查不证明归属。
- `validate_task_binding()` 校验版本化 `job_id → task_id` 候选映射与来源事件标识，不创建任务，不证明幂等或唯一性。

`ready` 仅表示本次候选通过分类门，不表示允许执行、达标或发布。非法结构抛 `ValueError`；未来 Router 必须捕获并转为修正问题，不能将异常路径当作就绪。`needs_confirmation` 和异常都不得派工。分类器、跨类别推理和教师 IM 提问的实际交付属于 N2。

## 数据与调用例子

```python
config = load_rubrics()
category = {
    'category': 'experiment_inquiry',
    'category_version': config['version'],
    'subject': 'science', 'grade': '初二', 'confidence': 0.91,
    'teacher_requirements': ['保留三个活动'],
    'needs_teacher_confirmation': False,
}
result = category_gate(category, config)  # needs_confirmation / unconfirmed_policy
# 只有调用方已有经审查的政策，才传实际阈值及 policy_confirmed=True。
```

类别优先维度：新授 I.B.2/I.C/II.A/III.E；探究 I.A/I.B.1/II.B/III.A；复习 I.B.2/III.B/III.C/III.E；项目 I.A/I.B.3/I.C/III.B。other 不预选维度。各类 II.C 始终保留；每轮报告仍完整保留 13 项，映射不裁剪报告。

任务关联草案：

```json
{"schema_version":1,"job_id":"J1","task_id":"t17","source_event_id":"lark:event:1"}
```

这里的 `schema_version=1` 仅标识 task_binding。路线图中 OV 作业 `schema_version=2` 尚未实现；当前 `OvStore.create()` 仍写 schema v1，`get()` 仍只返回 job_id/state/revision/round。不能把候选映射校验描述成 OV 已持久化这些字段。

## 验证

```bash
cd scieval-vika-p0
python3 -B -m unittest discover -s tests -v
```

26 个测试方法：t55 及继承原型 12 项，本次新增 14 项（每项含若干边界子用例）。覆盖五类、II.C、缺失输入、低置信边界、未确认政策、恶意字段和路径、报告引用、动作覆盖、停止配置和版本一致性。HTTP 和 OV 测试使用本机替身；双轮测试使用合成评分，不代表真实教师输入、真实 Worker 或外部服务验收。

## 后续阶段

N1 的工程草稿已可评审，业务契约尚未冻结：类别、阈值、II.C 解释、评分/停机与审批权责仍需确认，见 issue #7。不因此停止可独立推进的工程工作。

N2：独立 workers 实例、SciEval role/connector prompt、OV v2 状态读写与校验、task_create 之后的关联持久化和崩溃对账。详细 plan 放 OV，不能塞进 task_create.note。

N3：worker_loop、不可变轮次快照、稳定幂等键提交、报告按 job_id 回到原 task、报告归属与去重、第二轮计划引用第一轮报告。

N4：停滞/回归/轮次/预算、指定 hash 与 report ID 的人工审批、真实 OV/SciEval 隔离联调。保留既有状态机，最终先进入 awaiting_review，不自动发布。
