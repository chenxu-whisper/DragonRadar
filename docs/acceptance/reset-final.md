# Reset Full-Scope Final Acceptance (Task 29)

Recorded: 2026-10-27
Branch: `feature/task21r-task22` (worktree `.worktrees/task21r-task22`)

> 结论边界：本文件登记的是**已实现范围的开发完成与合成回归**状态。
> **开发完成不等于生产就绪。** 五个外部生产门禁（真实输入、官方规则、真实模型
> 评测、公开部署许可、≥20 交易日影子运行）在本机均无法取得真实材料，逐项保持
> `blocked`。Task 27（B01/B02、EX14/EX15）未实现，按已实现范围终验；本文件不构成
> 生产层面的最终验收结论，也不制造供应商、凭证、官方规则或测试结果。

## 1. 范围与结论

- 已交付并纳入终验：Task 20–26、Task 28（含 EX01–EX13、EX16，以及 E01–E05、
  V09–V11 对应的实时/龙虎榜/词云/轮动/NL 查询/偏好/情绪研究能力）。
- **未交付（blocked）**：Task 27 —— B01（EX14 PIT 回测 / 因子体检 / walk-forward
  滚动调权）与 B02（EX15 本地模拟账本 / AI 对照）。无迁移 `0014_research`，无
  `/api/research/backtests*` 路由，无对应前端面板与测试。
- 迁移水位：head 固定为 `0013_query`；`SCHEMA_REVISION == "0013_query"`。
- 本任务不新增第三方库依赖，不提供 Web 管理后台，题材/行情快照仍仅经本机 CLI
  导入与发布。

### 七个独立状态台账

下列七个状态相互独立，任一外部状态 blocked 都不得用合成回归结果替代。

| # | 状态 | 结论 | 证据/阻塞说明 |
|---|---|---|---|
| 1 | 开发完成（已实现范围） | 完成（已实现范围，Task 27 除外） | 见 §3 全量门禁真实退出码；Task 27（B01/B02、EX14/EX15）未实现。**此为开发完成，不等于生产就绪** |
| 2 | 合成回归（synthetic regression） | 合成数据通过（非真实有效性结论） | 全量 pytest 3941 passed / vitest 1025 passed / Playwright 320 passed，明细与两处主机环境性失败（性能门、1 个焦点时序断言）见 §3；合成数据不代表真实输入有效 |
| 3 | 真实输入（real input） | **blocked** | 本机无真实行情/新闻授权输入，禁止伪造 |
| 4 | 官方规则（official rules） | **blocked** | 无官方角色/选股规则授权文本，不能以产品规则冒充 |
| 5 | 真实模型评测（real model evaluation） | **blocked** | Task 27 未实现且无冻结真实样本，EX14 walk-forward 无法运行 |
| 6 | 公开部署许可（public sharing permit） | **blocked** | 未取得公开发布/分享许可，CI 改动只提交不推送 |
| 7 | ≥20 交易日影子运行（shadow run 20 trading days） | **blocked** | 未进行真实依赖影子运行，无逐日记录 |

<!-- t29-state:development-complete -->
<!-- t29-state:synthetic-regression -->
<!-- t29-state:real-input:blocked -->
<!-- t29-state:official-rules:blocked -->
<!-- t29-state:real-model-evaluation:blocked -->
<!-- t29-state:public-sharing-permit:blocked -->
<!-- t29-state:shadow-run-20-trading-days:blocked -->

> 上述 HTML 注释锚点由 `tests/test_reset_full_scope.py` 锁定，确保七个状态始终
> 显式登记，且本文件不得出现生产层面全量验收的通过性表述。

## 2. 需求与扩展追溯矩阵

### 2.1 57 项 PRD 需求（REQ）追溯

下表与 `scripts/check_prd_contracts.py` 的 57 项 `EXPECTED_REQS` 一一对应；任务归属
以 PRD《57项需求归属》表为准。B01/B02 为 blocked，证据只指向本文件，不伪造自动化
证据。

