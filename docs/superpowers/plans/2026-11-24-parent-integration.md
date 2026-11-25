# Approved remaining requirements: parent integration

Design approval: user confirmed all remaining unified designs on 2026-11-24.
Implementation worktree only; no staging, commit, publication or deployment.

1. Write failing application topology tests for new daily read routes, lineage
   ordering, index history, private promotion and research metadata boundaries.
2. Integrate workbench, public facade and owner constructors, copying only exact
   intended routes; preserve realtime/LHB authorization and feature boundaries.
3. Wire public and owner Caddy patterns and lazily loaded UI entrypoints. Keep
   owner clients absent from public bundles and scope reads to selected PUB.
4. Integrate local CLI entrypoints and sealed retention sidecar dispatch.
5. After module migration owners report stable readiness, verify the additive
   0015→0016→0017→0018 chain against disposable PostgreSQL 17 schemas. Generate
   the catalog contract from that database, then update verified release assets.
6. Finish analysis client/browser failure lifecycle evidence and finite B8
   residual cases. Perform one consolidated independent review and fixes.
7. Freeze current source, run applicable backend/frontend/browser tests, five
   build variants and the unchanged P95 ≤2.0s performance gate. Record actual
   command receipts and remaining real-input gates in final workspace artifacts.

Module ownership remains with active B2/B3/B4/B5/B7 agents. Parent owns shared
application, gateway, release/schema, CLI and integration files.
