---
title: Team and project memory
description: Keep a shared conclusion in an ordinary Scope, let members read it, and correct it as facts change.
---

# Team and project memory

A team or a project does not need a new object type. It needs a Scope that the group agrees owns its
collective record, a way for contributors to keep supplying material, one official conclusion that members can
read, and a path to correct that conclusion when the facts change. PowerContext already provides all four, so an
application maps its business team and project IDs onto ordinary Scopes and records the relationship itself.

This page describes the composition that runs today. Where a step depends on a capability that is not available
yet, the text says so instead of implying it works.

## Choose the Scope that owns the conclusion

Map each business ID to a Scope with an external reference, so an application can find the Scope without storing
a private mapping table:

```http
POST /v1/scopes

{"title":"Orders project","summary":"Conclusion the Orders team maintains","idempotency_key":"orders-project",
 "external_references":[{"kind":"project","value":"orders"}]}
```

Look it up later by the same reference:

```http
GET /v1/scopes?query=orders&query_field=external_reference_value&external_reference_kind=project
```

To pin a host identity or a business ID to one Scope, use a durable binding. The binding key is yours to define:

```http
PUT /v1/scope-bindings

{"key":{"integration":"project","kind":"project","external_id":"orders"},"scope_id":"S_PROJECT"}

POST /v1/scope-bindings/resolve

{"binding_keys":[{"integration":"project","kind":"project","external_id":"orders"}],"allow_default":false}
```

Neither relationship grants access, and neither makes one Scope a structural child of the other. A team and a
project pick their own Scopes and may sit in entirely different subtrees. A product that associates a team with
several projects, or a project with several teams, keeps that association in its own directory: PowerContext sees
only the Scopes and the grants.

Reading a Scope and writing to a Scope are separate permissions. Give each member the role the work needs:

| Member | Role on the project Scope |
| --- | --- |
| Contributes observations, reviews, incident reports | `scope.contributor` |
| Decides whether a proposed conclusion becomes official | `scope.reviewer` |
| Changes Scope configuration, including the Profile policy | `scope.admin` |
| Only reads the conclusion and opens cited evidence | `scope.viewer` |

```http
POST /v1/access/bindings/create

{"subject":{"type":"user","id":"mara"},
 "resource":{"type":"scope","scope_id":"S_PROJECT"},
 "role":"scope.reviewer",
 "idempotency_key":"mara-orders-reviewer"}
```

## Keep supplying authorized inputs

Contributors capture work records where the work happens, and contribute the ones the project may use to the
project Scope itself:

```http
POST /v1/scopes/S_ALICE_WORK/sources

{"content":"A callback retried on the new channel succeeded with an idempotency key."}

POST /v1/scopes/S_PROJECT/sources

{"content":"A callback retried on the new channel succeeded with an idempotency key."}
```

The first write is evidence in Alice's own Scope and needs her own write authority there. The second puts the
same observation into the project's Source Journal, where the project's Profile processing reads it, and needs
`scope.contribute` on the project.

A user who writes to their personal Scope and to a shared Scope at once can use the subject route, which creates
both Sources in one transaction:

```http
POST /v1/scopes/S_ALICE_WORK/subject-sources

{"subject_key":"alice","content":{"speaker":"alice","text":"A callback retried on the new channel succeeded."}}
```

Selecting a standing range that discovers new contributor material automatically, with each run pinning its exact
inputs, is part of cross-Scope sharing. It is not available yet. Until it is, contributing a record to the project
Scope is the supported way to make it an input, and it is explicit about what the project may use.

## Maintain one official conclusion

A Scope holds at most one Profile, `family=profile` with `artifact_id=profile`. Turn on reviewed generation so a
maintainer decides each change:

```http
PUT /v1/scopes/S_PROJECT/profile-policy

{"generation_enabled":true,"activation_mode":"review_required","expected_version":0}
```

Trigger one evaluation, then decide the proposal:

```http
POST /v1/profile/flush

{"scope_id":"S_PROJECT"}

POST /v1/candidates/approve

{"scope_id":"S_PROJECT","candidate_id":"candidate_...","expected_version":1}
```

`flush` returns the journal position it consumed, the number of Sources it processed, and a `candidate_id` when a
change is pending. In `review_required` mode nothing official changes until the Candidate is approved. With
`activation_mode=automatic` the Server commits the revision itself. Whatever the mode, the Profile does not exist
until some run or manual write produces it, so a new project Scope answers `404` until then.

A maintainer can also write the conclusion directly, which is the path for a first Profile or a correction that
does not come from a model:

```http
POST /v1/scopes/S_PROJECT/artifacts

{"family":"profile","content":{"content":"# Orders project\n\n- Retry with an idempotency key on the new channel."}}
```

Creating twice returns `409`, because the Profile is a Scope singleton. Update the existing one by replaying its
validator:

