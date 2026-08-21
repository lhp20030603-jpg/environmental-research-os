# V0.2 研究工作流

本流程把 broad topic 或 structured brief 推进为人工批准的研究设计。每个 Agent 只负责
一个明确节点；所有科学 output 都有版本、hash、input references 和 validation status。

## 1. 初始化两类入口

自由主题示例：

```yaml
intake_mode: broad_topic
broad_topic: Urban clean-air policy and unequal pollution exposure
```

```bash
uv run envresearch research init broad-topic.yaml \
  --config configs/research-default.yaml \
  --run-root runs/clean-air-broad --json
```

该入口先签发 `frame-charters` work order，要求恰好三个可区分、可解释、可全部拒绝的
candidate charters。结构化 brief 示例：

```yaml
intake_mode: structured_brief
structured_brief: Estimate how clean-air zones affect particulate exposure.
```

```bash
uv run envresearch research init structured-brief.yaml \
  --config configs/research-default.yaml \
  --run-root runs/clean-air-structured --json
```

structured path 仍须经过 `normalize-brief` 和 Gate 1，不能借已写好的问题绕过人类选择。
初始化 retry 必须使用同一个 brief、config 与 run root；config conflict 的 non-destructive
fail-closed semantics 见 [安全模型](security-model.md#fail-closed-config-transaction)。

## 2. Agent submission 与推进

从 `work-orders/<node-id>.json` 读取 exact order，生成文件名和 schema 必须与 order 一致：

```bash
uv run envresearch research submit runs/clean-air-broad frame-charters \
  runs/clean-air-broad/incoming/candidate-charters.json \
  --order-hash "$(jq -r .order_hash runs/clean-air-broad/work-orders/frame-charters.json)" \
  --json
uv run envresearch research advance runs/clean-air-broad --json
```

Submission 是 candidate，不是 authoritative artifact；内核验证 order hash、candidate hash、
schema、producer 和 transaction 后才 promotion。`advance` 遇到 gate 时以 exit code 2 和
稳定 `GATE_REQUIRED` JSON 返回，不代表系统故障。

## 3. Gate 1：选择 charter

审批者先读取 `artifacts/candidate-charters.json`（或 structured draft）、
`gate-contexts/gate-1/` 和 `gates/`。每次都先从 `research status --json` 的
`pending_gate_ids` 复制当前 ID；上游制品变更后可能是 `gate-1-r2`，不能硬编码
`gate-1`。然后选择 `gate_context.gate_id` 与它完全一致的 revision 文件：

```bash
RUN_ROOT=runs/clean-air-broad
CONTROL_ROOT="$(dirname "$RUN_ROOT")/.$(basename "$RUN_ROOT").worker-queue-control"
GATE_CAPABILITY_FILE="$CONTROL_ROOT/principals/gate.capability"
uv run envresearch research status "$RUN_ROOT" --json
CURRENT_GATE_ID=gate-1-r2  # 从 pending_gate_ids 原样复制
CONTEXT_FILE="$RUN_ROOT/gate-contexts/gate-1/0002.json"  # gate_id 必须匹配

# 生成 conditions-only JSON；jq 把 exact complete context object 原样嵌入。
jq --arg selected "charter-air" \
  '{gate_context: ., selected_candidate_id: $selected}' \
  "$CONTEXT_FILE" > gate-1-conditions.json

uv run envresearch research gate-decide "$RUN_ROOT" "$CURRENT_GATE_ID" \
  --approve --principal-capability-file "$GATE_CAPABILITY_FILE" \
  --rationale "Selected the strongest current charter." \
  --conditions-json gate-1-conditions.json --json
# 若需 connector coverage，先做第 4 节 API preflight，再执行下一行。
uv run envresearch research advance "$RUN_ROOT" --json
```

上述三个 gate 示例的 `jq` 是可选的 operator prerequisite。如未安装 `jq`，
可用已锁定的 Python runtime 生成同样的 conditions-only JSON（下例以 Gate 1
为例）：

```bash
uv run python -c 'import json,sys; p=json.load(open(sys.argv[1])); json.dump({"gate_context":p,"selected_candidate_id":sys.argv[3]},open(sys.argv[2],"w"),sort_keys=True)' \
  "$CONTEXT_FILE" gate-1-conditions.json charter-air
```

生成后应先读取 JSON，确认 `gate_context.gate_id` 与 `CURRENT_GATE_ID` 完全一致；
data/final gate 使用同一模式，只替换第二个条件字段。

`gate-1-conditions.json` 顶层只有 `gate_context` 和
`selected_candidate_id`；它不是完整 `GateDecision`，status/actor/rationale 均由 CLI options
提供。若要拒绝，保留 exact context 并运行完整 reject 命令：

```bash
uv run envresearch research gate-decide "$RUN_ROOT" "$CURRENT_GATE_ID" \
  --reject --principal-capability-file "$GATE_CAPABILITY_FILE" \
  --rationale "All current candidates require reframing." \
  --conditions-json gate-1-conditions.json --json
```

不要直接覆盖 `gates/*.json`。决定者必须与 request producer 不同。Gate 1 通过后，
literature cartographer 与 data scout 的 work orders 同时 ready；两者可并行由不同
provider 执行。

## 4. Connector fallback 与 Conditional Data Gate

Literature connector 正常时返回 records + provenance receipt；Zotero 或其他 provider
不可用时，gateway 保留 provider identity、reason code 和 diagnostic，run 进入 degraded
而不是伪造文献。Connector coverage binding 是 **API/library-only in V0.2**；当前
research CLI 没有 connector/bind command。

嵌入式 controller 必须在 Gate 1 已批准、但尚未调用 `advance()` 发布 literature order
的窗口中，先调用 `ResearchOrchestrator.bind_literature_coverage()` **BEFORE**
advancing approved Gate 1 into map-literature issuance：

```python
coverage = LiteratureGateway().literature_search(connector, query)
orchestrator.bind_literature_coverage(coverage)
summary = orchestrator.advance()
```

这一步必须发生在 `work-orders/map-literature.json` 存在之前。如果在 degraded
`ConnectorCoverage` receipt 绑定后 crash，重开同一 run 会从 durable receipt 重建 graph，
然后可继续 `research advance`；已发布的 order 仍绑定该 receipt。一旦 literature order
已经发布，**post-issuance hot-swap is unsupported**；更换 provider/coverage 需要由 controller
发布新 order 的未来版本，或使用新 run，不得修改当前 authoritative order。

Data feasibility 必须先报告 access、credentials、license、估计下载量、本地存储、API
calls、external cost、elapsed time 和 design suitability。任一候选属于 restricted/private、
收费、许可不清或超过 explicit acquisition budget，就停止 acquisition 并触发
Conditional Data Gate。审批只能允许记录明确条件下继续设计；它不授予凭据、不改变
license，也不允许 CI 下载数据。无风险的公开候选不触发该 gate。

触发时，从 `pending_gate_ids` 复制当前 `data-gate` revision，选择
`gate-contexts/data-gate/` 中匹配文件，用 exact context 生成 conditions-only JSON：

```bash
uv run envresearch research status "$RUN_ROOT" --json
CURRENT_GATE_ID=data-gate  # 从 pending_gate_ids 原样复制，也可能带 -rN
CONTEXT_FILE="$RUN_ROOT/gate-contexts/data-gate/0001.json"
jq --argjson risks '["private-air: credentials required"]' \
  --argjson terms '["Do not acquire until access and license are documented"]' \
  '{gate_context: ., approved_risk_reasons: $risks, access_conditions: $terms}' \
  "$CONTEXT_FILE" > data-gate-conditions.json

uv run envresearch research gate-decide "$RUN_ROOT" "$CURRENT_GATE_ID" \
  --approve --principal-capability-file "$GATE_CAPABILITY_FILE" \
  --rationale "Proceed only under the recorded access conditions." \
  --conditions-json data-gate-conditions.json --json
uv run envresearch research advance "$RUN_ROOT" --json
```

拒绝时将 `--approve` 替换为 `--reject`，修改 rationale，并保留当前 exact
`gate_context`。

## 5. 方法、批评与 Final Gate

系统先固定 estimand，再按 compatibility contract 从多方法 profiles 中给出 primary、
alternatives 和 rejection reasons，随后形成 identification memo。Independent design critic
输出 `blocking`、`major`、`minor` 或 `advisory` findings：

- 开放 `blocking` finding 阻止 plan assembly 和 Final Gate，必须先关闭；
- 开放 `major` finding 必须有 residual-risk statement，并在 Final Gate 的
  `accepted_major_ids` 中由人类逐项明确接受，否则不能批准；
- `minor` 和 `advisory` 可保留在 plan，但不得被静默丢弃。

Final Gate decision 同样必须复制 exact current `gate_context`。从 `pending_gate_ids`
复制实际 ID；修订后可能是 `final-gate-rN`，不能回放旧 context。无 major risk
时使用 `"accepted_major_ids": []`；有 major risk 时只能列出当前 review 中仍开放且含
residual risk 的 IDs：

```bash
uv run envresearch research status "$RUN_ROOT" --json
CURRENT_GATE_ID=final-gate-rN  # 用 pending_gate_ids 中的真实数字 revision
CONTEXT_FILE="$RUN_ROOT/gate-contexts/final-gate/NNNN.json"
jq --argjson ids '["major-spillover-risk"]' \
  '{gate_context: ., accepted_major_ids: $ids}' \
  "$CONTEXT_FILE" > final-gate-conditions.json

uv run envresearch research gate-decide "$RUN_ROOT" "$CURRENT_GATE_ID" \
  --approve --principal-capability-file "$GATE_CAPABILITY_FILE" \
  --rationale "Accepted the listed residual major risk; no blocking finding remains." \
  --conditions-json final-gate-conditions.json --json
uv run envresearch research advance "$RUN_ROOT" --json
```

批准后，唯一终点是 **approved analysis-plan.yaml**。拒绝使用同一 exact-context
conditions file 和完整 `research gate-decide ... --reject --principal-capability-file ... --rationale ... --conditions-json ...`
命令，不直接改写 gate artifact。

## 6. 状态、恢复与修改

```bash
uv run envresearch research status runs/clean-air-broad --json
uv run envresearch research advance runs/clean-air-broad --json
```

`status` 重新读取 durable state；`advance` reconciliation 后只签发当前 ready work。进程
中断后运行上述命令即可恢复；有效 checkpoint 不会重复执行。Gate 被拒绝、blocking
finding 需要关闭，或 Final Gate 要求修改时，使用公开修订入口：

```bash
uv run envresearch research revise "$RUN_ROOT" review-design \
  --actor "human-reviewer" \
  --principal-capability-file "$CONTROL_ROOT/principals/revision.capability" \
  --reason "Close the blocking comparison-design finding" --json
```

`NODE_ID` 必须是已完成的 worker node。系统先持久化 actor、reason、target 和当前
artifact refs，再将旧 artifact、order、submission 和 receipt 标记或归档为不可变的
superseded generation；仅失效目标与 descendants，保留无关 checkpoints。新 order 绑定
revision ID，旧 gate context 保留但不再 active。相同 actor/reason 的重试会恢复同一事务；
冲突修订会失败并保留可恢复状态。不要删除 events、checkpoints 或历史版本来强迫重跑。

`gate.capability` 和 `revision.capability` 是 owner-only bearer secrets；不要提交到 Git、
复制到日志、放入 command-line argument 或交给 worker。CLI 只接受 exact
owner-only capability file，并通过 pinned/no-follow control root 读取。`--actor` 仅是 caller attestation，durable principal 始终由
capability 对应的 `human-reviewer` assignment 决定。H8 之前创建、缺少 assignment 的旧
research run 会 fail closed，必须新建 run；这不影响独立的 V0.1 benchmark replay harness。

## 7. V0.2 明确停止边界

V0.2 stops after producing an **approved analysis-plan.yaml** and **does not execute empirical analysis**.
It does not clean real data, estimate models, generate results, write paper sections,
or execute a replication package. 这些工作需要新的 V0.3 scope、隔离执行设计和负责人批准。

## 8. Blind calibration 与 scientific release

Pilot-8 只证明八类 planning profiles 的 Tier-1 offline calibration 路径可以完成 citation
verification、masking、source-blind recommendation handoff 和 source-revision recovery。
它不属于 held-out evaluation，也不等于 V0.2 scientific release；当前明确状态为
**`scientific_release_pending`**。

Release 必须由真实独立环境经济学/政策专家提交两份 blind score sheets；发生规则定义的
分歧时，还需第三位独立 adjudicator 完成裁决。系统必须从 current authenticated artifacts
计算 readiness，并同时证明：held-out cases ≥ 16、passes ≥ 14、八个 method families 覆盖
完整且每类至少两个案例/至少一个通过/family mean ≥ 3.0、zero leakage、zero unverified
claims、zero unresolved adjudications。Pilot cases、缺失的专家评分或测试 fixture 都不能
计入这些门槛。

Orchestrator 只向 recommender workspace 投影 masked brief、leakage report 和 opaque
method profiles，不声明 connector inputs。Recommendation 中只有 `/fact_refs/<index>` 可以
承载 canonical `fact-*` evidence linkage；方法推理 prose 不得嵌入 fact ID、数量或
source-identifying marker。Citation gate 校验 exact current claim↔fact provenance，不做自动
semantic entailment 判定。

Worker 真正 offline 且无 repository access 是 trusted operator/runtime 的启动前条件。
Work-order policy strings 不提供 OS containment；未批准的 hostile code 不应在该边界内执行。
如需 hostile-worker containment，须另行批准并实现 container/OS backend。
不论 calibration 或 release gate 的结果如何，research workflow 仍在人工批准
`artifacts/analysis-plan.yaml` 后停止：不得执行分析、清理真实数据、生成结果、撰写论文，
也不得下载或运行 replication package。V0.3 Tier-2 adapters 需要单独的书面批准。
