# Task 26：白名单自然语言查询（EX12-13 / REQ-E05）实施计划

> 对应总体计划锚点：`docs/superpowers/plans/2026-09-20-prd-reset-refactor.md` Task 26（约 L615–624，含 2026-10-22 整合人修订注记）。
> 需求/契约：REQ-E05（AC-E05，leader_quant_prd.md:883）、AC-X02（:1217）、EX12-13（`docs/contracts/workbench-api.md`）、DEC06/10/11/12。
> 规格权威：`.trae/documents/task26-whitelist-query_spec.md` v2（用户已批准 D1–D5）。本计划只细化执行，不重开决策；与规格冲突以规格为准并修订本文件。
> 工作树：`.worktrees/task21r-task22`（绝对路径 `e:\ByteDance\Projects\Code\DragonRadar\.worktrees\task21r-task22`），分支 `feature/task21r-task22`，HEAD `ecf455b`。**禁止** merge/tag/push/版本号变更/发镜像/release/部署。无新第三方依赖（Python 与 npm 均无）。

## 已确认的设计决策（用户已随规格 v2 批准，摘要不重开）

- **D1**：`return_ratio` 固定取物化 `analysis.windows["20"].adjustments.raw.ranking`（20 交易日 / raw_close / B000300）。两层不可用：声明性（window 缺失/`available=false`/顶层 raw 契约翻 false）→ parse 成功、execute 200 + `rows:[]` + `data.unavailable=[{code:"return_ratio_unavailable",reason}]`；结构性（window available=true 但缺 adjustments.raw、11 字段闭集破坏、跨题材 boards 不一致）→ 503 DATA_UNAVAILABLE。不引用该字段的查询不依赖窗口。
- **D2**：新增迁移 `0013_query`（parent `0012_lhb`），downgrade 按 `backup_restore_only` 抛 RuntimeError；Task 27 顺延 `0014_research`（冻结计划三处已回流，待提交）。
- **D3**：语法仅中文、仅合取（且/并/和/，/、/空白）；parser_version `zh-whitelist-v1`；OR/否定/残词/未登录字段/操作符 → unsupported。
- **D4**：按 security 去重；一行携带 `sector_ids[]`/`roles[]`；sector/role 谓词 = 任一题材满足。
- **D5**：QueryPanel 挂 `pages/Analysis.tsx` reset 分支，但必须模块级 `import.meta.env.VITE_RESET_WORKBENCH === '1'` 常量折叠 `resetEnabled ? lazy(import) : null`（仿 App.tsx:78-81，**禁止**照抄 Analysis.tsx:11-13 ResetAnalysis 的无条件 lazy）；`/api/query/` 入 bundle 禁私 marker。

## Repository Research（已在 worktree 内逐行取证）

