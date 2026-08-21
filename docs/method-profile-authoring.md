# Method Profile 编写与扩展

Method profile 是 planning-only checklist，不是 estimator plugin。每个 pack 位于
`packs/methods/<profile-id>/`，包含 `pack.yaml` 与 `profile.yaml`；V0.2 禁止 command、
script、可执行 entrypoint 或任意 estimator code。

## Pack manifest

```yaml
id: example-method
kind: method
version: 0.2.0
kernel: ">=0.2,<0.3"
schema: ">=1,<2"
entrypoint: profile.yaml
```

目录名、manifest `id` 和 profile `profile_id` 必须相同；manifest/profile version 必须
一致且为完整 SemVer。`kind` 固定为 `method`，entrypoint 固定为 `profile.yaml`。

## Profile schema 逐字段说明

| 字段 | 规则与用途 |
|---|---|
| `profile_id` | canonical kebab-case；全局唯一，和 pack ID 一致。 |
| `version` | SemVer；和 manifest version 一致。 |
| `family` | canonical snake_case 方法族。 |
| `compatible_estimands` | 非空集合，仅允许 `causal` / `descriptive`。 |
| `required_data_structures` | 非空 snake_case 集合，如 `panel`、`cross_section`。任一匹配即可。 |
| `required_features` | 非空 snake_case 集合；compatibility 使用完整 conjunction，缺一即不匹配。 |
| `identifying_assumptions` | 至少两条、无重复；写成可审查的识别主张。 |
| `incompatibility_rules` | 至少一条；说明何时不应推荐该方法。 |
| `mandatory_diagnostics` | 至少两条；进入 analysis plan 的必要诊断。 |
| `falsification_checks` | 至少一条 negative control/placebo/伪证检查。 |
| `fallback_profiles` | 已注册 profile IDs；不得引用自身，允许空列表。 |
| `analysis_plan_fields` | 至少一条；该方法要求 plan 显式填写的字段。 |
| `methodological_references` | 至少一个稳定 ID，仅 DOI、ISBN、JSTOR、PMID、SSRN 或 arXiv。 |
| `estimator_entrypoint` | 必须为 `null`；V0.2 不执行估计。 |

所有 list 只能含非空且不重复的 string；未知字段、duplicate YAML keys、symlink、越界
entrypoint 和 execution-shaped field 都会 fail closed。

## Compatibility contract

`is_compatible(estimand_type, data_structure, features)` 只有在以下条件全部成立时返回 true：

1. estimand 在 `compatible_estimands`；
2. data structure 在 `required_data_structures`；
3. `required_features` 是可用 features 的子集。

每个新 profile 必须有 positive test，以及逐一删除 required feature、换错 data structure、
换错 estimand 的 counterexample tests。示例验证：

```bash
uv run pytest tests/unit/test_method_profiles.py -v
```

Review 还需确认 assumptions、diagnostics 和 falsification checks 对环境经济学的实际识别
问题有区分力，不是同义改写；fallback 必须改变设计而非形成循环。扩展 profile 只扩大
方法选择知识，不扩大 V0.2 execution boundary。