| REQ | 任务 | 自动化/页面证据 |
|---|---|---|
| G01 | 02、03、08 | tests/test_reset_publication.py · tests/test_reset_market.py · web/tests/e2e/reset-market.spec.ts · web/src/pages/Overview.tsx |
| G02 | 09、14 | tests/test_reset_market.py · tests/test_reset_news.py · web/tests/e2e/reset-navigation.spec.ts |
| G03 | 03、05、12 | web/tests/unit/spa-navigation.test.ts · web/tests/e2e/reset-navigation.spec.ts · web/tests/e2e/reset-preferences.spec.ts |
| G04 | 20 | web/tests/unit/reset-export.test.ts · web/tests/e2e/reset-preferences.spec.ts |
| M01 | 07、09 | tests/test_reset_ingestion.py · tests/test_reset_market.py · web/tests/e2e/reset-market.spec.ts · web/src/pages/Overview.tsx |
| M02 | 09 | tests/test_reset_market.py · web/tests/e2e/reset-market.spec.ts |
| N01 | 14 | tests/test_reset_news.py · web/tests/e2e/reset-news.spec.ts · web/src/pages/News.tsx |
| N02 | 14 | tests/test_reset_news.py · web/tests/e2e/reset-news.spec.ts |
| N03 | 14、18 | tests/test_reset_news.py · tests/test_reset_ai_tasks.py · web/tests/unit/reset-news-api.test.ts |
| L01 | 10、11、12 | tests/test_reset_role_api.py · tests/test_reset_role_publication.py · web/tests/e2e/reset-leaders.spec.ts · web/src/pages/Leaders.tsx |
| L02 | 07、11、12 | tests/test_reset_role_publication.py · tests/test_reset_role_api.py · web/tests/e2e/reset-leaders.spec.ts |
| L03 | 11、12 | tests/test_reset_role_contracts.py · tests/test_reset_role_publication.py · web/tests/e2e/reset-leaders.spec.ts |
| P01 | 13 | tests/test_reset_analysis.py · web/tests/e2e/reset-analysis.spec.ts · web/src/pages/Analysis.tsx |
| P02 | 13 | tests/test_reset_analysis.py · web/tests/e2e/reset-analysis.spec.ts |
| S01 | 12、13、14、16 | tests/test_reset_analysis.py · tests/test_reset_market.py · web/tests/e2e/reset-navigation.spec.ts · web/src/pages/SecurityDetail.tsx |
| U01 | 04、05、06 | web/tests/unit/reset-local.test.ts · web/tests/unit/reset-backup.test.ts · web/tests/e2e/reset-preferences.spec.ts |
| U02 | 04、05、06 | web/tests/unit/reset-note-provenance.test.ts · web/tests/unit/reset-local.test.ts · web/tests/e2e/reset-review.spec.ts |
| U03 | 05、17、18 | tests/test_reset_ai_tasks.py · web/tests/unit/review-draft.test.ts · web/tests/e2e/reset-review.spec.ts |
| U04 | 05、06、11 | web/tests/unit/reset-note-provenance.test.ts · web/tests/e2e/reset-review.spec.ts |
| R01 | 15 | tests/test_reset_regulation.py · tests/test_reset_regulation_api.py · web/tests/e2e/reset-regulation.spec.ts · web/src/pages/Regulation.tsx |
| R02 | 15、16 | tests/test_reset_regulation.py · web/tests/e2e/reset-regulation.spec.ts |
| R03 | 15、16 | tests/test_reset_regulation.py · web/tests/e2e/reset-regulation.spec.ts |
| R04 | 15 | tests/test_reset_regulation.py · tests/test_reset_retention.py |
| Q01 | 07、08、09 | tests/test_reset_publication.py · tests/test_reset_market.py · tests/test_reset_contracts.py |
| Q02 | 07、08、14、15 | tests/test_reset_ingestion.py · tests/test_reset_contracts.py |
| Q03 | 07、20 | tests/test_reset_ingestion.py |
| Q04 | 03、05、08、09、16 | tests/test_reset_ingestion.py · tests/test_reset_publication.py |
| D01 | 07、19 | tests/test_owner_api.py · tests/test_reset_public_gateway.py |
| D02 | 07 | tests/test_reset_ingestion.py |
| D03 | 08 | tests/test_reset_publication.py · web/tests/e2e/reset-market.spec.ts |
| D04 | 06、07、08、20 | tests/test_reset_retention.py · web/tests/e2e/reset-recovery.spec.ts |
| A01 | 17、18 | tests/test_reset_ai_tasks.py · web/tests/unit/reset-ai.test.ts |
| A02 | 14、17、18 | tests/test_reset_ai_tasks.py · tests/test_reset_news.py · web/tests/unit/reset-ai.test.ts |
| A03 | 12、17、18 | tests/test_reset_ai_tasks.py · web/tests/unit/reset-ai.test.ts |
| A04 | 05、17、18 | tests/test_reset_ai_tasks.py · web/tests/unit/review-draft.test.ts · web/tests/e2e/reset-review.spec.ts |
| A05 | 18、20 | tests/test_reset_ai_verification.py · tests/test_reset_ai_tasks.py · web/tests/e2e/reset-ai.spec.ts |
| E01 | 21、29 | tests/test_reset_realtime.py · web/tests/e2e/reset-realtime.spec.ts · docs/acceptance/reset-realtime.md |
| E02 | 22、29 | tests/test_reset_lhb.py · web/tests/e2e/reset-lhb.spec.ts · docs/acceptance/reset-lhb.md |
| E03 | 23、29 | tests/test_reset_wordcloud.py · web/tests/e2e/reset-wordcloud.spec.ts |
| E04 | 24、29 | tests/test_reset_rotation.py · web/tests/e2e/reset-rotation.spec.ts · docs/acceptance/reset-leaders.md |
| E05 | 26、29 | tests/test_reset_query.py · web/tests/e2e/reset-query.spec.ts · web/src/features/query/QueryPanel.tsx · docs/acceptance/reset-query.md |
| B01 | 27、29 | **blocked — Task 27 未实现（EX14 PIT 回测/因子体检/walk-forward）**；仅登记于本文件 |
| B02 | 27、29 | **blocked — Task 27 未实现（EX15 本地模拟账本/AI 对照）**；仅登记于本文件 |
| X01 | 19 | tests/test_owner_api.py · tests/test_reset_public_gateway.py |
| X02 | 17、18、19 | tests/test_reset_ai_verification.py · tests/test_reset_query.py · web/tests/unit/reset-ai.test.ts |
| X03 | 07、08、19、20 | tests/test_reset_retention.py · web/tests/e2e/reset-recovery.spec.ts |
| V01 | 03、20 | web/tests/unit/spa-navigation.test.ts · web/tests/e2e/reset-navigation.spec.ts |
| V02 | 03、12、16 | web/tests/unit/role-labels.test.ts · web/tests/e2e/reset-leaders.spec.ts |
| V03 | 09、12、13 | web/tests/e2e/reset-analysis.spec.ts · web/tests/e2e/reset-leaders.spec.ts |
| V04 | 03、05、12、13 | web/tests/e2e/reset-navigation.spec.ts · web/src/pages/SecurityDetail.tsx |
| V05 | 13 | tests/test_reset_analysis.py · web/tests/e2e/reset-analysis.spec.ts |
| V06 | 03、20 | web/tests/e2e/reset-realtime.spec.ts · web/tests/e2e/reset-preferences.spec.ts |
| V07 | 03、17 | web/tests/e2e/reset-realtime.spec.ts |
| V08 | 05、12、14、18 | tests/test_reset_news.py · web/tests/e2e/reset-news.spec.ts |
| V09 | 25、29 | web/tests/unit/reset-preferences.test.ts · web/tests/e2e/reset-preferences.spec.ts |
| V10 | 25、29 | web/tests/unit/reset-preferences.test.ts · web/tests/e2e/reset-preferences.spec.ts |
| V11 | 25、29 | web/tests/unit/reset-preferences.test.ts · web/tests/e2e/reset-preferences.spec.ts |

