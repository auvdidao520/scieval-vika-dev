# SciEval × Vika 教学设计评测闭环

本仓库用于开发 Vika 与 SciEval 的教学设计改稿闭环。设计基线见 [闭环架构 HTML](docs/scieval-vika-closed-loop-architecture.html)。

Vika 源码在 `vika/`，基线为 [OpenViking PR #7227 的 b8c290c 提交](https://github.com/volcengine/OpenViking/pull/7227/commits/b8c290cbd61add49642db807f2030acdf9dcd4e3)。上游许可与声明见 [Vika LICENSE](vika/LICENSE)。

## 本地开发

启动 Docker Desktop，在仓库根目录运行：

```powershell
docker compose run --rm vika-dev npm ci --no-audit --no-fund
docker compose run --rm vika-dev npm run check
docker compose run --rm vika-dev npm test
```

使用 `docker compose run --rm vika-dev bash` 进入开发容器。Node.js 固定为 24.22.2。依赖和运行数据使用本项目的独立 Docker 卷。开发资料和课件可放在 `workspace/`，该目录供容器访问并由 Git 忽略。测试使用模拟连接器和运行时，真实飞书、Codex 与 OpenViking 连接可在开发后按任务配置。

## 首个开发阶段

1. 定义评测请求和报告契约，保留评分证据和页段定位。
2. 用测试评测器跑通单教师、单作业的改稿状态流。
3. 实测重复评测稳定性，再接入真实评分引擎与 OpenViking 状态存储。
