# Production Hardening Checklist

## Identity

- run id: `production-hardening-20260809`
- branch: `agent/inspiration-generalization`
- baseline: `537ddd446df71c2ffaab25c2a1801e2be2df3c00`
- stage: implementation / main-test

## Final audit state

- [x] Gate A safety and integrity implementation
- [ ] Gate B production activation is complete; parent-death child cleanup remains a P0
- [ ] Gate D real provider-backed deployment completion
- [ ] Gate E scientific acceptance with external experts/gold set
- [x] Stable-tree engineering audit and release decision

## Gate A

- [x] redirect per-hop fail-before-connect plus adversarial tests
- [x] complete execution/build manifest plus drift tests
- [x] approve/reject/cancel operator decision paths plus E2E
- [x] conservative negation/uncertainty relation classification plus gold cases
- [x] online full Artifact closure verification plus tamper tests

## Gate B

- [x] durable job/outbox transaction
- [x] worker lease/heartbeat/reclaim and fencing
- [x] transition checkpoints and immutable completed-stage recovery
- [x] propagated cooperative monotonic deadline and measured scientific ledger snapshot
- [x] pin queued factory in the production profile
- [x] manage worker in deploy/start/stop/status/readiness/metrics
- [ ] supervisor-enforced hard kill is active, but a hard-killed parent can leave its independent-session child alive until the child deadline
- [x] complete terminal operations-attempt ledger separated from deterministic scientific ledger

## Gate C

- [x] run-bound report/evidence projection
- [x] strict length/URI/hash limits and tamper tests
- [x] Hermes-facing contract and architecture updates

## Gate D

- [x] one-command bootstrap/deploy implementation and Node/Python fail-fast
- [x] Dashboard/monitor start/stop/status and loopback health foundation
- [x] secret-safe event/metric primitives and static production E2E
- [x] real Node 22 bootstrap/build/component health-stop exercise
- [ ] real provider-backed full deploy/start/health/run/stop
- [x] worker-aware readiness and queue metrics
- [x] diagnostic failure events, external-dependency degradation, bounded log rotation, and operator failure acknowledgement
- [ ] CI workflow exists; coverage threshold, required checks/review, and protected-branch evidence remain open

## Gate E

- [x] deterministic bounded query allocation and metadata-quality audit code
- [x] expert Tag review/promotion/reject/rollback contract
- [x] pinned semantic adapter contract and hybrid/lexical ablation code
- [x] multi-candidate descriptive engineering evaluation code
- [ ] production consumption of an authenticated reviewed Tag release
- [ ] real pinned semantic provider used in production
- [ ] multi-source, adjudicated expert gold set and statistical release thresholds

## Validation

- [x] targeted tests
- [x] complete offline Gate: `985 passed, 14 skipped`
- [x] live Crossref Gate: `3 passed`
- [x] isolated MCP stdio Gate: `2 passed`
- [x] dependency checks in all three environments
- [x] bundle validation and component-level production exercise
- [x] `compileall` and `git diff --check`
- [x] requirement-by-requirement completion audit

## Next

- [x] Gate A implementation and targeted joint regression complete
- [x] Gate C implementation and targeted Gateway/public-Crossref regression complete
- [x] full stable-tree regression and production-readiness audit complete
- [x] pin queued factory in every installed/profile/probe production surface
- [x] own worker start/stop/status and fail readiness when it is absent
- [x] expose queue integrity/backlog/lease/failed-blocked operations metrics
- [x] prove installed-profile asynchronous production E2E and restart recovery with the static provider seam
- [ ] close parent hard-kill -> independently-sessioned child cleanup/reap and add the fault-injection Gate
- [ ] run a real provider-backed deployment
- [ ] complete external scientific acceptance Gate before changing the product label
