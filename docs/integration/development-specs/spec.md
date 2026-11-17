# Task 27：PIT 回测、因子体检、滚动验证与本地模拟组合 — 产品规格

## Overview

- **Summary**：在 DragonRadar 已发布不可变快照体系之上，交付 REQ-B01/B02（工程扩展 EX14/EX15）：点在时间（PIT）历史回测、因子体检、walk-forward 滚动验证与市场状态条件调权，以及纯本地、仅研究用途的模拟组合账本；并提供 AI 增益对照的同池实验框架（在真实评测材料缺失时固定 disabled）。
- **Purpose**：让用户能在**可复现、防前视、防幸存者偏差**的前提下检验研究假设，同时把"开发完成的合成研究能力"与"真实策略有效性"严格分开——后者仍由外部生产门禁决定。
- **Target Users**：本机 owner（私有工作台使用者，单人研究复盘）。公共应用不提供任何回测/模拟能力。

## Goals

- G1：回测信号严格 PIT——决策时点只能使用当时已可见的行情、证券状态、题材成员与复权版本；成交在最早下一可成交时点发生。
- G2：A 股交易约束内生化——T+1、停牌、涨跌停、退市/ST/上市状态、资金不足，均产生确定性成交或明确拒单。
- G3：研究结论可追溯——config_hash、input_manifests、strategy/method/cost/execution 版本、逐笔成交、拒单、净值、折叠、基线/消融、质量与覆盖率全部随 run 固化。
- G4：因子体检 + walk-forward + 条件调权形成"样本外"证据链，训练/测试严格不重叠。
- G5：本地模拟组合复用 Task04 原子事务语义，余额/持仓永不为负，绝不发出真实订单或同步到服务端。
- G6：AI 增益对照具备可运行的**对照框架**，但在无冻结真实评测样本前固定 disabled，不自产"人工真值"。

## Non-Goals

- N1：不接入任何券商 SDK、不提交真实委托、不管理真实仓位、不做银证/资金划转。
- N2：不声称任何策略在真实市场有效；真实授权数据、官方规则、真实模型评测（≥100 标注样本、原子事实支持率 ≥95%）、公开发布许可、≥20 交易日影子运行五项外部门禁维持 blocked。
- N3：不做分钟级/Tick 回测、不做高频撮合模拟；频率限定为日线、以交易日为调仓单位。
- N4：不提供任意 SQL、通用策略 DSL 或用户自定义公式引擎；首版仅一个内建基线策略与可插拔注册表接口。
- N5：不新增第三方计算/数值包（不用 pandas/numpy/scipy；沿用 Decimal 纯函数）。
- N6：不在公共应用挂载任何 `/api/research/**` 路由；研究端点不读取私人笔记或本地模拟组合。
- N7：不重写已交付的快照、发布、角色、情绪研究分等模块；只消费其已发布产物。
- N8：回测成交价口径不启用 qfq/hfq（沿用既有物化契约：qfq/hfq 返回 unavailable）；成交按 raw 价格记账，复权仅用于因子/收益计量并在结果中显式披露口径。

## Background & Context

- PRD v1.8 §2.3 将 REQ-B01/B02 列为"本次必须完成，研究限定"；附录 A 将其映射到 EX14-15 与 AC-B01/B02。
- 契约冻结面（当前实现刻意缺席，由 [test_reset_full_scope.py](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/ci-baseline-fix/tests/test_reset_full_scope.py) L635-775 锁定）：无 `dragon_radar.research` 包、无 `http/routes/research.py`、迁移 head 固定 `0013_query`、无前端面板与对应测试；EX14/EX15 在 EX 注册表登记为 `surface=none`。
- 接口契约已在 [workbench-api.md](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/ci-baseline-fix/docs/contracts/workbench-api.md) L88-94 冻结：
  - **EX14** `POST /api/research/backtests`：`snapshot_id, input_manifests, strategy_id, parameters, period, cost_model, execution_model, experiment, idempotency_key → run_id, status`；owner-only、CSRF、有界输入、幂等、principal 隔离；生产门禁为 PIT 数据与公开的模拟假设。
  - **EX15** `GET /api/research/backtests/{run_id}`（owner）：`config_hash, manifests, metrics, trades, rejections, equity, folds, baseline, ablation, quality`；`research_only=true`；无真实券商订单。
  - 模拟组合为本地 IndexedDB：`simulation_accounts / simulation_orders / simulation_fills / simulation_commands`，按 `local_profile_id` 分区，原子 command/revision 语义同 Task04。
