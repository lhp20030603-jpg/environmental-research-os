# 环境经济学 Design Benchmark 接入指南

> **Personal Pilot 说明（2026-08-21）：** 以下 V0.2 scientific-release 标准
> 仍未完成，但它不是个人使用锁。后续工程层和 Agent advisory checks
> 不会把 calibration corpus 改称为正式 held-out scientific evaluation。

V0.2 benchmark 用来验证“研究设计工作流能否正确停、正确退化、正确恢复”，不是声称
paper-level empirical reproducibility。所有 manifest 都必须给出 source、license、输入
fixture、expected phase、authoritative artifact inventory 和 versioned rubric thresholds。
这些分数是 workflow regression signal，不等同于发表质量评审。

## Tier 边界

Tier 0: repository-owned synthetic fixtures, allowed in CI.

Tier 1: open published-paper design benchmarks, metadata/evidence only in v0.2.

Tier 2: real replication packages, prohibited until v0.3 approval.

- Tier 0 可提交小型、自有、无敏感信息的 synthetic brief 和 expected design evidence。
- Tier 1 可用于学习公开论文如何定义问题、estimand、identification、diagnostics 和
  limitations；只保存合法可再分发的 metadata/evidence，不执行作者代码或真实数据。
- Tier 2 包含真实 replication archive、数据和执行环境。V0.2 registry 必须在执行前拒绝
  Tier 2，也必须拒绝任何 tier 上的 `executes_replication_package: true`。

## Pilot-8 校准与 release gate

`benchmarks/blind/pilot/` 中的八个 Tier-1 cases 是公开论文设计的 **calibration corpus**。
它们用于离线验证 source bindings、claim-level citations、结构化 masking、recommender
handoff、source revision 和 fail-closed recovery。Recommender 只能看到 opaque facts，且
orchestrator 只投影 masked brief、leakage report 和 opaque method profiles，不提供
connector input；不能看到论文身份、原方法、原结果或 curator-only map。
offline 和无 repository access 是 operator/runtime precondition，不是 policy strings 提供的
OS isolation。执行 hostile worker 前需另行批准真实 containment backend。

Recommendation 的 evidence linkage 只能位于结构化 `/fact_refs/<index>` 字符串叶。
每个 canonical `fact-*` 必须由精确 `ClaimUsage` 绑定到 current claim↔fact mapping。
数量、author-year、DOI、evidence marker 或 fact ID 不得写入方法推理 prose。
这是机械 provenance check，不是自动 entailment judge；方法合理性仍由独立专家盲评。

Pilot-8 不属于 held-out cohort，不能单独把状态从 `scientific_release_pending` 改为
released。当前 repository evidence 也不包含、不会生成虚构的 human expert score sheets。
正式 V0.2 scientific release 需要真实独立专家完成盲评，并同时满足：

- 至少 16 个 held-out cases，且至少 14 个通过；
- 八个 canonical method families 各至少两个 held-out cases、至少一个通过，且 family mean
  不低于 3.0；
- zero leakage、zero unverified claims、zero unresolved adjudications。

如两位专家的独立评分触发 disagreement rule，必须由第三位独立 adjudicator 完成锁定的
第三份评分与裁决；不得用测试 fixture、自签名或推断结果填补人工证据。Release readiness
必须从已验证的 current artifacts 重新计算，而不是手工修改状态。

离线校准和 revision recovery 回归：

```bash
uv run pytest tests/integration/test_pilot8_calibration.py \
  tests/integration/test_blind_recovery.py -q
```

## V0.2 接入流程

1. 选择一个公开、稳定、适合环境经济学/政策的设计案例，记录 authoritative citation、
   persistent identifier、版本和许可证据；不猜测 license 或 hash。
2. 优先从它的研究问题、design table、identification discussion、robustness plan 和
   limitations 提取 metadata/evidence。没有可合法再分发的证据时停止接入。
3. 在 `benchmarks/design/fixtures/<id>/` 创建 repository-owned `brief.yaml`、
   `benchmark.yaml` 和确定性的 `replay.yaml`。不要复制真实数据或外部代码。
