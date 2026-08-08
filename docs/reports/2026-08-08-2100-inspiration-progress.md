# Inspiration progress report — 2026-08-08 21:00–22:41 CST

## Verdict

The pre-approval completion and release audit is `PASSED`. It found no missing
P3.1–P3.3 technical work and no safe pre-approval execution step. The real
Crossref release remains `WAITING_FOR_EXACT_USER_APPROVAL` with exactly
`1 run / 0 result / 0 grant / 0 recovery`.

Three documentation/governance issues were closed without changing runtime
capability: the PR no longer implies PDF extraction, the final merge strategy
is frozen to a merge commit, and provider-specific long-running-service
backoff is explicitly deferred rather than left as an ambiguous beta checkbox.

A subsequent auxiliary engineering experiment added an independent,
read-only completed-run verifier. After additional audit rounds, its review-fixed
46-case representative Gate accepts two completed-workspace controls and the
bounded warning projection without writes and rejects 43 negative/adversarial
variants. These include selection/fetch probes that recompute the affected
intermediate pointer, bundle ID/pointer, and stage result ID, plus separate
canonical-URI, lineage-order, response/retry-budget, vector, bounded
Crossref-content, and cross-wired lineage probes. This result is
`SUPPORTED` only for a static Crossref transport through the production
component path; it is neither a live Crossref result nor exact v2 terminal
verification.

## Time, Git, and scope

