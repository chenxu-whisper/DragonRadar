# Task 28 规格：0-100 情绪研究分与阶段标签

- 日期：2026-10-26
- 分支/工作区：`feature/task21r-task22`（worktree `.worktrees/task21r-task22`）
- 上游依据：`docs/superpowers/plans/2026-09-20-prd-reset-refactor.md` Task 28；接口契约 `docs/contracts/workbench-api.md` EX16；PRD AC（研究功能须标注公式/样本/版本/局限，不得输出未来上涨概率、假官方规则或真实交易）。
- 状态：待批准（Specify 产出，未进入实现）。

## 1. 问题、用户与目标

复盘用户在市场总览页只能看到离散的「原始情绪观测」（涨停/跌停、最高板、晋级率、昨涨停中位数等事实）。
Task 28 在**不污染原始观测、不冒充官方指标、不声称预测**的前提下，新增一个**产品研究假设**：把同一发布快照内三项已观测比率等权合成为一个 0-100 的「情绪研究分」，并映射到五个同期数值分桶阶段，供用户在研究区参考。

- 用户：使用 Reset 研究工作台的本机所有者。
- 目标：
  1. 发布时一次性、确定性地物化研究分与其分项贡献（materialized on publish），API 读取不重算、结果随快照不可变。
  2. 用固定公式 `sentiment-research-v1` 输出分数、阶段与每个分项的 value/numerator/denominator/weight，缺项诚实给 null 与原因。
  3. 在前端以独立「研究分区」呈现公式、样本贡献与研究免责声明，与「原始观测区」「监管状态区」物理分离、互不替代。
- 非目标（明确不做）：
  - 不做因子体检、滚动训练、PIT 回测、模拟组合（属 Task 27）。
  - 不改变 Task 03/09 既有「情绪原始序列」与任何原始字段；不新增可变的全局「当前分数」。
  - 不声称该分能预测未来、周期顺序或胜率；不输出交易建议；不做 Web 管理后台。
  - 不新增数据库迁移、不改 DB schema、不新增第三方依赖；不动监管（regulation）域。
  - 不在默认（公开）应用中展示该研究面板（仅 Reset 工作台），但端点本身是公开只读 GET。

## 2. 冻结的计算口径（method_version = `sentiment-research-v1`）

对某一交易日，取**同一发布批次内、当日可见**的观测：

- `b`（市场广度）= `up / valid_total`：当日有效日收益证券中上涨家数 / 有效收益家数。
- `l`（涨跌停平衡）= `limit_up / (limit_up + limit_down)`：涨停家数 / 涨停+跌停家数。
- `p`（高位晋级）= `sealed ∩ tradable / tradable`：昨日「≥2 连板」集合中，今日可交易且今日封涨停的家数 / 今日可交易家数（与现有 `promote_rate` 同口径、同源）。

合成：`score = ROUND_HALF_UP( 100 * (b + l + p) / 3, 2 )`。

- 分数计算使用由计数直接得到的**未舍入** Decimal 比率；分项展示值可沿用现有 4 位小数量化，但二者不得混用导致分数偏差。
- 阶段（仅按当日分值做同期分桶，不暗示时间顺序或预测）：
  - `ice` 冰点：`score < 20`
  - `ebb` 退潮：`20 ≤ score < 40`
  - `divergent` 分歧：`40 ≤ score < 60`
  - `repair` 修复：`60 ≤ score < 80`
  - `climax` 高潮：`80 ≤ score ≤ 100`
- 三项**等权**，每项权重以精确分数 `{numerator:1, denominator:3}` 表达，避免 1/3 浮点。
- 可用性闸门（分项级，保守）：
  - `b` 仅当 `valid_total > 0` 且当日收益观测集非空时可得，否则 value=null、reason=`no_valid_daily_returns`（含覆盖缺失计数）。
  - `l` 仅当 `limit_up + limit_down > 0` 时可得，否则 value=null、reason=`no_limit_events`。
  - `p` 仅当 `tradable > 0` 时可得（停牌计 excluded、观测缺失计 missing），否则 value=null、reason=`no_tradable_members_from_yesterday_ge2_set`。
  - 任一分项不可得 → 当日 `score=null`、`phase=null`，并在 `reasons` 中列出全部不可得分项原因；**不重新分配权重、不以 0 或其它值补位**。
- 锚定样例：`b=0.6, l=1, p=0.5` → `(0.6+1+0.5)/3*100 = 70.00` → `repair`。

## 3. 功能需求

