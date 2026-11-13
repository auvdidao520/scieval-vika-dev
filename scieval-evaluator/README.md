# 本地教学材料评测接口（试点）

提供 `POST /v1/evaluations` 和 `GET /v1/evaluations/{evaluation_id}`，兼容已有的 `HttpEvaluator` 请求/报告格式。服务接收 UTF-8 HTML、校验摘要，调用配置的 OpenAI 兼容 chat completions 模型，返回 13 项报告，并检查证据文字是否出现在输入 HTML。相同幂等键与相同请求返回同一报告，不同请求返回 409；报告和键保存在本地 SQLite。只启动一个服务实例使用同一数据库。单线程同步处理，模型超时或格式、证据无效返回 502，不保存失败结果，可用同一键重试。意外崩溃发生在模型调用后、结果写入前时可能重复调用模型。

量表目前只有项目草案的维度名称，并非经过确认的完整评分锚点。文字匹配不能证明定位或教学判断正确，报告始终为 `human_review`，须教师复核。此服务自身不改稿或发布；下文的可选 TaskManager 适配会在 Worker 完成后调用它复评并安排有限轮返修。HTML 内容会发送给配置的模型服务，请只选可处理该材料的服务。接口仅监听本机，token 和数据库需要由运行者妥善保护；不要把真实材料或密钥提交到仓库。

需要 Python 3.10+ 和支持 `response_format: json_object` 的模型服务。准备独立的本地接口 token 文件，并通过环境变量提供模型 token。两个 token 不应相同。

```sh
export SCIEVAL_MODEL_TOKEN='模型服务密钥'
python3 scieval-evaluator/server.py --db /安全目录/evaluations.sqlite --token-file /安全目录/local-token --model-url https://模型服务/v1/chat/completions --model 模型名称
```

另开终端，提交本地 HTML 并写出 JSON 报告：

```sh
python3 scieval-evaluator/evaluate.py lesson.html --token-file /安全目录/local-token --job-id lesson-001 --round 1 --output report.json
```

相同 job、轮次和内容重复提交会复用结果；`--output` 要求新文件，避免覆盖。调用方应保存 job ID 和轮次，重试时保持不变。`HttpEvaluator` 也可直接提交并查询，失败时应检查并重试，不能把失败报告当作评测成功。当前示例只覆盖本地文件工作流；没有配置外部模型时不能生成真实评分。

运行测试：

```sh
PYTHONPATH=scieval-evaluator python3 -m unittest discover -s scieval-evaluator/tests -v
```

测试使用本地模拟模型，不代表真实模型的质量或外部服务已完成联调。

## 教师反馈分类（试点）

`POST /v1/feedback/classifications` 使用同一 Bearer token，接受评测请求相同的 `schema_version`、`job_id`、`round`、`rubric`、`policy_version`、`artifact` 字段及 `feedback` 字符串。返回多个指标 ID、教师原话、HTML 原文证据、置信度、验收条件和 `needs_clarification` 或 `human_review`。系统校验证据是否逐字存在，不能验证教学判断或定位是否正确。空证据、低于 0.7 的置信度或模型提出澄清时返回 `needs_clarification`；0.7 是试点保守门槛，未经校准。请求必须带 `Idempotency-Key`。同一键和相同请求复用已保存的分类，不同请求返回 409；模型失败不缓存，可用同一键重试。400 为输入无效，401 为认证失败，413 为请求过大，502 为模型或证据错误。键由可信调用方按来源和事件身份构造，此缓存本身不验证 IM 身份，也不能替代原生事件去重。失败不派工。

此接口没有连接 IM 身份校验、去重、Vika task_create/task_route、Worker、渲染评测、教师确认或发布，不能作为自动派工的授权。草案 rubric 尚无完整评分锚点，也没有真实模型的质量验证。见 [交互式闭环接口设计](闭环接口设计.html)，其中明确区分代码与待实现部分。

分类缓存与评测报告共用服务的本地 SQLite，沿用单实例假设。模型完成后、缓存写入前若进程崩溃，重试仍可能再次调用模型；这不提供跨系统恰好一次派工保证。

## Worker 修订稿复评交接（试点）

可信宿主取得 Worker 输出的本地 UTF-8 HTML 后，可显式调用复评交接。它上传文件实际字节到现有 `/v1/evaluations`，用同一 rubric 及 policy 评测，检查作业、轮次、摘要和评测身份，并返回 13 项分数、原文证据、建议和逐项 `pass` / `revise` / `human_review`。未评分项不会通过。相同任务、轮次、内容和策略的重试复用评测报告；不同内容产生独立报告。须由调用方为本次试点明确选择评分阈值：它并非已验证的教学标准。总体 `recommendation` 仅供参考，最终 `decision` 始终是 `human_review`，教师确认之前不能发布。