- Audit and auxiliary-experiment interval: 2026-08-08 20:42–22:41 CST.
- Branch: `agent/inspiration-generalization`.
- Audited pushed head: `42abc5986b7a60184b11864ba89329c09e24d22d`.
- Public PR:
  [#3 Generalize and harden the Hermes inspiration workflow](https://github.com/chimeraHHH/materials_screening_agent_hermes/pull/3).
- PR state before this report commit: Draft, `MERGEABLE/CLEAN`; GitGuardian
  passed on `42abc59`.
- This pass was read-only with respect to the Hermes/Gateway run: it issued no
  grant, made no act call, and performed no Crossref request.

## Independent completion audit

Three independent read-only reviews covered the plan/checklists, the v2
Gateway and Artifact contract, and GitHub publication state. They agreed that
P3.1, P3.2, and P3.3 have no remaining technical item. The only completion
chain that still depends on execution is:

1. exact user decision bound to the v2 manifest;
2. one one-time grant, atomically consumed by the same pending interaction;
3. the same persistent Gateway run performing `get → act → result`; host-session
   reuse was the plan, while the later `-z` act created a one-shot Hermes session
   without changing the run/manifest/grant;
4. terminal non-empty Crossref result and complete Artifact/ledger verification;
5. final run record, work report, full release checks, PR review/Ready/merge;
6. anonymous read and hash verification from public `main`.

The M3 provider-specific 429/backoff item is now recorded as deferred,
non-blocking service hardening. Current beta behavior already has typed
transient errors, bounded `Retry-After`, bounded retries, physical-attempt
accounting, and deterministic fault-injection coverage. The live Gate must not
manufacture throttling against a public service.

## Reconstructed approval binding

The persisted Gateway record was parsed through the strict v1 models and the
canonical interaction/action bytes were independently re-hashed:

- run: `inspiration-2c470e4810392aca2c9a7c4d`;
- request SHA-256:
  `0598117ef45e17ec44f328f3effff5722166e2df589b8695ff0ff1c5a25220c6`;
- interaction: `interaction-f2d2ab3eaa402a5ed4481e69`;
- interaction SHA-256:
  `e4124c90db33375f83ea87af04f937fb791130d3933c083224c2d9c52fc972e0`;
- execution-manifest SHA-256:
  `e6b0d901fa0a8ef60a2797e35ff4b6cc21c78f175c04258940a3e9f40324beda`;
- exact approve action JSON:
  `{"confirmed_by_user":true,"interaction_id":"interaction-f2d2ab3eaa402a5ed4481e69","kind":"approve"}`;
- exact approve action SHA-256:
  `d38358ce205391fe24321add1f711954b0c74e4dbdb635a3b1cb3edc42dcf73f`.

Reconstruction did not write a grant. Both Gateway databases remained mode
`0600`; the Hermes credential file also remained mode `0600`. The credential
runtime and complete v2 workspace are ignored by Git.

At 22:25–22:27 CST, a second read-only check copied only the Gateway database
files to a temporary directory, parsed the strict run record there, and used a
read-only existing-Artifact store to rebuild the prepared input from current
source components. It confirmed `INTERACTION_REQUIRED / 1 run / 0 result / 0
grant / 0 recovery`, reproduced manifest `e6b0d901…24beda`, and proved the v2
project tree's file hashes and metadata were unchanged. Invoking the standalone
completed-run verifier against v2 returned exit 2 with `Gateway run is not a
result-bearing terminal state`, as required before approval.

## Publication audit and corrections

The PR description incorrectly said `PDF-text extraction seams`. The code and
tests instead allow restricted offline JSON-LD/Highwire, JATS/XML, and HTML
extraction while PDF inputs fail closed. The PR description now states that
boundary accurately.

The repository permits merge, squash, and rebase, but P3.x uses many
audit-oriented checkpoint SHAs. The final integration is therefore frozen to
a merge commit; squash and rebase are excluded. The repository is a public,
single-user local-beta project with no branch protection, required review, or
required CI workflow. This release accepts independent read-only review, the
recorded local complete Gates, and GitGuardian as its publication gate. Shared
or publicly operated service work must add protected branches and required
CI/review first.

Unauthenticated reads already prove that the topic-branch evidence is public:

- repository API: `private=false`, `visibility=public`, default branch `main`;
- latest report local/remote SHA-256:
  `a59ef93dc55c9fca81293d7bb601a43730887b4852bde2071d9961fddf659666`;
- pending run record local/remote SHA-256:
  `98a01a630383197b15149d6c4456945c1cfc8c873697fd3d01e1081445eaf386`.

Final anonymous-read evidence must still be repeated against the merge SHA and
public `main` after the result report is merged.

## Verification performed

```text
.venv/bin/python integrations/hermes/scripts/verify_bundle.py
Hermes bundle valid

git diff --check
passed

Gateway state re-read
INTERACTION_REQUIRED / 1 run / 0 result / 0 grant / 0 recovery

GitHub
Draft / MERGEABLE / CLEAN / GitGuardian SUCCESS

Standalone completed-run verifier
46 passed in 59.15s

Related Gateway/runner/approval group
61 passed in 64.13s

Full non-opt-in repository suite on the verifier-bearing worktree
825 passed, 12 skipped, 710 warnings in 78.86s
```

The full suite and verifier evidence above are fresh for this worktree. Three
environment dependency checks, isolated four-tool MCP smoke, and live Crossref
`3 passed` remain the latest release evidence from the immediately preceding
checkpoint and are revalidated separately before this auxiliary checkpoint is
published. All release checks must run again after the exact result/report
changes.

The auxiliary experiment is recorded in
[`docs/runs/2026-08-08-completed-run-verifier.md`](../runs/2026-08-08-completed-run-verifier.md).
It did not issue a grant, call `materials_run_act`, or make a Crossref request.

## Remaining exact decision

The v2 approval permits bounded public Crossref metadata/abstract network
access with at most eight physical search attempts. Article-body fetch
requests, full-PDF reads, and internal materials-service model calls are all
zero. The required decision remains:

```text
Approve manifest e6b0d901fa0a8ef60a2797e35ff4b6cc21c78f175c04258940a3e9f40324beda for run inspiration-2c470e4810392aca2c9a7c4d and interaction interaction-f2d2ab3eaa402a5ed4481e69, action approve.
```

No earlier generic continuation can bind this exact one-time grant. The
superseded v1 interaction remains unapproved and unexecuted.

This audit makes no scientific, novelty, prior-art, patentability, expert-acceptance, or
validated-property claim. Every target property remains `UNKNOWN`, and
`scientific_conclusion=false` remains mandatory.

## Post-checkpoint disposition

This report preserves the state before the user's exact approval. The named v2
manifest was subsequently approved, its unique grant was consumed with no
recovery, and the run reached a result-bearing terminal state. Exact execution,
the pinned-runtime full-closure verification, and release evidence are recorded
in the final v2 run record rather than retroactively presented as part of this
pre-approval checkpoint.
