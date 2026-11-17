# Task 27：PIT 回测、因子体检、滚动验证与本地模拟组合 — 实施计划

> 切片原则：纯函数内核优先（模型 → 交易规则 → 账本 → PIT 装配 → 引擎 → 体检/滚动/对照），持久化与路由其后，前端最后。每个任务自身走完 TDD（RED→GREEN），结束时全量相关测试保持绿色；`test_reset_full_scope.py` 的冻结断言按各任务指定行**分批翻转**，不允许长期红门。
>
> 基线：PR #2 合入后从 `feature/task21r-task22` 新建 worktree。Python 一律 `uv run`；提交显式路径、多 `-m`；`.temp/` 不入库。

## Task 1: research 包骨架与冻结契约分批重定向

- **Status**: `completed`
- **Priority**: high
- **Depends On**: None
- **Completion Evidence** (2026-11-05, worktree `.worktrees/task27-research`, base `9e361d4`):
  - TDD RED→GREEN：先改契约/建占位测试得到 11 个精准失败（1 骨架契约 + 10 import 参数），建包后转绿。
  - 新建 `src/dragon_radar/research/`（`__init__.py` + models/pit/execution/ledger/engine/health/walk_forward/ablation/repository/worker 共 11 文件，纯内核/IO 分层写入 docstring）。
  - 新建 `tests/test_reset_backtest.py`、`tests/test_reset_research_validation.py`（import 契约参数化）、`web/tests/unit/reset-simulation.test.ts`（2 用例，vitest 实跑通过）、`web/tests/e2e/reset-research.spec.ts`（1 用例，playwright.reset 配置实跑通过、零 skip）。
  - `test_task27_artifacts_remain_absent` → `test_task27_skeleton_present_but_later_slice_artifacts_remain_absent`（TASK27_SKELETON 15 路径必须存在；TASK27_LATER_ARTIFACTS 5 路径仍缺席，归属 Task 10/12/14/15/16）；迁移 head/owner 路由/EX surface 三组强断言未动。
  - 门禁真实结果：pytest 三文件 22 passed/2 skipped（skip 均为 DRAGON_TEST_DATABASE_URL 缺失的 Postgres 门）；全仓 collect 3982 collected/1 deselected（既有）；`ruff check .` All checks passed；`ruff format --check .` 287 files already formatted；compileall OK；`tsc --noEmit` OK。
  - 未创建 `0014_research.py`、未挂载 research 路由、未建任何面板 tsx。
- **Description**:
  - 新建 `src/dragon_radar/research/__init__.py` 及占位模块（models/pit/execution/ledger/engine/health/walk_forward/ablation/repository/worker）。
  - 新建测试文件占位：`tests/test_reset_backtest.py`、`tests/test_reset_research_validation.py`、`web/tests/unit/reset-simulation.test.ts`、`web/tests/e2e/reset-research.spec.ts`（仅最小可收集/可编译骨架）。
  - 将 `test_task27_artifacts_remain_absent` 重构为**分阶段存在性契约**（包/文件出现即满足）；迁移 head、owner 路由挂载、EX surface 三组强断言暂保持原样，分别交由 Task 10/12/18 翻转。
- **Acceptance Criteria Addressed**: AC-15
- **Test Requirements**:
  - `rule` TR-1.1: `uv run pytest tests/test_reset_full_scope.py tests/test_reset_backtest.py tests/test_reset_research_validation.py -q` 全绿；research 包可导入；无新 ruff/compileall 错误。
  - `rule` TR-1.2: 前端占位测试文件可被 vitest/playwright 收集且不破坏既有套件；占位 tsx 不被任何生产模块 import（不进 bundle）。
- **Notes**: 不得提前创建 `0014_research.py` 或挂载路由；那两个断言在本任务后必须仍为红色方向的"缺席"约束。

## Task 2: 回测配置模型、有界校验与 config_hash

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 1
- **Completion Evidence** (2026-11-05)：
  - TDD RED→GREEN：models.py 空实现先得 ImportError RED；实现后 `tests/test_reset_backtest.py` 51 项（6 骨架 + 45 配置/哈希）全绿；三文件合计 62 passed/2 DB skip。
  - `research/models.py`：`_StrictFrozenModel`（strict/frozen/extra=forbid/allow_inf_nan=False）；CostModel（佣金/印花/滑点率显式有限正 Decimal + slippage_model/stamp_tax_side 闭集 + min_commission）、ExecutionModel、StrategyParameters、BacktestPeriod、WalkForwardSpec、DomainManifest/ManifestVersions/InputManifests（market 必填/域唯一）、ExperimentSpec（baseline 必填/腿唯一/种子 uint32）、BacktestConfig（strategy_id 白名单、walk-forward 窗口与周期跨度交叉校验）、BacktestSubmission（幂等键 UUID 且不入 hash）。
  - 集中常量：MAX_PERIOD_SESSIONS=250、MIN/MAX_PERIOD_SPAN_DAYS=2/400、MAX_TOP_N=100、调仓周期 1-60、folds 2-10、费率上界 0.01、min_commission 上界 100；COST_SENSITIVITY_MULTIPLIERS low0.5/base1/high2 固化（供 Task 4）。
  - config_hash：canonical JSON（sort_keys、compact、ensure_ascii、allow_nan=False）SHA-256 64hex；等价配置同 hash、7 类单字段变异均改 hash、幂等键不影响 hash。
  - 门禁：`ruff format`（models.py 重排）、`ruff check .` All checks passed、compileall OK。
  - **O4 相关**：配置有界常量已按保守值落地（见上）；worker 运行预算（wall-clock/计算量）留待 Task 10/11/13 标定后另行确认。
- **Description**:
  - `research/models.py`：不可变 pydantic 模型——`CostModel`（佣金/印花税/滑点/最小费，显式必填、有限正 Decimal）、`ExecutionModel`（下一可成交时点、涨跌停保守策略）、`StrategyParameters`（Top-N、调仓周期、权重=等权）、`WalkForwardSpec`（折数/窗口有界）、`ExperimentSpec`（baseline/ai_variant）、`BacktestConfig`（snapshot_id、strategy_id 白名单、period、input_manifests 声明、idempotency 外层不入 hash 的规则）。
  - canonical JSON（sort_keys、无 NaN、ensure_ascii）+ SHA-256 `config_hash`；`InputManifest` 绑定 market/topic/role 域版本、adjustment/calendar/pool 版本。
- **Acceptance Criteria Addressed**: AC-5, AC-6, AC-9
- **Test Requirements**:
  - `rule` TR-2.1: 非法/超界/缺字段/额外字段/NaN/Infinity/非有限成本一律 ValidationError；周期、证券数、折数上限生效（阈值集中常量）。
  - `rule` TR-2.2: 等价配置 hash 相同；任一字段变化 hash 变化；hash 为确定性 64 位 hex。
  - `rule` TR-2.3: ruff/format 零警告；模型 frozen/extra=forbid。

## Task 3: 交易规则纯函数内核（simulate_fill）

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 2
- **Completion Evidence** (2026-11-05)：
  - TDD RED→GREEN：execution.py 空实现先 ImportError；实现后三文件 85 passed/2 DB skip（新增 27 项：9 原因参数化 + 18 项方向/优先级/T+1/黄金样例/Decimal/raw 价）。
  - `research/execution.py`：`Order/Position/Fill/Rejection` strict frozen 模型 + `simulate_fill(order, bar, state, rules, cash, position)` 纯函数；无 IO/时钟；成交严格晚于 signal_date（违反抛 ValueError "next tradable session"）；bar/order security 不匹配抛错。
  - 拒单优先级固化：no_tradable_price → suspended → delisted → 涨跌停（复用 `analytics.metrics.closing_limit_hit` 的 up/down/none/unknown 口径）→ 卖出 total/available（insufficient_position 先于 t1_sell_blocked）→ 买入资金（含滑点+佣金保守足额）。9 类闭集原因各有确定性用例；limit-up 只挡买、limit-down 只挡卖；limit_date≠trade_date 与 limit_status=unknown 一律 unknown_limit_untradable。
  - 经济账 Decimal 定点：fill_price=raw close×(1±slippage) quantize 0.0001；gross quantize 0.01（ROUND_HALF_EVEN）；commission=max(比例费,min_commission)；印花税仅卖出；买 100@10 → 价格 10.0050/毛 1000.50/佣 5.00/净 -1005.50；卖 → 9.9950/999.50/5.00/1.00/993.50 黄金样例通过；adjustment_factor=1.5 不入成交价（N8）。
  - models.py 暴露公开 `StrictFrozenModel` 供内核模块复用（原内部别名统一替换）。
  - 门禁：ruff format（2 文件重排）、ruff check All passed、compileall OK。
- **Description**:
  - `research/execution.py`：`simulate_fill(order, bar, state, rules, positions) -> Fill | Rejection`，纯函数、Decimal、不触 IO/时钟。
  - 原因码集合：`suspended / limit_up_blocked_buy / limit_down_blocked_sell / t1_sell_blocked / insufficient_cash / insufficient_position / delisted / no_tradable_price / unknown_limit_untradable`。
  - 成交时点不早于信号日下一可成交交易日；raw 价格；未知涨跌停元数据按不可断言成交处理。
- **Acceptance Criteria Addressed**: AC-3, AC-4
- **Test Requirements**:
  - `rule` TR-3.1: 九类拒单各至少一个确定性用例，拒单不改变现金/持仓。
  - `rule` TR-3.2: T+1：当日买入份额当日卖出被拒；次日可卖。
  - `rule` TR-3.3: 涨停封死只拒买单、跌停封死只拒卖单；收盘等于限价位的判定复用 `closing_limit_hit` 口径。
  - `rule` TR-3.4: 全部运算 Decimal 定点（反测无 float 渗入），黄金样例手算成交金额/费税核对。

## Task 4: Decimal 账本、指标与成本敏感性

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 3
- **Completion Evidence** (2026-11-05)：
  - TDD RED→GREEN：ledger.py 占位先 ImportError；实现后三文件 91 passed/2 DB skip（Task 4 新增 6 项：账本恒等/负现金拒绝/T+1 冻结/黄金指标双口径/空流闭合/三档单调）。
  - `research/ledger.py` 纯函数、无 IO/时钟：`LedgerState(as_of, cash≥0, holdings)` 与 `Holding(quantity, available_quantity≤quantity)` strict frozen；`open_ledger / apply_fill / roll_overnight / mark_equity` 全部不可变返回新对象。买入增 quantity 不增 available（T+1 冻结），`roll_overnight` 统一解冻且 cash 不变，卖出同时扣减、quantity 归零即删键；卖超持仓/超可用/现金为负均 ValueError 兜底；`mark_equity` 对缺失或非正价格 fail-closed，绝不臆造价格。
  - 黄金往返（佣 0.0003/印花 0.001 卖/滑点 0.0005/min 5）：100000 → DAY1 买 100@10 后 cash 98994.50、mark(10.00)=99994.50 → DAY2 卖后清仓、cash 99988.00。
  - `summarize_run` 复用 `analytics.metrics`：total/benchmark 走 `interval_return`、excess 走 `excess_return`、max_drawdown 走 `maximum_drawdown`；比率 6 位、金额 2 位 ROUND_HALF_EVEN。黄金指标：total_return -0.000120、benchmark 0.000100、excess -0.000220、max_dd 0.000120、turnover 0.020000（Σgross/initial）、佣金 10.00、印花 1.00、总成本 11.00；不可成交笔数比 1/3=0.333333、金额比 500/2500=0.200000；benchmark_prices=None 时 excess 闭合为 0；空订单流/零分母一律 0.000000 不抛错。
  - `cost_sensitivity_models` 按 models.COST_SENSITIVITY_TIERS 顺序产出 low/base/high，等比缩放 commission/stamp/slippage/min_commission 四项（low 佣 0.00015/滑 0.00025/min 2.50，base 不变，high 佣 0.0006/印花 0.002），slippage_model/stamp_tax_side 沿用；同一信号流下三档 total_return 严格 low>base>high（TR-4.3）。
  - 修正 RED 测试内部矛盾：共享 `_round_trip_fills` 助手原本对三档成本硬编码 base 档 99994.50（数学上不可能），改为与档位无关的恒等式独立重算（初始现金+买净流+持仓市值）；base 档黄金值仍由黄金样例测试钉死。
  - 门禁：ruff format（2 文件重排）、ruff check All passed、compileall OK。
