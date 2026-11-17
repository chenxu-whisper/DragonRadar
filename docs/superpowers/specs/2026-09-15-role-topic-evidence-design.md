# 第四批角色与题材证据层技术设计

## 一、状态与目标

状态：设计已批准，独立规格复审通过，可进入详细实施计划。

目标：在现有不可变行情快照之上，引入独立不可变题材快照，并在发布时
物化可追溯的六类角色结果。结果必须解释资格、证据、反证、缺失和版本，
允许无候选；人工复核只追加结论，不修改规则结果。

关联需求：`REQ-M02`、`REQ-L01`、`REQ-L02`、`REQ-L03`、`REQ-S01`、
`REQ-D02`、`REQ-D03`、`REQ-D04`、`REQ-Q01`、`REQ-Q02`、`REQ-Q03`、
`REQ-Q04`、`REQ-X01`、`REQ-X02`。本批只定义与题材和角色数据直接相关的
删除／恢复防重曝行为，不宣称完成全项目 `REQ-X03`。

本批不包含消息聚类、监管规则、AI、真实数据源、动态权重、因子收益评估、
回测、模拟组合、盘中行情、公开分享和题材管理 Web 页面。

## 二、已确认决策

1. 六类角色均有稳定契约，但按能力分层输出，不使用缺失字段推断结论。
2. 题材快照与行情快照独立发布、独立授权和独立修订。
3. 题材批次只通过本机 CLI 导入和发布，Web 端只读。
4. 角色运行在发布时物化，读取请求不临时重算。
5. 排序使用资格门槛加字典序证据键，不生成加权总分或上涨概率。
6. 人工复核是所有者专属的不可变追加记录，不覆盖角色计算结果。
7. 同值候选保留并列；没有合格对象是正常结果。

## 三、总体架构

数据流分为五个边界：

1. 本机 CLI 校验并归档 `TopicBatch`。
2. 质量通过的批次发布为不可变 `TopicSnapshot`。
3. 物化命令显式选择 `MarketSnapshot`、`TopicSnapshot` 和代码内固定
   `RoleRuleSet`。
4. 同一事务写入不可变 `RoleRun`、因子观测、角色结果和证据。
5. 独立核验通过后追加激活事件，公共 API 只展示当前活动运行；所有者 API
   追加人工复核。

角色运行唯一键为：

```text
market_snapshot_id + topic_snapshot_id + rule_version
+ predecessor_role_run_id_or_zero + predecessor_activation_event_id_or_zero
```

相同组合重复执行返回同一结果。任一输入内容或规则改变都必须使用新标识，
不得覆盖旧运行。

## 四、题材数据契约

### 4.1 导入对象

`TopicBatch` 使用冻结 Pydantic 模型，拒绝未知字段，包含：

- `trade_date`、`as_of`、`source_time`、`provenance`
- `source_id`、`source_version`、内嵌 `SourceGrant`
- `exchange`、内嵌 `ExchangeCalendar`、`calendar_version`
- `taxonomy_version`、`membership_version`
- `coverage_from`、`coverage_to`
- `coverage_cells`
- `topics`
- `memberships`
- `page_manifest`

`page_manifest` 对单文件输入也必须存在，并包含：

- 规范化请求范围；
- 每页稳定页 ID、请求游标、后继游标、页哈希和接收行数；
- 来源声明总数；
- 唯一终止游标。

`coverage_cells` 对覆盖区间内每个 `topic_id + trade_date` 保存：

- `expected_member_count`
- `received_member_count`
- `complete_as_of`
- `status = complete | unavailable`
- `reason`

发布要求每个声明题材和日期恰有一个单元格；`complete` 必须满足接收数等于
应有数，`unavailable` 必须提供原因且不能把无记录解释为非成员。
`coverage_from` 和 `coverage_to` 是包含端点的交易日，必须存在于内嵌日历；
覆盖单元格必须恰好覆盖两端之间的每个日历会话。`received_member_count`
是在该日有效、`available_at <= complete_as_of` 的去重证券数。
`complete_as_of` 必须不晚于 `batch.as_of`。角色窗口只可使用
`complete_as_of <= knowledge_cutoff` 的 `complete` 单元格；更晚完成的单元格
在该运行中视为 `unavailable`，不得证明较早截止时点的成员集合完整。

`TopicDefinition` 包含：

- `topic_id`：稳定标识，不使用展示名称作为主键
- `name`
- `effective_from`、`effective_to`
- `available_at`
- `source_reference`

`TopicMembership` 包含：

- `topic_id`
- `security_id`
- `effective_from`、`effective_to`
- `available_at`
- `evidence_refs`

有效区间统一为左闭右开 `[effective_from, effective_to)`。`available_at`
必须是已知的 UTC aware datetime；未知时该定义或成员不能进入批次。
仅 `source_time` 可为 `None`，不得用采集时间替代。`evidence_refs`
仅保存来源内稳定引用，不保存
未经许可的全文。来源引用是有界纯文本或结构化引用；拒绝控制字符和
`javascript:`、`data:`、`file:` 等危险协议。

日期 `d` 的成员集合只包含有效区间覆盖 `d` 且在角色运行知识截止时间前
已知的记录。只有逐题材、逐日期的覆盖声明完整时，无成员记录才表示“不是成员”；
否则依赖该日期的角色窗口为 `insufficient`。

### 4.2 质量门禁

导入时拒绝：