### 后端模板与先例
- **analysis_jobs 是后端主模板**。严格模型：`analysis_jobs/models.py:33-45`（SafeIdentifier 模式、`_StrictFrozenModel` strict/frozen/extra=forbid/allow_inf_nan=False）；canonical JSON（sort_keys、separators=(",",":")、ensure_ascii、allow_nan=False）与 sha256 cache_key 在 :48-85。表定义（Core Table + FK owner_principals/publications 均 ondelete RESTRICT + UniqueConstraint(owner_id,idempotency_key)）在 :120-176。
- 仓储：`AnalysisJobRepository(database, owner_repository, publication_repository, *, clock=None)`（repository.py:80-94）；`_lock` = `SELECT pg_advisory_xact_lock(hashtextextended(:key,0))`（:99-104）；`_authorized_input`（:106-118）= select publications 行 → None 抛 NotFound → status!="published" 抛 Forbidden → `self.publications._authorized_records(connection,[row])[0]` 做许可复判；幂等提交（:159-191）：`_identifier(key)`（1..200，`storage/repository.py:47-50`）、锁键 `f"analysis-job:{owner_id}:{key}"`（:178）、同 key 异 request_hash 抛 Conflict（:189-191），**缓存命中路径同样经 `_authorized_input` 复判**（:233-245）。
- 路由：`analysis_jobs/routes.py` 全文 177 行即模板——`_AnalysisJobRoute(APIRoute)` 异常映射（:36-74：RequestValidationError/ValueError→400 INVALID_PARAM、Forbidden→403 FORBIDDEN、NotFound→404 NOT_FOUND、Conflict→409 VERSION_CONFLICT、SQLAlchemyError/StorageUnavailable/Legacy→503 STORAGE_UNAVAILABLE、`DataUnavailable/ArithmeticError/QualityRejected→503 DATA_UNAVAILABLE` :61-62、BoundaryError 分支 :65-72）；`_repository(request)` 读 `request.app.state.analysis_repository` 做 isinstance 守门（:77-81）；`_no_query` :84-86；`json_object(request, 封闭键集)` 来自 `owner/security.py:139-144`；主体依赖 `OwnerWrite`/`OwnerRead`（security.py:135-136，`owner.token`/`owner.csrf`）；成功体为 job 式扁平 JSON + `DecimalJSONResponse`（:166）；`install_analysis_job_routes(app)` 以 `app.router.routes.extend(router.routes)` 收尾（:135-174）。
- **owner 装配**：`owner/app.py:134-139` 构造 AnalysisJobRepository；`:172-179` 写 app.state（query 需新增 `app.state.query_repository`）；`:283-287` install 顺序（review→analysis_jobs→ai→news_review，最后 `add_middleware(OwnerGate,...)`）。query 安装插在 news_review 之后、add_middleware 之前。owner app 自带异常处理器（:216-230，ValueError→422 等），但自定义 APIRoute 的 handler 先捕获，不影响 query 归一。
- **gate 改动点（B3）精确定位**：`owner/security.py` 常量区 :27-29 增 `QUERY_PATH = "/api/query/"`；谓词区 :56-65 后增 `_query_path(path) = path.startswith(QUERY_PATH)`（覆盖 `/api/query/parse`、`/api/query/execute`）；**两个 except 分支都要接入**——:249 与 :257-261 的条件改为 `_analysis_path(...) or _news_review_path(...) or _query_path(...)`。归一复用 `_analysis_error`（:68-78：会话/CSRF→403 FORBIDDEN、422 invalid_request→400 INVALID_PARAM、413 保留 413 + INVALID_PARAM、未知→503）。会话/CSRF/body 131072/重复键/常量拒绝已在 `_check`（:266-355）对所有 `/api/` 路径生效，query 无需另写。
- **执行器投影先例**：`http/routes/roles.py:305-366` ladder 端点——`_role_payload(snapshot)`（:83-101，`snapshot.result("role")` 必须 status ready + 五键闭集 + trade_date 相符）、`_published_payload(snapshot)`（定义在 `http/routes/market.py:66-93`，内含 `_restore_decimals`）、`_names(market_payload)`、`_selected_topics`、`_adapted(topic, internal_role, rule_version)`（=adapt_role_evaluation 包装）；candidate 判定 `evidence.status=="candidate"`（:336）；boards 行校验（trade_date 当日/int/≥0/security_id∈member_ids，:341-347）；**跨题材 boards 不一致即 raise DataUnavailable**（:353-355）；角色排序键 `_ROLE_ORDER`（:40）。envelope 覆写先例在同文件 `_response`（:104-130：snapshot_envelope 后覆写 basis.price_type/benchmark、source、rule_version 取 payload、quality_flags 并集）与 `http/routes/stock.py:91-111`。
- 读模型事实：窗口物化循环仅 `("raw","adjusted")`（`readmodels/analysis.py:241`）；窗口结构五键 `{ranking,exclusions,curves,benchmark_return_ratio,benchmark_reasons}`（:327-333），**窗口级无 adjustments/available 子键**（N1：不可探测不存在的键）；窗口 stub 唯一 reason `insufficient_calendar_history`，形状 `{requested_offsets,available:false,reason}`（:348-359）；顶层 raw `available=true, price_basis="raw_close"`（:374-379），qfq/hfq 永久 false（:385-394）；`adjustment_version` 在 :372、eligible 列表在 :373；ranking 行 11 字段在 readmodels :283-297（HTTP 侧闭集校验 `http/routes/analysis.py:259-308`），`_published_eligible_ids` 在 :236-250；exclusions 行 `{security_id,reasons}`（:276-281）。search 名称兜底物化在 `readmodels/market.py:691-702`（`{security_id,code,name,exchange}`），挂载 :773。
- 契约：`coverage_view(expected,valid,excluded,missing)` 强制非负 int + 等式（`http/contracts.py:9-17`）；`snapshot_envelope` basis 默认硬编码 price_type="raw"、benchmark=None（:49-80，硬编码在 :73-78）。Decimal 编码 `DecimalJSONResponse` + simplejson（`http/serialization.py:89-101`），`return_ratio` 已在 FINANCIAL_DECIMAL_FIELDS（:15-44，共 26 键），无需补登。
- 异常裸类：`storage/repository.py:31-46` Conflict/Forbidden/NotFound/QualityRejected；`http/errors.py:17-30` StorageUnavailable/DataUnavailable。
- publications：`publication/tables.py:28-91`（status CHECK draft/published/revoked）；`PublicationRepository` 在 `publication/repository.py`（`_authorized_records(connection, rows, *, omit_forbidden=False, ai_domains=frozenset())` :863-870；`get_snapshot` :1274、`list_snapshots` :1323；PublishedSnapshot 工厂 :460/:857，模型 `publication/models.py:116`，`.result(domain)` 是取物化 payload 的入口）。owner_principals 表在 `owner/models.py:22-29`。

### 迁移与发布体系（规格清单之外的必改点，已取证）
- 模板：`migrations/versions/0012_lhb.py` 全文 51 行——revision/down_revision 模块常量、`table.create(connection)`、每表两名触发器（immutable_rows BEFORE UPDATE OR DELETE + immutable_truncate BEFORE TRUNCATE，均 EXECUTE FUNCTION reject_immutable_mutation()）、downgrade `raise RuntimeError(...)`。0013 仿此：单表 query_records + 同名两触发器。
- `migrations/release_manifest.json`：head `0012_lhb`、supported_from 12 项、revisions 20 条（filename/revision/parent/sha256/bytes）。**该文件无生产 writer**（全仓仅校验器与测试 helper）：手工改 head→`0013_query`、supported_from 追加、revisions 追加。**指纹算法 = `hashlib.sha256(LF 规范化字节).hexdigest()`、bytes = LF 字节长度**（非 git blob hash；见 `tests/test_release_migrations.py:122-131` refresh helper 与 :157-169 提交 blob 比对，且提交 blob 必须不含 CRLF）。
- 必改常量/断言（新增 0013 的标准连带面）：
  1. `storage/database.py:16` `SCHEMA_REVISION = "0012_lhb"` → `"0013_query"`（ready() 比对 version_num 与打包 schema 契约，:48-59）。
  2. `scripts/verify_release_migrations.py:49` `RELEASE_HEAD` 与 :51-64 `SUPPORTED_FROM`（被 :1590/:1698/:1880/:2485/:3206 等升级断言使用）。
  3. `tests/test_release_migrations.py:26-39` `SUPPORTED_FROM`、:144-150 heads/count（→`0013_query`/21）、:147-149 result。
  4. `src/dragon_radar/storage/schema_contract.json`：重新生成（元数据块在 :11533-11535，非文件尾——现文件 21063 行、尾部为 triggers 数组；CONTRACT_SCHEMA_VERSION=4 不变）。