- **Description**:
  - `research/ledger.py`：cash/holdings/equity 日终结算、trades/rejections 流水、账本恒等式。
  - 指标：总收益、基准超额（复用 metrics）、正值最大回撤（复用）、换手率、成本总额、不可成交比例（订单笔数与金额双口径）。
  - 三档成本（低/基准/高）运行同一信号流的敏感性装配。
- **Acceptance Criteria Addressed**: AC-3, AC-4, AC-6
- **Test Requirements**:
  - `rule` TR-4.1: 任意订单流结算后每日 `cash + Σ holdings×close = equity`（含费税）；现金/持仓永不为负。
  - `rule` TR-4.2: 指标值与手算黄金样例一致；不可成交比例两口径分母定义明确且守恒。
  - `rule` TR-4.3: 三档成本产出三套净收益，成本越高净收益单调不增（同信号流）。

## Task 5: PIT 输入装配与防幸存者偏差

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 2
- **Completion Evidence** (2026-11-05)：
  - TDD RED→GREEN：pit.py 占位先 ImportError；实现后三文件 106 passed/2 DB skip（Task 5 新增 14 项）。
  - `research/pit.py` 纯函数、无 IO/时钟/DB：`PitAnchor/PitFrame/UniversePolicy/PitWindow/VisibleDay/VisibleSecurity/PitExclusion` strict frozen；`assemble_pit_window(frames, *, anchor, policy)` 把已由 IO 层（T11/13 经 `list_published_window`）装载的不可变发布序列装配为逐日可见信息集，输出按 security_id/码字典序确定，可逐字节序列化复现。
  - 双重 PIT 闸门：①帧级 publication_sequence ≤ anchor 且 trade_date ≤ anchor、必须 published、日期升序唯一、market/topic batch trade_date 与帧一致，违反一律 ValueError；②日内复用 `domain.quality.state_at`（effective 区间 + available_at≤batch.as_of + 版本一致 + listing_date≤当日）与 `bar_issues`（late_bar 等闭集原因码直通），题材成员再按 `membership.available_at ≤ topic.as_of` 过滤；晚到但回溯生效的成分记 `late_topic_member` 剔除并计入 quality。
  - 五日合成发布序列夹具（test_reset_backtest.py Task 5 段）集中七类风险：A 稳态/B 当日转 ST/S 停牌窗 D3-D4/DEL D4 退市标记 D5 消失/LATE 回溯成分 D4 才发布/L 当日晚到 bar/NEW 窗口后上市。逐日池子断言：D1-D2={A,B,S,DEL,L}、D3={A,DEL}、D4={A,DEL,L,LATE}、D5={A,L,S,LATE}；LATE D1-D3 late_topic_member、D4 起入池；DEL 保留至最后生效日且 D4 is_delisting=True、D5 unobservable_state；NEW 任何历史折零出现；L 的 D3 late_bar 剔除；D3 复权因子钉死决策帧版本（a1/1.0）。
  - TR-5.1 逐字节不变：在锚定帧之后注入 D3 复权因子事后修订（1.1）与 D6 未来成分，anchor 不变时整个 window 的 model_dump(json) 与基线完全一致。
  - 缺题材域降级：topic 过滤开启但帧 topic=None 时降级为全池并记 `topic_domain_unavailable`（A3：可选域缺失不拖垮整体）。
  - TR-5.3 失败闭合用例：anchor revoked、帧 revoked、帧 sequence>anchor、帧 trade_date>anchor、乱序/重复 trade_date、market trade_date 不匹配、空帧序列、251 sessions（>250 上限）均 ValueError，绝不截断。
  - 门禁：ruff check --fix（I001 导入排序 2 处）、ruff format、ruff check All passed、compileall OK。
- **Description**:
  - `research/pit.py`：以锚定 publication + `list_published_window` 构造逐调仓日"可见信息集"；按 publication_sequence 与 `available_at` 双重过滤；逐日 PIT 证券池（上市/退市/ST/停牌/题材成员）；复权因子按当时版本取值。
  - 新增合成多日发布序列夹具（未来新闻、复权事后修订、晚到成分、退市、ST 变更、停牌窗口、涨跌停封死）。
- **Acceptance Criteria Addressed**: AC-1, AC-2
- **Test Requirements**:
  - `rule` TR-5.1: 修改锚定序列之后的任何数据（新闻/价格/复权/成员），回测可见信息集与信号逐字节不变。
  - `rule` TR-5.2: 晚到成分不进历史池；退市证券保留至最后可交易日；窗口后上市证券零出现；ST/停牌按配置规则处理。
  - `rule` TR-5.3: 窗口超 250 sessions / 锚定非 published / 跨 sequence 读取均失败闭合。

## Task 6: 基线策略与 run_backtest 引擎

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 4, Task 5
- **Completion Evidence** (2026-11-05，commit `394c53b`)：
  - TDD RED→GREEN：先在 test_reset_backtest.py 追加 Task 6 段（15 项）确认 ImportError 失败；engine.py 初稿转绿。本文件现 76 passed；三文件回归（test_reset_backtest/test_reset_full_scope/test_reset_research_validation）**121 passed/2 skipped**（2 skip = 本地无 DRAGON_TEST_DATABASE_URL）。ruff check All passed、ruff format、compileall OK。
  - `research/engine.py` 纯函数内核（无 IO/时钟/随机）：`FactorPoint/FactorFrame/FactorPartition/RankedSecurity/StrategySignal/QualityEvent/DayQuality/RunVersions/CostSensitivityResult/BaselineLeg/BacktestResult` strict frozen；`run_backtest(config, frames, factor_frames, *, anchor, policy=None, benchmark_closes=None)` 先 `assemble_pit_window`，再逐日校验因子帧一对一对齐（日期/factor_id/metric/window/unit 闭集 ValueError），low/base/high 三档各跑同一信号流，base 腿产出 trades/equity/metrics，三档 metrics 入 baseline.cost_sensitivity。
  - 策略注册表：未知 strategy_id fail-closed；`factor-cross-sectional-topn-v1` 仅对 Decimal 因子值排序 `(-value, security_id)`，None→missing、bool→excluded，coverage expected=可见池大小且 expected=valid+excluded+missing（pydantic 闭集）；rebalance 日 `index % period == 0` 出信号，卖单（字典序）先于买单；等权、成本感知 board-lot（100 股） sizing 用信号日收盘价；T→T+1 经 state_at/bar 预检后 simulate_fill，停牌/退市/无状态/bar 异常直接构造九类闭集 Rejection。
  - 估值沿用上一可见收盘价并记 `mark_price_carried`；修复"决策日质量记录整体覆盖执行日 carry 事件"缺陷（同一 session 先作为 T+1 执行日记 carry、再作为决策日记 coverage，现在 carry 被保留）；末日若为决策日只记 `decision_without_next_session`，绝不交易。
  - 黄金旋转经济学（五日平价 X1-X4，D3 轮换 X3/X4）：6 笔成交各 4900 股，终值 equity 99666.82、total_return -0.003332、turnover 2.940490、total_cost 186.18；跨进程 subprocess 调 `golden_engine_result_json()` 逐字节一致（TR-6.1）。
  - 修正 RED 测试自身三处错误（先独立手算复核再改测试，非放宽契约）：①夹具 `_eng_dt` 缺 minute 参数、`board_category` 用了非法枚举值（改为 `synthetic_test`）；②涨停场景夹具未把收盘价设为涨停价（`closing_limit_hit` 要求 close==limit_up，改为 D2 X1 close=11）；③拒单双口径原钉 1/6 数学错误——X1 买入被拒后不持仓，D3 换仓无 X1 卖单，全运行共 5 笔 intended、1 笔不可成交，正确值 **0.200000**。
  - 版本骨架：结果 schema `dragon-radar.research-backtest/v1`、research_only=true、RunVersions 含 strategy/factor/sizing/execution/ledger/valuation/cost method 及三档乘数与 adjustment/calendar/pool 版本；folds=None、ablation=None 留待 Task 8/9（TR-6.2）。
  - models.py：新增 `ResearchFactorId`（FAC-R01/R02/R03/C01/C02）；StrategyParameters 首字段 `factor_id="FAC-R01"` 进 canonical_json/config_hash；既有 Task 2 测试全部关键字构造、无哈希字面值钉死，零回归。
- **Description**:
  - `research/engine.py`：策略注册表（首版 `factor-cross-sectional-topn-v1`：用已物化 FAC-R/C 因子截面排序选 Top-N、等权、固定周期换仓）；T 日信号→T+1 成交；组装 metrics/equity/trades/rejections/cost 敏感性；quality 汇总。
  - 无时钟、无网络、无随机（或固定种子）；输出 schema `dragon-radar.research-backtest/v1`。
- **Acceptance Criteria Addressed**: AC-5, AC-6
- **Test Requirements**:
  - `rule` TR-6.1: 同配置跨进程两次运行结果逐字段一致；未知 strategy_id 拒绝。
  - `rule` TR-6.2: 结果含 EX15 要求的 metrics/equity/trades/rejections/quality 骨架与全部版本字段、research_only=true。
  - `rule` TR-6.3: 因子缺数据时按 coverage 降级（剔除/降权有显式规则），不编造因子值。

## Task 7: 因子体检

- **Status**: `completed`
- **Priority**: medium
- **Depends On**: Task 6
- **Completion Evidence** (2026-11-05，commit `911bae2`)：
  - TDD RED→GREEN：先在 test_reset_backtest.py 追加 Task 7 段（10 项）确认 ImportError；实现后本文件 86 passed，三文件回归 **131 passed/2 skipped**。ruff（--fix 4 处后 All passed）、ruff format、compileall OK。
  - `research/health.py` 纯 Decimal（prec=34 ROUND_HALF_EVEN）：`assess_factor_health(config, frames, factor_frames, *, anchor, policy=None, min_sample_size=5, quantile_points=...)` 复用同一锚定 PIT 窗口；config 钉的 primary factor 必须存在，每个因子流必须与窗口逐日一对一对齐且 factor_id/metric/window/unit 恒定，否则 ValueError 闭合。
  - 覆盖率分区逐池逐日汇总（复用 engine.FactorPartition，expected=valid+excluded+missing），None/缺测点=missing、bool=excluded、Decimal=valid，池外点永不入分区；六位数精确比率。分位数 type-7 线性插值（h=(n-1)p）；Spearman 并列取竞赛平均秩，逐会话算 Pearson 后跨会话等权平均，零方差/单配对会话关闭。
  - 样本不足（valid < min_sample_size，阈值有界 [2,250]）：`status=insufficient_sample`、原因 `insufficient_sample_size`、quantiles=None，但分区计数与精确比率仍给出（事实非估计）；相关不足原因闭集 `insufficient_paired_sample / no_eligible_sessions`，无数值不臆造（TR-7.1）。
  - 手算黄金值（TR-7.2）：X1-X5 单日 [1..5] 分位 p=0/0.1/0.25/0.5/0.75/1 → 1/1.4/2/3/4/5；A=[1..5] vs B=[5,3,3,2,1]（并列秩 3.5）Spearman = -9.5/√95 = **-0.974679**；常数列零方差关闭；三因子对按字典序 (FAC-C01,FAC-R01)/(FAC-C01,FAC-R02)/(FAC-R01,FAC-R02)。
  - 报告 schema `dragon-radar.research-health/v1`、research_only=true、window_start/end、sessions、anchor_sequence、manifests（来源版本）、逐因子 metric/window/unit；同输入 model_dump_json 逐字段一致。models.py 新增 RESEARCH_HEALTH_SCHEMA_VERSION 常量。
- **Description**:
  - `research/health.py`：样本量、缺失率（FAC coverage 分区）、分布分位数（Decimal）、因子间 Spearman 秩相关矩阵；输出版本/窗口/来源。
- **Acceptance Criteria Addressed**: AC-7
- **Test Requirements**:
  - `rule` TR-7.1: 覆盖率分区 expected=valid+excluded+missing 守恒；样本不足给原因无数值。
  - `rule` TR-7.2: 秩相关以已知小样本手算值（含并列秩）校验；分位数与手算一致。

## Task 8: walk-forward 滚动验证与五分类条件调权