- 可复用资产（已逐一核实）：
  - PIT 数据前提已在 [domain/models.py](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/ci-baseline-fix/src/dragon_radar/domain/models.py) 齐备：`MarketBatch.bars` 含 `available_at / adjustment_factor / limit_status / limit_up / limit_down`；`SecurityState` 含 `suspended / is_st / is_delisting / listing_date / effective_from/to / available_at`；`ExchangeCalendar.sessions`；`provenance` 与 `as_of` 构成知识截止。
  - 多日 PIT 序列读取已有 `PublicationRepository.list_published_window()`（锚定 publication_sequence，每交易日取当时可见最新发布，≤250 sessions 上限）。
  - 研究因子 18 项（FAC-R01/R02/R03/C01/C02）与覆盖率分区已在 [research_factors.py](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/ci-baseline-fix/src/dragon_radar/roles/research_factors.py) 实现，含 cutoff 过滤与 grant 失败闭合。
  - 纯 Decimal 指标在 [metrics.py](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/ci-baseline-fix/src/dragon_radar/analytics/metrics.py)：`interval_return / excess_return / maximum_drawdown / drawdown_curve / normalized_curve / closing_limit_hit`。
  - 异步任务生命周期范式在 [analysis_jobs](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/ci-baseline-fix/src/dragon_radar/analysis_jobs/models.py)：不可变任务表 + 状态 CHECK、owner 隔离、`idempotency_key/request_hash/cache_key`、advisory lock、租约 claim/renew/succeed/fail、不可变输入触发器；**现有状态机无 cancelled，本任务需以不破坏既有不变量的方式扩展**。
  - 情绪研究分 EX16（method `sentiment-research-v1`）已发布 score 0-100 与五阶段 phase。
  - 前端 IDB v4（7 store）+ 原子事务/回读校验；owner POST 客户端有同源 cookie + `X-CSRF-Token` + 15s 超时 + 调用方幂等键范式；reset 构建以 `VITE_RESET_WORKBENCH` 懒加载折叠做包体隔离。
- 早期工程计划见 [2026-09-20-prd-reset-refactor.md](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/ci-baseline-fix/docs/superpowers/plans/2026-09-20-prd-reset-refactor.md) Task 27 段（L628-640）与集成约束（L666）。

## Functional Requirements

### 回测配置与生命周期

- **FR-1**：owner 通过 `POST /api/research/backtests` 提交回测，请求体严格匹配 EX14 字段白名单；服务端校验有界性（周期长度、证券数量、折数、参数取值域），计算并固化 `config_hash` 与 `input_manifests`，返回 `run_id, status`。
- **FR-2**：回测为异步租约任务，状态机为 `queued / running / succeeded / failed / cancelled`；支持 owner 对本人未完成 run 取消；有运行预算（wall-clock 与计算量）上限，超限按失败闭合；任何失败都不得发布半成品结果。
- **FR-3**：同 owner + 同 `idempotency_key` + 同配置摘要重放返回原 run；键相同配置不同返回冲突；run 之间按 `config_hash` 复用不可变结果；owner 之间严格隔离。
- **FR-4**：`GET /api/research/backtests/{run_id}` 返回 EX15 完整结构（config_hash/manifests/metrics/trades/rejections/equity/folds/baseline/ablation/quality），金额与比率为 Decimal 字符串，处处带 `research_only=true` 与口径/版本披露。

### PIT 引擎与执行

- **FR-5**：逐日构造"当时可见信息集"：仅纳入 `available_at` 不晚于该决策时点、且 publication_sequence 不晚于锚定发布的行情/状态/题材成员/复权版本；未来消息、后来修订的复权因子、决策时点之后才加入的成分一律不得进入信号（防前视、防幸存者偏差）。
- **FR-6**：信号在 T 日收盘后形成，成交不早于 T+1 首个可成交时点；`simulate_fill(order, bar, rules)` 返回成交或带原因的拒单；拒单原因至少区分 `suspended / limit_up_blocked_buy / limit_down_blocked_sell / t1_sell_blocked / insufficient_cash / insufficient_position / delisted / no_tradable_price`。
- **FR-7**：T+1 禁止当日买入当日卖出；停牌无成交；涨停封死不可买入、跌停封死不可卖出（以 raw 收盘价等于有效涨跌停价的保守判定，未知涨跌停元数据按"不可断言可成交"保守处理）；退市证券在最后可交易日按规则强制平仓或计入拒单并披露。
- **FR-8**：确定性 Decimal 账本维护 cash/holdings/equity/trades/rejections；指标含总收益、基准超额、正值最大回撤、换手、成本总额、不可成交比例（按订单数与金额双口径）。
- **FR-9**：成本与执行假设显式配置且必填（佣金率、印花税、滑点模型、最小手续费等），不内置静默默认；回测结果同时给出至少 3 档成本敏感性（低/基准/高）。

