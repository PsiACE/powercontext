---
title: Team and project memory
description: Keep a shared conclusion in an ordinary Scope, let members read it, and correct it as facts change.
---

# Team and project memory

A team or a project does not need a new object type. It needs a Scope that the group agrees owns its
collective record, a way for contributors to keep supplying material, one official conclusion that members can
read, and a path to correct that conclusion when the facts change. Ordinary Scopes and their existing permissions
provide each of these, so an application maps its business team and project IDs onto ordinary Scopes and records
the relationship itself. A shared conclusion is written by an explicit set of maintainers: the Principal that
created it and any Principal granted the per-Artifact `artifact.maintainer` role.

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
| Only reads material in the Scope | `scope.viewer` |

These roles govern the project Scope. None of them grants access to another Scope, so `scope.viewer` on the
project lets a member read the project's own material and nothing else. Opening evidence that lives in a
contributor's Scope needs a separate grant on that Scope.

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

Writing an existing Artifact needs `artifact.write`. The Scope roles do not carry it: `scope.admin` and
`scope.reviewer` grant configuration and review, not content write. Authority comes from one of two places.

The Principal that created the Profile owns it. `artifact.owner` is one Principal per Artifact, is established at
creation, and cannot be granted through the Access binding API. The owner can replace the Profile and drive
reviewed generation against it.

A project Profile is usually maintained by several people, so bind them explicitly with the per-Artifact
`artifact.maintainer` role. It carries `artifact.read` and `artifact.write`, allows many bindings, accepts user and
service subjects, and confers neither `artifact.share` nor ownership:

```http
POST /v1/access/bindings/create

{"subject":{"type":"user","id":"mara"},
 "resource":{"type":"artifact","scope_id":"S_PROJECT",
             "identity":{"family":"profile","artifact_id":"profile"},
             "selector":null},
 "role":"artifact.maintainer",
 "idempotency_key":"mara-orders-profile-maintainer"}
```

An explicitly bound maintainer can then replace the conclusion and run reviewed generation against it, exactly as
the owner can:

```http
PUT /v1/scopes/S_PROJECT/artifacts/profile/profile
If-Match: "revision:2"

{"content":{"content":"# Orders project\n\n- Check transaction status before retrying on the old channel."}}
```

The grant is checked on the request, so revoking it stops further replacement, and a pending change can no longer
be approved because committing a revision needs current write authority. Revoke with the binding's current version:

```http
POST /v1/access/bindings/revoke

{"binding_id":"...","expected_version":1,"idempotency_key":"mara-orders-profile-maintainer-revoke"}
```

The binding is per Artifact identity and covers that identity's revisions. It does not transfer ownership: the
creating Principal keeps the Artifact, and every other Profile in any Scope keeps its own separate authority. A
project therefore grants maintainers one binding each and revokes it when that person should no longer write.

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
`artifact.read` on it, which a Scope role on the project supplies. Opening the exact Source or Artifact it cites
needs authority at *that* origin, checked on the request that opens it, and no project role substitutes for it. A
member who may read the project conclusion is therefore refused the private incident report behind it unless they
separately hold a grant on the Scope that stores it. That refusal is the intended behavior rather than a defect.

Evidence for a revision the project produced from its own Source Journal resolves inside the project Scope. Using
external Sources or Artifacts as Dream evidence, and rechecking each origin's authority at approval, is part of
cross-Scope processing and is not available yet.

## Query several member Scopes at once

A project often needs one answer assembled from several authorized member Scopes rather than one Scope at a time.
Declare the origins explicitly:

```http
POST /v1/scopes/S_PROJECT/artifacts/search:cross-scope

{"family":"experience",
 "payload":{"query":"retry trace idempotency"},
 "scopes":["S_ALICE_WORK","S_BOB_WORK"],
 "limit":4,
 "coverage":"partial"}
```