- **Status**: `done`
- **Priority**: high
- **Depends On**: Task 7
- **Description**:
  - `research/walk_forward.py`：折切分（训练/测试严格不重叠、覆盖测试期）、训练段拟合阈值/权重并冻结、测试段评价、逐折与聚合样本外指标。
  - 条件调权：调仓日按 PIT 发布读取 EX16 phase（冰点/退潮/分歧/修复/高潮）；调权映射仅用训练折状态拟合；phase 缺失降级不调权并记 quality。
- **Acceptance Criteria Addressed**: AC-8
- **Test Requirements**:
  - `rule` TR-8.1: 任意相邻折训练/测试不重叠；断言参数在测试段不可变（改测试段数据参数不变）。
  - `rule` TR-8.2: phase 取值全部来自不晚于调仓日的发布；未来 phase 不影响历史折。
  - `rule` TR-8.3: phase 缺失日不调权且 quality 记录原因，不产生臆造状态。
- **Completion Evidence** (2026-11-05, commit `eb4f5f9` on `feature/task27-research`):
  - 新增 `src/dragon_radar/research/walk_forward.py`（schema `dragon-radar.research-walk-forward/v1`，layout `walk-forward-anchored-v1`）：共享训练前缀 sessions[0,T) + K 个严格相邻不重叠、铺满 [T,T+KS) 的测试折；逐折独立 100000 起，另有一条覆盖拼接测试期的聚合 OOS 运行；`FoldResult/WalkForwardReport` 等均为 StrictFrozenModel，权重必须按 PHASES 顺序覆盖五相。
  - 冻结拟合 `fit_regime_map` 仅用训练段内决策日（index%period==0 且 ≤T-2）；`m_p`=当日可见证券等权池次日简单收益均值（6 位 HALF_EVEN，prec=34），`weight_p=clamp(1+m_p,0.5,1.0)` 只降险；未见相位单位权重 + `unobserved_in_training`。
  - PIT 闸门 `phase_as_of` 取 `trade_date<=day 且 publication_sequence<=当日帧序号` 的最新一条；晚发布/未来发布不可达（TR-8.2）；`validate_phase_publications` 对 duplicate/order/anchor 及非法 phase fail-closed。
  - 缺失/未拟合降级单位权重并记 `regime_phase_missing`/`regime_unobserved_in_training`（TR-8.3）。
  - `engine.run_backtest` 双向校验 spec↔phase 序列（空 tuple 允许），函数体内惰性 import `run_walk_forward`（避免循环导入），regime weight 仅缩放目标预算；非 walk-forward 结果保持 folds/walk_forward=None，Task 6 黄金结果与跨进程字节对拍不变。
  - 10 个 wf 测试钉死折切分不重叠/铺满、冻结黄金均值（ice -0.100000/0.900000、repair +0.050000/1.000000 封顶、晚发布 ice -0.025000/0.975000）、OOS 数量缩放（4400/4800/4900）、测试段变异不影响冻结参数、晚发布隔离、诚实降级、确定性与闭集校验。
  - GREEN 期独立复核修正两处测试侧问题：list==tuple 比较改为 list；标准相位夹具补 d4 ice@500（此前误假设 ice 跨 d2 repair 持续到 d4，与 FR-12「取当时可见最新发布」冲突）；并修正 `_run_wf(())` 误绑 closes_by_day 为 `_run_wf(phases=())`。
  - 验证：`pytest -k wf` 10 passed；backtest+research_validation+sentiment_research 202 passed/2 skipped；全量 2816 passed/1285 skipped/1 deselected；`ruff check .` All checks passed；三文件 `ruff format` 后复跑全绿；`compileall src tests` exit 0。

## Task 9: AI 增益对照框架（disabled 闭合）

- **Status**: `done`
- **Priority**: medium
- **Depends On**: Task 8
- **Description**:
  - `research/ablation.py`：baseline/ai_variant 同池同窗同成本同种子的实验装配；无冻结真实评测样本时 ai_variant 返回 `status=disabled, reason=real_evaluation_inputs_unavailable`；不接入任何模型推断作为收益腿。
- **Acceptance Criteria Addressed**: AC-11
- **Test Requirements**:
  - `rule` TR-9.1: disabled 时无 ai 收益数字产出；baseline 腿不受影响。
  - `rule` TR-9.2: 两条腿的输入池/窗口/成本/种子逐字段相同（冻结对照装配的接口契约测试）。
- **Completion Evidence** (2026-11-05, commit `af085d0` on `feature/task27-research`):
  - 新增 `src/dragon_radar/research/ablation.py`（schema `dragon-radar.research-ablation/v1`）：`AblationLegInputs`（snapshot_id + input_manifests 池 + period 窗 + cost_model + random_seed，StrictFrozenModel）、`AblationLeg`、`AblationReport` 与 `build_ablation_report`。
  - ai_variant 腿固定 `status=disabled, reason=real_evaluation_inputs_unavailable`；模型级校验禁止 disabled 腿携带 metrics（结构上无 equity/trades/uplift 字段），completed 腿必须有 metrics 且不得带 reason（TR-9.1）。
  - 两条腿共享同一个不可变 inputs 实例；`AblationReport` 校验 baseline=completed、ai_variant=disabled 且两腿 inputs 逐字段相等，否则 ValidationError（TR-9.2 接口契约在模型层 fail-closed）。
  - `engine.run_backtest` 惰性装配：仅当 `config.experiment.legs` 含 ai_variant 时构建 report；baseline-only 实验 `ablation=None`；baseline 经济结果与成本敏感性腿不变。
  - 翻转 Task 6 `test_result_carries_versioned_research_only_skeleton` 的 ablation 断言为 disabled 结构。
  - 验证：新增 7 个测试（disabled 无数字/同池同窗同成本同种子/baseline-only 缺席/模型闭集/输入不匹配拒绝/确定性）；研究三文件 209 passed/2 skipped；全量 2823 passed/1285 skipped/1 deselected（跨进程字节对拍仍相等）；`ruff check .` All checks passed；`ruff format` 后复跑全绿；`compileall src tests` exit 0。

## Task 10: 迁移 0014_research（runs + cache 表、cancelled 状态）

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 2
- **Completion Evidence** (2026-11-05)：
  - TDD 真实 PG 17（`dragon-radar-test-pg`，conftest 一次性 schema 自动 DROP）：迁移 head/ready/冻结契约由 0013 翻转为 0014；新增 39 个 Task 10 PG 用例（参数化展开），先跑出缺关系/`ready()` False/绑定参数 3 类红，再补齐实现与测试修正转绿。
  - `0014_research.py`（parent `0013_query`）+ 独立 SA 模块 `research/tables.py`（纯内核 `research/models.py` 保持零 SA 依赖）。
  - `research_backtest_runs`：owner+idempotency 唯一；request_hash/config_hash 64hex regex CHECK；owner_id/snapshot_id 双 FK ON DELETE RESTRICT；五态互斥大 CHECK `research_backtest_runs_state`（cancelled = 租约/result/failure 全 NULL 且 completed_at NOT NULL）；attempt_count≥0；`max_wall_clock_ms` BigInteger≥0 保守预算列（**不硬编码阈值**，O4 上限留 Task 11/13 标定）；claim 部分索引（queued/running）。行级 BEFORE UPDATE 守卫冻结 9 个输入列（RAISE `research backtest inputs are immutable`），仅调度/租约/lifecycle/result 列可变。
  - `research_backtest_cache`：config_hash 唯一；语句级 UPDATE/DELETE 与 TRUNCATE 触发器复用 0001 的 `reject_immutable_mutation()`（`immutable records cannot be changed`）。downgrade 抛 `Research backtest history cannot be downgraded`。
  - release manifest：head `0014_research`、22 revisions；0014 指纹 sha256 `2bf6fe5d…0abe`/1954 bytes（LF 规范化，与 git blob 校验一致）；verifier sentinel 新增 0014 基线（插 draft publication+queued run，supported upgrades 后 preserved）。
  - schema_contract：真实 PG17 重生成，**纯追加 558 行**（新增 2 表/13 约束/5 索引/3 触发器；全库 86 表/460 约束/225 索引/185 触发器），无任何既有条目改动。
  - 门禁（真实 PG，DB 门非 skip）：`test_reset_backtest.py` 139 passed；reset 五文件（backtest/full_scope/regulation/ai_tasks/lhb）614 passed（655s）；`test_release_migrations.py` 193 passed / 3 结构性 skip（POSIX 进程组×1 + 符号链接权限×2）/ 1 deselected（182s）；全量 `tests` 4130 passed / 14 skip（POSIX×2、symlink×2、opt-in TLS×10）/ 1 deselected（2903s）。
  - 全量唯一失败 `test_owner_cli.py::test_owner_pair_file_and_stdout_no_credentials` 已定位为**本机环境 harness 问题**：PSModulePath 混入 PS7 模块目录，导致重定向句柄启动的 powershell 5.1 子进程双重加载 Types.ps1xml，`Microsoft.PowerShell.Security` 无法自动加载（Get-Acl 为空且退出码仍 0）；净化 PSModulePath 仅保留 5.1 路径后该测试 6.17s 通过；落盘 SDDL 始终正确（`D:P` 仅 SYSTEM+owner）。与 Task 10 无代码交集。
  - 本机 Windows 环境注记：`localhost` 优先解析 `::1` 而 Docker 仅转发 127.0.0.1，每个新连接白等约 5s（表现为零 CPU 假死）；所有 PG 门禁须用 `127.0.0.1` 的 `DRAGON_TEST_DATABASE_URL`。
  - 测试修正：2 处 JSON 字面量 `:true` 被 `text()` 误当绑定参数 → 改 `jsonb_build_object('changed', true)`；清理 F401/E501。`ruff check .` All passed、289 files already formatted、compileall OK。
  - 提交：`c6cf917`（migration+tables+database+manifest+verifier+测试）、`999785e`（真实 PG 重生成 schema contract）。
- **Description**:
  - `migrations/versions/0014_research.py`（parent `0013_query`）：`research_backtest_runs`（owner 隔离、idempotency/request_hash/config_hash、snapshot_id FK、input_manifest JSONB、status 含 cancelled、租约三列、预算列、result/failure、时间戳；状态 CHECK 覆盖五态互斥）、`research_backtest_cache`（config_hash 唯一、全不可变）；触发器仿 0007c（输入列不可变 + cache 禁止改写）；downgrade 抛 RuntimeError。
  - 更新 `SCHEMA_REVISION`、`storage/schema_contract.json`、`migrations/release_manifest.json`；翻转迁移 head 契约断言为 `0014_research`。
- **Acceptance Criteria Addressed**: AC-10, AC-15
- **Test Requirements**:
  - `rule` TR-10.1: 真实 Postgres upgrade 成功且为唯一 head；尝试更新身份/输入列被触发器拒绝；cache 表 UPDATE/DELETE/TRUNCATE 被拒。
  - `rule` TR-10.2: 五态 CHECK 合法；非法状态转移被数据库拒绝；schema_contract/manifest 测试全绿。

