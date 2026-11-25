# 全57需求与16扩展接口收口矩阵

日期：2026-11-24。来源：对 d90a02e 的静态逐子句审计。
本表保留历史实现分类与真实剩余责任；它不是本次源码的执行成功收据。
B1 排名方向子句已实现并通过本次有界验证，B2–B7 统一设计已获用户批准，
模块实现和集中复核修复已交付；B8 五项有界补证已有分次凭据，
最终单次五场景及完整统一源码门禁待执行。
下表分类和“真实剩余”保留原静态审计时的事实，不表示当前代码仍缺这些功能。
本次各批次关闭范围以[收口状态](all-requirements-status-2026-11-24.md)及新验收收据为准。
原审计中下载补证范围由 [B8 矩阵](acceptance/all-requirements-residual-matrix-2026-11-24.md)
进一步收窄：六个现有 CSV 入口已有历史有界证据，本次修改仍需新源码验证。
本次状态与设计见 [收口状态](all-requirements-status-2026-11-24.md)。

分类：B=有界实现，C=历史专项工程子句覆盖，G=明确功能缺口，
V=缺针对性证据/适用性决定，D=真实输入或批准条件。
分类可并存，不能换算为整条需求已完成或完成百分比。

| ID | 验收子句 | 已审计分类 | 真实剩余 | 收口责任 |
| --- | --- | --- | --- | --- |
| REQ-G01 | 日期/版本选择；显式未知ID返回404，不以日期/最新版本回退；历史只读不重算 | B+C | 真实许可历史；不再列未知ID修复 | B9, D1 |
| REQ-G02 | 证券/题材/消息分组查询，实际归档目标和total，不猜当前映射 | B+C | 真实完整历史D；legacy缺detail维持unavailable，不当已补齐 | B8, D1 |
| REQ-G03 | 保留筛选、排序、分页和可恢复滚动位置；未保存笔记先确认离开 | C | 专项已覆盖原滚动缺口；转录可用断言，不自动关闭所有选择排列 | B8, B9 |
| REQ-G04 | 与当前筛选及版本一致；CSV公式注入转义 | B+V | 所有适用列表真实下载解码、metadata/单位/排序范围矩阵；独立export许可 | B8, D1, D4 |
| REQ-M01 | 指数卡片点击查看指数详情；原始情绪观测与研究分分离 | G+B | 独立指数price-history归档/接口/详情，不能用stock或市场数据伪造；真实指数许可 | B2, D1 |
| REQ-M02 | 题材上涨/涨停占比、成员收益中位数；成员不足5只标样本不足；可下钻 | B+C | 精确median/pool子句转录；真实成员history | B8, D1 |
| REQ-N01 | 列表：标题、来源、发布/获取时间、类型、AI摘要前100字；超历史截止材料不进入分析 | G+B | 列表仅available_at，缺published/received显示与AI100字；list serializer缺received_at/ai_summary；详情不是替代 | B4, D1 |
| REQ-N02 | event_id去重聚类 | B | 真实多来源语义聚类质量；本次未复审全部已bounded代码 | B8, D1 |
| REQ-N03 | 驳回保留审计；AI建议未经确认不写正式成员池 | B+C | 真实语义质量/许可；把P1不变/P2生效与409/撤权证据逐子句转录 | B8, D1, D3 |
| REQ-L01 | 六类角色，多选，支持证据/反证/缺失证据 | B+C | 正式factor/rule批准与全因子字段显示证据，不将six groups当所有factor完成 | B8, D2 |
| REQ-L02 | 历史梯队、连板/触板、固定昨日池反馈 | B | 封板/continuity排除与历史水位专项映射；真实输入 | B8, D1 |
| REQ-L03 | rule_version变化标记和不可比较原因 | B+V | 角色change的全部窗口/版本标签精确断言；rotation-only不可代替 | B8, D2 |
| REQ-P01 | N=1..250默认20需N+1；休市起顺延/止回退；缺价排除不前移；缺基准不填0；排序分页；超5秒转异步 | B+G+V | 双向rank B1；UI只GET rank，job handoff须B6适用性/强制慢路径证据；真实history | B1, B6, D1 |
| REQ-P02 | 100起点归一化收益、正回撤、缺段中断不连线、最多5 | B+V | plot/table/download精确值和custom/adjustment排除子句专项矩阵 | B8, D1 |
| REQ-S01 | 独立完整页；有OHLC才绘K线；role/seal/news/regulation同PUB | B+C | 真实OHLC/news历史；各内容缺项保留理由，非all fields关闭 | B8, D1 |
| REQ-U01 | LOCAL自选加入时间/原始PUB上下文、移除/撤销/重开 | B+C | 改名/duplicate/backup/dirty-late组合精确证据转录 | B8 |
| REQ-U02 | IndexedDB唯一权威；事务提交+回读S06；revision乐观锁/BroadcastChannel S07；失败不丢输入 | B+V | quota/abort/closedDB/multitab子句映射，不仅suite总数 | B8 |
| REQ-U03 | 确定性/AI/人工分层；60s提示、120s模板；晚到不覆盖保存文本；用户独立采纳 | B+C | 真实model quality；公开daily discovery由A04独立承担 | B3, D3 |
| REQ-U04 | 原始确认判断与后续验证期事实并列；支持/削弱/否定，纠正追加 | B+C | unavailable/not-due/correction/restore conflicts证据转录 | B8 |
| REQ-R01 | 完整性/数值计算/正式事件三区域；数据完整不等于安全 | B+D | 真正适用规则、event completeness attestation/独立批准 | B8, D1, D2 |
| REQ-R02 | threshold/warning_threshold独立；未经评审pending、不输出approaching | B+D | 官方复核/发布；源精度UI证据转录 | B8, D2 |
| REQ-R03 | 正式事件/限制附原文，actual restriction interval不等于计算 | B+C+D | 真实原文/许可/限制范围 | D1, D2 |
| REQ-R04 | 官方来源+回归样例、编制/复核/发布；撤回停新计算不删除旧结论 | B+D | 真实official records；精确operator regression/withdrawal chain转录 | B8, D2 |
| REQ-Q01 | 六时间戳：源事件、供应商发布、首次接收、计算完成、快照发布、页面更新；UTC存/北京时间显示；刷新不改源，未知不标实时 | G+B+D | daily六时钟archive/wire/display与域延迟未闭环；legacySnapshot固定source_time=null；真实18:30 SLA另D | B4, D1 |
| REQ-Q02 | 高低价倒置、负成交量、重复键进隔离；停牌不填0 | B+D | 实际supplier malformed schema/input；精确条款映射 | B8, D1 |
| REQ-Q03 | 同时间/单位/复权/统计池抽样比较；不平均冲突值、不拼不同来源分子分母 | G+D | 有界检索未找到双来源可比sample/workflow/report；现有quality/hash核验不等于reconciliation | B5, D1 |
| REQ-Q04 | 最后有效版本+影响范围；题材成交缺失停发中军排行，规则缺失待核验；受影响模块停新分析 | B+V | same-key transient retain已有；每affected analysis disable/stale label/unaffected LOCAL整链专项仍需映射 | B8 |
| REQ-D01 | storage/display/export/AI/sharing五权独立并对过期/撤销再授权 | D+B | 实际合同/账号/字段/用途/期限，synthetic grant不是许可 | D1, D4 |
| REQ-D02 | 采集、分页续传、幂等重试与原始lineage；缺页隔离 | B+D | 真实iFinD字段/响应/config/许可与首次采集；旧source-contracts不应用于判定后来collection不存在 | B8, D1 |
| REQ-D03 | snapshot不可变/修订新建；输入水位和publication sequence固定，不读时重算 | B+C | 真history D；concurrent/replay/watermark精确证据转录 | B8, D1 |
| REQ-D04 | 批次重跑、回退不清笔记 | B+V | server replay与LOCAL restore分开已有；未确认同次operator rollback→last valid后原note revisions/history selection不变证据；先加场景不假定代码坏 | B8, D1 |
| REQ-A01 | 无正文不声称全文解读；事件/主体/事实/不确定项和引用；关键事实定位原文 | B+C+D | 真实supported-fact质量/文本授权；citation target通过不等于all facts模型正确 | B8, D1, D3 |
| REQ-A02 | 证券ID须主数据存在；直接证据/推断区分；未经审核不修改正式成员池 | B+C+D | 真model suggestion语义与许可；审查生效链工程已有 | B8, D1, D3 |
| REQ-A03 | 引用因子、反证、缺口和失效条件；数字匹配快照、错配拦截 | B+C+D | 所有entry contexts/conditions映射；真model quality | B8, D3 |
| REQ-A04 | 每日事实/解释/待验证分层，点击展开完整AI分析；失败模板、晚到不覆盖编辑 | G+C+D | 独立公共reviewed-daily archive/publish eligibility/discovery不存在；owner job/private draft不是公共归档 | B3, D3, D4 |
| REQ-A05 | ≥100人工样本/开发冻结分离；事实支持≥95%、数字和重算100%；越权/假监管/未来冒充/伪造校验硬失败阻断；版本回退 | D+B | 真实唯一human samples/outputs/independent approval与release rollback证明 | D3 |
| REQ-E01 | RT-01~RT-12授权、时间、证券、session、幂等、乱序、PIT、缺口、价量、cooldown、replay/no-write | C+B+D | 工程12项有具体map/111三页；真实supplier latency/rights未闭 | B9, D1 |
| REQ-E02 | 龙虎榜source-scoped席位/金额/近历史、unknown与0区分，无person归因 | B+V+D | 已有keyboard/null/unknown/history断言；mixed-source/mapping/revision整链专项不足，许可D | B8, D1 |
| REQ-E03 | 词云点击词筛相关消息，licensed corpus，未计算sentiment为null并披露方法 | B+D | term-filter实际目标证据转录；真实语料/许可/方法 | B8, D1 |
| REQ-E04 | 题材演进/核心更替timeline，historical event PUB/version，不用current pool | B+D | 全event return/version break映射与真实history | B8, D1 |
| REQ-E05 | 白名单结构化自然语言，preview→显式confirm→execute，改文本/PUB使确认失效 | B+V | edited/denied/cache/return完整生命周期映射，不放宽SQL边界 | B8, D1 |
| REQ-B01 | PIT回测、因子体检、walk-forward、阶段条件调权；公开公式/输入版本/覆盖/限制 | B+V+G+D | health UI仅factor_coverage不等于纯health报告；深层result/manifests/folds语义需B7。生产FAC-R01 baseline已有受限adapter；real phase/其他方法扩展B7+D | B7, B8, D1, D2 |
| REQ-B02 | LOCAL模拟账本/T+1/涨跌停/成本，无broker；AI同池增益对照 | B+D+G | 账本engineering有界；AI leg disabled正确但不是真实comparison收益腿实现；需有限adapter+真实评测/PIT inputs | B7, D1, D3 |
| REQ-X01 | 双监听器、回环握手、CSRF、公开不连private、不信forwarded headers，构建/origin隔离 | C+B+D | 正式平台TLS/分享许可；工程actual trust与denial已有，非所有生产条件 | B9, D4 |
| REQ-X02 | 只读retrieval/白名单functions；Token/并发/超时/费用；无密钥/整库泄露或注入执行 | C+B+D | 真实provider permission/model quality，synthetic egress不是实provider授权 | B9, D3 |
| REQ-X03 | 显示撤回与获批存储删除分离；授权到期停读，旧结论/独立LOCAL按政策保留 | B+C+D | 真实删除政策、withdrawal/seal/replay专项转录；不把owner session期限修复当all source policy | B8, D1 |
| REQ-V01 | 七页同快照不丢上下文，market→证据→LOCAL复盘闭环 | C+B | 七表面任务覆盖，不自动覆盖所有fields、download和权限组合 | B8, B9 |
| REQ-V02 | 红涨绿跌+符号；角色中性标签；监管不等于涨跌 | B+C | 全状态非颜色线索精确断言转录 | B8 |
| REQ-V03 | 身份列/单位/排序/分页；20/50/100默认50，null-last；rank双向排序为已冻结缺口 | G+B+V | 固定return/excess/amount DESC、drawdown ASC，无order API；八列表sticky/unit/identity和decoded exports不能仅凭generic pass关闭 | B1, B8 |
| REQ-V04 | 全局一个drawer，repeat/Esc关闭/回焦点，返回恢复，dirty确认 | C+B | 原focus/scroll engineering covered；不要抹掉历史RED，不扩成所有新feature lifecycle | B9 |
| REQ-V05 | 图表口径、最多5、第6阻止，缺口不补0、不连跨缺口 | B+V | 图/table/download exact值、basis labels和all entry limit映射 | B8 |
| REQ-V06 | 四档视口/实际200%zoom/对比度与键盘主任务；PRD明列375和1440，中档按授权清单 | C | 此明确engineering matrix已当前覆盖；不把旧CSS/text-enlarge证据替代真实zoom，也不宣称all business rows关闭 | B9 |
| REQ-V07 | 真实进度/reduced motion/防重复，60s处理中/120s模板 | C+B+V | AI clock子句covered；每async control/reduced-motion具体assertion仍B8，不泛化计数 | B8, B9 |
| REQ-V08 | 原文引用精确定位；LOCAL冲突持续提示不静默覆盖 | C+B+V | 所有field类型/无关navigation持久提示/revocation组合映射；真实fragment rights | B8, D1 |
| REQ-V09 | 浅色/深色切换与UI-only偏好，不存业务payload | B+C | toggle和matrix证据可转录，不转换成每field visual parity | B9 |
| REQ-V10 | 36px紧凑/44px舒适，两种密度持久化 | B+V | 所有适用表精确尺寸/keyboard hit area子句映射 | B8 |
| REQ-V11 | Ctrl+K命令面板；loaded memory index/static allowed commands，native dialogs优先 | B+C+V | IME/empty index/access组合映射；不是完整历史grouped search | B8 |
| EX01 | POST /api/analysis/jobs：snapshot/start/end/benchmark/adj/symbols<=5/key→job_id,status；owner/CSRF/immutable input | B+V | API已有，Reset rank UI无caller；当前fast GET不能证明/否定强制慢路径handoff适用性 | B6, D1 |
| EX02 | GET /api/analysis/jobs/{job_id}：job_id,status,result,failure,input；initiating principal/cache再授权 | B+V | UI submit/poll/failure/return未接线；不fabricate daily envelope，不从backend isolation推UI completed | B6 |
| EX03 | association_id/decision/expected_revision/key→revision,audit_id；新topic publication后才生效 | B+C | 精确409/unknown/revoked映射；真实source association evidence | B8, D1 |
| EX04 | source+quote_batch identity、event/received/delay/status/exact quotes，explicit未知404不latest fallback | B+C+D | 真实supplier freshness/latency/许可，不将synthetic delay算真实SLA | B9, D1 |
| EX05 | 分钟batch/date/OHLC/volume/amount/revision/final/available_at；parent AND bar PIT，不gap fill | B+C+D | 真实全history与许可；工程PIT/finality子句已有专项 | D1 |
| EX06 | cursor limit1-100、events/next_cursor，source/rule/batch/window/value/threshold/quality；完整可遍历 | C+B+D | 旧无loadMore结论已失效；真实anomaly输入/授权 | B9, D1 |
| EX07 | source-defined sector net_inflow points/unit；unavailable null+reason，不用turnover proxy | B+C+D | 真实净流字段定义/许可 | D1 |
| EX08 | date/as_of/source/version/quality/basis/seven-field coverage/data；page/size/total/disclosure revision | B+V+D | multi-page/mixed source/version/basis整链断言映射；真实permitted rows | B8, D1 |
| EX09 | date/days1-250，source-scoped seat/mapping_version/contributions/revisions/exact金额，无person归因 | B+V+D | keyboard/30day/unknown/404已有；跨source/mapping revision历史UI全链未专项核实，真实source许可 | B8, D1 |
| EX10 | term/document_count/article_ids/sentiment/tokenizer_version，licensed fields且PUB固定 | B+D | click-filter实际目标/方法完整性转录与真实语料 | B8, D1 |
| EX11 | events at/sector/core/change/comparable/reason/input_manifest/历史PUB及returns，不用当前成员 | B+D | whole event target/return/version-break子句转录；真实historical archive | B8, D1 |
| EX12 | text1-500/PUB/key→query_id/typed_ast/explanation/unsupported；optional parser不能执行 | B+V | unsupported/edit/session/CSRF/bounded-body具体UI证据转录；model permission另D | B8, D3 |
| EX13 | confirmed_ast_hash/PUB/key→fields/rows<=100/total/coverage；拒绝changed或unapproved AST | B+V | edited/denied cached result/return context组合精确UI映射；permitted fields | B8, D1 |
| EX14 | snapshot/manifests/strategy/parameters/period/cost/execution/experiment/key→run/status；owner immutable config/input | B+V+D | actual kernel合成与cancel已有；真实PIT production scope限FAC-R01 baseline；阶段/额外获批方法B7+D | B7, B8, D1, D2 |
| EX15 | owner record run/status/config_hash/research_only/result/failure；result内manifests/metrics/trades/rejections/equity/folds/baseline/ablation/quality | B+V+G+D | validateRunRecord只outer/object-or-null；run/config/hash/深层manifest/metric/health/fold语义未闭；UI展示存在不是deep lineage通过。AI variant真实收益仍disabled | B7, B8, D3 |
| EX16 | score0-100/null、phase/null、components/method_version/coverage/research_only，归档读取不重算、不声称概率 | B+D | contribution/series/null精确显示映射与真实historical inputs | B8, D1 |

历史证据仍绑定原提交；最终整合必须对本次源码重新冻结、执行并保留原始日志。
真许可、规则批准、模型评估、分享权与20真实交易日不能由合成门禁代替。