### 因子体检、滚动验证、条件调权

- **FR-10**：因子体检对策略实际使用的每个因子输出：样本量、缺失率（复用 FAC coverage 的 expected/valid/excluded/missing 完整分区）、分布摘要（分位数）、秩相关（因子截面秩相关矩阵），并标注数据窗口与来源版本。
- **FR-11**：walk-forward 将 period 切分为严格不重叠的训练/测试折；阈值/权重仅在训练段拟合，冻结后应用于测试段；输出每折与聚合的样本外指标；折数与窗口长度有界且在配置中显式声明。
- **FR-12**：条件调权的市场状态五分类**采用 EX16 情绪研究分五阶段**（冰点/退潮/分歧/修复/高潮）；每个调仓日只读取当时已发布的 phase（经 PIT 序列），只允许使用训练折内可见状态拟合调权映射；phase 缺失时降级为不调权并记录质量原因，不得臆造状态。

### AI 增益对照

- **FR-13**：实验框架支持在同池、同窗、同成本、同随机种子下运行 baseline 与 ai_variant 两条腿并输出 ablation 对照；在冻结真实评测样本（≥100 条人工标注）缺失时，ai_variant 固定为 `disabled` 并返回结构化原因（`real_evaluation_inputs_unavailable`），不得自产人工真值、不得把模型推断当作收益标签展示。

### 本地模拟组合

- **FR-14**：新增 IndexedDB（升级 v5）四 store：`simulation_accounts / simulation_orders / simulation_fills / simulation_commands`，全部按 `local_profile_id` 分区；具备 command 幂等、revision 乐观锁、单事务读改写与提交后回读校验；Quota/abort/closed DB 不得产生半截记录。
- **FR-15**：本地账本执行与回测引擎**同一套纯函数规则**（T+1/停牌/涨跌停/同成本模型）；现金与持仓永不为负；账本可关闭重开后恢复；无任何 HTTP 组合同步、无订单提交端点。
- **FR-16**：模拟组合界面持续展示"仅研究模拟 · 不构成投资建议 · 不接券商/不发真实订单"，且不出现"安全再涨""无风险""预期胜率/上涨概率"类表述。

### 前端与边界

- **FR-17**：workbench 重置构建中新增回测面板（配置、提交、轮询、取消、结果：指标/净值/折/成交/拒单/因子体检/成本敏感性/AI 对照 disabled 态）与模拟组合面板；公共构建包体不得包含 research 块；workbench 公共（只读）应用运行时对 `/api/research/**` 保持零调用。
- **FR-18**：解锁 Task 27 冻结契约（迁移 head → `0014_research`、artifacts-absent 反转为存在性契约、EX14/EX15 surface 登记、owner 应用挂载、e2e 探针改写），并新增 Task 27 验收文档；`reset-final.md` 作为历史终验快照只追加"由 Task 27 解除"的指引，不改写其原始 blocked 结论。

## Non-Functional Requirements

- **NFR-1（确定性/可复现）**：相同 config_hash + input_manifests 的两次运行，结果逐字段一致（Decimal 定点、无 wall-clock/随机泄漏；任何随机过程使用配置内固定种子）。
- **NFR-2（性能边界）**：普通 GET 的 P95 ≤ 2.0s 契约不变；`GET /api/research/backtests/{run_id}` 为轻量读，不得以重算实现；回测只在异步 worker 运行，具备 wall-clock 与计算量预算，取消在协作式检查点生效（不依赖线程强杀）。
- **NFR-3（安全）**：owner-only + CSRF + 有界 body + principal 隔离；公共应用无 POST、无 `/api/research`；研究端点不读私人笔记/模拟账本；错误归一不回显堆栈。
- **NFR-4（质量门禁）**：ruff check/format、compileall、后端 pytest（zero skip）、前端 vitest/Playwright、构建隔离与生产包检查全部通过；不放宽任何既有阈值与超时。
- **NFR-5（可访问性/响应式）**：表单控件具备 `aria-invalid / aria-describedby`；375px 与 1440px 主路径可用；200% 缩放可读；状态变化有 `role=status/alert` 通告。
- **NFR-6（依赖）**：Python 与前端均不新增第三方依赖；不新增 Docker 服务（回测 worker 复用现有 analysis-worker 轮询进程）。

