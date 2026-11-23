# 执行层契约：Router 与 Worker 分工

本文补充《[闭环架构](scieval-vika-closed-loop-architecture.html)》中的执行层说明。

现有文档把「Runtime 改稿」描述为一个动作。实际落地时，这个动作里包含三件不同的事：
判断要做什么、真的去做、以及做完之后把结果交回状态机。如果三件事都写在提示词里，
换运行时、换模型或加一种任务时都会失控。因此建议在执行层引入 **Router / Worker** 的分工，
并把它固化成任务的输入输出契约。

## 1. 分工与边界

| 角色 | 负责 | 明确不做 |
|---|---|---|
| Router | 按 `taskType` 选择 Worker；传递任务契约、基准版本与来源；把结果交回编排层 | 不生成内容；不判定达标；不无限重试 |
| Worker | 读取背景知识、组装上下文、调用模型或评测器、按契约返回结构化结果 | 不写 OV 状态机；不覆盖较新版本；不自行推进轮次 |
| OV | 提供背景知识与上下文（skill、rubric、知识范围）；另由 kanban 承载迭代状态 | 不派发任务；不保存临时执行现场 |
| Vika Core | 事件去重、会话生命周期、出站动作与幂等 | 不含迭代状态机（与现有设计一致） |
| scieval | 产出结构化评测报告 | 不决定是否继续迭代 |

一句话：**Router 派任务，Worker 执行，OV 提供背景知识，状态机决定下一步。**

## 2. OV 的两种角色必须分开

同一个 OV 实例承担两类完全不同的数据，读写权限也应不同：

- **背景知识（只读）**：`skill`、`rubric`、`knowledge_scope`、已确认的优秀案例。
  Worker 可以检索，但不得写入。
- **迭代状态（由状态机写）**：`kanban` 卡片、轮次、产物版本与哈希、报告 id。
  只有编排层可以推进，Worker 只能读当前卡。

把两者混在一起，会出现「Worker 顺手改了状态」或「把某轮产物当成参考规范」的问题，
后者尤其危险：错误产物一旦进入知识区，会被后续轮次反复引用。

## 3. 任务契约

### 3.1 输入（Router → Worker）

```json
{
  "taskId": "task-20261123-0001",
  "taskType": "revise_document",
  "jobId": "job-7a12",
  "round": 2,
  "baseVersion": 3,
  "input": {
    "artifactUri": "ov://resource/job-7a12/round-1",
    "requirements": "教师原始要求与多因素反馈",
    "reportId": "report-20261123-0007"
  },
  "contextQuery": {
    "domains": ["skill", "rubric"],
    "maxItems": 8
  },
  "outputFormat": "html_document_v1"
}
```

### 3.2 输出（Worker → Router）

```json
{
  "taskId": "task-20261123-0001",
  "jobId": "job-7a12",
  "round": 2,
  "baseVersion": 3,
  "status": "completed",
  "artifact": {
    "uri": "ov://resource/job-7a12/round-2",
    "sha256": "内容哈希",
    "mediaType": "text/html"
  },
  "contextReferences": [
    {"uri": "ov://skill/equip-13", "title": "EQuIP 准则", "version": "3"}
  ],
  "usage": {"runtime": "pi", "elapsedMs": 0}
}
```

三条硬规则：

1. 结果必须回带 `taskId` 与 `baseVersion`，编排层据此判断结果是否已过期；
2. `status` 只允许 `completed` 或 `failed`，不接受「部分完成」；
3. 产物以不可变 URI + 内容哈希交付，不直接覆盖既有版本。

## 4. 版本与幂等

- 每次产物落盘产生新版本号，旧版本保留，便于回放与对比；
- 提交评测时带幂等键（与现有 outbox 设计一致），重投同一 `taskId` 不产生第二轮评测；
- 若 `baseVersion` 小于当前卡版本，结果标记为过期并丢弃，卡片退回 `pending_edit`，
  **轮次不递增**——轮次只在实际采用新产物后递增，否则会被无效结果消耗掉迭代上限。

## 5. 与 kanban 状态机的衔接

本文不改变状态语义，只增加两条记录要求：

- 每次状态转换记录 `taskId` 与产物 `sha256`，使「哪一版、依据哪份报告、改了什么」可完整回放；
- `need_revision → pending_edit` 的转换只由编排层执行，Worker 无权触发。

## 6. 为什么把 Router 放在执行层而不是提示词里

- 任务类型会增长（改稿、评测、报告汇总），用提示词分派会让每次改动都影响所有任务；
- 运行时可替换（Pi / Codex / Claude Code），契约不绑定具体运行时；
- 停机条件（轮次上限、停滞、回归）必须由代码判定，不能靠模型自觉。

## 7. 待确认

1. 任务分派由 Core 承担，还是由 Runtime 内的工具调用承担？前者需要改 Connector 白名单，
   后者需要在 workspace 内传递契约；
2. 事件负载能否携带结构化 payload（当前协议以文本为主）；
3. 结果回传走 `action` 出站，还是由评测/编排侧轮询拉取；
4. 结构化输出校验放在哪一层——建议放在编排层，Worker 只负责交付。

## 8. 落地顺序

1. 先定义 `taskType` 与契约字段，跑通单任务单 Worker；
2. 再接第二个 `taskType`，验证 Router 的分派是否与运行时解耦；
3. 最后补版本冲突与过期丢弃的回归测试。