## Task 11: research run repository（幂等/隔离/租约/取消/预算）

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 10, Task 6
- **Completion Evidence** (2026-11-11，worktree `.worktrees/task27-research`，head `7575b8d`，ahead 13)：
  - 严格 TDD（真实 PG 17 `dragon-radar-test-pg`，conftest 一次性 schema）：先在 `tests/test_reset_backtest.py` 末尾追加 Task 11 段（20 个测试函数、参数化展开 24 用例），首跑真实 RED 为 24 errors 全是 `ImportError: cannot import name 'ResearchRunRepository'`（exit 1，139 deselected）；再补实现转绿。
  - `research/repository.py`（占位 7 行 → 577 行，提交统计 +575/-5）：`ResearchRunRepository` 仿 `analysis_jobs/repository.py`——授权先行（`owners.authorize_connection` + CSRF 懒加载 `CsrfForbidden`）、调用方 READ COMMITTED 事务校验、`pg_advisory_xact_lock(hashtextextended(:key,0))`（键 `research-backtest:{owner_id}:{idempotency_key}`）、同 owner+键查现有行（`request_hash` 不同抛 `Conflict` 409、相同原样回放）、`research_backtest_cache.config_hash` 命中直接插 succeeded 行复用不可变结果、否则 queued；`claim` 用 `for_update(skip_locked=True).limit(1)`，attempts 耗尽落 failed `{"code":"attempts_exhausted"}`；`renew`/`_active_lease` 租约校验；`succeed` 用 `pg_insert.on_conflict_do_nothing(config_hash)` 后回读权威 result；`fail`/`cancel`；记录模型 `ResearchRunRecord`/`ResearchRunLease` 继承 research `StrictFrozenModel`，IO 模型置于 repository 保持 `models.py` 纯计算内核。
  - 四项 O4 设计决策（用户审批）：① wall-clock 预算 `DEFAULT_MAX_WALL_CLOCK_MS = 300_000`（5 分钟）模块常量，owner 不可传值，submit 时不可变写入 `max_wall_clock_ms`；② 计算量预算复用 `models.py` 既有有界常量（MAX_TOP_N=100、MAX_PERIOD_SESSIONS=250、folds 2–10 等），repository 不另设运行时闸门，结构化失败码 `budget_exceeded` 留 Task 13 worker 接线；③ running 取消由 owner 直接置位（`cancel()` 置 cancelled、清租约三列、写 completed_at，五态 CHECK 允许无需改表），worker 下一检查点经 `_active_lease` 发现租约失效即收手不写 result；④ `request_hash` 与 `config_hash` 同值（config canonical JSON 的 SHA256，幂等键不入 hash）。沿用默认：`lease_for=30s`（校验 1s–1h）、`max_attempts=3`（1–10）。
  - 关键坑：JSONB 往返后 UUID→str、date→str、tuple→list，strict 模型 `BacktestConfig.model_validate(dict)` 拒绝；`_record()` 改用 `model_validate_json(json.dumps(...))` 的 JSON 模式强制转换（同 analysis_jobs 范式），`succeed()` 直接取 `input_manifest.config.schema_version` 作 cache `engine_version`，不完整反序列化 config。
  - 门禁（真实 PG，DB 门非 skip，URL 强制 127.0.0.1 规避 ::1 假死；运行前净化 PSModulePath 仅留 PS5.1 路径）：Task 11 子集 24 passed / 139 deselected（35.31s）；`test_reset_backtest.py` 全文件 163 passed（84.35s，零回归）；定向四文件（full_scope/backtest/research_validation/analysis_jobs）276 passed（146.83s）；**全量 `pytest -q` 4155 passed / 14 skipped（POSIX×2、symlink×2、opt-in TLS×10）/ 1 deselected，2412.36s（40:12），exit 0**；收集口径父提交 `999785e` 可运行 4145 → HEAD 4169，净增恰好 24，与新增用例数一致。
  - `ruff check .` All checks passed（`ruff format` 2 files reformatted，修 E501×4 + I001×1 后复跑 24 passed 无语义变化）；`compileall -q src tests` exit 0。未改 schema（0014 表/触发器/契约零改动），未改 `research/__init__.py` 包面（留 Task 12），`worker.py` 仍占位（Task 13）。
  - 提交：`074bdad` feat(research): owner-scoped backtest run repository with leases and cancel（1 file +575/-5）、`7575b8d` test(research): Task 11 repository lifecycle suite on real PostgreSQL（1 file +683）。
- **Description**:
  - `research/repository.py`：仿 AnalysisJobRepository——授权先行、advisory lock、幂等重放/冲突、config_hash 缓存复用、claim/renew/succeed/fail；新增 `cancel(owner, run_id)`（仅 queued/running 可取消；running 由 worker 到检查点落 cancelled）、预算列与超限失败。
- **Acceptance Criteria Addressed**: AC-9, AC-10
- **Test Requirements**:
  - `rule` TR-11.1: 同键同配置重放返回原 run；键同配置不同 → 409；跨 owner 访问 → 404。
  - `rule` TR-11.2: queued 取消立即落 cancelled；running 取消在检查点生效；succeeded/failed 取消被拒；cancelled 不产出 result。
  - `rule` TR-11.3: 租约过期可重新 claim（次数上限）；预算超限落 failed 结构化原因且无半成品结果。

## Task 12: EX14/EX15 HTTP 路由与 owner 装配

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 11
- **Completion Evidence** (2026-11-12 落盘，worktree `.worktrees/task27-research`，head `791332a`，ahead 15)：
  - 严格 TDD（真实 PG 17，URL 强制 127.0.0.1、运行前净化 PSModulePath 仅留 PS5.1 路径）：先在 `tests/test_reset_backtest.py` 末尾追加 Task 12 段（9 个测试函数、参数化展开 15 用例），首跑真实 RED 为 **15 failed / 163 deselected（22.14s，exit 1）**——14 个 `TypeError: create_owner_app() got an unexpected keyword argument 'research_repository'` + 1 个 `State` 无 `research_repository` 属性，全部因功能缺失失败、无 typo。
  - 契约翻转（同 RED 阶段）：`tests/test_reset_full_scope.py` 五处冻结断言——EX14/EX15 元组由 `("27","none",None,("docs/acceptance/reset-final.md",))` 翻为 surface `"owner"`、路由 `/api/research/backtests` 与 `/api/research/backtests/{run_id}`、证据 `tests/test_reset_backtest.py`；`BLOCKED_EX` 由 `{"EX14","EX15"}` 改 `set()`（联动函数改名 `..._with_no_blocked_surface`）；owner app 测试翻转为断言两 research 路由存在且 POST/GET 方法正确（函数改名 `..._ex01_03_12_15_including_task27_research_routes`）；`TASK27_LATER_ARTIFACTS` 移除 `http/routes/research.py`；同步模块 docstring 与 workbench 注释。翻转后单文件验证 **1 failed（仅路由挂载，实现缺失）/ 13 passed**，翻转点自洽。workbench 泄漏否定断言（含两 research 路径）、`PRIVATE_PREFIXES` 含 `/api/research`、public 无 POST、B01/B02 blocked 全部保持不变。
  - GREEN 新增 `src/dragon_radar/http/routes/research.py`（164 行，仿 `analysis_jobs/routes.py` + `http/routes/ai.py` 范式）：自定义 `_ResearchRoute(APIRoute)` 归一 `RequestValidationError`/pydantic `ValidationError`/`Forbidden`/`NotFound`/`Conflict`/`SQLAlchemyError`/`StorageUnavailable`/`ValueError`/`TypeError`/`BoundaryError` → 固定信封（400 INVALID_PARAM / 403 FORBIDDEN / 404 NOT_FOUND "Resource not found" / 409 VERSION_CONFLICT / 503 STORAGE_UNAVAILABLE）；POST 走 `json_object` EX14 白名单（10 个 BacktestConfig 字段含 `schema_version`/`walk_forward` + `idempotency_key`，共 11 键），`BacktestConfig.model_validate_json(json.dumps(...))` 在 strict/extra-forbid 模型上重建 UUID/date/tuple，再组 `BacktestSubmission` 交 `repository.submit(token, csrf, ...)`，响应仅 `{run_id, status}`；GET 为轻量物化读、绝不重算，返回六键信封 `{run_id, status, config_hash, research_only=true, result, failure}`。`owner/app.py`（+19 行）：签名加 `research_repository` kwarg、纳入 `owned` None 判定元组、从注入实例推导 owner/publication、database 解析链加 fallback、database 存在时自动构造 `ResearchRunRepository(database, owner_repository, publication_repository)`、`app.state.research_repository` 注入、在 analysis 与 ai 路由之间 `install_research_routes(app)`；workbench/public 应用零 research state 与路由。
  - 排查并解决三个真实问题：① 无会话 POST 若不带 Origin 会被 OwnerGate 的 Origin 门先拦成 403，按 `test_owner_api.py` L480-485 既有浏览器形态约定补 `Origin` 头后正确到达会话检查返回 401（GET 无会话原生 401 不受影响）；② 本机 FastAPI 的 `RequestValidationError` 继承独立 `fastapi.exceptions.ValidationException(Exception)`，**不**继承 pydantic `ValidationError`，路由类必须双捕获；③ `ResearchRunRecord` 的 `ImmutableJsonObject` 经 pydantic `AfterValidator` 深度冻结为 `MappingProxyType`/tuple，simplejson 不识别而抛 `TypeError: Unsupported response value (mappingproxy)`，由中间件兜底成 503；在路由传输层加 `_jsonable()` 深度还原 Mapping→dict、tuple/list→list，标量原样不动（Decimal 字符串保持字符串，值语义零改动）。
  - 门禁（真实 PG，DB 门非 skip）：两文件 GREEN **192 passed（110.21s，exit 0）**；`ruff check .` All checks passed；`ruff format` 首次 2 files reformatted（research.py + test_reset_backtest.py 纯换行归一），复跑 290 files already formatted；`compileall -q src tests` exit 0；定向四文件（test_reset_backtest/test_reset_full_scope/test_owner_api/test_analysis_jobs）**348 passed（261.27s，exit 0）**；**全量 `pytest -q` 4170 passed / 14 skipped（POSIX×2、symlink×2、opt-in TLS×10）/ 1 deselected，2075.74s（34:35），exit 0，零失败**；计数核账：Task 11 基线 4155 + 本任务新增 15 用例 = 4170，精确一致。
  - 边界：未改 schema（0014 表/触发器/schema_contract 零改动）；未暴露 HTTP cancel（`repository.cancel` 保留给后续任务）；GET 无重算满足 P95≤2.0s 轻量读契约；零 skip；`.trae/` 仅在主仓落盘、未入 worktree 提交。
  - 提交：`1cf5be5` test(research): Task 12 EX14/EX15 owner HTTP contract suite（2 files +449/-17）、`791332a` feat(research): owner-only EX14/EX15 backtest HTTP routes（2 files +183，含新建 research.py 164 行）。
- **Description**:
  - `http/routes/research.py`：`install_research_routes`；POST 字段白名单（EX14 全集）+ OwnerWrite/CSRF + 有界 body；GET OwnerRead + principal 隔离；错误归一（400/403/404/409/503）；EX15 完整响应（Decimal 字符串、research_only）。
  - owner/app.py 装配与 state 注入；翻转 owner 路由契约与 workbench/public 泄漏否定断言；EX 注册表 EX14/EX15 surface 改 owner、路由与证据路径登记。
- **Acceptance Criteria Addressed**: AC-9
- **Test Requirements**:
  - `rule` TR-12.1: owner 应用含两条路由；workbench 只读应用与 public 应用均无；全表扫描无 POST 泄漏。
  - `rule` TR-12.2: 无会话/错 CSRF/越权/白名单外字段/超界参数的 HTTP 用例码正确；成功响应含 EX15 全字段与 research_only=true。