- 空或重复题材 ID；
- 同一题材和证券的有效区间重叠；
- 成员引用不存在的题材；
- 任一题材定义或成员缺少 `available_at`；
- `available_at > as_of` 的未来信息；
- 批次日期不在其声明范围内；
- 来源、版本或许可字段不一致；
- 请求范围、游标链、页 ID、页哈希、接收行数、来源总数或终止游标不连续、
  重复、未知或不一致。

题材成员不要求在导入时存在于某个行情快照。角色物化时再与所选行情快照
求交，并把未知证券、无有效状态及不在行情池中的成员列为缺失，不静默删除。
质量门禁还验证全部角色窗口位于 `coverage_from..coverage_to` 内。

### 4.3 持久化与命令

新增：

- `topic_import_batches`
- `topic_snapshots`
- `topic_import_attempts`

批次与快照表保存规范化内容哈希、原始归档、质量报告、发布时间和修订关系，并使用
数据库触发器阻止常规更新、删除和截断。导入尝试追加记录缺页、失败游标和
可重跑范围；失败尝试不产生可发布批次。

CLI：

```text
dragon-radar topic-import <path> --key <idempotency-key>
dragon-radar topic-publish <batch-id>
```

同键同内容返回原批次；同键不同内容冲突。发布失败不产生半成品快照。

## 五、规则版本与公共门槛

第四批只启用代码内固定 `role-v1`。规则内容以规范 JSON 一并写入
`RoleRun`，使旧结果不依赖未来代码解释。未来新增规则版本，不修改
`role-v1`。规范 JSON 同时保存 SHA-256；测试固定该摘要，同一
`rule_version` 对应不同摘要时拒绝启动。

`RoleRun` 固化：

- `knowledge_cutoff = min(market.as_of, topic.as_of)`；
- `visibility_mode = contemporaneous | retrospective`；
- 两个输入各自的 `as_of`、`provenance` 及实际输入最大 `available_at`。

所有行情、证券状态、题材定义和成员记录必须重新按
`available_at <= knowledge_cutoff` 过滤，不能只依赖批次导入时校验。任一输入
为 `retrospective` 时，运行只能标为 `retrospective`，不进入“当时可见”
默认视图，也不能与 contemporaneous 运行计算角色变化。

公共资格门槛：

- 行情与题材快照 `trade_date` 必须相同；
- 两个来源在物化时均允许本机存储和展示；
- 证券必须是题材当日有效成员，并存在于行情快照声明池；
- 证券当日状态有效，`security_type=common_stock`、非 ST、非退市整理；
- `SecurityState` 增加可空、版本化的 `security_type`、`board_category` 和
  `is_delisting`；旧归档缺少
  任一字段时仍可读取旧快照，但值规范化为 `None`，该证券不进入正式角色池；
- 现有 `SecurityState.board` 继续接受来源原值，不做破坏性枚举迁移；
  `board_category` 规范值为 `sh_main | sz_main | synthetic_test`。
  生产 `role-v1` 只允许 `sh_main` 和 `sz_main`；隔离测试使用不可在正常库
  激活的 `role-test-v1` 和 `synthetic_test`；
- 上市历史按所选行情快照交易所日历中满足
  `listing_date <= d <= trade_date` 的交易日计数，必须至少 120；日历未覆盖
  完整区间时为输入不足；
- 正式题材排名至少有 5 个满足该角色输入要求的有效成员；
- 因子只读取 `available_at <= knowledge_cutoff` 的事实；
- 缺少必要输入时不缩短窗口、不补零、不重分配因子优先级。

每次物化和读取在单一 UTC 操作时点同时检查两个快照归档内嵌授权与当前授权。
先按 `source_id` 字典序持有全部来源锁至事务提交，再访问快照或结果。所有者
写入的全局锁序固定为：主体、幂等命令、排序后的来源、业务资源。

仅 `local_display` 撤回时，获准存储的历史运行与复核可保留，但依赖该来源的
事实、因子、证据和导出立即不可读并清除客户端缓存。`local_storage` 或保留权
撤回时，模块保持关闭，按已批准保留策略删除或去标识化源数据及派生值，仅保留
允许留存的非敏感审计墓碑；恢复环境必须先重放撤回／删除账本并复核当前授权，
否则不启用服务。本批实现题材和角色依赖链的阻断与墓碑，不宣称完成其他数据域
的全项目删除策略。

## 六、角色计算

### 6.1 状态

规则能力等级：

- `ready`：可输出正式候选；
- `simplified`：可输出明确标注的简化候选；
- `watch_only`：只能输出观察项；
- `pending_validation`：需要未来快照确认；

每个题材与角色另有本次运行状态：

- `ready`
- `simplified`
- `watch_only`
- `pending_validation`
- `insufficient`：输入或覆盖不足；
- `no_candidate`：输入完整但无人通过候选条件。

运行状态同时返回应有、有效、合法排除、缺失数量和原因。规则能力等级不能
替代本次运行状态；例如趋势规则能力为 `ready`，具体题材仍可为
`insufficient` 或 `no_candidate`。

证券结果状态：

- `candidate`
- `simplified_candidate`
- `eligible_non_candidate`
- `watch`
- `pending_validation`
- `excluded`

### 6.2 排序约定

每类角色先执行硬资格门槛，再按固定证据元组进行字典序比较。每个键声明
升降序和原始单位。完全相同的元组使用同一 `tie_rank`；证券 ID 只能稳定
展示，不能打破业务并列。正式或简化候选是第一并列组，前提是该角色的
候选条件成立。比较使用 Decimal34 half-even 计算后的未展示舍入值；
`tie_rank` 使用从 1 开始的 dense rank，证券 ID 只决定并列组内展示顺序。