### 3.1 后端发布时物化
- FR1：市场读模型在发布绑定的输入上一次性产出研究块（追加到既有 market payload，新顶层键 `sentiment_research`），随发布不可变；读取路径不得重新跑批计算。
- FR2：研究块自描述：`method_version="sentiment-research-v1"`、`research_only=true`；含截至该快照交易日的研究序列，每交易日一点。
- FR3：市场读模型**容器 schema 不升版**（保持 `schema/schema_version` 现状），研究块作为纯追加键；旧快照不含该键。研究方法自身版本由 `method_version` 承载。不新增迁移、不改 `schema_contract.json`/`release_manifest.json`。
- FR4：研究序列与原始情绪序列使用同一套当日事实（同源、同口径），不得发明第二套涨跌停/晋级算法。
- FR5：每点形状闭集：
  - `trade_date`；
  - `score`：Decimal（编码为 JSON number，2 位小数）或 null；
  - `phase`：五枚举之一或 null；
  - `components`：恰好 `breadth`/`limit_balance`/`promotion` 三项，每项含 `value`(Decimal|null)、`numerator`(int)、`denominator`(int)、`weight:{numerator:1,denominator:3}`、`reason`(string|null)，并可附 coverage 明细（expected/valid/excluded/missing）；
  - `reasons`：string 数组（得分可得时为 `[]`）。
- FR6：纯 Decimal、确定性；同输入跨进程结果逐字节一致；不产生 NaN/Infinity。

### 3.2 EX16 只读端点
- FR7：新增 `GET /api/market/sentiment/research`，查询参数 `date` 或 `snapshot_id`（二选一，与兄弟端点一致）+ `days`（整数 1-120，缺省 20）。参数非法返回既有 400 口径。
- FR8：响应为共享选择信封，`data` 含：`method_version`、`research_only=true`、头条（该快照交易日点的 `trade_date/score/phase/components/reasons`）、`series`（研究序列按日升序、截取最后 `days` 点）。
- FR9：与 `/api/market/sentiment` 同挂载（公开只读市场路由），并加入 owner 应用 Reset GET 复制白名单 `_RESET_READ_PATHS`；不挂到任何写路径，不接受 POST。
- FR10：两层不可用（D1 同纪律）：
  - 选中对象不存在/非已发布 → 200 空信封（data=null），同兄弟端点；
  - 已发布但为**旧市场包、无 `sentiment_research` 键** → 200，data=null 且 `quality_flags` 含 `materialization_unavailable`，**不读时重算**；
  - 研究块存在但闭集结构损坏/类型错误 → 503 `DATA_UNAVAILABLE`；
  - 块完好但当日分项缺失 → 200 业务结果（score/phase=null + reasons），不是 5xx。
- FR11：历史固定（PIT）：对旧快照执行得到旧物化值；之后以修订输入重新发布只生成新快照，旧快照研究块不变。
- FR12：只读、无字符串 SQL、无写入；不读 notes/watchlist/simulation/AI 草稿。

### 3.3 前端 Reset 研究分区
- FR13：在 Reset 市场总览页新增**视觉与语义均独立**的「情绪研究分」分区，眉标明示「产品研究假设 · 非官方指标」，并持续可见 `research_only`/「仅研究」标识；展示头条分数、阶段中文标签、三分项的 分子/分母、权重、比率与缺失原因，以及公式行（b/l/p 定义与基准无关、20 板晋级口径）。
- FR14：三分区不得互相替代或混排为同一指标：研究分区不得改写/替换「情绪原始观测」区与监管状态区；缺分项时显示空态与原因，不补零、不伪造分数。
- FR15：fail-closed 响应校验：闭集键、phase 枚举、score 有限数与边界、components 形状、research_only 恒真、method_version 固定；畸形响应映射为错误态，不渲染半成品。
- FR16：快照绑定：随当前 `snapshot_id/date` 获取；不触碰全局 date/snapshot/q/sort 等状态；除取研究分本身外零新请求。
- FR17：仅 Reset 构建可用：经 `VITE_RESET_WORKBENCH` 折叠 + 动态 `import()` 懒加载（同 RealtimePanel 模式）。默认构建产物 0 个研究面板 chunk、0 个研究专属 marker；reset 构建产出独立懒 chunk，默认 entry 不突破预算。
- FR18：可访问性与主题：search/region 语义、键盘可达、焦点合理、375px 无横向溢出、1440px 正常、复用深色/密度 token、reduced-motion；全页 axe 干净。
- FR19：文案合规：研究分区与任何新文案不得出现「胜率」「未来上涨概率」「必涨/稳赚」「安全」「预测周期见顶/见底」等预测或诱导交易措辞；阶段仅表述为同期分桶。

## 4. 约束、依赖与假设

- 依赖：Task 08（发布物化）、Task 09（市场读模型/原始情绪序列）已在本分支交付；研究分供 Task 27 使用时只取发布时可见版本。
- 数据与外部条件：只需已授权、已发布市场批次内的涨跌/涨跌停/晋级观测；不依赖任何新外部供应商或凭证。方法有效性**不在本任务下结论**（由 Task 27 冻结样本报告评估）。
- 假设（如无异议按此实现，批准时可更正）：
  1. 面板仅在 Reset 工作台呈现（默认公开应用不展示），端点为公开只读 GET；
  2. `days` 缺省 20、上限 120，与 raw `/api/market/sentiment` 对齐；
  3. phase 线键用英文枚举（ice/ebb/divergent/repair/climax），中文标签仅在前端映射；
  4. 信封层 `coverage` 复用市场归档覆盖（同兄弟端点），分项可用性由组件 value/reason 表达，不另造第二套 coverage。