## Task 13: worker 接线（研究回测异步执行）

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 12, Task 9
- **Completion Evidence** (2026-11-12 落盘，worktree `.worktrees/task27-research`，head `4784e4f`，ahead 17)：
  - 严格 TDD（真实 PG 17，URL 强制 127.0.0.1、运行前净化 PSModulePath 仅留 PS5.1 路径、pycache 重定向 `.temp/`）：RED 在 `tests/test_reset_backtest.py` 末尾追加 Task 13 段（Part A 纯内核 5 函数 + Part B 真实 PG worker 16 用例，共 21 个净新增用例），并在 `tests/test_analysis_jobs.py` **原地翻转**既有 CLI 契约测试 `test_analysis_worker_command_constructs_dependencies_and_runs`（净新增 0，仅改断言）。两次真实 RED 运行均为 **22 failed / 243 deselected（exit 1）**——选择器命中 22 项 = 21 个新增 + 1 个原地翻转；根因全部为功能缺失（`run_backtest()` 无 checkpoint 形参、worker 空壳 ImportError/AttributeError、`cli` 无 `threading` 属性），无一笔断言级失败、无 typo；RED 阶段 ruff 已 All checks passed。
  - GREEN 五个实现文件：① `research/engine.py`（+23/-1）新增 `CheckpointFn`/`_noop_checkpoint`，`_simulate` 与 `run_backtest` 加 keyword-only 默认 no-op 钩子，每个决策迭代顶部触发，默认路径结果字节一致；② `research/walk_forward.py`（+18/-2）折边界 + fold/aggregate 两个 `_simulate` 全透传钩子；③ `research/repository.py`（+32/-0，**零 schema 改动**）新增只读 `check_lease(lease)`（begin 块复用 `_active_lease`，失活抛 Conflict）与系统级无授权 `fetch_run(run_id)`（NotFound 兜底）；④ `research/worker.py`（docstring 空壳 → 342 行）实现 `ResearchBacktestWorker`（claim→授权 PIT load_input→静态计算量预检→holder 闭包 checkpoint（先 wall-clock 比对不可变 `max_wall_clock_ms` 再 `check_lease`）→ThreadPoolExecutor 心跳（lease/3 超时 renew，Conflict 即 break）→future 异常分路结构化 fail / `_finish_or_rehydrate` 绝不写死 token），含 `ResearchRunCancelled`/`ResearchInputsUnavailable`/`ComputeBudgetExceeded`/`ResearchBudgetExceeded`、纯函数 `assert_compute_budget`、恒 fail-closed 的 `fail_closed_research_compute`，`run_forever` 严格对齐 AnalysisJobWorker（空队列才 sleep、KeyboardInterrupt 静默返回），`__all__` 7 名齐全；⑤ `owner/cli.py`（+26/-2）顶层 `import threading`（模块属性访问以支持 monkeypatch）+ research 双类导入，analysis-worker 分支按冻结序列装配 `ResearchRunRepository`→两 worker→名为 `research-backtest-worker` 的 daemon 线程（`kwargs={"poll_seconds":...}`）→主线程保留 analysis 循环；parser 零新增、compose 零改动、无新容器/新依赖（NFR-6）。
  - GREEN 期按真实报错最小修正、不扩张范围：① `except ... as error` 绑定名在块退出被 Python 隐式删除，lambda 闭包引用触发 ruff F821/F841 → 在 except 块内冻结 `payload` 普通局部变量再由 finish 钩子闭包；② `from models import 常量` 的导入期绑定使 monkeypatch 常量漂移不可见（DID NOT RAISE）→ 改为 `from dragon_radar.research import models as research_models` 经模块属性在调用时读取（对齐代码库既有 `_owner_models` 别名范式）；③ RED 时序笔误：stale-succeed 用例接管租约在 T10_NOW+21s 才过期，第二次 claim 前须把仓库时钟推进到 +22s；④ 最大合法 walk-forward 需 50+10×20=250 会话（=模型 cap），默认 172 天夹具周期承载不了 → period 拓宽到恰好 250 天；⑤ 真实引擎用例原在 compute 内另造夹具 config 致 `config_hash` 与提交不一致 → 改为提交与引擎同形 config、compute 直接跑 run 自身 config，保证 hash 可追溯（NFR-1）；⑥ JSONB 水合把结果冻结为 tuple/mappingproxy（ImmutableJsonObject），`isinstance(..., list)` 不成立 → equity 断言改为形状 + 交易日内容校验。
  - 门禁（真实 PG，DB 门非 skip，均取真实退出码）：RED 选择器转 GREEN **22 passed / 243 deselected（30.05s，exit 0）**；`ruff check .` All checks passed；`ruff format` 归一 3 个改动文件（cli/worker/test_reset_backtest，纯换行）后复跑 **290 files already formatted**；`compileall -q src tests` **exit 0**；定向四文件（test_reset_backtest/test_analysis_jobs/test_owner_api/test_reset_full_scope）**369 passed（339.99s，exit 0）**；**全量 `pytest -q` 4191 passed / 14 skipped（POSIX×2、symlink×2、opt-in TLS×10，结构性项不变）/ 1 deselected，2266.94s（37:46），exit 0，零失败；全日志扫描无 failed/error/exception/traceback**。计数核账：Task 12 基线 4170 + 本任务净新增 21 = **4191 精确一致**（RED 选择器的第 22 项是原地翻转的既有 CLI 测试，净新增 0；定向四文件 348→369 同为 +21，两处口径互证）。
  - 边界：未改 schema（无迁移/触发器/schema_contract 变更，AC-10 迁移面由 Task 11 已闭合的 0014 承担）；内核保持纯函数零 IO/时钟（钩子默认 no-op）；未暴露 HTTP cancel；不新增容器/依赖/CLI 选项（NFR-6）；回测仅在异步 worker 运行、双预算闸门 + 协作式检查点取消、无线程强杀（NFR-2）；`.trae/` 仅主仓落盘、未入 worktree 提交；发布边界约束有效（不 merge/tag/push/发版/部署）。
  - 提交：`2efd9e1` test(research): Task 13 worker lifecycle, budgets and checkpoint contract（2 files +742）、`4784e4f` feat(research): Task 13 lease-driven research backtest worker（5 files +441/-10）。
- **Description**:
  - `research/worker.py` 并在现有 analysis-worker 进程加入 research claim 循环（不新增容器）：load PIT 输入 → run_backtest → heartbeat renew → succeed/fail；在折间/调仓日设协作式取消检查点；wall-clock 与计算量预算闸门；异常结构化失败。
- **Acceptance Criteria Addressed**: AC-10
- **Test Requirements**:
  - `rule` TR-13.1: 端到端 queued→running→succeeded；取消在最近检查点落 cancelled；心跳防止长任务租约过期。
  - `rule` TR-13.2: 预算超限/引擎异常落 failed 且 result 为空、重试次数有上限；失败不影响后续 run claim。

## Task 14: 前端 IDB v5 simulation repository

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 4
- **Completion Evidence** (2026-11-12 落盘，worktree `.worktrees/task27-research`，head `ec0fced`，ahead 19)：
  - 严格 TDD（fake-indexeddb 6.2.5 + vitest 4，零新增依赖）：RED 仅改 `web/tests/unit/reset-simulation.test.ts` 一个文件，7 个 describe、44 个测试函数 / 52 个用例（九类拒单 it.each 展开），覆盖 TR-14.1（同键同摘要重放不增修订/异摘要冲突/stale revision 拒绝/回读失败不留半截）、TR-14.2（v4→v5 升级既有 notes/watchlist 完整、v5 旧库重开不报错）、TR-14.3（黄金样例端口对账）。两次客观失败证据：定向 vitest 集合期报 `Cannot find module '../../src/repositories/local/simulation'`（execution/ledger 连带缺失），exit 1；`tsc --noEmit` 恰好 3 个 TS2307（simulation/execution/ledger 三模块），无其他类型错误；全量 unit 33 文件通过 / 1 文件红，1025 既有断言全过（4.88s），零连带破坏。
  - GREEN 四个实现文件（+746/-23）：① `features/research/execution.ts`（247 行，纯函数零 IO/时钟）`D = Decimal.clone({precision:34, rounding:HALF_EVEN})` 独立上下文，不污染全局 decimal.js 默认 ROUND_HALF_UP（测试钉死 D.precision=34、D.rounding=HALF_EVEN、Decimal.rounding=HALF_UP）；金额量化为字符串输出（价 4 位、钱 2 位，HALF_EVEN）；导出 simulateFill/closingLimitHit/REJECTION_REASONS（9 字面量按后端声明序：suspended / limit_up_blocked_buy / limit_down_blocked_sell / t1_sell_blocked / insufficient_cash / insufficient_position / delisted / no_tradable_price / unknown_limit_untradable），Fill/Rejection 判别联合（`kind:'fill'|'rejection'`），拒单优先级与 `research/execution.py` 逐行对齐，security 不匹配与 trade_date≤signal_date 抛 Error；② `features/research/ledger.ts`（102 行）openLedger（负现金抛错）/applyFill（买增 quantity 不增 available（T+1 冻结）、卖校验总量与可用、清零删键、cash 负抛错）/rollOvernight（available=quantity、cash 不变）/markEquity（缺价/非正价 fail-closed），持仓量 toFixed(0) 字符串，全程返回全新不可变对象；③ `repositories/local/simulation.ts`（366 行）SIMULATION_STORES 四 store + createSimulationStores 逐个 `objectStoreNames.contains` 守卫（同一升级事务可重复调用），keyPath 全部以 local_profile_id 领衔复合键（accounts/orders/fills/commands）；SimulationRepository 经 DI 注入 LocalRepository、不自开连接——共享 `local.connect()` 的 v5 连接与 `local.profile()` 分区；openAccount/submitOrder/getAccount/listOrders/listFills 复刻 v4 putNote 范式：SHA-256 payload digest → commands 命中则同摘要重放/异摘要抛幂等冲突、expected_revision 不符抛版本冲突、单 readwrite 事务跨四 store 读改写、await done 后新 readonly 事务 JSON.stringify 全等回读（不符抛含"回读"错误）、catch 内 tx.abort() 并吞 done rejection；成交路径 revision+1 写 fill/account/order/command，拒单路径 order 落 rejected+reason、不写 fill 不动 cash/revision 但 command 仍记录；④ `repositories/local/repository.ts`（+31/-23）`indexedDB.open` 版本 4→5，onupgradeneeded 重构 if/else 后末尾无条件增量挂四 store（oldVersion=0 与升级路径均覆盖，v1-v4 迁移逻辑原样保留、v4 数据零改动），新增公开 `connect()`；simulation 侧仅 `import type { LocalRepository }`，运行时依赖单向 repository→simulation→execution/ledger，无环。
  - GREEN 期按真实报错最小修正、不扩张范围：① RED sameDaySell 笔误——signal_date=FILL_DAY 且 bar 同日触发"trade_date≤signal_date 抛错"而非 t1 拒单，改回默认 SIGNAL_DAY（<FILL_DAY）+ position('100','0') 并显式断言 reason=t1_sell_blocked；② quota 用例断言过宽——失败下单回滚后误要求 commands 全空，实际开户命令 open-1 应持久化，收窄为 orders/fills 为空、commands 恰 1 行 key=open-1 且同键重试可成功；③ 重开幂等真实缺陷——openAccount 重放历史命令时严格全等回读，但账户已被后续下单推进到 revision 1 必不等，实现加 `replayed` 标志：重放路径仅验证账户仍存在（对齐 putNote 重放语义），新开户才全等回读，submitOrder 重放直接返回存储 result；④ tsc TS2739/2783——SecurityStateInput 两字段必填与部分覆盖夹具冲突，后端 pydantic 两字段默认 False 且内核 `is True` 判定，端口改为 `suspended?`/`is_delisting?` 可选、判定语义不变；⑤ ledger 的 `Decimal.Instance` 非法类型标注改为 default import 的 Decimal 类类型。RED 期另修三处测试自身瑕疵（对象字面量漏冒号、decimal.js 类型无无参 `config()` 重载改读静态 D.precision/D.rounding、IDBObjectStore.prototype.get monkeypatch 改具名参数 + `original.call(this, query)`）。
  - 黄金样例（与 `tests/test_reset_research_validation.py` 同一夹具，钉死于 TR-14.3）：买 100 股 fill_price 10.0050 / gross 1000.50 / commission 5.00 / stamp 0.00 / net -1005.50；卖 9.9950 / 999.50 / 5.00 / 1.00 / 993.50；往返终现金 99988.00；mark(10.00)=99994.50；10000 股速率佣金 30.02 验证 ROUND_HALF_EVEN；成本模型 commission 0.0003 / stamp 0.001 仅卖 / slippage 0.0005 / min 5。后端基准源 `research/execution.py`（196 行）、`research/ledger.py`（274 行）、`analytics/metrics.py` L374-403 closing_limit_hit。
  - 门禁（cwd=`web/`，PowerShell，均取真实退出码）：定向 vitest **52/52 passed（exit 0）**；全量 vitest **34 文件 / 1077 passed（exit 0）**，计数核账：RED 基线 33 文件 1025（红文件集合期零收集）+ 本文件 52 = 1077 精确闭合（相对原 2 个脚手架用例净增 50）；`tsc --noEmit` **exit 0**；`npm run build` **exit 0**，entry 307,971 字节 < 预算 315,500；`npm run check:bundle` **exit 0** 无私有 marker；`$env:VITE_PRIVATE_REVIEW='1'; npm run build` **exit 0**（PrivateApp chunk 正常产出），恢复默认构建后 grep 确认默认 dist 不含 `simulation_accounts`（零泄漏）；完整 `npm test`（vitest + node:test 12 项）**exit 0**。
  - 边界：零新增第三方依赖（NFR-6，decimal.js 10.6.0 / fake-indexeddb 6.2.5 / vitest 4 均已在依赖内）；未碰 e2e（冻结断言 Task 17 才反转）与后端 schema/迁移链；backup 格式零改动——exportLocalBackup 仍只读 4 个 review store，测试断言备份键集合恰为 counts/exported_at/format_version/notes/profile_mapping/revisions/verifications/watchlist 且序列化结果不含 `simulation_`；TS strict 全过；`.trae/` 仅主仓落盘、未入 worktree 提交；发布边界约束有效（不 merge/tag/push/发版/部署）。
  - 提交：`f933732` test(research): Task 14 IDB v5 simulation ledger and trading rule port contract（1 file +904/-16）、`ec0fced` feat(research): Task 14 IDB v5 simulation repository with decimal trading kernel（4 files +746/-23）。