`role-v1` 限定单交易所运行，题材有效成员必须与行情快照日历交易所一致；
跨交易所题材整体返回 `insufficient`。逐因子的基准集合均使用观察日有效且
完整满足该因子窗口的固定成员集合：

- 窗口收益、回撤和收益中位数使用有效区间连续覆盖整个窗口、在截止前已知且
  窗口内价格完整的成员交集；
- 每日题材成交份额使用该日有效且已知的成员集合，并要求该日
  `coverage_cells.status=complete`；
- 多日平均成交份额要求待排名证券覆盖整个窗口，每日先按当日成员集合计算
  份额，再对每日份额取算术平均；
- 观察日资格数量只计算观察日有效、已知且满足对应角色全部输入的成员。

偶数样本中位数是中间两个 Decimal 值的算术平均；基准有效数、应有数、
排除数和缺失数随因子保存。

公共公式固定为：

```text
adjusted_price(d) = close(d) * adjustment_factor(d)
return(a,b) = adjusted_price(b) / adjusted_price(a) - 1
relative_return(a,b,benchmark) = security_return(a,b) - benchmark_return(a,b)
maximum_drawdown(a,b) =
  max over d in [a,b] of (1 - adjusted_price(d) /
    max(adjusted_price(k) for k in [a,d]))
topic_amount_share(i,d) =
  amount(i,d) / sum(amount(member,d) for all eligible topic members)
mean(values) = sum(values) / count(values)
mean_absolute_deviation_rate(values) =
  mean(abs(value - mean(values))) / mean(values)
```

价格统一使用同一行情快照中的复权收盘价；成交额使用原始 `currency` 单位，
不得换算或跨币种求和。所有除数必须为有限正数，否则该观测不可计算。

### 6.3 六类角色

#### 趋势龙头

- 状态：`ready`
- 必需输入：连续 61 个交易日有效复权收盘价；题材和市场有效基准。
- 因子：60 日相对市场收益、20 日相对市场收益、60 日最大回撤。
- 候选条件：20 日和 60 日相对收益均大于 0，且 60 日最大回撤不高于
  同题材合格成员的 60 日最大回撤中位数。
- 排序：相对收益 60 日降序、相对收益 20 日降序、最大回撤升序。

市场基准使用同一行情快照声明池的有效证券收益中位数，题材参考另列，
不得用题材参考替代市场基准。20／60 日收益分别使用 `T-20..T` 和
`T-60..T` 的 21／61 个价格点；基准成员在整个对应窗口都必须合格。

#### 中军

- 状态：`ready`
- 必需输入：连续 20 个交易日的成交额；每个交易日题材有效成员成交额
  覆盖完整；单位一致。
- 窗口定义：最近 5 日为 `T-4..T`，20 日为 `T-19..T`，均包含观察日。
- 因子：最近 5 日平均题材成交份额、20 日平均成交额、20 日成交额
  平均绝对偏差率。
- 候选条件：最近 5 日平均题材成交份额和 20 日平均成交额均不低于
  同题材合格成员中位数。
- 排序：题材成交份额降序、平均成交额降序、偏差率升序。

缺少流通规模时不以总市值或价格替代。任一日题材成交分母不完整时，
该题材中军排名为 `insufficient`。偏差率固定为
`mean(abs(amount_d - mean20)) / mean20`；`mean20=0` 时不可计算。

#### 情绪龙头

- 状态：`simplified`
- 规则 ID：`emotion-daily-v1`
- 必需输入：当日有效涨跌停价、已知收盘涨停序列、`T-5..T` 六个有效
  价格点和当日成交额。
- 候选条件：当日收盘涨停且连板数至少 1。
- 5 日相对收益固定为个股 `T-5..T` 收益减同题材合格成员同窗口收益中位数。
- 排序：连板数降序、5 日相对题材收益降序、当日题材成交份额降序。

结果必须显示“仅日线简化候选”；不声称掌握首次封板、开板回封或封单质量。

#### 补涨

- 状态：`simplified`
- 规则 ID：`catchup-daily-v1`
- 必需输入：互不重叠的前 20 日窗口和当前 5 日窗口、题材收益中位数、
  个股成交额。
- 窗口定义：以观察日为 `T`，当前收益窗口为 `T-5` 至 `T`，
  前期收益窗口为 `T-25` 至 `T-5`；成交额均值分别使用
  `T-4` 至 `T` 的 5 个交易日和 `T-24` 至 `T-5` 的 20 个交易日。
  因此至少需要连续 26 个交易日的有效价格和 25 个交易日的有效成交额。
- 候选条件：前期落后题材中位数，当前窗口相对题材转强，且当前 5 日
  平均成交额高于前期 20 日平均值。
- 排序：当前相对题材收益降序、前期落后幅度降序、成交额变化降序。

公式固定为：

```text
prior_lag =
  topic_median_return(T-25,T-5) - security_return(T-25,T-5)
current_excess =
  security_return(T-5,T) - topic_median_return(T-5,T)
amount_change =
  mean(amount[T-4..T]) / mean(amount[T-24..T-5]) - 1
```

三个量均为 Decimal；候选条件是三者严格大于 0。任一成交额均值为 0、
单位不一致或窗口成员覆盖不完整时不可计算。

#### 跟风

- 状态：`watch_only`
- 参考核心必须是同题材唯一的趋势龙头第一并列组；没有唯一核心时整体
  `insufficient`，不得从并列中按证券 ID 选择。
