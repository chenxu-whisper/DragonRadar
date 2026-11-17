# Task 28 实施计划：0-100 情绪研究分与阶段标签

- 关联规格：`.trae/documents/task28-sentiment-research_spec.md`（AC1-14）
- 工作区：worktree `.worktrees/task21r-task22`，分支 `feature/task21r-task22`
- 纪律：TDD（每切片先 RED）；Decimal 全程；闭集校验；精准 `git add -- <path>`；`.trae/` 不入库；不 merge/tag/push/发版/部署；不新增迁移与第三方依赖。
- 测试环境（每条 Shell 同设）：
  `$env:DRAGON_TEST_DATABASE_URL="postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"; $env:PYTHONPYCACHEPREFIX="$PWD\.temp\pycache"`
- 状态字段取值：pending / in_progress / completed / blocked / cancelled。

## 切片总览（依赖序）

A 纯函数 → B 发布物化 → C EX16 路由 → D 前端面板与构建隔离 → E 前端单测/E2E → F 全量门禁与验收记录。后端可先纯 TDD；D/E 依赖 C 的线契约（可先按冻结契约并行）。

---

## Task A：研究分纯函数与阶段分桶（纯 Python，无 DB）

- Status: pending
- Priority: high
- Depends On: None
- Description:
  - 新建 `src/dragon_radar/readmodels/sentiment_research.py`：
    - 常量 `METHOD_VERSION="sentiment-research-v1"`、`RESEARCH_ONLY=True`、分项 reason 闭集（`no_valid_daily_returns`/`no_limit_events`/`no_tradable_members_from_yesterday_ge2_set`）、phase 枚举 `ice/ebb/divergent/repair/climax`、等权 `WEIGHT={numerator:1,denominator:3}`。
    - `phase_for(score: Decimal) -> str`：严格按 spec §2 边界（<20 / [20,40) / [40,60) / [60,80) / [80,100]）。
    - `score_components(b, l, p)`：输入三个「未舍入」Decimal 比率或 None；任一 None → 返回 score=None/phase=None/原因聚合；否则 `score=ROUND_HALF_UP(100*(b+l+p)/3, 2)` 并分桶。
    - `build_component(value, numerator, denominator, reason, coverage=None)`：冻结分项 `{value,numerator,denominator,weight,reason[,coverage]}` 闭集。
    - `build_research_point(trade_date, components, reasons)` / `materialize_sentiment_research(points)`：冻结单点与整块 `{method_version,research_only,series}` 的闭集形状。
- Acceptance Criteria Addressed: AC1, AC2, AC3
- Test Requirements:
  - rule TR-A.1：b=0.6/l=1/p=0.5 → score=Decimal("70.00")、phase=repair；断言用未舍入比率（4 位舍入不得改变 2 位结果，补一个非整比率样例佐证）。证据：`tests/test_reset_sentiment_research.py`。
  - rule TR-A.2：参数化 0/19.99/20/39.99/40/59.99/60/79.99/80/100 → 期望桶；越界（<0/>100）拒绝。
  - rule TR-A.3：每个分项独立 None 用例 → 该分项 value=null+固定 reason；点 score/phase=null、reasons 含对应项；权重不重分配、不补零；三分项齐备且分母>0 才得分。
  - rule TR-A.4：纯 Decimal；同输入确定性一致；无 float/NaN（白盒断言无 float 参与金融运算）。
- Notes: 新测试文件先建并跑 RED，再实现。

## Task B：发布时物化研究块（市场包追加键，不升容器 schema）

- Status: pending
- Priority: high
- Depends On: A
- Description:
  - 在 `readmodels/market.py` 中把「当日情绪事实」抽成单一可复用助手（输出 sealed/limit_down/prior_ge2/tradable/promote 的 num/denom/excluded/missing，以及当日 breadth 的 up/valid_total+coverage），由现有 `_sentiment.daily()` 与新研究序列**共用同一事实源**（行为保持重构；现有 market 测试必须零改动通过）。
  - 在 `materialize_market` 内为序列交易日逐点构建研究点（b 用当日 `_daily_returns`；l/p 用共用事实），产出并追加顶层键 `sentiment_research`（由 sentiment_research 模块装配）。
  - 不修改市场容器 `schema/schema_version`；不新增迁移；`schema_contract.json`、`release_manifest.json` 必须无 diff。
  - 复核 `publication/service.py`：市场 payload 整体持久化，追加键随现有链路落库；**除非**确有 readiness/coverage 装配需要，否则不改（偏离上游计划文件处在此记录理由：追加键无需独立域物化）。