## Constraints

- **Technical**：
  - 迁移链线性：`0014_research` parent 必须为 `0013_query`；downgrade 遵循 backup-restore-only 抛错；同步更新 `release_manifest.json`、`storage/schema_contract.json`、`SCHEMA_REVISION`。
  - 纯函数内核与 IO 分离：成交/账本/指标/walk-forward/因子体检为不触网、不触库、不读时钟的纯函数，worker/路由仅做装配。
  - 服务端表结构与触发器范式对齐 0007c（身份/输入列不可变，仅状态/租约/结果列可变；cache 表完全不可变）。
  - 前端数值沿用 string Decimal；IDB 升级必须保留 v1-v4 数据迁移路径与 backup/restore 兼容性。
- **Business**：
  - 研究输出必须公开公式、输入版本、覆盖率与限制；不是未来收益概率、投资承诺或官方监管结论。
  - 开发完成 ≠ 生产就绪；五项外部门禁在本任务完成后仍逐项保持 blocked 登记。
- **Dependencies**：迁移 0004/0007/0008/0010/0012/0013 已在链；EX16 情绪研究分已交付；`list_published_window` 已可用；实施基线为 PR #2 合入后的 `feature/task21r-task22`。

## Assumptions

- A1：**默认决策（用户 2026-11-04 跳过澄清，按推荐项执行，审批可修正）**：
  - 首版 strategy_id 目录 = 单基线"研究因子截面 Top-N、等权、固定周期换仓"策略 + 可插拔注册表接口；不实现六类角色信号策略。
  - 市场状态五分类 = 复用 EX16 情绪研究分五阶段，不新建独立分类规则。
  - AI 增益对照 = 仅交付同池对照框架与 disabled 态，不接入现有 AI provider 构造收益变体。
  - 实施起点 = PR #2 合入后从 `feature/task21r-task22` 新建独立 worktree。
- A2：现有合成夹具需要补充**多日连续发布序列**专用夹具，覆盖停牌、涨跌停封死、T+1、退市、ST、复权因子修订、成分后纳入（幸存者偏差）等场景；夹具只服务测试，不暗示真实数据已接入。
- A3：回测锚定一个已发布 publication，决策日序列通过 `list_published_window` 获取；输入域至少含 market，topic/role 等域缺失时相应因子降级而非失败整体。
- A4：回测预算的具体保守上限（最大证券数、调仓次数、折数、wall-clock）在 Plan 阶段基于合成规模标定并写入配置校验，禁止运行时无限扩张。

## Acceptance Criteria

### AC-1：PIT 防前视被固定夹具证伪拦截
- **Type**： `rule`
- **Given**：一个多日发布序列夹具，其中包含 T 日决策时点之后才可见的新闻/行情修订、向后修订的复权因子、以及 T 日之后才纳入指数/题材的证券。
- **When**：以锚定 publication 运行基线回测。
- **Then**：T 日信号与成交不读取任何上述"未来"信息；测试通过改变 future 数据断言 trades/equity 逐字节不变；存在一条输入 `available_at` 晚于决策时点即被剔除并计入 quality。
- **Pass Condition**：`tests/test_reset_backtest.py` 中未来消息/复权修订/晚到成分三类用例全部通过。
- **Evidence**：pytest 输出 + 夹具差异对照断言。

### AC-2：防幸存者偏差与 PIT 证券池
- **Type**： `rule`
- **Given**：夹具含回测窗口内退市、窗口后才上市、ST 状态变更、题材成员变更的证券。
- **When**：逐日构造可选券池。
- **Then**：每个调仓日的池子由当日 PIT 状态（上市/未退市/非 ST 剔除规则按配置/当日成员身份）决定；退市证券保留其退市前成交并在末日按规则处理；窗口后上市证券不出现在任何历史折中。
- **Pass Condition**：证券池逐日快照断言与退市处理断言通过。
- **Evidence**：pytest 用例与逐日池子快照。