4. `benchmark.yaml` 声明 `tier: 0` 或 `tier: 1`，并固定
   `executes_replication_package: false` 和
   `rubric_version: research-quality-v1`；通过 `replay_fixture: replay.yaml` 绑定 behavior
   input，列出 expected authoritative files 和每项 rubric threshold。
5. 使用 registry/replay 测试验证真实 orchestrator phase、artifact inventory、connector
   degradation、conditional approval、revision 和 interrupted recovery，不以字符串
   快照代替行为验证。

```bash
uv run pytest tests/integration/test_research_acceptance.py -v
```

## Research-quality rubric v1

Manifest 必须且只能按下列六个 keys 声明 1–5 的 threshold：

1. `contribution_clarity`：research question 和与备选方向的可辨识差异。
2. `evidence_coverage`：source、evidence row 和 estimand evidence refs 的闭合链条。
3. `data_feasibility`：suitability、access、license、structure/features，以及 restricted
   candidate 的 exact conditional approval。
4. `estimand_precision`：population、unit、treatment、outcome、counterfactual、horizon
   和 target parameter。
5. `identification_credibility`：installed method compatibility、assumptions、threats、
   diagnostics 和 robustness plan。Retained method 必须 compatible；`REJECTED` candidate 必须
   附带 `rejection_evidence` declared unmet `estimand_type`、`data_structure_set` 或
   `feature_set` requirement 及非空 explanation。Semantic validation 会对 installed
   profile 和 current data capabilities 重新校验完整 requirement set；即使 features
   分散在不同 datasets，也只有单个 suitable dataset 满足全集时才算 compatible。
6. `uncertainty_disclosure`：residual risks、data boundaries、fallback rules 和 finding
   closure。

Replay 从恢复后的 durable artifacts、semantic validation、run manifest 和
`decision-log.jsonl` 计算 `ResearchQualityScores`；不读取 manifest 中的自报分数。
`DesignFixtureReplay` 返回六项 scores、逐项 threshold result、open blockers 和
`overall_pass`。受控 replay 对每项使用 `max(manifest threshold, 3)`；任一项低于
该值或存在未关闭 `blocking` finding 都必须 fail。

Inventory 是 actual-vs-expected 的 exact authoritative shape。因 revision/invalidation ID 是内容与
事务绑定的动态值，manifest 使用 `{revision-<node>-<ordinal>}` 和
`{invalidation-<node>-<ordinal>}` 作为仅针对 ID path segment 的 canonical placeholder；同一
node 的多个 transaction 保留独立 ordinal，不会被合并。其余
namespace、文件名和数量仍必须完全一致。每个成功 replay 必须包含
`research-run-manifest.json`、`decision-log.jsonl` 和 approved
`artifacts/analysis-plan.yaml`；rework fixture 还必须包含 revision journal、intent 和归档的
worker transaction。

Repository-owned fixtures 覆盖 broad/structured happy paths、conditional data approval、
connector degradation、blocking-review revision 和 interrupted recovery。它们都必须从磁盘
重开并到达 exact-context Final Gate 的 approved plan；connector degradation 不允许
伪造 source，conditional approval 不授予下载或执行权限。

`replay.yaml` 不是说明性备注，而是 replay driver 必须读取的 strict input：

```yaml
schema_version: "1.0"
scenario: connector_degradation
```

`scenario` 只能是 `happy_path`、`connector_degradation`、
`conditional_data_approval`、`blocking_review_revision` 或
`interrupted_recovery`。缺字段、多字段、未知 scenario 都在任何 workflow
操作前失败；修改 scenario 必须改变真实 public-path replay，并由 exact inventory
捕获新的 authoritative state。

## CI 禁止项

CI 不得下载外部论文包、数据集或中间文件，不得需要登录、API key 或人工 cookie，也不得
执行真实 replication package。首次出现必须依赖 Tier 2 才能回答的问题时，停止当前接入
并通知项目负责人；只有 V0.3 明确批准后才能设计隔离 runner、license review 和 provenance
流程。该批准与 V0.2 scientific release 是两个独立 decision gates；任何一方通过都不会
自动授权另一方。

V0.1 的 metadata-only published-paper catalog 保持兼容，但它不自动升级为 V0.2 Tier 1
design fixture，更不构成 Tier 2 执行授权。