### 2.2 工程扩展 EX01–EX16 追溯

EX 为工程扩展，不计入 PRD 原 29 项接口；EX14/EX15 属 Task 27，surface 为 `none`。

| EX | 任务 | 应用面 | 路由 | 证据 |
|---|---|---|---|---|
| EX01 | 13 | owner | `POST /api/analysis/jobs` | tests/test_analysis_jobs.py · tests/test_owner_api.py |
| EX02 | 13 | owner | `GET /api/analysis/jobs/{job_id}` | tests/test_analysis_jobs.py |
| EX03 | 14 | owner | `POST /api/news/{article_id}/associations/review` | tests/test_reset_news.py |
| EX04 | 21 | workbench (GET) | `/api/realtime/quotes` | tests/test_reset_realtime.py · web/tests/e2e/reset-realtime.spec.ts |
| EX05 | 21 | workbench (GET) | `/api/realtime/minutes` | tests/test_reset_realtime.py · web/tests/e2e/reset-realtime.spec.ts |
| EX06 | 21 | workbench (GET) | `/api/realtime/anomalies` | tests/test_reset_realtime.py · web/tests/e2e/reset-realtime.spec.ts |
| EX07 | 21 | workbench (GET) | `/api/market/sector-flow/intraday` | tests/test_reset_realtime.py · web/tests/e2e/reset-realtime.spec.ts |
| EX08 | 22 | workbench (GET) | `/api/lhb` | tests/test_reset_lhb.py · web/tests/e2e/reset-lhb.spec.ts |
| EX09 | 22 | workbench (GET) | `/api/lhb/seats/{seat_id}` | tests/test_reset_lhb.py · web/tests/e2e/reset-lhb.spec.ts |
| EX10 | 23 | workbench (GET) | `/api/news/wordcloud` | tests/test_reset_wordcloud.py · web/tests/e2e/reset-wordcloud.spec.ts |
| EX11 | 24 | workbench (GET) | `/api/topics/rotation` | tests/test_reset_rotation.py · web/tests/e2e/reset-rotation.spec.ts |
| EX12 | 26 | owner | `POST /api/query/parse` | tests/test_reset_query.py · web/tests/e2e/reset-query.spec.ts · web/src/features/query/QueryPanel.tsx |
| EX13 | 26 | owner | `POST /api/query/execute` | tests/test_reset_query.py · web/tests/e2e/reset-query.spec.ts |
| EX14 | 27 | **none — blocked (Task 27)** | 不存在 `/api/research/backtests` | 仅登记于本文件 |
| EX15 | 27 | **none — blocked (Task 27)** | 不存在 `/api/research/backtests/{run_id}` | 仅登记于本文件 |
| EX16 | 28 | workbench (GET) | `/api/market/sentiment/research` | tests/test_reset_sentiment_research.py · web/tests/e2e/reset-sentiment-research.spec.ts · web/src/features/research/SentimentPanel.tsx · docs/acceptance/reset-sentiment-research.md |