### AC-3：A 股交易约束与拒单分类
- **Type**： `rule`
- **Given**：覆盖停牌、涨停封死买、跌停封死卖、T+1 当日卖、资金不足、持仓不足、退市、无价的订单夹具。
- **When**：调用 `simulate_fill` 与账本更新。
- **Then**：每类订单返回对应 rejection 原因码且不产生持仓/现金变动；可成交订单严格在信号日的下一可成交时点以 raw 价格成交；涨跌停元数据未知时按不可断言成交保守处理。
- **Pass Condition**：9 类原因码各有确定性用例；账本恒等式（现金+持仓市值=权益）每日成立。
- **Evidence**：`tests/test_reset_research_validation.py`。

### AC-4：T+1 与资金/持仓非负
- **Type**： `rule`
- **Given**：任意合法订单流（含同日买卖混合）。
- **When**：顺序结算。
- **Then**：当日买入份额当日不可卖；现金不得为负（买成交前校验）；不得卖出超过可用持仓；所有数值为 Decimal 定点。
- **Pass Condition**：属性式/穷举式订单流用例通过，无 float 参与。
- **Evidence**：pytest。

### AC-5：确定性、可复现与版本固化
- **Type**： `rule`
- **Given**：同一锚定发布与同一配置。
- **When**：两次（含跨进程）提交回测。
- **Then**：config_hash 相同；第二次命中结果复用；两次 metrics/trades/equity/folds 逐字段一致；结果内记录 strategy/method/cost/execution/adjustment/calendar/pool 版本与 input_manifests。
- **Pass Condition**：复现用例与结果缓存用例通过。
- **Evidence**：pytest + EX15 响应字段断言。

### AC-6：成本敏感性与不可成交比例
- **Type**： `rule`
- **Given**：低/基准/高三档显式成本模型与含拒单的运行。
- **When**：运行回测。
- **Then**：metrics 分别给出三档净收益/成本总额；不可成交比例按订单笔数与金额双口径披露；成本模型字段缺失或非有限正数时请求被 400 拒绝。
- **Pass Condition**：三档对照与拒绝用例通过。
- **Evidence**：pytest。

### AC-7：因子体检输出完整且口径正确
- **Type**： `rule`
- **Given**：含缺失值的因子面板夹具。
- **When**：运行体检。
- **Then**：每个被用因子输出版本、窗口、样本量、缺失率（expected/valid/excluded/missing 完整分区且和相等）、分布分位数、因子间 Spearman 秩相关；数据不足时给原因而非编造数值。
- **Pass Condition**：体检结构与分区守恒用例通过；秩相关用已知小样本手算值校验。
- **Evidence**：`tests/test_reset_backtest.py`。

### AC-8：walk-forward 严格样本外与条件调权 PIT
- **Type**： `rule`
- **Given**：配置折数 K 的 period 与五阶段相位序列（含相位缺失日）。
- **When**：执行滚动验证。
- **Then**：训练/测试折两两不重叠且覆盖测试期；参数仅由训练折拟合后冻结；调权状态按调仓日 PIT 发布 phase 取值，训练折内见不到测试折状态；phase 缺失日降级为不调权并记入 quality。
- **Pass Condition**：折不重叠/参数冻结/相位 PIT/降级四类用例通过。
- **Evidence**：pytest。

### AC-9：EX14/EX15 契约与权限边界
- **Type**： `rule`
- **Given**：owner 会话、伪造会话、公共应用三类调用方。
- **When**：调用 POST/GET research 端点。
- **Then**：owner+CSRF 可提交/查询本人 run；无会话/错 CSRF → 403；越权访问他人 run → 404；字段白名单外字段/超界参数 → 400；公共应用与 workbench 只读路由表中不存在该路径；响应含 research_only 与全部 EX15 字段。
- **Pass Condition**：路由装配契约、权限、校验、白名单用例通过。
- **Evidence**：pytest（含更新后的 `test_reset_full_scope.py`）。

### AC-10：任务生命周期、取消与失败闭合
- **Type**： `rule`
- **Given**：排队/运行中的 run 与超过预算的配置。
- **When**：owner 取消、worker 心跳续租、预算超限、worker 崩溃。
- **Then**：取消在检查点生效并落 `cancelled`；租约过期可被重新 claim（次数有上限）；超限/异常落 `failed` 结构化原因且 result 为空；不存在半成品 succeeded；状态 CHECK 与不可变输入触发器覆盖所有迁移。
- **Pass Condition**：取消/租约/预算/失败用例通过；迁移在真实 Postgres 上 upgrade 成功。
- **Evidence**：pytest + 迁移测试。

