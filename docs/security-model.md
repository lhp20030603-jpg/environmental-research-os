# 安全模型

> **当前层级说明（2026-08-21）：** 以下 V0.2 边界仍是公开 Personal Pilot
> 的基础。后续 Evidence、Paper、Factory 和 Personal advisory 层增加了更
> 严格的 exact-reference 与 owner-private authority，但不会把系统变成
> hostile-code sandbox，也不授权未经审查的外部执行。

V0.2 是受信本地 research-design orchestrator，不是 hostile-code sandbox。外部 Agent、
connector 和候选文件都不因此获得对 authoritative state 的写权限；未知代码若要执行，
仍需 container 或 operating-system isolation。

Blind workflow 能强制的边界是：orchestrator 只把 masked brief、leakage report 和
opaque method-profile projection 写入 source-blind workspace，且 work order 不声明任何
connector input。`No network or connector access` 等 policy constraint 是可审计的运行合同，
不是 OS-enforced containment。真正的 offline、无 repository access 和禁止额外路径读取
必须由受信 operator/runtime 在启动 worker 前实现并核验。若威胁模型包含
hostile worker code，必须另行批准并接入真实 container/OS isolation backend。

## 文件与命令边界

- 路径必须是安全相对路径；关键文件通过 pinned directory descriptor、no-follow read、
  single-link regular-file validation 和 fail-if-exists publication 处理。
- Decision log、revision journal、revision intent 和它们的 lock/head state 也属于
  critical control state。公开 journal 路径为 benchmark inventory 保持不变，但每次
  read/append/recovery 都使用 retained descriptor，并要求 owner-only、regular、
  single-link inode。它们的 HMAC key、hash-chain head 和 lock 位于独立 private
  control root；symlink、hardlink、FIFO、parent swap、file replacement、truncation
  或断链一律 fail closed，不会读写外部 victim。
- Revision recovery 只在 authenticated journal event 与实际 archive、checkpoint
  invalidation、artifact/gate supersession 及 decision audit side effect 一致时继续。
  Revision intent 会同时绑定当时的 gate context ID/hash，或经认证的
  `no active gate` 状态；后来新建的 gate 不能掩盖缺失的 supersession。
- Run manifest 同时记录 method profile version 和 canonical SHA-256 content identity；
  resume 和 Final Gate approval 前都重新校验，因此未升版却更改 scientific rule
  会导致运行终止。

V0.2 hardening 将 research-run manifest 升级为 schema `1.1`，并为 journal
record/head 和 protected revision intent 显式记录 format `1.0`。旧的 plain
JSONL 或只记录 profile version 的 manifest 不会被静默“升级”；resume 将
fail closed，要求在保留原运行证据后显式创建新的 hardened run。
- V0.1 command runner 只接受显式 `argv`，使用 `shell=False`，且 executable 必须在
  allowlist 中并固定到受信 absolute path。
- 原始输入先验证 hash；只从只读 `raw/` 派生 writable work directory，永不回写 raw。
- Manifest environment 默认拒绝，仅允许 `LANG`、`LC_ALL`、`TZ`、
  `SOURCE_DATE_EPOCH` 和 `PYTHONHASHSEED`。禁止在配置、fixtures、Git 或 CI logs 放入
  secrets、tokens、credentials 或真实敏感研究数据。

## Agent 与 gate trust boundary

- Work order 是 immutable、content-addressed contract；submission 一律不可信。
- Trusted scheduler 将 producer principal 与 execution context 写入 work order；private
  control anchor 同时认证 assignment 和 order hash。Queue 在隔离 transaction 中重新计算
  candidate hash、验证 schema、caller-observed order hash、路径、receipt assignment 和
  authoritative control anchor，验证后 orchestrator 才可 promotion。Caller 只更换
  component/context strings 不能创建新 principal。
- Agent 只能写 incoming/candidate 区；`artifacts/`、`gates/`、`gate-contexts/`、
  `decisions/`、`node-checkpoints/`、`work-orders/`、events 和 control root 由内核管理。
- Gate 与 revision 由 owner-only control state 中分配、认证的 human principal 执行，
  并与相关 worker/critic principals 分离；任意 CLI actor label 不构成认证。公开
  gate/revision API 与 research CLI 都必须提供对应的 owner-only capability，且
  gate decision 的 exact digest 会写入 HMAC-protected control record。每次决定必须回显
  exact immutable gate context；`blocking` 不能靠风险接受绕过，`major` 只能在 Final
  Gate 被明确记录为 accepted residual risk。
- Revision transaction 枚举所有具有 live order、submission/receipt、checkpoint、gate 或
  artifact generation 的 descendants。尚未 checkpoint 的 issued generation 使用经认证的
  cancellation marker 安全归档 receipt/staging crash residue；已完成 node 缺失 public
  submission evidence 则 fail closed。接受时 sealed order inputs 必须等于 current DAG refs。Final Gate 会再次验证每个
  current artifact envelope edge，任何 superseded producer generation 都 fail closed。

H8 assignment 之前创建、缺少 `principal_assignment` 的 research work order/receipt 不会被
静默改写或重算 hash；reopen 会 fail closed，操作者应保留旧 run 作为证据并
创建新 run。独立的 V0.1 benchmark replay compatibility 不受此研究队列 schema 升级影响。

## Fail-closed config transaction

Research initialization 在一个锁定 transaction 中绑定 internal run identity 与用户提供的
exact config bytes。发布采用 no-replace：已有文件若与期望 bytes 不同，初始化立即失败，
不会覆盖、合并或自动删除。

如果 crash 或第二个 identity conflict 发生在部分发布之后，transaction 采用
**non-destructive fail-closed config transaction retry semantics**：保留已发布的精确文件，
在 error note 中列出残留；操作者必须检查两份 identity，显式纠正冲突 entry，然后只能以
同一 run identity 重试。冲突 identity 始终 blocked。该策略避免 rollback 删除并发创建或
仍可用于安全 retry 的证据。

## 数据与 benchmark 边界

CI 仅能使用小型 repository-owned synthetic fixtures。V0.2 可记录公开论文与数据来源的
metadata/evidence，但不下载、提交或执行真实 replication archive。第三方数据始终受其
license、access control 和 distribution terms 约束；需要 Tier 2 前必须先取得项目负责人
的新批准。

Blind citation gate 只验证机械的 provenance contract：`method-recommendation`
只能在 `/fact_refs/<index>` 字符串叶上声明 canonical `fact-*` ID，且该叶的
`ClaimUsage` 必须精确对应 current claim↔fact map 及 current source/map/brief refs。
方法推理 prose 必须保持 source-independent；该 gate 不声称自动判断语义 entailment。
