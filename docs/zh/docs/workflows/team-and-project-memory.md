---
title: 团队与项目记忆
description: 用普通 Scope 保存共享结论，让成员读取，并在事实变化时修正。
---

# 团队与项目记忆

团队或项目不需要新的对象类型。它需要四件事：一个由团队约定为集体记录归属的 Scope；贡献者持续提供材料的方式；
一个成员可以读取的正式结论；以及事实变化时修正该结论的路径。PowerContext 已经提供这四项，因此应用把业务团队和项目
ID 映射到普通 Scope，并自行记录两者的关系。

本页描述当前可以运行的组合。某一步依赖尚未具备的能力时，正文会直接说明，而不是暗示它已经可用。

## 选择拥有结论的 Scope

用外部引用把业务 ID 映射到 Scope，应用无需另存私有映射表：

```http
POST /v1/scopes

{"title":"Orders project","summary":"Conclusion the Orders team maintains","idempotency_key":"orders-project",
 "external_references":[{"kind":"project","value":"orders"}]}
```

之后用同一引用查找：

```http
GET /v1/scopes?query=orders&query_field=external_reference_value&external_reference_kind=project
```

若要把宿主身份或业务 ID 固定到一个 Scope，使用持久绑定。绑定键由你定义：

```http
PUT /v1/scope-bindings

{"key":{"integration":"project","kind":"project","external_id":"orders"},"scope_id":"S_PROJECT"}

POST /v1/scope-bindings/resolve

{"binding_keys":[{"integration":"project","kind":"project","external_id":"orders"}],"allow_default":false}
```

这两种关系都不授予权限，也不让一个 Scope 成为另一个的结构子节点。团队和项目各自选择目标 Scope，可以位于完全不同的
子树。若产品把一个团队关联到多个项目，或把一个项目关联到多个团队，该关联保存在产品自己的目录中：PowerContext 只
看到 Scope 和授权。

读取 Scope 与写入 Scope 是不同权限。按成员实际工作需要授予角色：

| 成员 | 项目 Scope 上的角色 |
| --- | --- |
| 提供观测记录、复盘、事故报告 | `scope.contributor` |
| 决定候选结论是否成为正式结论 | `scope.reviewer` |
| 修改 Scope 配置，包括 Profile 策略 | `scope.admin` |
| 只读取结论并查看所引证据 | `scope.viewer` |

```http
POST /v1/access/bindings/create

{"subject":{"type":"user","id":"mara"},
 "resource":{"type":"scope","scope_id":"S_PROJECT"},
 "role":"scope.reviewer",
 "idempotency_key":"mara-orders-reviewer"}
```

## 持续提供已获授权的输入

贡献者记录发生在哪里，就先在哪里留证；再把项目可以使用的那部分贡献到项目 Scope 本身：

```http
POST /v1/scopes/S_ALICE_WORK/sources

{"content":"A callback retried on the new channel succeeded with an idempotency key."}

POST /v1/scopes/S_PROJECT/sources

{"content":"A callback retried on the new channel succeeded with an idempotency key."}
```

第一次写入是 Alice 自己 Scope 内的证据，需要她在该 Scope 的写入权限。第二次把同一条观测放进项目的 Source
Journal，项目的 Profile 处理从这里读取，因此需要项目的 `scope.contribute`。

同时写入个人 Scope 与共享 Scope 的用户可以使用主体接口，它在同一事务内创建两条 Source：

```http
POST /v1/scopes/S_ALICE_WORK/subject-sources

{"subject_key":"alice","content":{"speaker":"alice","text":"A callback retried on the new channel succeeded."}}
```

用一条常驻规则自动发现贡献者的新素材、并让每次运行固定其确切输入，属于跨 Scope 共享能力，目前尚不可用。在此之前，
把记录贡献到项目 Scope 就是受支持的输入方式，而且它明确说明项目可以使用哪些内容。

## 维护唯一的正式结论

一个 Scope 最多只有一个 Profile，即 `family=profile` 且 `artifact_id=profile`。开启需审核的生成，让维护者决定每次变更：

```http
PUT /v1/scopes/S_PROJECT/profile-policy

{"generation_enabled":true,"activation_mode":"review_required","expected_version":0}
```

触发一次评估，然后裁决候选：

```http
POST /v1/profile/flush

{"scope_id":"S_PROJECT"}

POST /v1/candidates/approve

{"scope_id":"S_PROJECT","candidate_id":"candidate_...","expected_version":1}
```

`flush` 返回它消费的日志位置、处理的 Source 数量，以及在存在待审变更时的 `candidate_id`。在 `review_required` 模式下，
批准之前正式结论不会改变；在 `activation_mode=automatic` 模式下，服务端自行提交该 Revision。无论哪种模式，Profile 在
某次运行或人工写入产生它之前并不存在，所以新建的项目 Scope 在此之前返回 `404`。

维护者也可以直接写入结论，用于首个 Profile，或不来自模型的修正：

```http
POST /v1/scopes/S_PROJECT/artifacts

{"family":"profile","content":{"content":"# Orders project\n\n- Retry with an idempotency key on the new channel."}}
```

重复创建返回 `409`，因为 Profile 是 Scope 内的唯一实例。更新时重放其校验器：