Each origin is authorized on its own against its actual Scope, in your declared order, and that order is the merge
tie break. Declaring an origin is not a grant: Scope relationships, Context References, and Artifact lineage confer
no authority, so an origin you may not read is refused rather than silently skipped.

The response reports what actually happened. `coverage.complete` says whether every declared origin answered, and
`coverage.failed_origins` names the ones that did not, so `"partial"` returns only authorized hits and never
presents an incomplete search as complete. With `"complete"`, a required origin that fails fails the query. Each
hit carries its origin address and its rank within that origin:

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

Here the member could read Alice's work Scope and not Bob's, so the answer is explicitly incomplete: Alice
contributed a hit and Bob is named as failed. That is not an error to hide — the response never claims to have
searched an origin it could not read.

Scores are not comparable across origins, so merge uses each origin's own rank and a stable tie order. The same
local Artifact ID in two Scopes stays two distinct inputs, because identity is the complete origin address. Open a
hit through the existing exact-revision read, which rechecks access at that address and never falls back to the
current head:

```http
GET /v1/scopes/S_ALICE_WORK/artifacts/experience/art_1f0c/revisions/3
```

A query is bounded. `max_scopes` limits how many origins may be declared, `limit` caps the merged result count,
and `max_candidates` bounds per-origin candidate work as a ceiling on the Family's effective window — it never
widens a declared or default limit. `limit` must not exceed `max_candidates`.

The budget interacts with coverage, and the two modes differ:

- With `"partial"`, a budget smaller than the effective per-origin window is allowed. The query runs, and the
  response reports the shortfall rather than hiding it: `coverage.complete` is `false`, `reason` is
  `"candidate_budget"`, and `bounded_origins` names each origin that was searched with a narrowed window.
- With `"complete"`, the same request is refused before any origin is searched (`422`,
  `cross_scope_query_bounds`), because a narrowed range cannot honestly be reported as completely searched.

Either way results are never silently truncated: you either see the gap in coverage or the range is rejected. A
`"partial"` gap is not a whole-query failure. Restricting a query to one Artifact or revision is refused, because
the searchable projection indexes current heads only.

## Correct it, and revisit on new facts

Correction is available to the Profile's owner and to every Principal holding an `artifact.maintainer` binding on
it. Three situations look similar and end differently:

| Situation | What the Server does |
| --- | --- |
| The owner writes a new revision while a proposal is pending | The pending Candidate's baseline is no longer the head. Approval is refused with `409`. |
| The refused proposal is no longer wanted | Reject it. A stale Candidate can still be rejected. |
| New facts arrive | Capture them, then trigger a new evaluation against the current head. |

```http
POST /v1/candidates/reject

{"scope_id":"S_PROJECT","candidate_id":"candidate_...","expected_version":1,
 "reason":"A maintainer corrected the conclusion first; reassess against the new baseline."}
```

Do not try to approve the old proposal after the head moved. Reassessment is a new run against the current
revision, which is why the notice the Server returns is a conflict rather than a silent overwrite.

Reviewed generation against an existing Profile needs the same `artifact.write` authority. Without current write
authority, a Principal cannot modify the conclusion, cannot start a new evaluation, and cannot approve a change.
One caveat: `POST /v1/profile/flush` may still return an already-pending Candidate, because reporting that state is
a read and not a new write. That response never restores commit authority — approval remains refused until write
authority is granted again. A project that wants several maintainers therefore binds `artifact.maintainer` for
each of them rather than sharing one identity.

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
- **Automatic discovery of which Scopes to query.** A query takes an explicit origin list; nothing expands it,
  and structural children, Context References, and Artifact lineage grant no authority.
- **Transferring ownership of a shared conclusion.** Maintenance is shared through `artifact.maintainer`, but the
  `artifact.owner` relation is not reassignable. See the maintenance note above.
- **Archiving or deleting a whole team or project Scope.** Scope lifecycle operations are still being defined.

When those arrive they extend this workflow rather than replace it: the Scope that owns the record, the roles,
the singleton conclusion, and immutable revisions all stay as described.