应用面物理隔离由契约测试锁定：workbench 仅 GET/HEAD 且不泄露 `/api/analysis|query|
ai|research` 私有命名空间；owner 提供 EX01–03/12/13 但不提供 Task 27 研究路由；
public 无 POST 且无私有命名空间。

## 3. 全量门禁记录（真实退出码）

> 规则：所有结果以实际命令退出码、环境、耗时、skip 原因如实登记；外部/平台不可达保持
> `blocked`，**不预填 PASS、不把环境性失败记成产品缺陷，也不把开发门禁表述成生产验收**。
> 记录时间 2026-10-27。Windows 本地无法完成 Linux-only 私有 TLS、Linux Docker 生产
> 隔离镜像与 full `verify_reset.py`（既有平台 skip），这些项在 CI（Ubuntu）验证并在此
> 据实标注。

环境基线：Windows + PowerShell 5；Python 3.12（uv）；PostgreSQL 17 测试容器
`dragon-radar-foundation-test-db`（`127.0.0.1:55432`）；Node 前端（Vitest/Playwright，
本地 browser channel `msedge`，CI 用 chromium）。

| 门禁 | 命令 | 结果 |
|---|---|---|
| PRD 契约 | `uv run python scripts/check_prd_contracts.py` | **EXIT=0**；`REQ=57 endpoints=29 examples=29 JSON=30`（2026-10-27，存档 `.temp/acceptance/t29-check-prd-contracts.txt`）。已纳入 `verify_reset.REQUIRED_GATES` 首位与 CI `verify` 作业 |
| 全范围后端契约 | `uv run pytest -q -rs tests/test_reset_full_scope.py` | **14 passed / 0 failed**（PG17 就绪；无 DB URL 时为 12 passed / 2 skipped，集成项自动 skip 不 fail） |
| 全量后端 | `uv run pytest -q -rs`（PG17 容器 55432） | **3941 passed / 15 skipped / 1 failed，EXIT=1，54:20**；唯一 failed 为性能门（主机锁睿频所致，环境性，见下节），非功能回归。功能测试全绿；较 Task28 无 DB 基线 3928 passed 净增 14（新 `test_reset_full_scope.py`） |
| Ruff lint | `uv run ruff check .` | **EXIT=0**（全量干净） |
| Ruff format | `uv run ruff format --check .` | **EXIT=0**，278 files formatted；唯一既有例外 `docs/superpowers/plans/2026-10-09-task21-release-audit.md`（基线既有，未改动） |
| compileall | `uv run python -m compileall -q src tests` | **EXIT=0** |
| verify_reset 单测 | `uv run pytest -q tests/test_verify_reset.py` | **51 passed / 0 failed / 0 skipped**（新增 `prd_contracts` gate 后自动适配，未改该测试文件） |
| 迁移/CI 契约 | `uv run pytest -q tests/test_release_migrations.py` | **139 passed / 57 skipped**（无 DB/平台项 skip）；CI 作业命令以成员资格断言，`ci.yml` 新增步骤不破坏契约 |
| 前端单测 | `npm test`（vitest + node 门禁脚本） | **vitest 1025 passed** + 10 个 node 门禁脚本通过，EXIT=0 |
| 前端类型 | `npx tsc --noEmit` | **EXIT=0** |
| 前端构建 | `npm run build` + `npm run check:bundle` | **均 EXIT=0**；default entry `index-DlbHibfg.js` 307,971 B（约 300.75 kB） |
| 四构建隔离 | default / reset / sharing / private | **四变体均 EXIT=0**：default 300.75 kB（`index-DlbHibfg.js`）· reset `VITE_RESET_WORKBENCH=1` 322.14 kB（`index-BVHr6z05.js`）· sharing 叠加 `VITE_PUBLIC_SHARING=1` 319.95 kB（`index-DWfPbCmQ.js`）· private `VITE_PRIVATE_REVIEW=1` 308.46 kB（`index-DcLdItHi.js`）。构建后已删 `web/dist` |
| Playwright（5 套，本机 msedge） | `npm run test:e2e[:reset/:private/:ai:public/:ai:private]` | 合计 **320 passed / 1 failed**：legacy 80 passed +1 failed（drawer 焦点时序，环境性，见下节）· reset **166 passed**（含新增跨域 9）· private **69 passed** · ai-public **1 passed** · ai-private **4 passed** |
| 新增跨域 E2E | `playwright.reset.config.ts` 跑 `reset-full-scope.spec.ts` | **9 passed / 0 skipped / console 零 error（25.8s）**；Task 27 段为通过性边界断言，不用 `test.skip()` |
| 生产隔离 | Linux Docker 镜像路由控制（`test:e2e:production-isolation`） | **blocked on Windows**；以 CI Ubuntu 为准，CI 改动只提交不推送 |
| 性能门 | `tests/performance/test_reset_queries.py` → `.temp/acceptance/reset-performance.json` | **本机当前窗口不达标（环境性）**；最近一次达标证据 2026-10-26 同机同码 agg P95=1.733s/max=1.748s。详见下节；权威以 CI Ubuntu 为准 |
| full verify_reset | `uv run python scripts/verify_reset.py --mode full` | **blocked on Windows**（15 平台 skip + Linux-only TLS/Linux Docker）；CI Ubuntu `task20_acceptance` 运行（只提交不推送） |