- **Description**:
  - `web/src/repositories/local/simulation.ts`：IDB 升级 v5，新增 `simulation_accounts / simulation_orders / simulation_fills / simulation_commands`（按 local_profile_id 分区）；command 幂等 + revision 乐观锁 + 单事务读改写 + 提交后回读；v4 数据零改动迁移；backup/restore 兼容评估。
  - TS 端结算规则为 Task 3/4 纯函数的严格端口，共享黄金样例夹具。
- **Acceptance Criteria Addressed**: AC-12, AC-13
- **Test Requirements**:
  - `rule` TR-14.1: fake-indexeddb 下：同键同摘要重放不新增修订、键同摘要不同冲突、stale revision 拒绝、回读不一致报错且不留半截记录。
  - `rule` TR-14.2: v4→v5 升级后既有 notes/watchlist 数据完整；v5 旧库打开不报错。
  - `rule` TR-14.3: 与后端共享黄金样例的成交/拒单/现金/持仓结果完全一致；余额持仓不负。

## Task 15: SimulationPanel 本地模拟账本界面

- **Status**: `completed`
- **Priority**: medium
- **Depends On**: Task 14
- **Completion Evidence** (2026-11-12 落盘，worktree `.worktrees/task27-research`，head `f2853b0`，ahead 21)：
  - 严格 TDD（vitest 4 node 环境 + renderToStaticMarkup + fake-indexeddb 6.2.5，零新增依赖）：RED 新建 `web/tests/unit/reset-simulation-panel.test.ts`，6 个 describe、37 个 it 块（it.each 展开后 53 个用例），覆盖 TR-15.1（黄金买入成交经济学、同键幂等重放不增修订/行数、重复开户拒绝、T+1 拒单现金与 revision 不动、资金不足拒单不写 fill、stale revision 回滚无半截、先校验后触库、重开恢复三表全等、全序列 fetch spy 零调用）与 TR-15.2（披露常驻、aria-invalid/describedby 与同 id FieldError、九类拒单中文映射、禁用语义扫描、懒挂载源码契约、SSR 每渲染路径含 role=status）。RED 客观失败证据：定向 vitest 集合期报 `Cannot find module .../SimulationPanel`（转换成功无语法错误），exit 1；`tsc --noEmit` 恰好 1 个 TS2307（SimulationPanel），无其他类型错误，exit 2；全量 vitest 34 文件通过 / 1 文件红、1077 既有断言全过，零连带破坏；ruff All checks passed。RED 期两处自洽修正：删除自相矛盾的 blank-date 用例行（确立"账本日期可选、非空才校验日历有效性"语义，非日历日期行仍保留）；ESM 无 require，改为顶部 default + 命名导入。
  - 勘察期发现并修复 Task 14 潜伏红灯：Task 14 只跑 web 门禁未跑 Python，`tests/test_reset_full_scope.py` 仍把 `simulation.ts` 与 `SimulationPanel.tsx` 钉在 TASK27_LATER_ARTIFACTS 缺席清单（自 ec0fced 起潜伏）。本任务 RED 一并翻转制品契约（+7/-7）：缺席清单仅留 BacktestPanel.tsx（Task 16），research 目录 glob 断言改为 `{SentimentPanel.tsx, SimulationPanel.tsx}`，新增 simulation.ts is_file 断言；翻转后探针仅因 SimulationPanel 缺失而红，根因纯净。
  - GREEN 四个实现文件（feat 提交 +775/-1）：① `features/research/SimulationPanel.tsx`（648 行，零 fetch/XHR/api/useResource 引用，仅 import execution 的 D 与类型、simulation 仓库类型、本组件 css）——常量层 `SIMULATION_DISCLOSURE`（FR-16 逐字文案）、`DEFAULT_SIMULATION_RULES`（黄金成本模型 0.0003/0.001 仅卖/0.0005/min 5）、`REJECTION_LABELS`（9 个 RejectionReason 全覆盖中文，键集合与 REJECTION_REASONS 排序全等）、`MAX_SIMULATION_QUANTITY=1e9`；纯函数层 emptyOpenAccountDraft/emptySimulationDraft（保守空草稿）、validateOpenAccountDraft（资金必填非负、as_of 可选非空须日历日）、validateSimulationDraft（证券必填/非 `..`/≤200、side 白名单、数量正整数 ≤1e9、信号/成交日历日且 trade>signal、收盘价正 Decimal、limited 时 limit_date===trade_date 且涨跌停价正、down<up，非 limited 忽略涨跌停字段）、buildOrder/buildPriceBar（先校验失败抛拼接错误、trim 证券、adjustment_factor '1'、非 limited 涨跌停字段给 null）、buildSecurityState、rejectionText；编排层 openSimulationLedger/placeSimulationOrder 先校验再触库（坏草稿不产生命令行），rules 固定 DEFAULT_SIMULATION_RULES；组件层 SimulationDisclosure/OpenAccountForm/SimulationOrderForm（limited 条件渲染、busy 与 accountReady 双重禁用）/RejectionAlert（role=alert）/SimulationLedger（空态"尚未开立模拟账本"、三表、已成交/已拒单、拒单一律中文、data-label 响应式预埋）与默认容器（挂载即 Promise.all 读三表实现重开恢复，loading 初值 true 使 SSR 必含 role=status，mounted ref 防迟到响应，key 用 crypto.randomUUID()，submit 带 account.revision）；aria 范式 errorAttributes 输出 aria-invalid + `aria-describedby="sim-<field>-error"`，FieldError 渲染同 id span.field-error role=alert。② `features/research/simulation-panel.css`（119 行）全部规则限定 .simulation-panel 根、仅用全局 token（--line/--bg-*/--text-*/--accent/--up/--down/--warn/--border-textarea/--row-h），深色随 [data-theme] 自动切换；表单双列网格、760px 断点转单列且三表转 data-label 卡片（thead sr-only 裁剪、tbody/tr 块化、td 96px 标签网格，colSpan 空态行特殊处理）、319px 兜底；过禁用文案扫描。③ `features/local-review/LocalReview.tsx`（+15/-1）顶部 `const SimulationPanel = lazy(() => import('../research/SimulationPanel'))`（匹配测试字面量，无静态 from 导入），`useMemo` 构造单个 SimulationRepository 注入（复用 localRepository 单例连接与 profile），页面顶部 role=group 页签栏（"我的笔记"默认 / "模拟账本"，aria-pressed 态），模拟 tab 内 Suspense 渲染；笔记面全部既有功能与 e2e 标签（我的复盘/保存笔记）原样保留，状态钩子在 LocalReview 根部、切签不丢草稿，离开保护不受影响。④ `features/local-review/local-review.css`（+3）页签栏样式随 LocalReview chunk 先于面板 chunk 加载。
  - GREEN 期一次转绿、无测试返工：定向 vitest 首次运行即 53/53 passed。
  - 门禁（cwd=`web/`，PowerShell，均取真实退出码）：定向 vitest **53/53 passed（exit 0，658ms）**；`tsc --noEmit` **exit 0**；全量 vitest **35 文件 / 1130 passed（exit 0，5.27s）**，核账：Task 14 基线 1077 + 本文件 53 = 1130 精确闭合；`npm run build`（默认）**exit 0**，entry 307,971 字节 < 预算 315,500，产物哈希 `index-BhZqnmBV.js` 与 Task 14 完全相同（默认包零影响）；`npm run check:bundle` **exit 0** 无私有 marker；`$env:VITE_PRIVATE_REVIEW='1'; npm run build` **exit 0**（PrivateApp chunk 正常，resetEnabled 折叠故无模拟 chunk）；reset 构建 `$env:VITE_RESET_WORKBENCH='1'` 正面验证：build **exit 0** 且独立产出 `SimulationPanel-D9dr0W9J.js`（14.99 kB）+ `SimulationPanel-Dl4V6vlr.css`（3.02 kB）懒 chunk，LocalReview chunk（25.02 kB）grep 不含任何面板逻辑/披露文案（仅页签文案与 Suspense fallback），entry 不含 simulation 标记；该 env 下 check:bundle 报私有 marker"我的复盘"为设计使然（check-default-bundle 是默认构建门禁，quality-gates 冻结序列中从不带 reset env 运行）；恢复默认构建后 grep 确认默认 dist 对 `simulation_accounts/simulation_orders/模拟账本/SimulationPanel/simulation-panel/本地模拟/SimulationRepository` **零命中**；完整 `npm test`（vitest 1130 + node:test 12 项）**exit 0**。
  - Python 侧（cwd=worktree 根）：`pytest tests/test_reset_full_scope.py::test_task27_skeleton_present_but_later_slice_artifacts_remain_absent -q` **1 passed（exit 0）**；`ruff check` 与 `ruff format --check` 该文件均 **exit 0**。
  - 边界：零新增第三方依赖（NFR-6）；未碰 e2e（reset-full-scope 边界探针只访问 overview/梯队/涨幅、不进 view=review，页签内"模拟账本"不触发其冻结断言，Task 17 才反转）；未碰后端 schema/迁移、App.tsx 门控、state.ts/View、顶层导航（仍冻结 6 项）；默认构建经 localReviewEnabled env 折叠死代码消除；TS strict 全过；`.trae/` 仅主仓落盘、未入 worktree 提交；发布边界约束有效（不 merge/tag/push/发版/部署）。
  - 提交：`4527e7b` test(research): Task 15 SimulationPanel local ledger UI contract（2 files +671/-7，含新测试与 full_scope 契约翻转）、`f2853b0` feat(research): Task 15 local SimulationPanel with zero-network IDB ledger（4 files +775/-1）。
- **Description**:
  - `web/src/features/research/SimulationPanel.tsx`：账本选择/下单（含约束即时校验）、持仓与流水、拒单原因、重开恢复；持续展示"仅研究模拟/不接券商/不发真实订单"；无任何组合同步网络请求。
- **Acceptance Criteria Addressed**: AC-12, AC-14
- **Test Requirements**:
  - `rule` TR-15.1: 下单→成交/拒单→刷新后恢复闭环可用；网络面板无订单/账本上传请求。
  - `rubric` TR-15.2: 可用性与披露；scale 1-5；anchors 1=无法闭环或无研究披露，3=可用但拒单/余额难核对，5=全链路清晰且研究限定处处可见；threshold >= 4；evidence 为组件测试 + 手动走查记录。