- **schema_contract 生成命令（需实库）**：`uv run python scripts/verify_release_migrations.py --write-schema-contract src/dragon_radar/storage/schema_contract.json --database-url $env:DRAGON_TEST_DATABASE_URL`（CLI 分支 verify_release_migrations.py:3383-3409；先 verify_manifest，再 `require_clean_migration_tree()` :558-571 = `git diff --quiet HEAD -- src/dragon_radar/migrations`，再对实库 upgrade head 后 capture）。→ **执行顺序约束：迁移文件与 release_manifest.json 必须先提交（migrations 目录相对 HEAD 干净）才能重生成契约**；库名必须为 `dragon_test`（conftest.py:12-30 同款要求）。
- **不可动**：`scripts/audit_reset_release.py:27-38` 的 SUPPORTED_FROM 冻结在 0010_ai_tasks（Task21 历史审计证据脚本）；`scripts/verify_reset.py`（固定白名单，full_backend = 全量 `uv run pytest -q -rs`，:216，自动收纳新测试）；CI 三构建强制化归 Task 29。
- pytest 基建：`tests/conftest.py:12-51` database_url（无 `DRAGON_TEST_DATABASE_URL` 即 skip、强制库名 dragon_test、每测独立 schema）；owner HTTP 集成先例 `tests/test_analysis_jobs.py:1268-1369`（httpx ASGITransport + create_owner_app 注入 + Origin/CSRF/双 Cookie + 403/扁平键集/重放/409 断言）；LHB PG 体例 `tests/test_reset_lhb.py:1537-1559`（分段 upgrade）、:1715-1834（命名约束/索引/触发器穷举断言）。CI verify job 全量 pytest 在 `.github/workflows/ci.yml:42`（PG service :13-26），无 DB 时本地 skip 不阻塞纯函数切片。

### 前端
- `web/src/features/ai/client.ts` 单文件内聚类型+fail-closed validator+请求：路径白名单正则（:102）、请求前校验（:321-324）、`csrfToken()` 读 `__Host-dragon-private-csrf`（:303-309）、`aiRequest`（:311-377：credentials same-origin、cache no-store、POST 才设 Content-Type/X-CSRF-Token、15s 超时 :334-337、外部 AbortSignal 桥接 :328-333、非 2xx 读 code、401 dispatch `private-session-lock`、JSON 失败 502 malformed_response）、`createAICommand`（:380-387，crypto.randomUUID() 实测可直接用真实 Node crypto）。
- `web/src/pages/Analysis.tsx` 全文 78 行：reset 分支 :22-27（LazyLoadBoundary + Suspense，props 整体透传）；**模块级新增本地 `const resetEnabled = import.meta.env.VITE_RESET_WORKBENCH==='1'`**（不得从 App 导入，避免环）与折叠 lazy；QueryPanel 作为 :24 `<ResetAnalysis>` 的兄弟节点挂入同一 LazyLoadBoundary（插 :23 后或 :24 后）。props 可获：state/patch/refreshToken/open/compare/snapshot?（AnalysisProps :15-20）；行点击开抽屉直接调 `open(security_id, undefined, trigger)`（先例 ResetAnalysis.tsx:142-143，App.tsx setDrawer 不改 view）。
- 折叠黄金先例：`web/src/App.tsx:22-24` resetEnabled 常量、:78-81 CommandPalette 折叠 lazy（含注释）。Analysis 被 App.tsx:18 静态导入，模块图恒在默认构建内——只有 env 常量折叠能消除动态 import 节点。
- bundle 门禁：`web/scripts/check-default-bundle.mjs` 全文 55 行；forbidden 列表 :32-39（现值 6 个：我的复盘/人工验证/.review-workspace/dragon-radar-local-v3/`/api/owner/`/`/api/ai/`）→ 追加 `'/api/query/'`（client 路径串前缀，命中 parse/execute 两路径）；扫描静态闭包 ∪ `dist/assets/**/*.js`（:29-44）；预算默认 315500 / sharing 320000（:46-49）；Task25 后 entry 实测 307931B，本任务默认构建增量应为 0。
- 三构建命令（`npm run build` = `tsc --noEmit && vite build`）：默认（无 env，workbench.Dockerfile:6 / ci.yml:220）；reset（`$env:VITE_RESET_WORKBENCH='1'`，出 query 懒 chunk，对齐 playwright.reset.config.ts:21）；私人镜像（private.Dockerfile:6-9：VITE_PRIVATE_REVIEW=1 + VITE_RESET_WORKBENCH=1 + VITE_PRIVATE_AI=1；CI 收窄版 ci.yml:222 仅 VITE_PRIVATE_REVIEW=1，query 同样被折叠）。公开 sharing（public.Dockerfile:6-8：RESET=1+PUBLIC_SHARING=1，build 后跑 check:bundle）作为预算旁证。
- 快照三元来源：`snapshot.snapshot_id`（请求实际用 `state.snapshot`）、`snapshot.trade_date`、`snapshot.result.revision`（类型 web/src/types.ts:2-14）；SnapshotHeader 切换只 `patch({ snapshot, security:'', compare:[], page:1 })`（SnapshotHeader.tsx:57-61）——「回到原快照」按此键集并带回原 date。
- E2E mock：`web/tests/fixtures/review.ts` reviewServer()（:17-25）+ mockReview `page.route('**/api/owner/v1/**',...)`（:27-28，含 /pair、/session、csrfDenied 开关）；并存接线先例 `web/tests/e2e/reset-ai.spec.ts:63-85`（mockMarket + reviewServer + mockReview 后再叠加精确 page.route，Playwright 后注册者优先）。mockMarket 的 glob（market/snapshots/stock/v1）均不覆盖 /api/query；**E2E mock 不设 CSRF cookie、不校验 X-CSRF-Token**（仅 review.ts:59 开关模拟），query 幂等键在 JSON body 内（EX12/13 契约），不是 header。
- 测试运行器：无 vitest script，单测用 `npm.cmd test -- --run tests/unit/reset-query.test.ts`（透传 vitest，test-runner.mjs:7）；vitest 全局 node 环境、include `tests/unit/**/*.test.ts`（vitest.config.ts:6-10）。Playwright reset：固定 5174、workers 2、testMatch reset-*.spec.ts、env RESET=1/PRIVATE=0（playwright.reset.config.ts:21/:28-33/:63）；注意 :64 `testIgnore` 固定排除 reset-ai 与 reset-ai-public 两个 spec——新 `reset-query.spec.ts` 命中 testMatch 且不在 ignore，核回归计数时勿把这两个 ignore 文件计入；`--repeat-each` 仅手工执行（CI 无）。
- features/ 下无 query 目录；IDB 无名称冲突（QueryPanel 零本地持久化）。