### 3.1 两处主机环境性失败（非产品回归）的判定证据

终验在本机遇到两处失败，均定位到主机/平台环境而非产品代码；产品 `src/`、SQL、迁移与
相关前端源码在本任务中零改动（`git status` 可核），不修改阈值、不删除/跳过测试、不伪造绿色。

1. **性能门 P95（唯一的全量 pytest 失败项）**
   - 预算：固定 100 标的 / 61 会话，4 个公开 GET 路径 ×20 并发 ×3 轮，P95 ≤ 2.0s，要求
     0 错误、0 写入。
   - 本机 2026-10-27 多次实测 round1 P95 在 2.10–3.90s 抖动；放宽预算的一次性临时探针
     （探针文件用后即删，未入库）显示三轮**稳态**全部偏慢：P50 2.15–2.71s、P95
     2.97–3.88s、agg P95=3.866s，且每轮 **Python 进程 CPU 秒同步翻倍**
     （历史达标 1.70–1.78s → 当天 3.09–4.16s）。
   - 排除项：端口转发健康（`SELECT 1` 30 次 P50=1.59ms）、PG 版本一致（17.11）、整机
     CPU 空闲（19–29%）、重启 PG 容器无效（3.90→2.10s 仍不达标）。
   - 根因：性能计数器实测单核满载时 `% Processor Performance` 仅约 100–109%、
     `% of Maximum Frequency` 86–97%，即 i9-13950HX 被锁在基频约 2.2GHz、**单核睿频未
     拉起**（该突发负载高度依赖单核睿频），吞吐约为正常窗口的 1/2.4，与观测变慢 2.4×
     吻合。属主机电源/睿频状态（平衡方案/平台调温或厂商性能模式），非查询/SQL 回归。
   - 处置：保留 2026-10-26 同机同码达标证据（agg P95=1.733s）作为"该门可在正常睿频窗口
     通过"的佐证；权威结果以 CI Ubuntu（原生 PG、无桌面负载、睿频正常）为准。本机恢复
     睿频（接电 + 高性能/厂商性能模式）后应可复现绿色，不改预算阈值。
