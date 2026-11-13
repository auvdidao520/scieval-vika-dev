# 本地教学材料评测接口（试点）

提供 `POST /v1/evaluations` 和 `GET /v1/evaluations/{evaluation_id}`，兼容已有的 `HttpEvaluator` 请求/报告格式。服务接收 UTF-8 HTML、校验摘要，调用配置的 OpenAI 兼容 chat completions 模型，返回 13 项报告，并检查证据文字是否出现在输入 HTML。相同幂等键与相同请求返回同一报告，不同请求返回 409；报告和键保存在本地 SQLite。只启动一个服务实例使用同一数据库。单线程同步处理，模型超时或格式、证据无效返回 502，不保存失败结果，可用同一键重试。意外崩溃发生在模型调用后、结果写入前时可能重复调用模型。

量表目前只有项目草案的维度名称，并非经过确认的完整评分锚点。文字匹配不能证明定位或教学判断正确，报告始终为 `human_review`，须教师复核。此服务不自动改稿、发布，也未接入 Vika TaskManager。HTML 内容会发送给配置的模型服务，请只选可处理该材料的服务。接口仅监听本机，token 和数据库需要由运行者妥善保护；不要把真实材料或密钥提交到仓库。

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

`POST /v1/feedback/classifications` 使用同一 Bearer token，接受评测请求相同的 `schema_version`、`job_id`、`round`、`rubric`、`policy_version`、`artifact` 字段及 `feedback` 字符串。返回多个指标 ID、教师原话、HTML 原文证据、置信度、验收条件和 `needs_clarification` 或 `human_review`。系统校验证据是否逐字存在，不能验证教学判断或定位是否正确。空证据、低于 0.7 的置信度或模型提出澄清时返回 `needs_clarification`；0.7 是试点保守门槛，未经校准。该接口每次调用模型，没有幂等缓存；400 为输入无效，401 为认证失败，413 为请求过大，502 为模型或证据错误。失败不派工。

此接口没有连接 IM 身份校验、去重、Vika task_create/task_route、Worker、渲染评测、教师确认或发布，不能作为自动派工的授权。草案 rubric 尚无完整评分锚点，也没有真实模型的质量验证。见 [交互式闭环接口设计](闭环接口设计.html)，其中明确区分代码与待实现部分。
