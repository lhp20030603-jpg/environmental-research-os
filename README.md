# Environmental Research OS

**Personal Pilot / Research Prototype** — 一套面向环境经济学与环境政策研究的、制品驱动的论文写作工作流。

> Product status: `scientific_release_pending`

这是科学验证状态，不是系统锁。个人用户可以正常安装、运行和修改本项目；它只表示当前版本还没有通过真实 held-out cases 和独立专家评分所要求的正式科学发布门槛。

## 已实现能力

| 层级 | 当前能力 | 状态 |
|---|---|---|
| Research Design V0.2 | 自由主题/结构化 brief、候选 research charter、方法筛选、文献与数据可行性、两个主要 gate、可恢复 Artifact DAG | 可用 |
| Evidence Runner V0.3 | 对明确授权的本地 CSV 运行 DiD/event study、Panel FE、IV/2SLS、local-linear RDD、RCT ITT、Synthetic Control、环境测量和 Meta-analysis | 可用，需可选 R runtime |
| Valuation Core V0.3.1 | Hedonic pricing、Travel-cost、single-bounded CV 与 conditional-logit DCE | 可用，需可选 R runtime |
| Paper Builder V0.4 | 从精确 evidence refs 构建 claim-evidence ledger、argument map、draft、独立 audit、revision closure 和 release candidate | 可用 |
| Research Factory V1 | 把已批准的 research design 与已审计 paper release 组装为可恢复、可审批的 governed run | 可用 |
| Personal advisory validation | 四个 canonical behaviors、Scientific/Evidence/Synthesis Agent review、来源重建与建议报告 | 内部 API/测试 harness，尚无稳定 CLI |

方法不由系统硬编码选定：它应跟随 estimand、数据结构、可用变异、识别假设和可行性。当前 Python package version 仍为 `0.2.0`，因为该版本号同时是现有 V0.2 artifact contract 的稳定身份；上表展示的是在该稳定内核上已实现的各 capability layer。

## 快速开始

需要 Python 3.11–3.13 和 [uv](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/lhp20030603-jpg/environmental-research-os.git
cd environmental-research-os
uv sync --locked --dev
uv run envresearch --help
uv run pytest
```

不想先手动排查环境时，可运行：

```bash
uv run python scripts/preflight.py
```

该预检只检查 Python、`uv`、lockfile、CLI 和必需目录，不会下载数据或运行计量任务。需要同步环境或检查本地 R 时：

```bash
uv run python scripts/preflight.py --sync
uv run python scripts/preflight.py --with-r
```

## 最小示例：创建 research-design run

```bash
uv run envresearch research init \
  benchmarks/design/fixtures/broad-topic/brief.yaml \
  --config configs/research-default.yaml \
  --run-root runs/clean-air-broad --json

uv run envresearch research advance runs/clean-air-broad --json
uv run envresearch research status runs/clean-air-broad --json
```

Agent 根据 `work-orders/<node-id>.json` 生成 candidate，再用受控提交命令交回。Gate decision 不是普通 Agent submission；它依据当前 immutable context 记录责任人的明确决定。完整示例见 [Research workflow](docs/research-workflow.md)。

## 主要命令

```text
envresearch research       Discover/Design workflow
envresearch econometrics   Local-data econometric recipes
envresearch paper          Audited paper release builder
envresearch factory        Governed end-to-end run
envresearch benchmark      Synthetic/replay benchmark tools
envresearch replication    Approved container-only replication path
```

详细操作手册：

- [Research workflow](docs/research-workflow.md)
- [Local econometrics](docs/guides/local-econometrics.md)
- [V0.3 Evidence Runner](docs/econometrics-v03-operator-guide.md)
- [V0.4 Paper Builder](docs/paper-builder-v04-operator-guide.md)
- [V1 Research Factory](docs/research-factory-v1-operator-guide.md)
- [Architecture](docs/architecture.md)
- [Security model](docs/security-model.md)

## R、Docker 与跨平台说明

- Python 研究设计、编排、审计和测试默认不需要 R。
- 本地计量执行的 reviewed baseline 使用 R 4.4.3；`fixest` 和 `did` 是常用可选包，部分方法还依赖 reviewed frozen package pack。项目不会自动修改 system/user R library。
- Docker/Podman 只与经批准的 Tier-2 replication package 路径相关，不是默认安装或默认可运行路径。
- macOS 和 Linux 可直接使用上述命令。Windows 建议使用 PowerShell 或 WSL；路径和 shell quoting 应按当前 shell 调整。某些 owner-private file-authority 检查主要在 POSIX 环境下验证。

## 数据、隐私与仓库边界

CI 只使用 repository-owned synthetic fixtures。不要提交真实研究数据、未公开论文、个人 Obsidian 知识库、API keys、凭证、`runs/` 或本地生成的 artifacts。外部数据和 replication package 必须先确认 access、license、provenance 和 budget。详见 [SECURITY.md](SECURITY.md) 与 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 当前限制

- 尚未完成正式 held-out scientific evaluation，因此不宣称通用科学有效性。
- Personal Validation 已具备 canonical-case 与三角 Agent advisory review 基础，但没有稳定公共 CLI、自动修复闭环或产品级评分。这不阻塞上述核心工作流。
- 项目不自动判定论文结论“正确”；它保存证据、限制、lineage 和问题，便于用户修正后 rerun。
- 下载的外部 author code 与未审查 container 不会自动执行。

更完整的版本说明见 [Personal Pilot release notes](docs/releases/v1-personal-pilot.md) 和 [CHANGELOG.md](CHANGELOG.md)。

## 开发与验证

```bash
uv sync --locked --dev
uv run ruff check .
uv run mypy src
uv run pytest --cov=envresearch --cov-report=term-missing --cov-fail-under=80
uv lock --check
uv run python scripts/publication_audit.py
git diff --check
```

参与贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## License

[MIT](LICENSE) © 2026 Haopeng Liu