- Acceptance Criteria Addressed: AC4, AC7, AC8, AC14
- Test Requirements:
  - rule TR-B.1：`materialize_market(batch,None)["sentiment_research"]` 闭集正确（method_version/research_only/series，每点 FR5 形状），且头条交易日点与手工计数一致。证据：新测试文件。
  - rule TR-B.2：容器 `schema/schema_version` 与 5 个 `_MATERIALIZED_VIEW_FIELDS` 保持不变；payload 仅纯增键（断言既有键集合仍为子集）。
  - rule TR-B.3：无迁移/契约 diff：`git status --porcelain` 不含 schema_contract.json/release_manifest.json/versions。
  - rule TR-B.4（PIT 不可变）：经 `PublicationService` 以输入 v1 发布、再以修订输入 v2 发布，旧快照 result("market") 的 sentiment_research 逐字段不变，新快照独立。
  - rule TR-B.5：研究序列 l/p 与原始 sentiment series 同日计数一致（单一事实源回归）。
  - rubric TR-B.6：工程质量（无第二套涨跌停/晋级算法、无 float）；scale 0-2；anchors 0=重复算法/口径漂移，1=轻微重复，2=共用助手单一事实源；threshold≥1.5；证据：diff 与复审。
- Notes: PG 段在无 `DRAGON_TEST_DATABASE_URL` 时按既有体例 skip。

## Task C：EX16 只读端点与两层不可用

- Status: pending
- Priority: high
- Depends On: B
- Description:
  - 在 `http/routes/market.py` 新增 `GET /api/market/sentiment/research`（紧邻 `/api/market/sentiment` 注册；无路径参数冲突）：
    - 入参 date|snapshot_id、`days`（query_integer 1-120，缺省 20）；非法参数沿用既有 ValueError→400。
    - 读取 `payload["sentiment_research"]`，**只读闭包校验**（method_version/research_only/series 形状、类型；损坏→DataUnavailable 503）；不触碰 batch、不重算。
    - data = {method_version, research_only, 头条点字段(trade_date/score/phase/components/reasons), series: 按日升序末 days 点}；经 `_published_response`（信封层 coverage 复用市场归档）。
    - 旧快照缺键 → 复用 `materialization_unavailable=True` 的 200 data=null；非 Published/缺失 → empty_envelope。
  - 在 `owner/app.py` `_RESET_READ_PATHS` 增加 `/api/market/sentiment/research`（Reset GET 复制白名单）。公开市场路由随现有挂载自动可达，不改公开写边界。
- Acceptance Criteria Addressed: AC5, AC6, AC8
- Test Requirements:
  - rule TR-C.1：合成发布后 GET 200，data 含 method_version/research_only=true/头条/series；series 末 days 点按日升序；days 默认 20、边界 1/120、越界 400。
  - rule TR-C.2：date 与 snapshot_id 选择一致且快照绑定；无 date 又无 id → 400（同兄弟）。
  - rule TR-C.3：旧包（无 sentiment_research 键）→ 200 data=null 且 quality_flags 含 materialization_unavailable，且不调用物化（白盒/计数断言不重算）。
  - rule TR-C.4：研究块闭集损坏（错误 phase/非数 score/缺 components 等）→ 503 DATA_UNAVAILABLE。
  - rule TR-C.5：非发布/空 → 空信封；POST → 不被接受（405/不挂写）。
  - rule TR-C.6：owner Reset 应用经 `_RESET_READ_PATHS` 可复制读取该路径（既有 owner 集成测试体例补一条）。
- Notes: Decimal 经现有 DecimalJSONResponse 输出 JSON number；score 已 2 位量化。

## Task D：前端研究分区（reset-only 懒加载）与构建隔离

- Status: pending
- Priority: high
- Depends On: C（线契约冻结即可并行）
- Description:
  - 新建 `web/src/features/research/SentimentPanel.tsx`、`types.ts`（必要时 `sentiment.css`，优先复用既有 token/class）：
    - fail-closed 校验 EX16 data（闭集键、phase 枚举、score 有限且 0-100、components 三项闭集、weight={1,3}、research_only 恒真、method_version 固定）；畸形→错误态。
    - 展示：研究分区眉标「产品研究假设 · 非官方指标」+ 持续「仅研究」；头条分数/中文阶段（冰点/退潮/分歧/修复/高潮）；b/l/p 分项 分子/分母/比率/缺失原因、等权说明；公式行；分项缺失时空态与原因。
    - 面板自持取数（props：snapshot/date/refreshToken/days?，内部用既有 useResource），不写全局 state；除该资源外零请求。
  - 改 `web/src/pages/Overview.tsx`：仿 RealtimePanel，`const SentimentPanel = import.meta.env.VITE_RESET_WORKBENCH==='1' ? lazy(()=>import('../features/research/SentimentPanel')) : null;`，在 reset 分支「情绪原始观测」之外的**独立 section** 挂载（LazyLoadBoundary+Suspense），与监管/原牧区视觉分隔；默认分支零引用。
  - 改 `web/scripts/check-default-bundle.mjs`：仿 D5 增加研究 marker（用唯一串 `/api/market/sentiment/research`）——reset 构建禁入 entry 静态闭包；默认构建对 allScripts 兜底零产出。
