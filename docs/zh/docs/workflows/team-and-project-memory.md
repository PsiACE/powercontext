---
title: 团队与项目记忆
description: 用普通 Scope 保存共享结论，让成员读取，并在事实变化时修正。
---

# 团队与项目记忆

团队或项目不需要新的对象类型。它需要四件事：一个由团队约定为集体记录归属的 Scope；贡献者持续提供材料的方式；
一个成员可以读取的正式结论；以及事实变化时修正该结论的路径。普通 Scope 及其既有权限已经满足这四项，因此应用把业务团队
和项目 ID 映射到普通 Scope，并自行记录两者的关系。共享结论由一组显式维护者写入：创建它的 Principal，以及任何被授予
`artifact.maintainer` 角色（Artifact 级别）的 Principal。

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
| 只读取 Scope 中的材料 | `scope.viewer` |

这些角色只作用于项目 Scope，都不能授予访问另一个 Scope 的权限。因此在项目上的 `scope.viewer` 只能让成员读取项目自身的
材料，不能扩展到其他任何内容。打开存放在贡献者 Scope 中的证据，需要在该 Scope 上另行授权。

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

写入已存在的 Artifact 需要 `artifact.write`。Scope 角色不包含它：`scope.admin` 和 `scope.reviewer` 授予的是配置与审核，
而不是内容写入。该权限来自以下两种途径之一。

创建该 Profile 的 Principal 拥有它。`artifact.owner` 每个 Artifact 只属于一个 Principal，在创建时建立，且无法通过 Access
绑定接口授予。所有者可以替换该 Profile，也可以对它驱动需审核的生成。

项目 Profile 通常由多人维护，因此请用 Artifact 级别的 `artifact.maintainer` 角色显式绑定他们。它包含 `artifact.read` 与
`artifact.write`，允许绑定多个主体，接受 user 与 service 主体，并且不包含 `artifact.share`，也不改变所有权：

```http
POST /v1/access/bindings/create

{"subject":{"type":"user","id":"mara"},
 "resource":{"type":"artifact","scope_id":"S_PROJECT",
             "identity":{"family":"profile","artifact_id":"profile"},
             "selector":null},
 "role":"artifact.maintainer",
 "idempotency_key":"mara-orders-profile-maintainer"}
```

被显式绑定的维护者随后可以像所有者一样替换结论，并对它执行需审核的生成：

```http
PUT /v1/scopes/S_PROJECT/artifacts/profile/profile
If-Match: "revision:2"

{"content":{"content":"# Orders project\n\n- Check transaction status before retrying on the old channel."}}
```

该授权在每次请求上校验，因此撤销后会停止后续替换，且待审变更不再能被批准，因为提交新 Revision 需要当前的写入权限。撤销时使用
绑定的当前版本：

```http
POST /v1/access/bindings/revoke

{"binding_id":"...","expected_version":1,"idempotency_key":"mara-orders-profile-maintainer-revoke"}
```

绑定按 Artifact 身份授予，覆盖该身份的各 Revision。它不转移所有权：创建该 Artifact 的 Principal 继续拥有它，任何其他 Scope
中的 Profile 都各自保有自己的权限。因此项目为每位维护者授予一条绑定，并在其不应再写入时撤销。

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

读取结论与查看证据是不同权限。读取已提交的 Profile 需要它上面的 `artifact.read`，项目上的 Scope 角色可以提供它；打开它
所引用的确切 Source 或 Artifact，需要*那个*来源上的权限，并在打开该证据的那次请求上校验，任何项目角色都不能替代它。
因此可以读取项目结论的成员，若没有在存放该证据的 Scope 上另行获得授权，仍会被拒绝查看背后的私有事故报告。这种拒绝是预期
行为而不是缺陷。

项目用自己的 Source Journal 产出的 Revision，其证据在项目 Scope 内解析。运行会以它被准入时的同一限定形式保存选择，因此事后可以从
运行的 `input_manifest` 读到该运行确切使用的 Scope 与引用：

```json
{"input_manifest":{"scoped":[{"scope_id":"S_PROJECT",
                              "artifact":{"family":"experience","artifact_id":"art_1f0c","revision":3}}]}}
```

这是同 Scope 运行的结果存储，而不是请求字段：该限定形式命名的是运行自身的 Scope。本地选择保留既有的本地形式，回读本身不授予任何
新权限——打开所引 Revision 仍然是上文所述的那次独立的确切读取。

在 Dream 运行中选择*另一个* Scope 的材料目前会被拒绝。每一个这样的选择——而不只是本部署无法执行的那些——都会在运行被接受之前、
在任何证据被读取之前、在任何生成被尝试之前，以 `unsupported_shared_use` 失败。因此项目结论目前只能由项目 Scope 自身的证据产出，
而且公开的 Dream 创建请求根本没有提供指定其他 Scope 的途径。从贡献者 Scope 产出结论，以及自动发现可能准入哪些外部 Scope，都不可用。

## 一次查询多个成员 Scope

项目常常需要从多个已获授权的成员 Scope 汇总一个答案，而不是逐个读取。请显式声明来源：

```http
POST /v1/scopes/S_PROJECT/artifacts/search:cross-scope

{"family":"experience",
 "payload":{"query":"retry trace idempotency"},
 "scopes":["S_ALICE_WORK","S_BOB_WORK"],
 "limit":4,
 "coverage":"partial"}
```

每个来源都按其实际 Scope 单独授权，顺序按你声明的顺序，该顺序也是合并时的并列裁决顺序。声明来源不等于授权：Scope
关系、Context Reference 与 Artifact 血缘都不授予权限，因此你无法读取的来源会被拒绝，而不是被静默跳过。

