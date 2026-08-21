# 架构

> **当前层级说明（2026-08-21）：** 本文档定义稳定的 V0.2 research-design
> kernel。Personal Pilot 已在其上增加 V0.3/V0.3.1 Evidence Runner、V0.4
> Paper Builder 与 V1 governed Research Factory，但不替换这些 V0.2
> artifact contracts。各阶段操作手册见 README。

Environmental Research OS V0.2 将科学制品、编排状态、Agent 交换协议和人工决定分成
可独立验证的层，同时保留 V0.1 benchmark replay。

```text
CLI / provider-neutral adapter
  -> ResearchOrchestrator
       -> Artifact DAG + content-addressed node checkpoints
       -> Gate 1 / Conditional Data Gate / Final Gate
       -> immutable WorkOrder -> untrusted WorkerSubmission
       -> versioned research artifacts + decision/event history
  -> approved analysis-plan.yaml (stop)

V0.1 CLI
  -> BenchmarkRegistry -> BenchmarkRunner -> RunEngine -> canonical report
```

## Research Artifact DAG

两个入口只在第一个节点不同：broad topic 进入 `frame-charters`，structured brief 进入
`normalize-brief`；随后汇合到 `approve-charter`。Gate 1 通过后，`map-literature` 和
`inspect-data` 同时 ready，之后依次形成 estimand、method candidates、identification
memo、independent design review 和 analysis plan。节点只读取声明的 immutable input
references，并只发布声明的 output paths。

每个有效节点都有 content-anchored checkpoint。上游制品版本变化只失效其 descendants；
未受影响节点保持有效。`status` 和 `advance` 都会从 artifacts、events、gate contexts、
work orders 与 checkpoints 恢复，不依赖原进程内存，也不会重复已验证节点。

## 层次职责

- `models`：frozen schema、artifact envelope、finding 与 research design contracts。
- `storage`：原子发布、安全相对路径、canonical serialization 和 hash。
- `kernel`：Artifact DAG、events、gates、decision log、checkpoint 与 recovery。
- `research`：双入口 orchestration、条件数据 gate、review policy 和最终绑定。
- `connectors`：provider-neutral literature/data gateway；连接器失败产生明确 degraded
  coverage receipt，而不是虚构来源。
- `methods` + `packs/methods`：仅规划、不执行 estimator 的可扩展方法 profile。
- `workers`：immutable work order、隔离 submission transaction、hash binding 与 promotion。
- `benchmarks`：V0.1 replay 和 V0.2 repository-owned design fixtures。
- `cli`：薄适配层；机器接口通过 `--json` 保持稳定 error code。

## Gate 与停止边界

Gate 1 选择一个 charter；受限、私有、收费、许可不清或超过 acquisition budget 的数据
触发 Conditional Data Gate；Final Gate 在无开放 `blocking` finding 时审查最终设计，
开放 `major` finding 只有在人类明确接受对应 residual risk 后才可通过。Gate decision
必须绑定当次完整 artifact context，旧版本批准不可重放到新制品。

Final Gate 通过后只把 `artifacts/analysis-plan.yaml` 提升为 `approved`。V0.2 不创建
execution、results 或 paper artifacts；执行分析属于需另行批准的 V0.3。

## CI 与 benchmark

CI 安装锁定环境，运行 Ruff、mypy 和 coverage 不低于 80% 的完整测试套件。它只执行
repository-owned synthetic fixtures。Tier 1 仅用于公开论文设计的 metadata/evidence
校验；Tier 2 的真实 replication packages 在 V0.2 禁止。