- 观察条件：证券 `T-5..T` 收益大于 0 且严格低于参考核心同窗口收益。
- 5 日平均成交额变化固定为
  `mean(amount[T-4..T]) / mean(amount[T-9..T-5]) - 1`；分母为 0、
  单位不一致或 10 日数据不完整时该证券不可计算。
- 展示排序：5 日收益降序、上述成交额变化降序；只产生 `watch`。
- 固定缺口包含盘中启动先后和独立催化证据；相关性不解释为因果。

#### 接力龙头

- 状态：`pending_validation`
- 前序运行必须是上一交易日、相同 `rule_version`、相同
  `visibility_mode`、相同市场基准定义且题材身份连续的唯一活动运行。
- 旧核心是前序唯一趋势候选，新对象是当前唯一趋势候选，且二者不同。
- 转弱／转强固定为旧核心 5 日相对题材收益小于等于 0、新对象大于 0，
  且题材当日收益中位数大于 0。
- 当日只生成 `pending_validation` 结果并固定后续 5 个交易日验证窗口，
  不输出正式候选。窗口成熟后生成引用原结果的新不可变验证记录，
  不更新原结果。
- 没有前序运行或题材版本不可比时返回 `insufficient`。

同日修订、规则变化、visibility mode 变化、市场基准变化为“不可比”。
成员变化单独记录为题材成员变更，不得归因为行情驱动的角色变化。

后验窗口是原观察日之后同交易所的 `T+1..T+5` 五个交易日，并固定使用
当时各日可见且与原规则、visibility mode 和题材身份连续的活动运行。
`role_validations.outcome` 为：

- `supported`：新对象五日收益严格高于旧核心且严格高于题材成员中位数；
- `weakened`：新对象严格高于旧核心，但不高于题材中位数；
- `refuted`：新对象不高于旧核心；
- `unavailable`：任一对象停牌、价格缺失、题材不可比或五日窗口不完整。

上述五日收益统一为 `return(T,T+5)`；题材成员中位数固定使用在 `T` 已知、
身份持续覆盖 `T..T+5` 且六个价格点完整的成员。停牌或成员变化不从分母中
静默删除，而使该待验证结果为 `unavailable`。

验证记录保存五日端点、双对象和题材基准原值、输入运行 ID 与原因；同一待验证
结果只允许一个当前验证链，更正仍以不可变追加记录完成。

### 6.4 连板矩阵与角色变化

`RoleRun` 同时物化独立的 `limit_ladder_entries` 和 `role_change_entries`，
不从页面临时推导。

连板矩阵按交易日、题材、证券保存：

- 已知连续收盘涨停数；
- 最近 10 个交易日的 `10天M板`，并明确它不等于 `M连板`；
- 前一交易日收盘涨停而当日已知未涨停的“断板”；
- 仅在 OHLC 和当日有效涨停价完整且质量门禁确认 `high <= limit_up` 时，
  以 `high == limit_up` 且 `close < limit_up` 标记“触板未封”；
  停牌或已知 `no_limit` 中断连续涨停序列，未知限价使序列和触板状态不可用。

角色变化只比较 `previous_comparable_run_id`。每条记录分开保存：

- `comparison_status = comparable | not_comparable`
- `change_type = added | exited | tie_changed | unchanged`
- `membership_changed: bool`
- `reasons`

`not_comparable` 时 `change_type` 必须为空。成员变化可以与任一可比变化并存，
不与 `change_type` 互相覆盖。规则、taxonomy、visibility mode、市场基准或
必要计算口径变化时整体标为 `not_comparable`，不得将变化归因为行情。

## 七、物化与存储

新增：

- `role_runs`
- `role_topic_statuses`
- `factor_observations`
- `role_results`
- `role_validations`
- `role_validation_commands`
- `limit_ladder_entries`
- `role_change_entries`
- `role_reviews`
- `role_materialization_commands`
- `role_materialization_attempts`
- `role_quality_samples`
- `module_control_events`
- `role_run_activation_events`

`RoleRun` 保存双快照 ID、规则版本、规则规范 JSON、截止时间、状态、质量摘要、
前序可比运行 ID 和物化时观察到的前序激活事件 ID。前一交易日没有活动运行时
两者使用零值。物化过程按题材 ID、角色、证券 ID 取得稳定锁顺序，在单事务内
完成；异常时不保留部分结果。

`factor_observations` 保存原始值、单位、窗口、方向、样本量、覆盖率、输入版本
及失败原因。`role_results` 保存题材、证券、角色、状态、`tie_rank`、排序元组、
支持证据、反证和缺失项。两者发布后不可修改或删除。

`role_topic_statuses` 对每个题材×角色始终写一行，保存规则能力等级、本次运行
状态、应有／有效／合法排除／缺失数量和原因，即使没有任何证券结果也能表达
`insufficient` 或 `no_candidate`。连板和变化表使用本节 6.4 的固定字段，
并通过 `role_run_id` 限制删除外键绑定运行。

`role_materialization_commands` 保存 `key`、规范请求摘要、运行 ID 和创建时间。
摘要覆盖双快照 ID、规则版本与规范参数。同键异摘要返回冲突；同键同摘要在
重新检查当前授权后返回原运行。数据库唯一约束为
`(market_snapshot_id, topic_snapshot_id, rule_version,
predecessor_role_run_id, predecessor_activation_event_id)`；不同键并发命中
同一组合时，在取得组合 advisory lock 后返回已提交运行。账本、运行、结果和
审计同事务提交。