响应会如实报告实际发生的情况。`coverage.complete` 表示是否所有声明来源都给出了结果，`coverage.failed_origins` 指出没有
给出结果的来源；因此 `"partial"` 只返回已授权的命中，绝不会把不完整的检索呈现为完整。使用 `"complete"` 时，任一必需来源
失败都会使查询失败。每条命中都带有其来源地址和在该来源内的排名：

```json
{"hits":[{"origin":{"scope_id":"S_ALICE_WORK",
                    "artifact":{"family":"experience","artifact_id":"art_1f0c","revision":3}},
          "origin_rank":1,
          "retrieval_score":2.37e-06}],
 "coverage":{"complete":false,
             "reason":"origin_failure",
             "searched_origins":["S_ALICE_WORK"],
             "failed_origins":["S_BOB_WORK"],
             "bounded_origins":[]}}
```

此处该成员可以读取 Alice 的工作 Scope，但不能读取 Bob 的，因此答案明确是不完整的：Alice 提供了命中，Bob 被列为失败。
这不是需要掩盖的错误——响应绝不会声称检索过它无法读取的来源。

分数在不同来源之间不可比较，因此合并使用各来源自身的排名与稳定的并列顺序。同一个本地 Artifact ID 出现在两个 Scope 中仍然是
两个不同输入，因为身份是完整的来源地址。命中请用既有的确切 Revision 读取打开，它会在该地址上重新校验权限，且不会回落到
当前头：

```http
GET /v1/scopes/S_ALICE_WORK/artifacts/experience/art_1f0c/revisions/3
```

查询是有边界的。`max_scopes` 限制可声明的来源数量，`limit` 限制合并后的结果数量，`max_candidates` 作为该 Family 有效窗口的
上限限定每个来源的候选工作量——它绝不会放宽已声明或默认的 limit。`limit` 不得大于 `max_candidates`。

预算与覆盖度相互作用，两种模式表现不同：

- 使用 `"partial"` 时，预算小于有效单来源窗口是允许的。查询照常执行，响应会如实报告缺口：`coverage.complete` 为
  `false`，`reason` 为 `"candidate_budget"`，`bounded_origins` 列出每个以收窄窗口检索过的来源。
- 使用 `"complete"` 时，同样的请求会在检索任何来源前被拒绝（`422`、`cross_scope_query_bounds`），因为收窄后的范围无法
  诚实地报告为已完整检索。

两种情况下结果都不会被静默截断：你要么在覆盖度中看到缺口，要么请求被拒绝。`"partial"` 的缺口并不等于整个查询失败。把查询
限制到单个 Artifact 或 Revision 会被拒绝，因为可检索投影只索引当前头。

## 修正它，并在新事实出现时重新评估

修正操作对该 Profile 的所有者、以及每个在其上持有 `artifact.maintainer` 绑定的 Principal 可用。三种情形看起来相似，但结果
不同：

| 情形 | 服务端行为 |
| --- | --- |
| 候选待审期间所有者写入新 Revision | 待审候选的基线不再是当前头。批准返回 `409`。 |
| 被拒绝的候选已不再需要 | 拒绝它。已过期的候选仍然可以被拒绝。 |
| 出现新事实 | 记录它们，然后针对当前头触发新的评估。 |

```http
POST /v1/candidates/reject

{"scope_id":"S_PROJECT","candidate_id":"candidate_...","expected_version":1,
 "reason":"A maintainer corrected the conclusion first; reassess against the new baseline."}
```

头已移动后不要再尝试批准旧候选。重新评估是针对当前 Revision 的一次新运行，这也是服务端返回冲突而不是静默覆盖的原因。

针对已有 Profile 的需审核生成需要同一份 `artifact.write` 权限。缺少当前写入权限时，Principal 无法修改结论、无法启动新的
评估、也无法批准变更。需要注意一点：`POST /v1/profile/flush` 仍可能返回已经存在的待审 Candidate，因为报告该状态属于读取，
而不是新的写入。该响应绝不会恢复提交权限——在重新获得写入权限之前，批准会一直被拒绝。因此希望多人维护的项目应为每位维护者绑定
`artifact.maintainer`，而不是共用一个身份。

## 个人记忆不受影响

以上内容不改变个人用法。用户仍然记录自己的资料、维护自己的 Profile 策略，并在 `Prepare` 中选择自己的 Profile。项目结论
是独立 Scope，有自己的 Revision、受众和历史。之后删除或关闭项目，不得删除任何人的个人记录。

## 目前尚不可用

以下内容明确列出，以免被误认为已可用的行为：

- **自动发现贡献者的新素材。** 常驻共享规则和固定的运行清单属于跨 Scope 共享。当前受支持的输入方式是把记录贡献到项目
  Scope。
- **直接从外部贡献者 Scope 产出项目结论。** 跨 Scope Dream 准入尚不可用，因此正式结论目前来自项目 Scope 自己的
  Journal、人工写入，或针对该 Journal 的已审候选。
- **自动发现应当查询哪些 Scope。** 查询使用调用方声明的来源清单；不会有任何机制扩展它，结构子节点、Context Reference
  与 Artifact 血缘都不授予权限。
- **转移共享结论的所有权。** 通过 `artifact.maintainer` 共享维护权限，但 `artifact.owner` 关系不可重新指派。见上文维护说明。
- **归档或删除整个团队或项目 Scope。** Scope 生命周期操作仍在定义中。

这些能力到来时会扩展本流程而不是替换它：拥有记录的 Scope、角色、唯一结论和不可变 Revision 都保持上述形态。