## Task 16: BacktestPanel 与 owner API 接线

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 12, Task 9, Task 8
- **Completion Evidence** (2026-11-12 落盘，worktree `.worktrees/task27-research`，head `2cba166`，ahead 23)：
  - 严格 TDD（vitest 4 node 环境 + renderToStaticMarkup，零新增依赖），RED 三线客观失败：① 前端新建 `web/tests/unit/reset-backtest-panel.test.ts`（816 行、76 用例），定向 vitest 集合期报 `Cannot find module '../../src/features/research/backtest-client'`、exit 1、零用例收集；② full_scope 翻转（`tests/test_reset_full_scope.py` +9/-7）：workbench 私有清单加 `/api/research/backtests/{run_id}/cancel`、owner 断言加 POST、`TASK27_LATER_ARTIFACTS` 清空元组（原含 BacktestPanel.tsx）、tsx glob 期望集改三元素 `{BacktestPanel, SentimentPanel, SimulationPanel}.tsx`，带 DB env 跑出 **2 failed / 12 passed**（cancel 路由 KeyError + glob 缺 BacktestPanel，精确双根因）；③ 后端 `tests/test_reset_backtest.py`（+240）cancel HTTP 区块 7 函数，初跑 7 failed/4 passed。
  - GREEN 后端（`src/dragon_radar/http/routes/research.py` +18/-1）：新增 `cancel_backtest` 薄路由 `POST /api/research/backtests/{run_id}/cancel`，依赖 `OwnerWrite`、`_no_query(request)`，`json_object` 白名单恰 `{idempotency_key}`（越界 key/非字符串即 `invalid_request()`），`assert owner.csrf is not None` 后调 `_repository(request).cancel(owner.token, owner.csrf, run_id)`（terminal→Conflict、未知/跨 owner→NotFound 均由既有仓库层映射，零新增错误映射），返 `DecimalJSONResponse(_safe_record(record))`；模块 docstring 同步补 cancel 语义。`-k research_http_cancel` **11 passed / 199 deselected**；与 full_scope 合跑 223 passed / 1 failed（唯一失败即前端 glob，交付后消）。
  - GREEN 前端五件（feat 提交 +1344/-4）：① `features/research/backtest-client.ts`（287 行）`BacktestClientError`（`uncertain`：status 0/408/≥500 且非 503）、`RESEARCH_PATH` 正则字面量白名单（collection / `{uuid}` / `{uuid}/cancel`，UUID 段严格 1-5/89ab 版本位）、`researchRequest`（AbortController+15s 竞速、仅 POST 带 `__Host-dragon-private-csrf` cookie 读出的 X-CSRF-Token、credentials same-origin、cache no-store、AbortError/408 透传、网络错→0 network_error、坏载荷→502 malformed_response、401 派发一次 window `private-session-lock`、ALLOWED/DEFAULT 错误族表 400/403/404/409/503、401 码透传无默认）、`createBacktestCommand`（crypto.randomUUID 幂等键、非 UUID 拒绝）、`submitBacktest`（响应恰 `{run_id,status}` 否则 502）、`getBacktestRun`/`cancelBacktestRun`（非 UUID 直接 reject 不发 fetch）、`pollBacktestDecision`（queued/running→1000ms 再轮、终态→0）、类型 `BacktestRunRecord`（6 键、research_only 必须 true）与五态联合；② `features/research/BacktestPanel.tsx`（853 行）冻结常量（策略/schema/因子/三档成本/ablation 禁用原因/披露文案/9 键中文拒单映射）、`emptyBacktestDraft` 黄金默认、`validateBacktestDraft`（中文片段 回测周期/持仓数量/调仓周期/因子/费率/最低佣金/随机种子/折数，250 session 上限）、`validateManifestContext` fail-closed（错误含"研究清单"）、`buildBacktestPayload`（恰 11 键、execution_model 固定两值、walk_forward 关闭为 null）、SSR 子组件 Disclosure/ConfigForm（`bt-{field}-error` aria 前缀、invalid/busy/!manifestReady 禁用提交）/ResultView（六分区、无 result 显"暂无物化结果"、拒单全中文不泄原码）/AblationSection（AI 区仅 disabled 原因、零收益数字）、默认容器 props `{manifest}`、`role="status"`、setTimeout 1000ms 轮询、取消按钮 busy 禁用且仅 queued/running 生效，`Ex14Payload` 以 type 别名满足索引签名；③ `features/research/backtest-panel.css`（168 行）规则全限定 `.backtest-panel` 根、仅用全局 token（无 `--red`）、760px/319px 响应式；④ `pages/Analysis.tsx`（+10/-2）`resetQueryEnabled` 重命名 `resetWorkbenchEnabled`，模块级 `ResetBacktestPanel` env 折叠 + 动态 `import('../features/research/BacktestPanel')`（注释留存 `import('../research/BacktestPanel')` 契约记号与目录基准说明，无静态 from 导入）；⑤ `App.tsx`（+8/-1）门控块内 LazyLoadBoundary+Suspense（fallback"正在加载研究回测…"）持久挂载，`manifest={null}` 有意 fail-closed（前端无清单数据源、禁止伪造 UUID）。
  - GREEN 期两次单根因修正：① `RESEARCH_PATH` 初版用单引号字符串拼接致 `\\d` 退化为 `d`、UUID 段全失配（62 passed/14 failed），改正则字面量后 76/76；② `tsc --noEmit` 恰好 3 错误——pages 下 `../research/BacktestPanel` 不可解析（改可解析路径+注释契约）、`Ex14Payload` interface 缺索引签名（改 type 别名）、App manifest prop 为前者连锁（随①消除），复验 exit 0。
  - 门禁（cwd=`web/`，PowerShell，均取真实退出码）：`tsc --noEmit` **exit 0**；定向 vitest **76/76 passed（exit 0，599ms）**；全量 vitest **36 文件 / 1206 passed（exit 0，5.96s）**，核账：Task 15 基线 35 文件 1130 + 本文件 76 = 1206 精确闭合；`npm run build`（默认）**exit 0**，entry `index-DWXFoNOF.js` 307,976 字节 < 预算 315,500，dist 对 `backtest/BacktestPanel/研究回测` **零命中**；`npm run check:bundle` **exit 0**（staticAssets 与基线同集）；reset 构建 `$env:VITE_RESET_WORKBENCH='1'` **exit 0** 且独立产出 `BacktestPanel-BzJQpOlP.js`（24.29 kB）+ `BacktestPanel-5vbPasya.css` 懒 chunk，宿主 entry 仅含 mapDeps 文件名/dynamic import 装配与 Suspense fallback 一处"研究回测"，`backtest-client/提交回测/ablation` 在宿主 **0 命中**；`$env:VITE_PRIVATE_REVIEW='1'` 构建 **exit 0** 且 BacktestPanel 产物 **0 个**（折叠边界正确）；恢复默认构建后 entry 哈希与首次完全一致、grep 零泄漏、check:bundle 再判 **exit 0**；完整 `npm test`（vitest 1206 + node:test 12 项）**exit 0**。
  - Python 侧（cwd=worktree 根，容器 `dragon-radar-foundation-test-db` 127.0.0.1:55432 库 `dragon_test`，带 `DRAGON_TEST_DATABASE_URL`）：`pytest tests/test_reset_backtest.py tests/test_reset_full_scope.py -q` **224 passed in 238.53s（exit 0）**，原 glob 红灯转绿、零 skip；`ruff check` **All checks passed**、`ruff format --check` **290 files already formatted**，均 exit 0；收尾 `docker stop dragon-radar-foundation-test-db`。
  - 边界：零新增第三方依赖（NFR-6）；未碰 e2e（Task 17 才反转 Playwright 探针）；未碰后端 schema/迁移链与 CLI；禁用词扫描通过（安全再涨/无风险/预期胜率/上涨概率/胜率/必涨/稳赚/预测/包赚 零命中）；TS strict 全过；`.trae/` 仅主仓落盘、未入 worktree 提交；发布边界约束有效（不 merge/tag/push/发版/部署）。
  - 提交：`08113ae` test(research): Task 16 BacktestPanel and cancel route contracts（3 files +1065/-7）、`2cba166` feat(research): Task 16 owner backtest workbench with cancel route and lazy fold（6 files +1344/-4）。
- **Description**:
  - `web/src/features/research/BacktestPanel.tsx`：配置表单（周期/策略/参数/三档成本/折数，有界校验、aria-invalid/describedby）、提交（crypto UUID 幂等键、CSRF 客户端范式）、轮询、取消；结果区展示 metrics/equity/folds/trades/rejections/因子体检/成本敏感性/ablation disabled 原因；研究口径与版本披露。
  - 在 reset Analysis 树挂载，懒加载受 `VITE_RESET_WORKBENCH` 折叠；研究 API client 复用 owner 同源/CSRF/15s 范式。
- **Acceptance Criteria Addressed**: AC-9, AC-11, AC-14
- **Test Requirements**:
  - `rule` TR-16.1: 提交/轮询/取消/错误归一的 mock 用例通过；非法配置禁用提交并有可访问错误描述；ablation 区只显示 disabled 原因无收益数字。
  - `rubric` TR-16.2: 研究工作流可用性；scale 1-5；anchors 同 AC-14；threshold >= 4；evidence 为 vitest + Playwright 走查。