快照、运行、因子、结果、验证、复核和命令账本使用限制删除外键；历史表禁止
应用角色执行 `UPDATE`、`DELETE` 和 `TRUNCATE`。保留权撤回只能进入专用
清除事务：先关闭模块并锁定来源，写入只含对象 ID、哈希、原因和时间的删除
账本，再按以下矩阵处理：

| 对象 | 清除动作 |
| --- | --- |
| `factor_observations`、`role_topic_statuses`、`role_validations`、`limit_ladder_entries`、`role_change_entries` | 删除所有来源派生值 |
| `role_quality_samples` | 删除原值、核验值和样本上下文，仅在删除账本保留通过／失败计数 |
| `role_results` | 专用权限改写为 `purged` 墓碑，清空证券、题材、排名、证据和缺失 |
| `role_runs` | 保留去标识运行墓碑及规则版本，双快照外键置空 |
| `topic_snapshots`、`topic_import_batches`、`topic_import_attempts` | 清除归档、题材身份、来源引用和质量明细，保留不含来源内容的 ID／哈希墓碑 |
| 物化／后验验证命令与尝试 | 清除请求摘要与失败明细，保留命令 ID、结果状态和时间 |
| 激活／控制事件 | 保留事件 ID、运行墓碑引用、动作、时间；原因按运维日志保留策略处理 |
| `role_reviews` | 保留所有者原创理由并继续引用 `purged` 角色结果墓碑 |

墓碑之间保留限制删除外键，因此不删除 `role_runs` 或 `role_results` 主行。
专用事务按子表清除、结果墓碑、运行墓碑、题材墓碑顺序执行；普通服务账号不能
绕过不可变触发器。

正式激活前必须写入 `role_quality_samples`：保存运行 ID、确定性样本选择依据、
独立核验来源、同一截止时间、单位、复权版本、统计池、原值、核验值、差异和
处理结论。上下文不同直接标不可比较，不平均冲突值、不跨来源拼分子分母。
核验只使用同一双快照中的获授权输入，由独立实现或人工复算产生，不引入第三方
数据源；若未来接入第三方核验源，必须作为第三个 `SourceGrant` 纳入同一授权、
锁、撤权和清除链。核验覆盖全部候选，并对每个题材×角色从非候选中按规范哈希最小值固定抽取
`min(3, non_candidate_count)` 个样本；无候选时从所有可计算结果抽取
`min(3, calculable_count)` 个。没有可计算结果时必须核验对应
`role_topic_statuses` 的计数和原因。样本文件由独立复算或人工核对产生，
不能由被测计算器自报。所有 Decimal 因子、排序元组、计数和状态要求精确相等，
不设置数值容差；任何必需样本缺失或失败时，不得追加激活事件。

抽样键固定为 UTF-8 字节串
`role_run_id + \"\\n\" + topic_id + \"\\n\" + role + \"\\n\" + security_id` 的
SHA-256，小写十六进制升序。候选集合为 `candidate` 和
`simplified_candidate`；非候选集合为 `eligible_non_candidate`、`watch` 和
`pending_validation`，`excluded` 不计入可计算集合。并列候选全部核验，不抽样。

CLI：

```text
dragon-radar role-materialize \
  --market-snapshot <id> \
  --topic-snapshot <id> \
  --if-active-event <event-id|0> \
  --key <idempotency-key>
dragon-radar role-verify <role-run-id> --samples <path>
dragon-radar role-activate <role-run-id> --reason <text> --key <key> --if-current <event-id|0>
dragon-radar role-disable --reason <text> --key <key> --if-current <event-id|0>
dragon-radar role-recheck <role-run-id> --topics <ids> --key <key>
dragon-radar role-validate-pending --through-market-snapshot <id> --key <key>
dragon-radar topic-attempts [--status <status>]
dragon-radar topic-attempt-show <attempt-id>
dragon-radar topic-retry <attempt-id> --path <path> --key <key>
dragon-radar role-attempts [--status <status>]
dragon-radar role-attempt-show <attempt-id>
```

模块关闭、恢复、活动运行切换、回退和范围复核均追加事件，记录操作者、原因、
前后版本、范围和结果。激活命令在模块范围 advisory lock 下检查幂等键及
`if-current`；事件流投影是唯一权威当前状态。回退只改变活动指针，不修改快照、
运行、笔记、自选或人工复核。每个 `market_snapshot_id + visibility_mode`
同时最多有一个活动运行；只有核验通过的运行可以激活。

激活、切换、回退、恢复和 `role-recheck` 都必须在统一锁序下重新验证两份
快照内嵌授权及当前授权；激活还必须确认运行保存的前序激活事件仍是上一交易日
对应投影的权威事件。任一授权或前序事件不匹配即拒绝且不追加成功事件，调用方
须按新投影重新物化。

`role-recheck` 仅以当前代码重新计算指定题材并和原不可变运行逐字段比较，
写入核验事件，不生成第二个同组合运行。结果不同则关闭模块并要求发布新
`rule_version` 或新输入快照。输入失败后的正式重跑必须使用修订后的题材／行情
快照或新规则版本。恢复环境在复核当前授权和撤回／删除账本前保持角色模块关闭。

题材与角色尝试命令列出失败阶段、缺失行／题材、请求范围和可重跑范围。
`topic-retry` 只接受原失败范围及新幂等键，重新验证整份分页清单；部分成功仍
只追加尝试记录，不发布部分批次。角色物化失败同样追加
`role_materialization_attempts`，正式重试仍走 `role-materialize` 的完整原子
事务；不存在按题材拼接多个运行的路径。