## 文件清单

### 新建
- `src/dragon_radar/query/__init__.py`
- `src/dragon_radar/query/models.py`：枚举（Field/Op/UnsupportedCode/ParserKind）、QueryAst/Predicate/SortSpec 严格模型、wire 模型（typed_ast 含 ast_hash；extra=forbid）、值域校验、canonical JSON + ast_hash（Decimal 经 model_dump(mode="json")）、`query_records` Core Table（规格 §3.5 全部列/约束）、explanation 确定性生成器可放此或 parser.py。
- `src/dragon_radar/query/parser.py`：`parse_query(text, *, role_names) -> ParseOutcome`（zh-whitelist-v1；NFKC/全角容忍；6 位小数 ROUND_HALF_UP 量化；unsupported 封闭枚举；默认排序规则；explanation 生成）。
- `src/dragon_radar/query/executor.py`：`execute_query(ast, snapshot, *, market_payload, role_payload) -> QueryResult`（纯内存；按需取源；三分桶；投影复用 roles.py:305-366 同款规则；D1 两层不可用）。
- `src/dragon_radar/query/repository.py`：`QueryRepository(database, owner_repository, publication_repository, *, clock=None)`；`parse(token, csrf, *, text, snapshot_id, key)` 与 `confirm_and_execute(token, csrf, *, query_id, confirmed_ast_hash, snapshot_id, key)`（或等价两方法）；咨询锁 `query:{owner}:{key}`；主体隔离/幂等/复判；只读 payload 装载（PublishedSnapshot.result）。
- `src/dragon_radar/query/model_parser.py`：`ModelQueryParser` Protocol、同构 AST 再校验、降级（切片 3）。
- `src/dragon_radar/http/routes/query.py`：`_QueryRoute`、`install_query_routes(app)`，POST `/api/query/parse`、POST `/api/query/execute`（仅 owner app）。
- `src/dragon_radar/migrations/versions/0013_query.py`。
- `tests/test_reset_query.py`：QRY-BE 1–10（纯函数段无 DB 可跑；迁移/仓储/路由 PG 段无 DRAGON_TEST_DATABASE_URL 时 skip）。
- `web/src/features/query/types.ts`、`client.ts`、`preview.ts`、`QueryPanel.tsx`。
- `web/tests/unit/reset-query.test.ts`、`web/tests/e2e/reset-query.spec.ts`。
- `docs/acceptance/reset-query.md`（切片 4，单独 docs 提交）。

### 修改
- `docs/superpowers/plans/2026-09-20-prd-reset-refactor.md`（**已改妥待提交**，切片 0）。
- `src/dragon_radar/owner/app.py`（构造 QueryRepository、app.state、install 调用）。
- `src/dragon_radar/owner/security.py`（QUERY_PATH 常量 + `_query_path` + 两 except 分支）。
- `src/dragon_radar/storage/database.py:16`（SCHEMA_REVISION）。
- `src/dragon_radar/storage/schema_contract.json`（重生成）。
- `src/dragon_radar/migrations/release_manifest.json`（手工回填 head/supported_from/revisions）。
- `scripts/verify_release_migrations.py`（RELEASE_HEAD + SUPPORTED_FROM）。
- `tests/test_release_migrations.py`（SUPPORTED_FROM + head/count 断言）。
- `web/scripts/check-default-bundle.mjs`（marker）。
- `web/src/pages/Analysis.tsx`（模块级折叠挂载）。

### 明确不改
`http/app.py`、`http/public_app.py`、owner `_RESET_READ_PATHS`、`scripts/verify_reset.py`、`.github/workflows/ci.yml`、`scripts/audit_reset_release.py`、ResetAnalysis 的 lazy 写法、既有 5 个脏文件（`docs/acceptance/reset-news.md` 待用户放行；`web/src/format.ts`、`web/src/stock-validation.ts`、`web/tests/e2e/workbench.spec.ts`、`web/tests/fixtures/market.ts` CRLF/stat 假脏）——全程不暂存不触碰；`.trae/` 不入库。

## Implementation Steps（TDD 切片，依赖序）