### AC-11：AI 对照框架 disabled 闭合
- **Type**： `rule`
- **Given**：无冻结真实评测样本的环境。
- **When**：experiment 指定 ai_variant。
- **Then**：该腿返回 `status=disabled, reason=real_evaluation_inputs_unavailable`；baseline 腿正常；任何界面/API 不展示该腿的收益数字或"AI 增强收益"措辞；同池同窗同成本同种子的对照装配在样本就位后可启用（接口存在且被测试冻结）。
- **Pass Condition**：disabled 与同池配置用例通过。
- **Evidence**：pytest + 前端文案断言。

### AC-12：本地模拟组合账本
- **Type**： `rule`
- **Given**：浏览器 IndexedDB（fake-indexeddb 单测 + Playwright 真实浏览器）。
- **When**：下单、成交、重开账本、并发/重复提交、Quota 失败。
- **Then**：四 store 按 profile 分区；command 幂等（同键同摘要重放不产生新修订，键同摘要不同冲突）；revision 乐观锁；提交后回读一致才确认；余额/持仓不负；重开后完整恢复；无任何 `/api/research` 之外的组合同步请求且不存在订单上传端点。
- **Pass Condition**：`reset-simulation.test.ts` 与 e2e 通过。
- **Evidence**：vitest + Playwright + 网络零调用断言。

### AC-13：本地账本与回测共享交易规则
- **Type**： `rule`
- **Given**：同一批 bar/状态输入与同一成本模型。
- **When**：分别在服务端回测内核与前端账本模拟器（TS 端口）对同一订单流结算。
- **Then**：成交/拒单/现金/持仓结果按同一规则一致（以固定黄金样例双向对账）。
- **Pass Condition**：黄金样例对账用例（后端 pytest + 前端 vitest 各持同一夹具）通过。
- **Evidence**：双端测试与共享夹具。

### AC-14：前端面板可用性与研究披露
- **Type**： `rubric`
- **Dimension**：研究工作流可用性与披露完整性
- **Scale**：1-5
- **Anchors**：1 = 无法完成提交/查看/取消闭环或缺失研究限定披露；3 = 闭环可用但口径/拒单/折结果难以核对；5 = 配置→提交→轮询→取消→指标/净值/成交/拒单/体检/敏感性全链路清晰，研究限定与禁用原因处处可见，无收益承诺措辞
- **Pass Threshold**：>= 4
- **Evidence**：Playwright 走查截图、可访问性树、375/1440 视口与 200% 缩放检查、文案扫描。

### AC-15：工程隔离与回归质量
- **Type**： `rubric`
- **Dimension**：纯函数化、构建隔离与零回归
- **Scale**：1-5
- **Anchors**：1 = 引擎混入 IO/时钟或公共包体泄漏、回归红；3 = 功能可用但内核不纯或隔离靠人工约定；5 = 内核纯函数零 IO、research 块仅 workbench 懒加载、全部门禁（ruff/compileall/pytest zero-skip/vitest/Playwright/bundle 隔离）全绿且无阈值放宽
- **Pass Threshold**：>= 4
- **Evidence**：CI 全部门禁真实退出码、bundle 检查、依赖清单 diff（无新增三方包）。

## Open Questions

- [ ] O1（默认 A1，待审批确认）：首版仅单基线因子截面策略；后续角色信号策略是否另立任务。
- [ ] O2（默认 A1，待审批确认）：五分类固定复用 EX16 phase；若产品后续要独立市场状态分类，另开规格。
- [ ] O3（默认 A1，待审批确认）：AI 对照首版只交付框架 + disabled；启用条件 = 冻结真实评测样本通过独立评测门禁。
- [ ] O4：回测预算具体上限数值（证券数/调仓数/折数/wall-clock）在 Plan 中按合成规模标定后请用户确认保守阈值。
- [ ] O5：模拟组合与回测 run 的关联深度——首版本地面板仅手工记账（可引用某 run 的配置作为账本初始参数），不自动回放 run 交易；如需"一键从 run 镜像建账本"请在审批时指出。