`role-validate-pending` 是接力后验验证的唯一触发入口。命令固定读取
`through-market-snapshot` 的交易所日历，选择恰好成熟到 `T+5` 的待验证结果，
按来源 ID 排序锁定原运行及五日窗口涉及的全部来源，并重验批内与当前授权。
`role_validation_commands` 保存键和规范请求摘要；同键重放返回原结果，
不同键命中同一待验证结果时由唯一约束返回已有验证。未来新增数据不得更新
既有验证，只能通过原结果的不可变验证更正链追加记录。

## 八、人工复核

人工复核只在所有者入口开放，状态为：

- `confirmed`
- `rejected`
- `needs_evidence`

`RoleReview` 包含 `id`、`owner_id`、`role_result_id`、复核状态、
`reason`、`version`、`created_at` 和 `supersedes`。理由是 1 至 2000 个
Unicode 码点的非空纯文本，拒绝 NUL。首次记录 `supersedes IS NULL`；
更正只能指向同 owner、同 role result 的当前链头，禁止自指、分叉和环。
旧记录不可修改。

数据库建立 `(owner_id, role_result_id, id)` 唯一键和同列复合自外键。
所有查询由服务端 owner 限定。POST 携带幂等键和 `If-Match` 预期最新版本；
首建使用 `If-Match: "0"`、`supersedes=null` 并生成版本 1；后续更正使用
当前版本。缺少版本返回 428，冲突返回 409 和当前最新记录。全局锁序沿用
主体、幂等命令、排序后的来源、角色结果和复核链头。复核状态不改变
`RoleResult.status`、排名或证据。

复核草稿及结果未知命令按主体和 `role_result_id` 保存在现有 `SessionStore`
内存，不进入 URL 或持久浏览器存储。关闭抽屉、切题材、角色、运行、行情快照、
主导航、popstate、退出和 beforeunload 都使用同一离开保护。确认离开后，
迟到成功不得重开抽屉或覆盖新上下文。

## 九、API

公共只读 API：

```text
GET /api/v1/topic-snapshots
GET /api/v1/topic-snapshots/{topic_snapshot_id}
GET /api/v1/role-runs
GET /api/v1/role-runs/{role_run_id}
GET /api/v1/role-runs/{role_run_id}/topics
GET /api/v1/role-runs/{role_run_id}/topics/{topic_id}
GET /api/v1/role-runs/{role_run_id}/topics/{topic_id}/results
GET /api/v1/role-runs/{role_run_id}/limit-ladder
GET /api/v1/role-runs/{role_run_id}/changes
GET /api/v1/role-results/{role_result_id}
```

集合端点契约：

| 端点 | 集合与筛选 | 稳定排序／高水位 |
| --- | --- | --- |
| `/topic-snapshots` | 快照；交易日、来源 | `trade_date DESC, source_id ASC, revision DESC, id ASC`；固定首次 `published_at` |
| `/role-runs` | 运行；双快照、规则、visibility | `market_trade_date DESC, activated_at DESC, id ASC`；固定首次控制／激活事件序号 |
| `/role-runs/{id}/topics` | `role_topic_statuses`；题材、运行态 | `topic_id ASC`；运行不可变，无动态高水位 |
| `/role-runs/{id}/topics/{topic}/results` | 证券结果；角色、状态 | `role ASC, tie_rank ASC NULLS LAST, status ASC, security_id ASC`；运行不可变 |
| `/role-runs/{id}/limit-ladder` | 连板记录；题材、日期 | `trade_date DESC, topic_id ASC, streak DESC NULLS LAST, security_id ASC`；运行不可变 |
| `/role-runs/{id}/changes` | 变化记录；题材、角色、变化类型 | `topic_id ASC, role ASC, security_id ASC`；运行不可变 |
| `/api/owner/v1/role-results/{id}/reviews` | 当前 owner 复核 | `version DESC, id ASC`；固定首次最大版本 |

允许的服务端排序只有：

- 运行题材：`topic_id | name | valid_count | status`；
- 角色结果：`rank | security_id | status`；
- 连板记录：`trade_date | streak | security_id`。

每个允许字段支持 `asc | desc`；未指定时使用表中默认顺序。可选排序值作为
游标签名的一部分，末尾始终追加表中默认元组及记录 ID 形成唯一键。角色运行、
变化和复核列表不提供可选排序，前端不得只对当前页重新排序。

列表固定分页 20／50／100，默认 50，返回
`{items, page_size, next_cursor}`。游标绑定筛选条件、稳定排序键和排序版本；
第一页固定创建时高水位，后续页不能读入更晚记录。排序元组固定为：

- 题材快照：`trade_date DESC, source_id ASC, revision DESC, id ASC`；
- 角色运行：`market_trade_date DESC, activated_at DESC, id ASC`；
- 运行题材：`topic_id ASC`；
- 角色结果：`role ASC, tie_rank ASC NULLS LAST, status ASC, security_id ASC`；
- 人工复核：`version DESC, id ASC`。

角色运行游标还绑定首请求观察到的模块控制／激活事件序号，并按该序号重建
活动投影，避免并发激活或回退造成跨页重复、遗漏。非法、过期或与筛选不匹配的
游标返回 400。UUID、题材 ID、角色和状态严格白名单校验。

题材快照响应只携带题材快照、题材来源、截止时间、覆盖和质量。角色运行及
角色结果响应必须回显 `role_run_id`、双快照 ID、规则版本、visibility mode、
知识截止时间、`market_as_of`、`topic_as_of`、两个 provenance、各自相对
页面请求时刻的延迟和质量状态。`GET /api/v1/role-runs` 支持并严格校验
`market_snapshot_id`、`topic_snapshot_id`、`rule_version` 和
`visibility_mode`，前端不能只按日期关联运行。