### 切片 0：冻结计划回流提交（切片 2 动工前，独立提交）
1. 仅暂存 `docs/superpowers/plans/2026-09-20-prd-reset-refactor.md`（diff 必须恰为现 +5/−3 三 hunk：Task26 文件清单+注记、Task27 0014、迁移链），`git diff --cached --stat` 核对。
2. 提交：`docs: reserve migration 0013 for whitelist query (Task 26)`（-m 即可；不用 -A/.）。提交后 `git status` 确认 5 个脏文件原样保留。
3. `git grep 0013_research`（仅 tracked 文件；`.trae/` 未跟踪不入库，其内历史叙述不计）必须无输出。

### 切片 1：纯函数层（models.py + parser.py，无 HTTP/无 DB）
1. RED：先写 `tests/test_reset_query.py` 的纯函数段（文件先建，PG 段占位 skip 或后补），至少覆盖 QRY-BE 1/2/3/4 中不依赖 DB 的部分：
   - 四个锚点例句（规格 §3.3）的 AST 精确断言；合取词等价；全角标点/空白 NFKC；枚举英文大小写不敏感。
   - 百分数/小数/全角符号等价：5%、0.05、0.050 → AST 全等；**6 位小数 ROUND_HALF_UP 量化后 ast_hash 相同**；超 6 位精度 → unsupported `unsupported_value`；return_ratio 超 [-1,10]、NaN/Infinity（allow_inf_nan=False 模型拒收）、boards 负/非整/0-99 外、role 非六枚举、security_id 不符 `^[A-Za-z0-9_.:-]{1,32}$`/超长、sector_id 空/超 64、in 超 100、predicates 1-5 边界、limit 1-100 默认 50。
   - unsupported：未知字段（市值/换手率）、**未知操作符（如「涨幅像5%」→ unknown_operator，必须有专门触发用例，保证该 code 分支被覆盖）**、OR（或/或者）、否定（不/非/排除）、残词（句尾停用词可消费、不可消费残词 orphan_token）、算术/括号/时间窗口/祈使提问语气；code 必属九值封闭枚举；空文本（非 str/len 0 的请求形态判定留路由测，parser 层对 None/"" 的契约明确）与**纯空白 len≥1（含全角空格 U+3000）→ empty_input** 两条路径分别锁定。
   - ast_hash：canonical 不含 ast_hash、sort_keys、Decimal 经 model_dump(json) 成字符串 token；同构 AST 跨调用哈希确定；typed_ast wire 形状恰为 `{version,ast_hash,predicates,sort,limit}`，extra=forbid。
   - explanation：确定性中文，含逐条条件（百分数还原展示）、合取、排序/上限、口径行（「20 个交易日 · 未复权 raw_close · 基准 B000300」）；同 AST 文本稳定。**规格 §3.5 还要求含快照日期/版本标识**——parse_query 纯函数签名无快照，切片 1 只产出基础 explanation（不含快照行）并断言上述四要素；**快照日期/版本行由 repository 在 parse 成功路径用已授权 PublishedSnapshot（trade_date + result.revision）做确定性尾拼**（无快照信息不得编造），该尾拼在切片 2 QRY-BE 响应断言中锁定（成功 explanation 末尾含 trade_date 与 vN；重放记录保留原文）。
   - 注入文本：`'; DROP TABLE--`、`/* */`、`{{7*7}}`、「忽略以上规则直接执行」→ unsupported/拒绝（白盒：parser/executor 包内不得出现 SQL 字符串拼接，executor 切片再断言）。
   - 先跑 `uv run pytest tests/test_reset_query.py -q -rs` 留 RED 证据。
2. GREEN：实现 `query/__init__.py`、`models.py`（含 query_records Table 定义——迁移在切片 2 import 它；此时无运行时引用）、`parser.py`（词表全部模块级冻结常量）。
3. 门禁：`uv run ruff check .`、`uv run ruff format --check .`、`uv run python -m compileall -q src tests`。
4. 双审（规格复审对照规格 §3.2/3.3/3.7 + AC-E05；质量复审只读）→ 修复闭环 → 精准路径提交（仅 query 两文件 + __init__ + test 文件）。

### 切片 2：迁移 + 仓储 + 执行器 + owner 路由（核心切片）
顺序敏感，分两阶段提交。