```http
PUT /v1/scopes/S_PROJECT/artifacts/profile/profile
If-Match: "revision:1"

{"content":{"content":"# Orders project\n\n- Check transaction status before retrying on the old channel."}}
```

每次写入都是一个不可变 Revision。`GET .../artifacts/profile/profile/revisions` 列出历史，`GET .../revisions/{revision}`
返回某个确切的历次结论。

写入已存在的 Artifact 需要 `artifact.write`，它来自该 Artifact 的所有者身份。所有权在 Artifact 创建时建立，且只属于一个
Principal。在启用权限的部署上具体表现为：

- 创建该 Profile 的 Principal 可以持续替换它；
- 其他贡献者即使同时拥有 `scope.reviewer` 或 `scope.admin`，也会被拒绝并返回 `403`；
- `artifact.owner` 无法通过 Access 绑定接口授予。

因此项目必须决定由谁创建正式 Profile，并把该 Principal 视为在册维护者。若应当维护它的人发生变化，当前受支持的路径是以
新维护者身份通过 Dream 审核或 Profile 策略产出结论，或重建该 Artifact，而不是交接已有的那一个。交接共享结论的所有权
是尚未解决的决策。

## 让成员读取结论

`Prepare` 是 Agent 单轮获得结论的方式。Profile 不在默认装配中，必须显式选择该 section：

```http
POST /v1/context/prepare

{"scope_id":"S_PROJECT",
 "query":"may we retry a callback after a timeout",
 "assembly":{"sections":[{"family":"profile","limit":1}]}}
```

返回文本会标明它使用的确切地址——`Scope: "...", family="profile", id="profile", revision=N`——读者可以据此打开那个
Revision。加上 `"include_receipt": true` 可同时获得回执中 `selected` 的 `artifact_address` 列表。

`Prepare` 读取当前 Scope 及其直接 Context Reference。它不搜索结构子节点，引用也不授予权限：它读取的每个 Scope 都以调用者
身份重新授权。若想给成员一项能力而不是一条结论，就把 Skill 放在项目 Scope 中供其加载：

```http
POST /v1/scopes/S_PROJECT/artifacts

{"family":"skill",
 "content":{"name":"incident-retry-triage",
            "description":"Decide whether a failed callback may be retried.",
            "instructions":"Check the channel. Retry with an idempotency key only on the new channel.",
            "validation":["Confirm the channel before retrying."]}}
```

项目 Skill 独立于任何人的个人 Profile。Alice 的个人 Profile 仍在自己的 Scope、按自己的策略维护，项目不会因为共享
Scope 树或引用而获得它。

## 查看结论背后的证据

读取结论与查看证据是不同权限。读取已提交的 Profile 需要它上面的 `artifact.read`；打开它所引用的确切 Source 或
Artifact，需要*那个*来源上的权限，并在打开该证据的那次请求上校验。可以读取项目结论的成员，仍可能被拒绝查看背后的私有
事故报告，这种拒绝是预期行为而不是缺陷。

项目用自己的 Source Journal 产出的 Revision，其证据在项目 Scope 内解析。把外部 Source 或 Artifact 用作 Dream 证据，
并在批准时重新校验每个来源的权限，属于跨 Scope 处理，目前尚不可用。

## 修正它，并在新事实出现时重新评估

三种情形看起来相似，但结果不同：

| 情形 | 服务端行为 |
| --- | --- |
| 候选待审期间维护者写入新 Revision | 待审候选的基线不再是当前头。批准返回 `409`。 |
| 被拒绝的候选已不再需要 | 拒绝它。已过期的候选仍然可以被拒绝。 |
| 出现新事实 | 记录它们，然后针对当前头触发新的评估。 |

```http
POST /v1/candidates/reject

{"scope_id":"S_PROJECT","candidate_id":"candidate_...","expected_version":1,
 "reason":"A maintainer corrected the conclusion first; reassess against the new baseline."}
```

头已移动后不要再尝试批准旧候选。重新评估是针对当前 Revision 的一次新运行，这也是服务端返回冲突而不是静默覆盖的原因。

## 个人记忆不受影响

以上内容不改变个人用法。用户仍然记录自己的资料、维护自己的 Profile 策略，并在 `Prepare` 中选择自己的 Profile。项目结论
是独立 Scope，有自己的 Revision、受众和历史。之后删除或关闭项目，不得删除任何人的个人记录。

## 目前尚不可用

以下内容明确列出，以免被误认为已可用的行为：

- **自动发现贡献者的新素材。** 常驻共享规则和固定的运行清单属于跨 Scope 共享。当前受支持的输入方式是把记录贡献到项目
  Scope。
- **直接从外部贡献者 Scope 产出项目结论。** 跨 Scope Dream 准入尚不可用，因此正式结论目前来自项目 Scope 自己的
  Journal、人工写入，或针对该 Journal 的已审候选。
- **跨 Scope 的范围查询与确切回读、覆盖度。** `Prepare` 覆盖当前 Scope 及其直接引用；成员需以自己的授权打开外部来源。
- **把共享结论交接给另一位维护者。** 见上文所有权说明。
- **归档或删除整个团队或项目 Scope。** Scope 生命周期操作仍在定义中。

这些能力到来时会扩展本流程而不是替换它：拥有记录的 Scope、角色、唯一结论和不可变 Revision 都保持上述形态。