题材详情返回成员的 `security_id`、有效区间、`available_at` 和安全来源引用。
运行题材详情还返回按同一合格成员集合计算的 1／5／20／60 日收益中位数、
当日上涨占比、当日收盘涨停占比、当日题材总成交额相对此前 5 个交易日总成交额
均值的变化，以及各指标应有／有效／排除／缺失数量。分母为 0 或任一日覆盖
不完整时对应指标为不可用。消息事件不在本批实现，固定返回
`events_status=not_implemented` 和空列表；因此本批只完成 `REQ-M02` 的题材
成员与量价指标部分，不宣称完成事件部分。

默认公共列表只返回各市场快照和 visibility mode 的当前活动运行。
`GET /api/v1/role-runs/{role_run_id}` 只返回当前活动或曾经激活且仍获授权的
历史运行；从未激活、核验失败或模块关闭时不可作为公开候选数据读取。
所有 `/role-runs/{id}/...` 子端点和直接 `/role-results/{id}` 端点先执行同一
父运行公开资格检查；知道子资源 ID 不能绕过激活、核验、模块状态或授权门禁。

统一错误体为 `{code, message, domain?, retryable, current?}`，稳定错误码包括：

- 400 `invalid_query | invalid_cursor | invalid_identifier`；
- 401 `session_expired`；
- 403 `source_access_denied | entry_forbidden`，前者的 `domain` 为
  `market | topic | role_evidence`；
- 404 `not_found`，同时覆盖不存在和非当前 owner；
- 409 `idempotency_conflict | version_conflict`，仅所有者响应可携带 `current`；
- 422 `quality_rejected`；
- 428 `precondition_required`；
- 503 `storage_unavailable | module_disabled`。`module_disabled` 携带关闭原因、
  控制事件 ID 和可用时的替代活动运行元数据，不包含失权证据。

所有者 API：

```text
GET  /api/owner/v1/role-results/{role_result_id}/reviews
POST /api/owner/v1/role-results/{role_result_id}/reviews
```

角色事实读取时在同一事务重新验证行情与题材的批内授权和当前授权。不存在、
无权和来源失权保持不同内部原因，对外不泄漏其他主体资源。所有者复核列表
在来源展示权撤回后仍可返回 owner 自己的复核 ID、状态、理由、版本和
`role_result_id` 墓碑，但不返回证券、题材、因子、证据或其他来源派生内容；
保留权撤回后的可读字段继续服从删除账本。

## 十、前端

新增“龙头梯队”顶层页面：

- 顶部沿用交易日与行情快照，并显示匹配的题材快照、规则版本和物化时间。
- 顶部并列显示 `market_as_of`、`topic_as_of`、各自来源时间／未知、provenance
  和相对页面请求时刻的延迟；`retrospective` 使用持续可见标识，不混入
  “当时可见”模式。
- 左栏为题材列表，显示有效／应有／缺失成员和运行状态。
- 主区为六类角色切换与候选矩阵，显示并列名次和状态。
- 独立区域展示连板矩阵和角色变化；触板输入不完整时隐藏触板统计并说明原因。
- 证据抽屉显示因子值、单位、窗口、来源、反证、缺失及规则定义。
- 个股抽屉和完整详情增加角色证据入口。
- 私人构建在证据抽屉中显示人工复核历史和追加操作；默认构建只读。

URL 保存 `view=leaders`、`role_run`、`visibility_mode`、`topic`、`role`、
`role_result`、分页和排序，不保存
复核正文或所有者凭证。`role_run` 必须是 UUID：

- 仅指定 `role_run` 时，先读取运行，再用一次 `replaceState` 补齐其
  `snapshot` 和 `date`；
- 同时指定 `role_run` 与 `snapshot/date` 时必须完全匹配，否则显示版本冲突，
  不回退；
- 仅指定 `snapshot` 且未指定 `visibility_mode` 时固定使用
  `contemporaneous`；显式选择 `retrospective` 后才读取回溯运行。只查询
  `market_snapshot_id + visibility_mode` 精确相同的活动运行：
  零个显示“尚未生成”，一个可规范化选中；若服务端异常返回多个活动运行，
  显示版本冲突并禁止自动选择；
- 切换行情快照清除 `role_run`、`topic`、`role_result` 并重置分页；
- `topic`、`role`、运行状态、分页及 leaders 专用排序字段均进入白名单；
  非法参数只执行一次规范化，不产生历史循环。

规范 URL 始终包含 `snapshot` 和 `date`。独立列表使用命名空间参数：
`topic_cursor/topic_size/topic_sort/topic_order`、
`result_cursor/result_size/result_sort/result_order`、
`ladder_cursor/ladder_size/ladder_sort/ladder_order`、
`change_cursor/change_size`、`review_cursor/review_size`；切换其所属资源只
清除对应游标。

无题材快照、运行无题材、题材深链不存在、筛选为空、输入不足和完整输入无候选
分别显示独立状态。只有同一资源键的网络错误或 5xx 可保留旧数据并标旧时间；
版本、选择或授权变化必须清空旧候选。

授权状态按 `market`、`topic`、`role_evidence` 分域维护代次。任一依赖来源失权
立即清除对应事实、因子和证据缓存，但不误删其他仍获权数据或所有者原创复核
理由；导航选择保留，便于授权恢复后重读。