2. **legacy E2E 单个焦点时序断言**
   - `tests/e2e/snapshot-context.spec.ts:53` 在 `Shift+Tab` 后**非轮询地同步**读取
     `document.activeElement.closest('dialog')`。该用例及 drawer/导航源码相对 HEAD
     `bfa3cdf` 均无改动。
   - 隔离 `--repeat-each 3`：第 1 次（浏览器/dev server 冷启动）失败、第 2/3 次通过；
     4 文件子集复跑 53 passed / 同点 1 failed。页面快照显示 drawer 与工作台渲染正常，
     仅采样瞬间焦点尚未落入 dialog。锁频主机放大了该即时断言的时序窗口。
   - 处置：判定为既有测试在锁频主机上的环境性 flaky；CI Ubuntu/chromium 为权威。本任务
     不扩大范围改写该既有 E2E（避免无关改动与额外复审）。

### 3.2 性能门范围决策（不冒充 Task 27 契约）

计划要求"分钟级轮询与研究任务**同时运行**"的组合性能场景；研究任务为 Task 27
（B01/B02、EX14/EX15）交付物，整体组合负载随 Task 27 一并 **blocked**，不在合成数据上
推断其有效性。既有 Task 20 性能门已覆盖：公开 GET 20 并发 P95 预算、owner 慢任务
（5.1s 合成延迟）与公开读路径的资源隔离、公开应用零写入。本任务**不新增**独立基准去
冒充该组合契约；`MEASURED_PATHS` 由 `verify_reset.py` 与测试共享、证据结构用精确字段
校验，扩展牵连面大，亦不宜在 Task 27 缺件时单方面扩张。

## 4. 跨域 E2E（reset-full-scope）

新增 `web/tests/e2e/reset-full-scope.spec.ts`，由 `web/playwright.reset.config.ts`
（`testMatch: reset-*.spec.ts`）自动收集。覆盖：固定历史快照跨导航上下文不丢失 →
实时批次独立（不用实时数据替换固定日线）→ 龙虎榜与合成授权边界 → 词云 → 题材轮动
→ NL 查询解析/预览/显式确认/执行并绑定快照 → 本地笔记断网可写 IndexedDB 与 reload
恢复 → 授权撤回/AI 关闭/owner 私有边界 → 两主题/密度/Ctrl+K 命令面板。

Task 27 段**不使用 `test.skip()`**（verify_reset 测试型门禁要求 0 skip），改写为通过的
边界断言：不发 `/api/research/**` 请求（测试将 `**/api/research/**` 注册为返回
`TASK27_MUST_BE_ABSENT` 的探针并记录调用，正常路径断言零调用）、无回测/模拟账本 UI
入口，并在测试名/注释中注明 Task 27 blocked。

结果：**9 passed / 0 skipped / console 零 error（25.8s，2026-10-27，本机 msedge +
`playwright.reset.config.ts`）**，并计入 reset 套件 166 passed（= Task28 基线 157 +
本新增 9）。

## 5. Task 27 / B01 / B02 / EX14 / EX15 边界

- Task 27（研究回放与模拟账本）在本分支完全未实现，按已批准范围决策以已实现范围终验，
  不用原 P1/P2 延期替代交付。
- 后端无 `dragon_radar.research` 包、无 `http/routes/research.py`、无
  `0014_research` 迁移；前端 `web/src/features/research/` 仅含 `SentimentPanel.tsx`
  （Task 28）；无 simulation repository、Backtest/Simulation 面板与对应测试。
- 契约测试 `test_task27_artifacts_remain_absent` /
  `test_owner_app_serves_ex01_03_12_13_but_not_task27_research_routes` /
  `test_migration_head_is_0013_query_with_no_task27_revision` 锁定该边界。
- 真实模型评测（状态 5）与研究任务并发负载因 Task 27 缺失而 blocked，不在合成数据上
  推断有效性。

## 6. 发布边界

本任务继续遵守：不 merge、不 tag、不 push、不改版本号、不发布镜像、不正式 release、
不部署。`.github/workflows/ci.yml` 的改动**只提交不推送**；`.trae/` 永不入库；
`web/dist` 用完即删。