- Acceptance Criteria Addressed: AC9, AC10, AC11, AC13, AC14
- Test Requirements:
  - rule TR-D.1（在 Task E 落测试）：校验器对各类畸形响应拒绝（见 TR-E 系列）。
  - rule TR-D.2：四构建枚举证据——默认 dist/assets 0 个 SentimentPanel chunk 且 marker 0；reset 出独立懒 chunk 且可达；sharing/private 行为符合各自开关；entry 字节 ≤ 预算（默认 315,500/sharing 320,000），`npm run check:bundle` 退出 0。
  - rule TR-D.3：研究分区文案对禁用词（胜率/未来上涨概率/必涨/稳赚/安全/预测见顶见底等）零命中（源串断言）。
  - rubric TR-D.4：可访问性/视觉（独立分区、375px 不溢出、深色/密度 token、键盘焦点、reduced-motion）；scale 0-2；threshold≥1.5；证据 E2E axe 与复审。
- Notes: 不改默认公开应用信息架构；阶段中文仅前端映射，线值用英文枚举。

## Task E：前端单元与 Playwright reset E2E

- Status: pending
- Priority: high
- Depends On: D
- Description:
  - 新建 `web/tests/unit/reset-sentiment-research.test.ts`：EX16 响应校验器（合法 70.00/repair；缺键/多键/坏 phase/越界与非数 score/缺分项/research_only≠true/method_version 不符全拒）；分项 reason/空态映射；禁用词源扫描。
  - 新建 `web/tests/e2e/reset-sentiment-research.spec.ts`（playwright.reset.config；在 `**/api/**` mock 增加 `/api/market/sentiment/research` 分支，复用 reset-market 信封夹具风格）：
    - 三分区独立：原牧区不含研究分、研究区展示分数/阶段/三分项/公式/research_only；
    - 缺分项 → score 空态与原因、不补零；畸形响应 → 错误态不渲染半值；
    - 快照绑定（切日期/快照随资源重取，不改全局其它状态）；
    - 全键盘可达、375px 无横向溢出、全页 axe 干净；无禁用文案。
- Acceptance Criteria Addressed: AC9, AC11, AC13
- Test Requirements:
  - rule TR-E.1：vitest 新文件全绿；`npm run test -- --no-fileParallelism` 全量绿（reset 相关不回归）。
  - rule TR-E.2：新 spec `--repeat-each 3` 稳定通过；全量 reset Playwright 不回归。
  - rule TR-E.3：axe 与 375px 断言通过（变异一条 heading 级别应能使 axe 失败的既有守法可参考）。
- Notes: 真实回放证据与合成 E2E 分开陈述。

## Task F：全量门禁、独立复审与验收记录

- Status: pending
- Priority: medium
- Depends On: A,B,C,D,E
- Description:
  - 后端：`uv run pytest -q -rs`（PG17 全量）、`uv run ruff check .`、`uv run ruff format --check .`（既有 task21 计划文件例外不算新增）、`uv run python -m compileall -q src tests`。
  - 前端：vitest 全量、`tsc --noEmit`、四构建 + check:bundle、相关 reset E2E。
  - 独立只读复审（新上下文子代理，规格/计划/代码/证据齐备）：覆盖 AC 与两份 rubric；按结论开 issue 并统一修复，避免无限审查轮次。
  - 新建 `docs/acceptance/reset-sentiment-research.md`（独立 docs 提交；Delivered/Verification/Commits/Remaining Risk 房式），冻结公式、reason/phase 闭集、无迁移/无外部输入、方法有效性归 Task27。
  - 代码与文档分次提交；提交前核验 5 个受保护脏文件与 `.trae/` 不暂存。
- Acceptance Criteria Addressed: AC12, AC13, AC14（独立证据收口）
- Test Requirements:
  - rule TR-F.1：全量 pytest 0 failed（skip 原因如实）；ruff/compileall 退出 0；前端全量门禁退出 0；记录真实命令输出与数字。
  - rule TR-F.2：独立复审 R1=pass（含 AC13/AC14 两份 rubric≥1.5 与证据），否则开 issue 返修后新上下文复审。
  - rule TR-F.3：提交序列精准（功能/文档分离），受保护文件零暂存，`git status` 仅剩既有脏文件与 `.trae/`。
- Notes: 不在本任务声明研究方法「有效」或生产全验收（Task29 终验）。