```http
PUT /v1/scopes/S_PROJECT/artifacts/profile/profile
If-Match: "revision:1"

{"content":{"content":"# Orders project\n\n- Check transaction status before retrying on the old channel."}}
```

Every write is an immutable revision. `GET .../artifacts/profile/profile/revisions` lists the history, and
`GET .../revisions/{revision}` returns one exact past conclusion.

Writing an existing Artifact needs `artifact.write`, which comes from being that Artifact's owner. Ownership is
established when an Artifact is created and belongs to one Principal. Concretely, on an Access-enforced
deployment:

- the Principal that created the Profile can keep replacing it;
- another contributor, even one also holding `scope.reviewer` or `scope.admin`, is refused with `403`;
- `artifact.owner` cannot be granted through the Access binding API.

So a project must decide who creates the official Profile and treat that Principal as the maintainer of record.
If the person who should maintain it changes, the supported route today is to produce the conclusion through
Dream review or the Profile policy with the new maintainer's identity, or to recreate the Artifact, rather than to
hand over an existing one. Handing a shared conclusion between maintainers is an open decision.

## Let members read it

`Prepare` is how an Agent turn receives the conclusion. Profiles are not part of the default assembly, so select
the section explicitly:

```http
POST /v1/context/prepare

{"scope_id":"S_PROJECT",
 "query":"may we retry a callback after a timeout",
 "assembly":{"sections":[{"family":"profile","limit":1}]}}
```

The returned text names the exact address it used — `Scope: "...", family="profile", id="profile", revision=N` —
so a reader can open precisely that revision. Include `"include_receipt": true` to receive the receipt's
`selected` list of `artifact_address` values for the same answer.

`Prepare` reads the current Scope and its direct context references. It does not search structural children, and a
reference grants no authority: each Scope it reads is authorized as the caller. To give members a capability
rather than a conclusion, keep a Skill in the project Scope and let them load it:

```http
POST /v1/scopes/S_PROJECT/artifacts

{"family":"skill",
 "content":{"name":"incident-retry-triage",
            "description":"Decide whether a failed callback may be retried.",
            "instructions":"Check the channel. Retry with an idempotency key only on the new channel.",
            "validation":["Confirm the channel before retrying."]}}
```

A project Skill is independent of anyone's personal Profile. Alice's personal Profile stays in her own Scope
under her own policy, and the project never gains access to it by sharing a Scope tree or a reference.

## Open the evidence behind a conclusion

Reading a conclusion and inspecting its evidence are different permissions. Reading the committed Profile needs
`artifact.read` on it. Opening the exact Source or Artifact it cites needs authority at *that* origin, checked on
the request that opens it. A member who may read the project conclusion may still be refused the private incident
report behind it, and that refusal is the intended behavior rather than a defect.

Evidence for a revision the project produced from its own Source Journal resolves inside the project Scope. Using
external Sources or Artifacts as Dream evidence, and rechecking each origin's authority at approval, is part of
cross-Scope processing and is not available yet.

## Correct it, and revisit on new facts

Three situations look similar and end differently:

| Situation | What the Server does |
| --- | --- |
| A maintainer writes a new revision while a proposal is pending | The pending Candidate's baseline is no longer the head. Approval is refused with `409`. |
| The refused proposal is no longer wanted | Reject it. A stale Candidate can still be rejected. |
| New facts arrive | Capture them, then trigger a new evaluation against the current head. |

```http
POST /v1/candidates/reject

{"scope_id":"S_PROJECT","candidate_id":"candidate_...","expected_version":1,
 "reason":"A maintainer corrected the conclusion first; reassess against the new baseline."}
```

Do not try to approve the old proposal after the head moved. Reassessment is a new run against the current
revision, which is why the notice the Server returns is a conflict rather than a silent overwrite.

## Personal memory is unaffected

Nothing above changes personal use. A user still captures their own records, keeps their own Profile policy, and
selects their own Profile in `Prepare`. A project conclusion is a separate Scope with its own revisions, its own
audience, and its own history. Deleting or closing a project later must not delete anyone's personal records.

## What is not available yet

Stated plainly, so nothing here is mistaken for working behavior:

- **Automatic discovery of new contributor material.** Standing sharing rules and pinned run manifests belong to
  cross-Scope sharing. Contributing a record to the project Scope is the supported input path today.
- **Producing the project conclusion directly from external contributor Scopes.** Cross-Scope Dream admission is
  not available, so an official conclusion is currently produced from the project Scope's own Journal, a manual
  write, or a reviewed proposal over that journal.
- **Range queries across Scopes with exact readback and coverage.** `Prepare` covers the current Scope and its
  direct references; a member has to open a foreign origin under their own grant.
- **Handing a shared conclusion to a different maintainer.** See the ownership note above.
- **Archiving or deleting a whole team or project Scope.** Scope lifecycle operations are still being defined.

When those arrive they extend this workflow rather than replace it: the Scope that owns the record, the roles,
the singleton conclusion, and immutable revisions all stay as described.
