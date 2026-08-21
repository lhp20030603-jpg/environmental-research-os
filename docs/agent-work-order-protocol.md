# Agent Work-Order Protocol

该协议允许 Claude、Codex、本地模型或人工 worker 替换执行同一研究节点，而不让 provider
直接修改 authoritative state。Provider-neutral adapter 只做四件事：读取 order、读取
声明的 immutable inputs、生成 exact candidate files、调用受控 submission boundary。

## WorkOrder 是唯一任务授权

`work-orders/<order-id>.json` 包含 `node_id`、`node_version`、role、exact input artifact
references、expected schema、expected filenames、policy constraints 和 evidence requirements。
`order_hash` 是除自身外所有 canonical fields 的 SHA-256。Order 还包含 scheduler 分配的
`principal_assignment`（principal、producer、execution context 与 verification mode），并由
private control anchor 一起认证。Adapter 不会自行填写或替换这些字段；它通过提交
**unchanged order_id、observed order_hash and candidate bytes** 保留 issued identity，不能自行增删
输入、输出或规则。重复 issue 只有 exact same order 才幂等，identity collision 必须
fail closed。

原有的 **unchanged order_id and candidate bytes** retry 规则仍然成立；新增 observed
`order_hash` 使该 retry 进一步绑定到 worker 实际读取的 generation。

## Submission 永远不可信

Worker 把候选文件放入 run root 的普通 incoming 区，再调用：

```bash
uv run envresearch research submit <run-root> <order-id> \
  <run-root>/incoming/<expected-filename> \
  --order-hash <hash-copied-from-work-order> --json
```

Queue 不信任 filename、schema、caller producer/context strings、hash 或文件系统路径。
`--producer-*` 仅为旧 adapter 的 unverified local labels，不能改变 trusted identity。
**trusted queue verifies the caller-observed and anchored order_hash**：
它在 pinned roots 下重新读取 source、计算 candidate SHA-256、
验证 order anchor/order hash，并把该 hash binding 写入 queue-authored `receipt.json`，再原子发布
`worker-submissions/<order-id>/transactions/<filename>.submission/`，其中 candidate 与
`receipt.json` 必须同时存在。孤立、额外、symlink、hardlink 或未锚定 transaction
都被拒绝。**same-order/same-bytes retry is idempotent**；同一 transaction identity 下的
**conflicting duplicate is rejected**。Orchestrator collect 后还要根据 expected Pydantic schema
验证，才能将内容 promotion 为 versioned artifact。
兼容表述 **trusted queue verifies the anchored order_hash** 仍然适用；caller-observed
comparison 是在原有 anchored verification 之前新增的 generation check。
Receipt 必须逐字段匹配 order 中的 scheduler assignment；省略 observed hash、提交旧
generation hash 或只更换 component/context/actor strings 都 fail closed。`review-design`
使用独立 critic principal；其 assignment/context 必须与被审阅 upstream principals 不同。

## Authoritative namespace

以下状态只能由内核写入：

- `artifacts/` 及 `.versions/`；
- `work-orders/`、authenticated queue control root 与 receipts；
- `gates/`、`gate-contexts/`、`decisions/` 和 event log；
- `node-checkpoints/`、invalidation archive 和 run identity/config files。

Agent/provider 不得直接编辑、删除、移动或“修复”这些路径，也不得把它们作为 candidate
filename。Worker submission 不能等价于 gate approval；人类 gate 使用独立 principal 和
exact context binding。

## Provider-neutral adapter 规则

1. Adapter 不把 provider session ID、prompt 或模型私有状态当作 durable workflow state。
2. Adapter 只暴露 order 明示的 inputs；不得扫描其他 submissions、secrets 或历史数据。
3. Provider outage 应返回可审计 diagnostic/reason code，run 可 degraded 或等待重试；不得
   伪造 evidence 来保持进度。
4. Retry 必须使用 unchanged order ID 和相同 candidate bytes；queue 根据已锚定 order hash
   判定幂等。内容改变必须由 orchestrator 重新签发 order，不得伪装成 retry。
5. Adapter 不执行外部代码或真实 replication package；V0.2 worker 只生成 design artifacts。
6. 最小权限凭据留在 connector/provider boundary，不写入 order、candidate、receipt 或 log。

## Failure 与 recovery

Submission 中断时不要手工删除 staging/transaction；再次运行同一 submit，由 queue 在锁内
reconcile exact anchor。若返回 hash/schema/path conflict，停止并保留 evidence，由 operator
检查 order 和 candidate。进程重启后运行 `research status` / `research advance`；有效
artifact/checkpoint 不会因为 provider 更换而重做。

Ancestor revision 会把所有具有 live durable state 的 descendants 纳入 transaction，包括
尚未提交的 order、submission/receipt、checkpoint、gate 与 current artifact generation。
Incomplete generation 可以在没有 receipt 时被取消并归档；旧 order hash 之后永远不能被
接受。Final Gate 还会逐一核对 current artifact envelope 的 input edges 是否等于 current
DAG producer generations。