## Task 17: 构建隔离与 reset-research E2E

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 15, Task 16, Task 13
- **Completion Evidence** (2026-11-13 落盘，worktree `.worktrees/task27-research`，head `1adda8d`，ahead 25)：
  - 严格 TDD 三线推进，零新增第三方依赖（NFR-6）：① 后端 RED 在 `tests/test_reset_backtest.py` 尾部新增 Task 17 区块（+335，10 用例，分 Part A/B/C），`tests/test_reset_full_scope.py` 翻转路由面契约（+2：workbench 缺席清单加 `/api/research/manifests/{snapshot_id}`、owner 清单加 GET 断言）；② 前端 RED 扩 `web/tests/unit/reset-backtest-panel.test.ts`（+210，26 新用例，三个 describe：manifest 传输契约、资源 fail-closed 生命周期、App 折叠隔离源码断言）；③ 边缘/构建 RED 扩 `web/scripts/production-isolation.test.mjs`（+114，node:test 钉死锚定正则与 Caddyfile 字面量）、`web/scripts/test-production-isolation.mjs`（+54/-1，Docker drill research 探针）与两个 Playwright 规格。
  - GREEN 后端（feat 提交，`research/repository.py` +66、`http/routes/research.py` +6）：纯函数 `manifest_from_snapshot`（L75-117）把授权后的 `PublishedSnapshot` 投影为严格内核 `InputManifests`——domain UUID 取自不可变 publication 行（market 必有、topic/role 按可空输入缺席），adjustment/calendar/pool 版本令牌取自已发布 MarketBatch，wire 载荷可直接 `InputManifests.model_validate_json` 回环；`ResearchRunRepository.input_manifest`（L208-222）复用 EX14 授权路径：owners 缺失即 Forbidden、`authorize_connection` 鉴权、`_authorized_input` 统一映射未知 id→NotFound / 非 published→Forbidden，只读不重算；薄路由 `GET /api/research/manifests/{snapshot_id}` 依赖 `OwnerRead` + `_no_query`（query 串→INVALID_PARAM、坏 UUID→INVALID_PARAM），与既有 `get_backtest` 同构，`__all__` 导出构建器。
  - GREEN 前端（feat 提交）：`backtest-client.ts`（+76/-2）把 `BacktestManifestContext` 类型收编为传输层单一事实源（面板改 type 再导出保 import 面），`RESEARCH_PATH` 扩为 backtests/manifests 闭命名空间，新增 `validateManifestResponse` fail-closed 传输校验（恰三键、1-3 唯一 domain 且必含 market、逐项 UUID、三令牌有界字符集/长度，违例一律 502 malformed_response）与 `getBacktestManifest`（非 UUID 本地 400 不发 fetch）；新文件 `manifest-resource.ts`（52 行）提供无 hook 订阅工厂 `subscribeBacktestManifest`——加载态推 null、成功推校验对象、失败/取消冻结流且 AbortError 不回弹，迟到响应永不触达调用方；`App.tsx`（+31/-3）在 reset+analysis+有快照条件下经 `await import('./features/research/manifest-resource')` 动态接线（失败静默降级、面板维持禁用），把 Task 16 有意的 `manifest={null}` 替换为真实资源，默认构建整体折叠该依赖。
  - 真实产品缺陷修复（feat 归属，非弱化断言）：E2E 在 1440px + `zoom:2`（有效 720 CSS px）实测 `BacktestResultView` 11 列「成交流水」宽表致 `scrollWidth 1876 > clientWidth 1440`。修复为引入共享 `TableScroll`（`components/Common`，`.table-scroll{overflow-x:auto;overscroll-behavior-x:contain}`，SentimentPanel 已有先例）包裹结果区全部 5 张表（净值曲线/成交流水/拒单说明/成本敏感性/滚动折数，各带 `role=region` 可读标签），表自身 aria-label 不变故 `getByRole('table',{name})` 解析不受影响；同文件 `reset-backtest-panel.test.ts` vitest 102/102 不回归。
  - 构建隔离（cwd=`web/`，PowerShell，真实退出码；每个变体独立进程内联 env、互不污染）：默认构建（清全部 VITE_ env）`npm run build` **exit 0**，entry `index-CzTuV5LH.js` **308,115 字节** < 预算 315,500，且与 Task 17 前默认构建哈希/字节完全一致（默认包零影响）；`npm run check:bundle` **exit 0**，staticAssets 闭包仅 entry+chart 三 chunk+jsx-runtime+CSS；对 dist 全资产做 7 marker 扫描（`/api/research`、`仅研究模拟`、`模拟账本`、`BacktestPanel`、`/api/owner/`、`/api/ai/`、`我的复盘`）**全部零命中**。`check-default-bundle.mjs`（+15）新增 researchModuleMarkers 契约：默认构建对 allScripts 兜底扫描、reset 构建只扫 entry 静态闭包（research 只允许存在于 env 折叠后的懒 chunk）。private 构建（`VITE_PRIVATE_REVIEW=1`）**exit 0**，entry 308.60 kB、PrivateApp 42.94 kB 独立懒 chunk；reset 构建（`VITE_RESET_WORKBENCH=1`）**exit 0**，entry `index-tIz7G0mj.js` 323.03 kB；sharing 构建（`VITE_RESET_WORKBENCH=1`+`VITE_PUBLIC_SHARING=1`）**exit 0**，entry `index-DeTlM_D4.js` 320,840 字节——超 320,000 预算为 Task 16 既存（HEAD 同 env 已 320,406，本任务增量 434 字节），且 CI（ci.yml L239-241）仅对默认构建跑 check:bundle、private/sharing/reset 变体只验证构建成功，预算行无回归、未擅改阈值。reset env 下跑 check:bundle 报私有 marker「我的复盘」为设计使然（reset 导航在非 sharing 下静态编入 review 入口），非门禁、非缺陷。
  - 边缘隔离与 Docker drill：`owner.Caddyfile`/`private.Caddyfile` 各 +15，在默认拒绝 `@api` 回退之前插入两条锚定白名单（`researchRead ^/api/research/(manifests|backtests)/<uuid>$` 限 GET/HEAD、`researchWrite ^/api/research/backtests(/<uuid>/cancel)?$` 限 POST），任何 sibling 路径落空空体 404；`workbench.Caddyfile`（+1/-1）把 `/api/research/*` 并入私有命名空间 404（位于 method 405 守卫之后，故 GET→空体 404、POST→空体 405）。支撑模块 `production-isolation-support.mjs`（+54）导出双锚定 pattern、四类探针与分类函数，node:test 同时钉死 pattern 与三个 Caddyfile 字面量/顺序，防边缘重写悄悄放宽命名空间。实跑 `npm run test:e2e:production-isolation`（双栈真实构建+migrate+owner-pair）**exit 0**：`{"private":"https://localhost:32768","public":"http://127.0.0.1:32769","status":"passed"}`——owner 白名单读穿透到应用返 JSON `NOT_FOUND` 信封、白名单写无 CSRF 被应用 gate 拒 403 `csrf_forbidden`、sibling 边缘空体 404；workbench 全命名空间 GET 空体 404、写 405，容器 `/srv` grep 研究 marker 零命中，compose 项目自动 down --volumes 清理无残留。
  - Playwright（`npm run test:e2e:reset`，baseURL 5174，workers 2）：重写 `reset-research.spec.ts`（+401/-15，4 测试零 skip）——① manifest 闸门持有提交（gate Promise 释放前按钮禁用+「研究清单未就绪」文案+零 POST），释放后提交→1s 轮询→排队/运行/已完成→物化结果五表渲染，1440px/200% zoom 下 `scrollWidth<=clientWidth` 无横向溢出；② 协作取消命中 cancel 路由、终态后 1.3s 内轮询冻结；③ 375px 畸形 manifest fail-closed（零写零轮询，仅一次 manifest GET）；④ 375px 本地模拟账本开户→首单→IDB 四 store 回读（TR-14.1：开户 revision 0、首单成交后 **revision 1**，reload 后「账本 revision 1」恢复）且全程零网络、零 `/api/research` 调用；每测试均断言 console error 为空。`reset-full-scope.spec.ts`（+91/-26，9 测试零 skip）把原 Task 27 blocked 探针改写为 owner 正向边界：提取共享 `mockResearchBoundary`（仅放行绑定快照 ID 的 manifest GET，其余 research 调用一律响亮 500 防 dev-proxy 穿透），overview/leaders 断言研究区 count 0 且零 research 调用，analysis 断言面板+disclosure 可见、manifest GET 绑定所选 publication、零写、模拟账本入口 count 0（review-only 懒加载），NL query 测试补装 research 边界消除 ECONNREFUSED 噪声。全量 **170 passed（3.7m）/ 0 skip / 0 flaky**。dev-server 侧 `ECONNREFUSED 127.0.0.1:8020` 代理日志为 vite 服务端输出而非浏览器 console error（三个老规格未 stub research/sentiment，manifest 经资源层静默 fail-closed 置 null；所有带 console 断言的测试全过即证浏览器零 error），且超出 Task 17 归属文件，裁决不扩散修改老规格。
  - 前端门禁（本任务代码冻结后复验）：`tsc --noEmit` **exit 0**；全量 vitest **36 文件 / 1232 passed（exit 0）**（核账：Task 16 基线 1206 + 本任务 26 = 1232 精确闭合）；完整 `npm test`（vitest + scripts node:test）**exit 0**，node:test **19 pass / 0 skip**。
  - Python 门禁（cwd=worktree 根，容器 `dragon-radar-foundation-test-db` 127.0.0.1:55432 库 `dragon_test`，带 `DRAGON_TEST_DATABASE_URL`，真实退出码）：`ruff check` **All checks passed**、`ruff format --check` **290 files already formatted**，均 exit 0；`python -m compileall -q src tests` **exit 0**；`python scripts/check_prd_contracts.py` **exit 0**（REQ=57 endpoints=29 examples=29 JSON=30）；CI 同款全量 `pytest -q -rs -W error --cov --deselect tests/performance/test_reset_queries.py` 首轮 4210 passed/1 failed/14 skipped/2 deselected（42:37），唯一失败 `test_owner_cli.py::test_owner_pair_file_and_stdout_no_credentials` 经系统调试定性为**本机既存环境故障、与 Task 17 零代码关联**：pwsh 7 进程树把 PS7 版 `PSModulePath` 透传给测试硬编码的 `powershell.exe`（5.1），自动加载优先命中为 .NET Core 编译的 `Microsoft.PowerShell.Security 7.0.0.0`，在 5.1 加载失败致 `Get-Acl` 静默返回 null（`assert None`）；该测试与 `owner/windows_files.py` 相对 HEAD 字节未改，CI ubuntu 走 POSIX 分支不触达此代码。注入仅含 5.1 路径的干净 `PSModulePath` 后定向复跑 `test_owner_cli.py` **18/18 passed（exit 0）**，同 env 全量复跑 **4211 passed / 14 skipped / 2 deselected（42:15，exit 0）**，TOTAL 覆盖率 93%、`research/repository.py` 97%、`http/routes/research.py` 95%；14 skip 经 `-rs` 全部留痕且均为既存平台条件（10× 缺 `DRAGON_TEST_PRIVATE_CA` 的活 TLS 探针、2× WinError 1314 符号链接特权、2× POSIX 进程组 skipif），Task 17/27 测试文件零 skip；2 deselected 为专用性能 job 认证的性能模块。收尾 `docker stop dragon-radar-foundation-test-db`。
  - 边界：零新增第三方依赖（NFR-6，package.json 依赖面零改动）；未碰后端 schema/迁移链/CLI；未碰顶层导航与 state；TS strict 全过；`.trae/` 仅主仓落盘、未入 worktree 提交（worktree `git status` 最终洁净，`.temp/` 全程 gitignored）；发布边界约束有效（不 merge/tag/push/发版/部署，HEAD 无 tag、分支仅 ahead 无 upstream 推送）。
  - 提交：`91db378` test(research): Task 17 build isolation and reset-research E2E contracts（7 files +1207/-42）、`1adda8d` feat(research): Task 17 owner manifest channel with edge and bundle isolation（11 files +423/-87，含新增 `manifest-resource.ts`）；两提交合计 18 files +1630/-129，相对 Task 16 基线 `2cba166` 文件集精确闭合（工作树 diff 统计的 1578 不含未跟踪新文件 52 行，1578+52=1630）。
- **Description**:
  - 更新 `check-default-bundle.mjs` 与生产隔离脚本：公共/默认包体不含 research 块；owner 块仅 workbench。
  - `web/tests/e2e/reset-research.spec.ts`：owner 会话下回测配置→提交→轮询→取消/完成结果核对；模拟账本下单/恢复；研究文案；console 零 error；375/1440 + 200% 缩放。
  - 改写 `reset-full-scope.spec.ts` Task 27 探针：workbench 只读运行时 `/api/research/**` 零调用断言保留，新增 owner 授权后的调用正向断言（不用 skip）。
- **Acceptance Criteria Addressed**: AC-14, AC-15
- **Test Requirements**:
  - `rule` TR-17.1: bundle 检查证明公共产物无 research/simulation 模块；生产隔离脚本通过。
  - `rule` TR-17.2: Playwright 全用例通过、零 skip、console 零 error；只读运行时零 `/api/research` 调用。

## Task 18: 验收文档、门禁集成与全量回归

- **Status**: `completed`
- **Priority**: high
- **Depends On**: Task 17
- **Completion Evidence** (2026-11-16，工作树 `.worktrees/task27-research`，HEAD `1adda8d`，本次改动未提交)：
  - `docs/acceptance/task27-research.md` 已完成 B01/B02、EX14/EX15、AC-1…AC-15 追溯；README 同步当前实现，`reset-final.md` 仅追加后续指引；五项外部生产门禁保持 blocked，生产因子输入默认装配仍失败闭合。
  - 后端四个完整文件分片 1064 / 1065 / 1064 / 1063 passed，共 4256 passed / 0 skipped，均 EXIT=0；合并覆盖率 92%。真实 TLS 10、Windows junction 2、指定 PostgreSQL 容器恢复 1、专用性能 1 均单独实跑通过，0 skipped；Windows ACL/reset 脚本检查通过。
  - 真实研究引擎 100 证券 × 61 交易日与三轮各 20 公开 GET 并发：普通整体 P95 1.089s，研究并发三轮 1.075 / 1.037 / 1.159s，均 ≤2.0s；公开零写；仅研究 run/cache 两表变化，其余指纹一致；原始性能验证器零错误，预算未放宽。
  - 前端 Vitest 1237 + Node 28 passed；五套 Playwright 81 / 170 / 69 / 1 / 4 passed，合计 325，0 skipped；研究 UI 四例最终复测通过并留存 trace/整页截图。四构建及 TypeScript 通过，默认 bundle 307976≤315500，sharing 319955≤320000，全资产隔离扫描通过；Docker 生产隔离/API 契约运行时通过，临时栈已清理。
  - PRD 契约 57 REQ / 29 endpoints / 29 examples / 30 JSON，Ruff lint/format（292 文件）、compileall、diff whitespace 均 EXIT=0。独立规格 PASS、质量 APPROVED；前端与工程 rubric 均 4/5，达到 ≥4；依赖与运行时配置 diff 为空。
  - 最终证据位于 `docs/acceptance/evidence/task27/manifest.json`：549 源文件、52 产物的 SHA-256、所有 AC 链接和 4256 已采集节点均已逐项校验通过。源码摘要 `85b336270f8d2eff234e874413bbf124f174d252680844ceeadc86d618d60410`。
  - 本记录仅为本地 CI 等价命令及合成开发验收；未生成正式 `verify_reset --mode full` 受信报告，无远程 CI 运行记录。未提交、push、merge、tag、改版本、发布镜像或部署。
- **Description**:
  - 新增 `docs/acceptance/task27-research.md`（AC-B01/B02 证据、合成夹具与真实材料分离登记、五外部门禁仍 blocked）；`reset-final.md` 仅追加"Task 27 已解除，见新文档"指引，不改写原始结论；翻转 REQ_REGISTRY B01/B02 与 EX_REGISTRY EX14/EX15 状态/证据，`test_blocked_requirements_are_exactly_task27...` 更新为"无 blocked REQ"契约。
  - 评估 `verify_reset.py` 是否新增研究负载门（owner 慢任务与普通 GET 并发 P95 ≤ 2.0s 不退化；不延长超时）；跑全量后端/前端/CI 回归并留存真实退出码。
- **Acceptance Criteria Addressed**: AC-1…AC-15
- **Test Requirements**:
  - `rule` TR-18.1: `check_prd_contracts.py`、pytest 全量 zero-skip、ruff、compileall、vitest、Playwright、bundle 检查全部真实通过并登记命令/退出码。
  - `rule` TR-18.2: 新验收文档逐项链接 AC 证据；五项外部门禁仍标 blocked；无"生产就绪"越界措辞。
  - `rubric` TR-18.3: 工程隔离与零回归；scale 1-5；anchors 同 AC-15；threshold >= 4；evidence 为 CI 13+ 门真实结果与依赖 diff（无新增三方包、无新增容器）。