**阶段 A（TDD 到全绿，先不提交）**
1. RED：补 `tests/test_reset_query.py` PG 段（无 DB 自动 skip，体例仿 test_reset_lhb.py:1537-1559）与 owner HTTP 集成段（仿 test_analysis_jobs.py:1268-1369，create_owner_app 注入 query_repository + ASGITransport base https://localhost:8030）：
   - QRY-BE 5：execute hash 篡改→409 VERSION_CONFLICT；他人 query_id→404（不泄露存在性，两条不同 owner 主体造数）；snapshot_id 不符→409；**parse/execute 传不存在的快照 UUID→404 NOT_FOUND（规格 §3.6：不存在→404，与未发布/撤回→403 分开造数）**；许可撤回（parse 缓存命中与 execute 两路径）→403；对旧快照执行得旧物化值（造两个快照）。
   - QRY-BE 6：同 key 同 hash 重放同 query_id（且复判出版物）；同 key 异 hash→409；execute 只读幂等重复一致。
   - QRY-BE 7（D1+D4）：window 缺失 / available=false（reason 透传）/ 顶层 raw available 翻 false → 200 rows:[] + `data.unavailable=[{code:"return_ratio_unavailable",reason}]` + coverage 四 int 满足等式（missing 计入无法判定行）；同快照 boards/role/sector-only 查询成行且 name 取 search 物化；window available=true 缺 adjustments.raw、11 字段闭集破坏、跨题材 boards 不一致 → 503 DATA_UNAVAILABLE。**D4 专门用例：同一证券属多个题材、sector/role 谓词仅在其中一个题材成立 → 成行（任一满足），行内 sector_ids/roles 为全量集合；再造同证券跨题材 boards 不一致组合 → 即使谓词本可命中仍 503（结构损坏优先）；sector/role 两题材都不满足 → excluded 而非 missing（字段齐全）**。
   - QRY-BE 9：0013 升级后 query_records 表/列/CHECK/Unique/FK 存在；UPDATE/DELETE/TRUNCATE 三触发器全阻断（体例 test_reset_lhb.py:1715-1834，断言触发器名与函数）；downgrade 抛 RuntimeError；manifest/contract 一致性（head、supported_from、revisions 指纹）。
   - QRY-BE 10（gate 归一，owner app 黑盒）：/api/query/parse 无会话→403 FORBIDDEN（体含 code，非 401 session_unauthorized）；错误 Content-Type→400 INVALID_PARAM（非 422）；CSRF 失败→403 FORBIDDEN；body>131072→413 + INVALID_PARAM；query string→400；未知 JSON 键/重复键→400。
   - 响应契约：parse 成功体恰四键 `{query_id,typed_ast,explanation,unsupported:[]}`；unsupported 200 体 `{query_id:null,typed_ast:null,explanation:null,unsupported:[...]}` 且不落库（查表计数）；execute 信封 data 恰 `{fields,rows,total,unavailable}`、coverage 恰四 int、rows≤100、total=切片前 valid 数；Decimal raw token 9007199254740993.01 精度断言（wire 为 JSON number）；fields 含六列且 return_ratio 列带口径说明；basis 后处理断言 price_type=raw_close/benchmark=B000300/unit=ratio 扩展 adjustment_version/rounding；rule_version 取 analysis payload。
   - 请求形态：空 text（len 0/非字符串/>500）、snapshot_id 非 UUID、缺键/多键（execute 出现 date 或第 5 键）、idempotency_key 非 1..200 安全串 → 400。
2. GREEN 实现：
   - `0013_query.py`（from dragon_radar.query.models import query_records；触发器 SQL 仿 0012；downgrade raise）。
   - `repository.py`：锁/复判/幂等严格对齐 analysis_jobs repository（行号见 Research）；parse 成功才 insert（unsupported 不写）；ast JSONB 只存 body 四键；execute 内 hmac.compare_digest；记录非本人 → NotFound；execute 前再次 `_authorized_records`；execute 幂等键仅 `_identifier` 格式校验。
   - `executor.py`：宇宙 = analysis 物化 eligible（`_published_eligible_ids` 同款闭集自洽校验）；return_ratio/topics 取 20/raw ranking（11 字段闭集同款校验，**不抄算法只复用投影事实**；ranking security_id 唯一性校验）；boards/roles/sector 投影严格镜像 roles.py:305-366（candidate、trade_date 当日、跨题材不一致 DataUnavailable、角色按 _ROLE_ORDER 排序）；名称优先 ranking.name，窗口声明性不可用时取 market.search.securities，再不可得 null；按需取源（不引用 return_ratio 不装载窗口）；Python Decimal 比较；三分桶 + coverage_view；排序白名单单键 + security_id 字典序兜底 + limit 切片；D1 声明性路径携 unavailable 明细。
   - `http/routes/query.py`：_QueryRoute（复制 analysis_jobs 异常表）、两 POST、`_no_query`、json_object 封闭键集（parse：{text,snapshot_id,idempotency_key}；execute：{query_id,confirmed_ast_hash,snapshot_id,idempotency_key}）、parse 扁平 JSON 直接返回；execute 经 snapshot_envelope 后显式覆写 basis（仿 roles.py _response / stock.py:91-111）；DecimalJSONResponse。
   - owner 装配：app.py 构造 QueryRepository（:134-139 区块）、app.state.query_repository（:172-179 区块）、install（:283-287 区块，middleware 之前）。
   - security.py：QUERY_PATH + `_query_path` + :249/:257-261 两分支接入。
   - 版本连带：database.py:16、verify_release_migrations.py RELEASE_HEAD/SUPPORTED_FROM、test_release_migrations.py 常量与断言；手工回填 release_manifest.json（head/supported_from/revisions：sha256 对 LF 字节计算、bytes 为 LF 长度；文件以 LF 落盘）。
3. 纯 Python 门禁：`uv run pytest tests/test_reset_query.py tests/test_release_migrations.py -q -rs`（无 DB 时 PG 段 skip，但 test_release_migrations 多数用例不需实库——图/指纹/解析类必须全绿；若指纹因未提交失败属预期，进入阶段 B）、ruff/format/compileall、全量 `uv run pytest -q -rs`（PG 段 skip 时其余必须全绿）。
4. 双审（规格复审：规格 §3.4-3.6 + DEC06/10/12 逐条；质量复审：SQL 注入面、TOCTOU、Decimal、触发器、信封键闭集）→ 修复闭环。

**阶段 B（提交顺序 + 实库重生成）**
5. 环境检查：`$env:DRAGON_TEST_DATABASE_URL` 指向库名 dragon_test 的 PG17（用户本地 Docker/WSL）。不可用则暂停告知用户，不得伪造契约。
6. **提交 B（迁移指纹面）**：仅暂存 `0013_query.py`、`release_manifest.json`、`storage/database.py`、`scripts/verify_release_migrations.py`、`tests/test_release_migrations.py`；提交（建议 `feat: add immutable query records migration 0013 (Task 26)`）。提交后立即：
   - `git cat-file blob HEAD:src/dragon_radar/migrations/versions/0013_query.py` 校验无 CRLF；
   - `uv run pytest tests/test_release_migrations.py -q`（此时 HEAD blob 指纹应一致；supported_from/head/count 全绿）。