```sh
python3 scieval-evaluator/review_worker.py revised.html --task-id t1 --job-id lesson-001 --round 2 --pass-score 2 --token-file /安全目录/local-token --output review.json
```

示例阈值仅用于演示，不代表已确认的验收标准。当前交接需要可信宿主手动调用并提供真实任务 ID、作业 ID 和轮次，尚未从 Vika Worker 完成事件自动接收文件，也没有自动回派或轮次控制。原生 `task_route` 接受已认证的输入事件，并不是任意评测报告投递接口；不能伪造事件来回派。需要与 Vika 宿主约定可信交接方式后接线。报告中的定位只做非空检查，文字证据只检查是否在 HTML 源码中，没有渲染检查；量表、模型、阈值均未校准，也没有教师确认或发布集成。

Vika 宿主可通过 `TaskManager.bindLessonContext` 一次性绑定教案、作业、轮次、量表、策略、试点评分线和任务目录内的预定 HTML 路径；Worker 的报告不能设置这些字段。任务以结构化 `done` 结束后，宿主调用 `lessonReviewHandoff` 捕获该路径的 HTML 字节及摘要，再在可信进程内将其交给 `review_host_handoff`。此对象本身没有签名，不得从 Worker 或不可信网络接收；宿主负责认证和传递。接口尚未接入生产生命周期，需宿主显式调用，不会自动回评或派工。这里的信任边界是进程级调用边界，不是同一操作系统账户之间的隔离；生产部署须隔离 Worker 对数据库及宿主进程的写权限。评分仍仅供参考，最终决策始终为 `human_review`。

### Worker handoff persistence (development only)

Vika's host-only `persistLessonReviewHandoff(taskId)` captures the prescribed
HTML and bound review context once per final report in the task database.
`getPersistedLessonReviewHandoff(reportId)` retrieves the same snapshot after
restarting the task manager, even if the Worker artifact has changed or gone.
These functions do not submit or retry a SciEval evaluation. They are not wired
to Worker completion: the current Worker shares the daemon's identity and can
access host state. A separately verified isolation boundary and a trusted host
caller are prerequisites before using the handoff as authority. The existing
human-review decision remains mandatory. No container or DragonAPI integration
is provisioned by this change.


SciEval 公布的评分编码为 0=N/A、1=Inadequate、2=Adequate、3=Extensive（[数据说明](https://github.com/SciEval-Benchmark/SciEval/blob/main/index.html)）。交接中 0 转人工复核，不作为通过或待修改；阈值只能选 1–3。当前提示词仍只有指标名称，没有完整的逐项锚点，也没有经过 SciEval 标注数据验证。公开项目说明其数据需申请，因此本原型不能宣称评分已对齐或经过基准验证。

`HttpEvaluator.classify_feedback(request, feedback, idempotency_key)` 复用评测上传的 HTML 字节、摘要和 rubric，通过现有 HTTP 接口提交反馈，并校验返回的作业、摘要、rubric 和状态。它是分类客户端，不会触发派工或复评；调用方仍须验证反馈来源并使用稳定的事件幂等键。

## afterTurn 试运行

宿主可为 `TaskManager` 配置 `lessonReview: {script, url, tokenFile}`，或为 daemon 设置 `SCIEVAL_HOST_REVIEW_SCRIPT`（`host_review.py` 绝对路径）、`SCIEVAL_URL` 和 `SCIEVAL_TOKEN_FILE`。可信宿主代码还必须在 Worker 完成前调用 `bindLessonContext`，指定任务内的 HTML 路径、作业、轮次、rubric、策略和试点阈值。只有绑定的任务才会进入该流程；当前没有面向教师的绑定入口。

结构化 done 后，afterTurn 捕获 HTML，调用 `host_review.py` 和现有 HTTP 评测接口。`revise` 把未通过项作为上下文交给同一原生任务继续修改，最多回派两次；`pass`、无法判断、达到轮次上限均交人工复核。评测失败时标记 blocked，不报告通过。示例和集成测试使用假教案和假评测服务，评分编码沿用 0=N/A、1–3，13 个 ID 沿用仓库定义；完整评分锚点、渲染检查和真实评测质量尚未验证。人工确认和发布也未接入，不能把该试运行当作生产可用功能。

当前 Worker 与宿主共享操作系统权限，绑定和快照不能防止 Worker 篡改宿主数据库；真实材料使用前须建立隔离。进程在评测中重启会恢复任务重新执行，尚无完整的跨进程评测恢复。评测等待期间仍占用原生任务槽，新到的请求先交 Worker 处理再复评；停止任务后丢弃在途评测结果。适配器进程最多运行 180 秒。