- 工程约定：TDD（先 RED）、Decimal 全程、闭集校验、精准 `git add -- <path>`、`.trae/` 不入库、不 merge/tag/push/发版/部署。

## 5. 验收标准（AC）

类型仅 `rule`（可客观判定）或 `rubric`（评分维度）。

- **AC1（rule）** 公式确定性：纯函数以 b=0.6/l=1/p=0.5 输出 `score=Decimal("70.00")`、`phase="repair"`；使用未舍入比率计算后 ROUND_HALF_UP 到 2 位；同输入跨进程序列化一致。证据：新增后端测试断言。
- **AC2（rule）** 阶段边界精确：对 0、19.99、20、39.99、40、59.99、60、79.99、80、100 等分值映射到正确桶（20→ebb，80/100→climax，<20→ice）。证据：参数化后端测试。
- **AC3（rule）** 缺项语义：任一分项分母为 0/观测不可得 → 该分项 value=null+固定 reason，头条 score/phase=null 且 reasons 非空；不重分配权重、不补零。证据：后端测试。
- **AC4（rule）** 发布物化：发布后市场 payload 含追加键 `sentiment_research`，其 `method_version=sentiment-research-v1`、`research_only=true`、序列每点闭集形状正确；容器 schema 标识保持不变；无新迁移、`schema_contract.json`/`release_manifest.json` 无 diff。证据：物化测试 + git diff/迁移测试。
- **AC5（rule）** EX16 契约：`GET /api/market/sentiment/research` 对 date/snapshot_id + days(默认20/范围1-120) 返回信封 data（method_version、research_only、头条、series 末 days 点、按日升序）；非法参数→400；路径在市场路由与 `_RESET_READ_PATHS` 均可用。证据：HTTP 测试。
- **AC6（rule）** 两层不可用：无旧块的已发布快照→200 data=null + materialization_unavailable（不重算）；闭集损坏→503 DATA_UNAVAILABLE；非发布/缺失→空信封；分项缺失→200 null+reasons。证据：HTTP 测试。
- **AC7（rule）** PIT 不可变：以修订输入二次发布后，旧快照研究块逐字段不变、新快照独立。证据：双发布后端测试。
- **AC8（rule）** 读路径不重算：白盒断言端点只读取已归档 payload（无批处理重算、无字符串 SQL）。证据：代码审查 + 测试。
- **AC9（rule）** 前端 fail-closed：构造缺键/额外键/非法 phase/非数 score/缺 components/research_only≠true/method_version 不符等畸形响应，校验器全部拒绝并进入错误态。证据：vitest。
- **AC10（rule）** 构建隔离：默认构建 dist/assets 中 0 个 SentimentPanel chunk、0 个研究 marker；reset 构建产出独立懒 chunk 且功能可达；`check:bundle` 与 entry 预算（默认 315,500 / sharing 320,000）通过；新增 marker 纳入 bundle 门禁。证据：四构建枚举 + 门禁日志。
- **AC11（rule）** 文案合规：对研究分区及新前端文案做禁用词扫描（胜率/未来上涨概率/必涨/稳赚/安全/预测…）零命中；阶段文案仅为同期分桶表述。证据：测试/构建脚本断言。
- **AC12（rule）** 全量门禁：新增后端 pytest（RED→GREEN）、新增前端 vitest、新增 Playwright reset E2E 通过；`ruff check .`、`ruff format --check`（既有 task21 例外不算新增）、`compileall`、全量 pytest、前端 vitest 全量与相关 reset E2E 全绿；退出码/跳过原因如实记录。
- **AC13（rubric，阈值 ≥1.5/2）** 可访问性与视觉质量：研究分区信息层级清晰、三分区边界一目了然、公式与样本可读；键盘/焦点/375px/深色/密度/reduced-motion 到位，全页 axe 干净。锚点：0=分区混杂或有明显可访问性缺陷；1=可用但边界或对照偏弱；2=分区独立、对照充分、axe 与响应式均通过。证据：E2E + 独立复审。
- **AC14（rubric，阈值 ≥1.5/2）** 工程质量：最大化复用市场读模型助手、无重复算法、Decimal 端到端、闭集形状与命名一致、两层不可用与既有端点同构。锚点：0=出现第二套涨跌停/晋级算法或 float 金融计算；1=可运行但有重复/口径漂移风险；2=单一事实源、类型/口径/异常映射与既有市场端点完全一致。证据：独立只读复审。

## 6. 开放问题

无阻塞性开放问题；第 4 节四条假设在批准时一并确认。