全局最多打开一个详情抽屉；角色证据抽屉与证券抽屉互斥。渲染前验证响应中的
`role_result_id`、`role_run_id`、`topic_id`、`role` 和 `security_id`
全部匹配当前请求。抽屉使用模态语义，打开后聚焦关闭按钮，Esc 关闭并把焦点
还给触发控件；若触发控件已经卸载，则聚焦页面主标题，再回退到当前角色选择器。
上下文变化或撤权时关闭并清除事实。375px 下为全屏，内部独立滚动，不造成
页面整体横溢。

来源引用只以转义文本展示。外链仅允许 `https`，使用新窗口安全属性；服务端
不自动抓取引用，也不把未获 AI 权限的内容发送到模型。

移动端按题材列表、角色选择、候选表、证据详情顺序纵向展开；表格仅在自身
容器横向滚动。颜色不单独承担候选、缺失或复核状态语义。

## 十一、失败与降级

- 没有匹配角色运行：显示“尚未生成”，不请求临时计算。
- 双快照日期或截止时间不兼容：物化拒绝并记录原因。
- 题材少于 5 个有效成员：六类角色显示 `insufficient`。
- 个别证券缺输入：该证券按角色排除，原因进入证据。
- 完整输入但无人满足条件：显示 `no_candidate`。
- 物化中断：事务回滚；同键重试不重复写入。
- 来源失权：受影响 API 返回 403，页面清除对应事实但保留导航上下文。
- 旧请求返回：按现有请求代次机制丢弃，不覆盖新题材或角色。
- 人工复核冲突：保留输入，展示当前服务端记录后重试或追加更正。
- 模块关闭或运行回退：页面立即停止展示已停用运行，保留 URL 上下文并说明
  当前活动版本；不得清空自选、笔记或复核历史。

## 十二、测试与验收

### 12.1 领域与计算

- 冻结模型、Unicode、时区、未知字段、分页清单、重复和有效区间。
- `knowledge_cutoff` 两侧输入、回溯模式、未来成员和未来行情扰动。
- 20／60 日窗口、奇偶中位数、最大回撤约束、成交份额、偏差率和
  补涨非重叠窗口的精确端点。
- 正、负、零、缺失、停牌、ST、退市整理、证券类型、上市历史不足、
  跨交易所、单位冲突和除零。
- 六类角色的能力等级与运行状态、无候选、dense rank 和 Decimal 原值并列。
- 连板、`N天M板`、断板、触板未封不可用及角色变化不可比。
- 接力 `T+5` 成熟、四种后验结果、重复验证幂等及未来输入不改写旧验证。
- 输入顺序变化不改变规范化结果。
- 未来数据加入或未来题材修订不改变历史运行。

### 12.2 存储与 API

- 导入、分页缺口、发布、修订、不可变触发器、限制删除外键和幂等冲突。
- 双快照与规则摘要唯一性、事务回滚、组合锁、双键并发物化和命令账本。
- 物化与每次读取在锁内重新校验两个来源的批内授权和当前授权；分别撤权。
- 质量抽样的同截止时间、单位、复权和统计池；上下文不同不可比较；
  差异不平均，失败阻断激活。
- 活动运行切换、模块关闭、范围复核、回退、后验验证命令，以及所有操作前的
  双来源授权和恢复前删除账本门禁。
- 游标绑定、查询白名单、稳定排序、统一错误码及无跨快照混读。
- 人工复核的复合外键、主体隔离、If-Match、幂等、并发和无分叉更正链。
- 来源引用控制字符、危险协议、转义和安全外链。

### 12.3 前端

- URL 一次性规范化、仅 `role_run`、仅 `snapshot`、双参数错配、零／一／多
  匹配运行及切换快照清理上下文。
- 无题材快照、运行无题材、题材不存在、筛选为空、正式候选、简化候选、
  观察、待验证、证据不足和无候选。
- 连板矩阵、角色变化和不可比原因。
- 证据抽屉响应身份错配、互斥抽屉、反证、缺失原因、双来源分别撤权和迟到响应。
- 人工复核成功、冲突、结果未知、会话恢复、全部离开入口及离开后的迟到成功。
- 首条复核 `If-Match: "0"`、脏复核草稿叠加分域撤权、撤权期间迟到 POST，
  以及 `role_run` 解析与默认行情快照选择的竞态。
- 默认构建零 owner 请求且不加载私人复核模块。
- 两套 Playwright 均完成“选题材→选角色→开证据”；私人套件继续完成复核。
- 375／768／1024／1440px、CSS 200%、键盘、焦点恢复、灰度和 axe。

### 12.4 最终门禁

- Ruff 零问题、`compileall` 通过、后端全量测试通过。
- 前端单测、默认与私人构建、bundle 门禁、两套 Playwright 通过。
- PostgreSQL 迁移与不可变约束在容器中验证。
- 使用独立合成题材和行情数据执行真实浏览器验收。
- 正常库在验收前后保持无测试数据。
- 独立规格审查和质量审查均无未关闭 P0/P1/P2。

## 十三、交付边界

第四批完成后可声明：

- 题材成员和角色结果具备可追溯、不可变、可复算的本机 P0 基线；
- 六类角色均有明确状态和证据入口；
- 趋势龙头与中军可在完整数据下输出正式候选；
- 情绪龙头与补涨只输出标注清楚的日线简化候选；
- 跟风与接力龙头不输出未经时序验证的正式结论；
- 人工复核与规则结果严格分层。

不得声明真实数据覆盖、因子有效性、未来收益、AI能力、监管结论、
盘中能力、公网分享或完整 P0 发布已经完成。
