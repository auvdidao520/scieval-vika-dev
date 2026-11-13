# 原生任务教案复评试运行

从 PR #34 的 host_review 适配器继续开发，接回 t108 尚未提交的改动。新增的是 `TaskManager.afterTurn` 的实际调用路径，不增加调度器。宿主绑定的任务完成后捕获指定 HTML，Python 适配器上传同一份字节，未达试点阈值则把指标、证据和修改建议交给同一任务继续修改。最多返修两轮，随后无论通过、无法判断或达到上限，都交教师复核。

## 代码入口

- `vika/packages/daemon/src/daemon.mjs`：从环境变量注入可选评测适配器。
- `vika/packages/daemon/src/tasks.mjs`：`hostReviewer` 启动适配器；`bindLessonContext` 绑定宿主选择的 HTML 路径与策略；`afterTurn` 捕获产物、校验返回身份、追加返修上下文、生成教师复核报告。
- `scieval-evaluator/host_review.py` → `review_host_handoff` → `HttpEvaluator`：上传快照字节，检查 13 项指标 ID、分数、摘要、策略和原文证据。
- `vika/packages/daemon/test/tasks.test.mjs` 的 `lesson review afterTurn`：本地假评测 HTTP 服务、实际 Python 子进程和模拟 Worker 的集成验收。

## 运行与验证

```sh
node --test vika/packages/daemon/test/tasks.test.mjs
PYTHONPATH=scieval-evaluator python3 -m unittest discover -s scieval-evaluator/tests -v
cd vika
npm ci --ignore-scripts --no-audit --no-fund
npm run check --workspace=@vika/daemon
node --test packages/daemon/test/*.test.mjs
```

集成场景从 `<p>初稿</p>` 开始，假评测返回 13 项 1 分，原生任务收到待修改指标后由模拟 Worker 写入 `<p>改进</p>`；第二次复评返回 2 分，路由只收到教师复核报告。它验证代码接线和字节交接，不验证真实模型的教学评分质量。测试另覆盖两轮返修上限、评测失败、无结构化完成报告、评测身份不匹配、N/A 转人工复核、新请求在评测期间到达，以及停止任务后丢弃在途结果。

t109 发现并修复：原实现过早释放任务槽，会让新请求与评测并行；评测返回后可能覆盖 stopped；host 报告没有同步任务摘要和草稿；无结构化完成报告会绕过评测。四项均先补用例复现，再改调用顺序与状态更新。适配器还设置 180 秒进程上限。

## 接入范围

需要宿主显式调用 `bindLessonContext`，并配置 `SCIEVAL_HOST_REVIEW_SCRIPT`、`SCIEVAL_URL`、`SCIEVAL_TOKEN_FILE`，详见评测器 README。未绑定任务保留原有行为。尚无面向教师的自动绑定入口，也没有把 IM 教师反馈分类自动接成首次派工；教师最终确认和发布仍由人处理。

指标 ID 和分数编码沿用仓库契约，0=N/A，1–3 为评分。现有模型 prompt 缺少完整 SciEval 逐项锚点，阈值未校准；不能宣称真实评分质量已对齐。评测中断重启会重新运行任务，没有完整的跨进程评测续传。本次未启动、重启或修改当前运行服务，未改变任何模型供应商或凭据配置。

## t109 实测结果

- 任务生命周期用例 31 项通过，Python 评测器 12 项通过，daemon 语法检查通过。
- 安装隔离工作树依赖后，daemon 扩展回归运行得到 1,034 项通过、7 项失败、4 项取消、9 项跳过，因此全量回归未通过。失败/取消涉及真实 durable host 启动超时、Pi/MCP 与 Pi Worker 超时、图片缩放超时、Lark 进程组清理断言。Pi/MCP 测试在三分钟以上仍未退出，手动终止了本工作树的该测试子进程，其余结果完整回收。未动在线服务或其他任务进程。
- 原生 Worker 调度端到端用例通过。本次变更未修改上述真实运行时、图片或 Lark 实现，也未进一步定位这些扩展回归失败的根因，不能据此声称生产可用。