7. **重生成 schema 契约**：`uv run python scripts/verify_release_migrations.py --write-schema-contract src/dragon_radar/storage/schema_contract.json --database-url $env:DRAGON_TEST_DATABASE_URL`；检查 git diff 仅新增 query_records 相关对象与尾部元数据（无意外漂移）；`uv run python -c "from dragon_radar.storage.database import Database; ..."` 或直接跑 ready 相关既测确认 SCHEMA_REVISION 一致。
8. **提交 C（功能面）**：暂存 query 包（repository/executor/routes/__init__；models/parser 已在切片 1）、owner/app.py、owner/security.py、storage/schema_contract.json、tests/test_reset_query.py；带 DB 跑 `uv run pytest tests/test_reset_query.py -q -rs` 与全量 pytest（PG 段全绿、不 skip）后提交。
9. 复核工作树：5 个保护脏文件仍未暂存；无意外文件。

### 切片 3：可选模型接缝（model_parser.py）
1. RED：QRY-BE 8——fake adapter 提合规 AST → 采纳、parser_kind=model、model_version 落库、explanation 仍由确定性生成器产出；提越权字段/超范围值/错误形状 → 不采纳 + unsupported `model_ast_rejected`（不写 model kind）；provider 抛错/超时 → 降级确定性结果、`model_unavailable` 语义不向客户端泄露堆栈；确定性解析本身成功时不调用模型；模型永无 execute 通道（EX13 只认真实落库 AST）；出站仅原文 + snapshot_meta（AC-X02）。
2. GREEN：Protocol + 同构 pydantic 再校验（复用 models，不允许第二套 schema）+ owner app 仅在显式注入时启用（默认恒 None，`provider_from_environment()` 现状不变）。**模型调用前置双门：adapter 显式可用 且 该出版物具备 AI 出站许可**（复用 `_authorized_records(..., ai_domains=...)` 既有机制，见 publication/repository.py:863-870；许可不具备时确定性 unsupported 绝不调模型），并加一条「无 AI 许可即便注入 fake adapter 也不调用」断言。
3. 门禁 + 双审 + 精准提交。

### 切片 4：前端 + E2E + 三构建 + 验收文档
1. RED 单测 `web/tests/unit/reset-query.test.ts`（node 环境，先跑 `npm.cmd test -- --run tests/unit/reset-query.test.ts` 留证据）：
   - types validator：parse/execute 成功体精确键集；typed_ast 键集 + ast_hash 64hex；data 恰 4 键；coverage 仅四 int 且等式；rows≤100；枚举封闭；畸形包/多余键/非对象/coverage 缺槽 → 抛 malformed；401 触发 private-session-lock。
   - client：路径白名单仅 /api/query/parse|execute（反斜杠/问号/他路径拒）；credentials/cache/CSRF 头（POST）/15s 超时/外部 abort/幂等键注入（crypto.randomUUID 真实可用）。
   - preview 纯函数：AST→条件数组/排序/上限/口径视图模型；IME 阻断（isComposing||keyCode 229，复用 Task25 思路）；确认态指纹（改文案即作废）。
2. GREEN：types.ts/client.ts/preview.ts/QueryPanel.tsx；Analysis.tsx 模块级折叠挂载；marker 追加。QueryPanel 行为契约（规格 §4）：textarea maxlength=500；**未选中快照时面板禁用（输入/按钮 disabled）并显示「请先选择已发布快照」提示，且不发任何请求**（规格 §4 挂载条款，单测与 E2E 均覆盖）；解析后预览卡（条件/口径/排序/上限）+ unsupported role=alert 列表；执行按钮仅在「最近一次 parse 成功且文本未改」可用；取消不发 execute；结果表六列 ≤100 行、表头钉快照日期+版本；行内代码按钮调 `open(id, undefined, trigger)`；结果绑定快照：state.snapshot 变化显示旧快照条 + 「回到原快照」（patch 键集仿 SnapshotHeader:57-61 并带原 date）；面板只用本地 state，不写全局 date/snapshot/q/sort/compare；除两 POST 外零新请求；aria search/form、焦点管理、Esc、375/1440、深色 token、reduced-motion 沿用既有规范。
3. E2E `web/tests/e2e/reset-query.spec.ts`（reset 配置）：文件内建 queryServer()+`page.route('**/api/query/**',...)`（后注册优先；wire 严格按 EX12-13），并存 mockMarket + reviewServer/mockReview（会话流）；用例按规格 §5.2 六组（执行计数断言取消=0、确认态作废、结果绑定快照+回到原快照 URL 归位、unsupported/409/403/413/空结果提示、全键盘+IME 组合期 Enter 不解析、375 无横溢+深色对比复用 token），**另加空快照态：初始无选中快照时面板禁用/提示可见/请求计数为 0**。新 spec 先 `npx.cmd playwright test --config playwright.reset.config.ts tests/e2e/reset-query.spec.ts --repeat-each 3`。
4. 门禁全量（均在 `web/` 目录）：`npm.cmd test`（CI 同款：vitest 全量 + scripts node:test）+ 规格 §5.2 追加门禁 `npx.cmd vitest run --no-fileParallelism`（全量单测串行复跑一次）、`npx.cmd tsc --noEmit`、四构建 + chunk 枚举（**默认构建必须最先在干净 env 会话跑**，每次换构建前用 PS5 原语清 env：`Remove-Item Env:VITE_RESET_WORKBENCH,Env:VITE_PRIVATE_REVIEW,Env:VITE_PRIVATE_AI,Env:VITE_PUBLIC_SHARING -ErrorAction SilentlyContinue`）：
   - 默认（四 env 均不存在）`npm.cmd run build` → `npm.cmd run check:bundle`（含新 marker）→ `Get-ChildItem dist/assets` 枚举：0 个 query chunk、无 query 文案、entry ≤315500（预期 ~307931 近零增量）；
   - `$env:VITE_RESET_WORKBENCH='1'; npm.cmd run build` → 存在 query 懒 chunk（ResetAnalysis chunk 照旧存在，与本任务无关）；清 env；
   - `$env:VITE_PRIVATE_REVIEW='1'; $env:VITE_RESET_WORKBENCH='1'; $env:VITE_PRIVATE_AI='1'; npm.cmd run build`（私人镜像同款 env）→ query chunk 存在且默认私有 marker 不泄漏；清 env；
   - 旁证：`$env:VITE_PUBLIC_SHARING='1'; $env:VITE_RESET_WORKBENCH='1'; npm.cmd run build; npm.cmd run check:bundle`（entry ≤320000）；清 env；
   - `npm.cmd run test:e2e:production-isolation`；全量 reset Playwright（`npm.cmd run test:e2e:reset`，无回归；注意 reset 配置 ignore 两个 ai spec）。
5. 双审（规格复审：规格 §4/§5.2/AC-E05/X02/D5；质量复审：DCE 证据、fail-closed、a11y、mock 隔离）→ 修复闭环。
6. 代码与文档分两次提交：代码一次；`docs/acceptance/reset-query.md` 一次（AC-E05/AC-X02/EX12-13/DEC 锚点映射、D1 两层不可用证据、三构建 marker+chunk 枚举+entry 字节数、QRY-BE 与 E2E 证据、提交清单、已知边界/Backlog）。

## Validation（汇总）
- 后端：`uv run ruff check .`；`uv run ruff format --check .`；`uv run python -m compileall -q src tests`；`uv run pytest tests/test_reset_query.py -q -rs`（有 DB 全绿）；`uv run pytest tests/test_release_migrations.py -q`；全量 `uv run pytest -q -rs`；迁移 verifier CLI（write-schema-contract）退出 0。
- 前端：`npm.cmd test`（CI 同款）+ `npx.cmd vitest run --no-fileParallelism`（规格 §5.2 串行门禁）；`npx.cmd tsc --noEmit`；四构建（默认/reset/私人/sharing 旁证）+ check:bundle + chunk 枚举（干净 env 会话先跑默认）；新 spec `--repeat-each 3`；全量 reset Playwright；production-isolation。
- Git：精准路径暂存（绝不 -A/.）；长提交信息写 UTF-8 无 BOM 临时文件 `git commit -F` 后删除；不处理 5 个既有脏文件；切片 0/B/C 与前端两次提交各自独立。
- 分支边界：不 merge/tag/push、不 bump 版本、不发镜像、不部署。

## Risks
1. **迁移发布面漏改**：SCHEMA_REVISION/RELEASE_HEAD/SUPPORTED_FROM/测试断言/manifest 五处连带，漏一即 verify 或 ready 失败。切片 2 清单已穷举；提交 B 后以 test_release_migrations 全绿为闸。
2. **schema_contract 必须实库且 migrations 树干净**：无 dragon_test 库则切片 2 无法收尾——前置检查环境；禁止手改 JSON 伪造。
3. **指纹与 CRLF**：manifest sha/bytes 按 LF 字节；Windows 工作区 CRLF 假象多——以 `git cat-file blob` 与 verifier 为准，不用工作区文件直算。
4. **gate 归一漏第二分支**：security.py 两个 except（:249/:257）只改一个会有一类异常穿透 503/原码；QRY-BE 10 黑盒逐条锁。
5. **DCE 照抄错误模板**：Analysis.tsx 内 ResetAnalysis 是无条件 lazy（默认构建出 chunk）；必须 env 常量折叠。四重隔离证据（marker/默认 0 chunk/reset 出 chunk/私人可用）缺一不可。
6. **中文词法歧义**：词表冻结常量化、单测锁死；宁可 unsupported 附原因，不猜测执行；6 位量化保护哈希稳定与用户阈值不被静默改变。
7. **读模型结构漂移/半坏投影**：复用 ladder/rank 同款 fail-closed 闭集；声明性不可用走 200 coverage + data.unavailable，结构性损坏走 503，二者不得互窜（N1：窗口级无 raw available 键）。
8. **TOCTOU/主体越权**：parse 缓存命中与 execute 均事务内复判出版物+许可；他人记录 404 不泄露；hash 用 hmac.compare_digest。
9. **entry 预算**：面板逻辑全部在懒 chunk；前端仅最小预览模型；默认构建 entry 增量须为 0 量级，超标不外移预算（调预算需另行批准）。
10. **E2E mock glob 吞路径**：/api/query 独立注册且置于 mockMarket/mockReview 之后；不复用 `**/api/owner/v1/**`；不引 CSRF cookie 假设。

## 流程约束（每切片不变）
实现子代理（显式传入 worktree 绝对路径与「带 file:行号取证、禁搜主仓」要求）→ 独立规格复审（只读）→ 独立质量复审（只读）→ TDD/复审修复闭环 → 精准暂存提交。保留 RED 证据。每切片结束更新本计划状态；实质范围变更（新文件/新决策/契约偏离）停下重新提请批准。
