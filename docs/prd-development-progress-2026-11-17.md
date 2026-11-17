# DragonRadar PRD 与开发进度对照

日期：2026-11-17。需求基线：[开发需求PRD](file:///E:/ByteDance/Projects/Code/DragonRadar/docs/DragonRadar_开发需求%20PRD.md)。
实现基线：`.worktrees/task27-research`，分支 `feature/task27-research`，包含未提交修改。
当前源码身份：`source-sha256:c88f1e32576999090e52639412c9b4f08c488b4a8208c6c7369b9015632df4ea`。

目前处于“主要产品能力已实现、本机工程验证已有证据、真实生产材料及正式验收未闭环”的阶段。
P0 七页、快照、质量降级、浏览器复盘及AI任务链已有工程实现；
P1 实时/龙虎榜/词云/轮动/自然语言查询及偏好已有实现；
P2 已有研究回测、因子体检、滚动验证和本地模拟账本，但真实研究适配与AI收益对照仍有限制。

不能据此宣布产品100%完成或可以生产上线。以下分别描述代码覆盖、验证事实和启用条件，
不将合成回归、测试数量、文件数量或接口数量换算成总体完成百分比。

## 1. 核对方法与证据边界

本次读取PRD、当前源码及测试、分模块验收和最近交付记录，实际重跑PRD结构检查与生产诊断。
没有重新执行全量pytest、前端单测、Playwright、性能或正式CI，也未查询邮箱。

| 本次实际检查 | 结果 | 含义 |
| --- | --- | --- |
| 当前根目录PRD结构检查 | 57 REQ、29接口规格、29响应示例、30 JSON，退出0 | 文档结构及示例检查通过；不证明57项业务全部验收 |
| 当前生产证明验证器 | 退出2，5项全部blocked，trust_policy=not_configured | 当前生产准入未通过 |
| 历史Task 27 manifest与当前文件摘要比较 | 549个历史登记文件中537个一致、12个变化或缺失 | 历史全量结果不能整体重称为当前源码的全量验证；新增文件还不在该旧清单中 |
| 当前候选源码身份 | 与N1C交付记录一致 | 可定位当前实现；不是生产签章 |

PRD附录A的57项可按分期归类为47项P0、8项P1及2项P2。
P1功能表另有“板块资金流分时曲线”这一没有独立REQ-ID的体验扩展，
不能把功能表行数直接当作需求ID数。PRD的29项接口规格包含25项HTTP接口和4项IndexedDB Repository接口，
不是29个HTTP路由。

本报告中的“工程已实现”表示已找到对应源码和验收/测试追溯。
“条件启用”表示实现会按授权、数据或规则缺项关闭；关闭本身可能符合PRD。
“部分完成”表示框架存在，但需求中的实际运行或有效性证明尚未完成。

## 2. 分期与里程碑进度

| 分期/里程碑 | 工程进度 | 当前主要缺口 |
| --- | --- | --- |
| P0 / M1 基础框架 | 已实现七页导航、日期/快照上下文、抽屉与部署骨架 | 正式构建验收和实际部署尚未完成 |
| P0 / M2 数据管线 | 校验、不可变快照、发布、质量降级、供应商采集/映射/样本归档已实现 | 获许可的真实iFinD字段/账号/额度、日历、证券映射及首批真实输入尚未闭环 |
| P0 / M3 确定性页面 | 市场总览、消息、梯队、分析、个股抽屉、监管三区域已实现 | 页面中的真实内容、部分盘中字段与正式监管规则仍须相应材料 |
| P0 / M4 AI能力 | 四类任务、轮询、取消、审核、引用/数字校验及降级已实现 | 至少100个人工标注真实样本及真实模型质量门尚未通过 |
| P0 / M5 我的复盘 | IndexedDB自选/笔记、冲突控制、草稿、历史验证、恢复已实现 | 有历史本机验收证据；当前整个产品的正式验收报告仍未生成 |
| P0 / M6 涨幅分析 | 区间收益、超额、回撤、最多5标的对比已实现 | 真实同口径复权、基准与完整历史覆盖仍是运行前提 |
| P1 扩展 | 实时授权读模型、分钟K/异动/分时资金流、龙虎榜、词云、轮动、偏好及白名单NL查询已有实现 | 真实实时数据许可与延迟实测、龙虎榜/新闻等真实来源尚未完成 |
| P2 研究 | PIT回测、因子体检、walk-forward、本地模拟账本和AI对照框架已有实现 | 生产算子首版仅支持获批FAC-R01与baseline；真实阶段序列适配和AI收益腿未启用 |

源码依据：前端[页面目录](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/web/src/pages)、
[本地复盘](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/web/src/features/local-review/LocalReview.tsx)、
[HTTP路由](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/src/dragon_radar/http/routes)、
[研究内核](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/src/dragon_radar/research)。
验收依据：[历史终验与追加指引](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/docs/acceptance/reset-final.md)、
[Task 27最新验收](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/docs/acceptance/task27-research.md)。

## 3. 57项需求逐项对照

下表的状态是当前工程覆盖判断，验证事实以第5节对应日期为准。
同组源码、测试及历史验收追溯可在历史终验的REQ矩阵定位；
B01/B02采用后续Task 27文档，D02采用最新供应商交付记录。

| REQ | PRD需求 | 当前工程状态 | 剩余条件或边界 |
| --- | --- | --- | --- |
| G01 | 交易日及历史快照 | 工程已实现 | 真实发布序号、快照及可见时间须来源一致 |
| G02 | 全局搜索 | 工程已实现 | 仅检索可见/授权数据 |
| G03 | 跨页上下文保持 | 工程已实现 | 有导航与偏好测试追溯 |
| G04 | 筛选及版本一致导出 | 工程已实现 | 导出须服从实际许可；CSV公式注入已纳入追溯 |
| M01 | 指数、广度、原始情绪 | 工程已实现、条件启用 | 真实行情与完整统计池未交付 |
| M02 | 热力图、题材热度及成交 | 工程已实现、条件启用 | 题材成员、覆盖率及重叠成交按契约处理 |
| N01 | 消息列表及详情 | 工程已实现、条件启用 | 真实新闻/公告及正文展示授权待确认 |
| N02 | 消息去重聚类 | 工程已实现 | 真实来源事件关系仍需实际输入 |
| N03 | 题材建议确认/驳回 | 工程已实现、条件启用 | 人工审核与版本绑定保留；真实AI质量未证明 |
| L01 | 六类角色与证据 | 工程已实现、分层启用 | 趋势/中军正式候选；情绪/补涨简化；跟风/接力观察或待验证 |
| L02 | 连板、触板及反馈 | 工程已实现、条件启用 | 缺完整授权触板集合时不能声称掌握封板质量 |
| L03 | 规则版本变化提示 | 工程已实现 | 产品规则实现不等于正式规则获批 |
| P01 | 区间收益及排名 | 工程已实现、条件启用 | 真实同口径价格、复权与历史覆盖待接入 |
| P02 | 超额、回撤与对比曲线 | 工程已实现、条件启用 | 缺真实基准不能计算超额，缺段不能补0 |
| S01 | 个股详情抽屉 | 工程已实现 | 其中行情/消息/监管仍受各域许可与缺项控制 |
| U01 | IndexedDB自选 | 工程已实现 | 浏览器本地权威存储，不自动上传 |
| U02 | 笔记保存与冲突 | 工程已实现 | 事务提交/回读、revision与多标签冲突有追溯 |
| U03 | 复盘模板及AI草稿 | 工程已实现、条件启用 | 确定性模板可降级，真实模型质量仍未闭环 |
| U04 | 判断的历史验证 | 工程已实现 | 核验事实与笔记绑定快照，不覆盖原始规则结果 |
| R01 | 监管规则匹配 | 工程已实现、条件启用 | 适用正式规则及批准材料缺失 |
| R02 | 数值计算与接近预警 | 工程已实现、条件启用 | 未评审阈值须pending，不能输出获准预警 |
| R03 | 正式事件与限制 | 工程已实现、条件启用 | 真实公告原文与来源许可待接入 |
| R04 | 复核及撤回 | 工程已实现 | 工程生命周期已有；实际独立批准尚缺 |
| Q01 | 来源时间及延迟 | 工程已实现 | 无真实源时间不能标为实时 |
| Q02 | 字段/单位/唯一性/价格校验 | 工程已实现 | 真正厂商字段与口径需依据交付配置 |
| Q03 | 同口径抽样核验 | 工程已实现、条件启用 | 真实独立比对数据未到位 |
| Q04 | S04/S05降级 | 工程已实现 | 当前缺项时关闭新分析符合契约 |
| D01 | 来源配置与许可控制 | 工程已实现 | SourceGrant与验证器不能代替真实合同许可 |
| D02 | 采集、分页、恢复与幂等 | 部分完成：通用链路已实现 | 真正iFinD响应配置与首次获授权采集未完成 |
| D03 | 不可变快照发布/修订 | 工程已实现 | 生产快照仍需真实输入与完整版本血缘 |
| D04 | 重跑、回退及恢复 | 工程已实现 | 有历史恢复演练；正式发布流程仍未通过 |
| A01 | AI消息摘要 | 工程已实现、条件启用 | 正文许可、引用支持率及真实模型评测待补 |
| A02 | AI题材建议 | 工程已实现、条件启用 | 仅建议/草稿，不自动改不可变题材快照 |
| A03 | AI解释候选 | 工程已实现、条件启用 | 引用数字须匹配快照；真实模型质量待补 |
| A04 | AI复盘增强 | 工程已实现、条件启用 | 用户授权片段、草稿与保存判断分离 |
| A05 | 真实模型评测、升级及回退 | 部分完成：评估/验证框架已有 | ≥100个真实人工样本、无泄漏分组与真实输出未通过质量门 |
| E01 | 实时看盘与异动 | 工程已实现、条件启用 | 无真实获许可分钟/盘口输入和真实延迟验收 |
| E02 | 龙虎榜与席位 | 工程已实现、条件启用 | 真实来源/许可待补；席位标签不证明自然人归属 |
| E03 | 交互词云 | 工程已实现、条件启用 | 真实新闻覆盖及授权仍是前提 |
| E04 | 题材轮动与核心更替 | 工程已实现、条件启用 | 连续快照及成员/核心事实须可追溯 |
| E05 | 自然语言查询 | 工程已实现、受限范围 | 白名单结构化查询，不是任意自然语言执行器 |
| B01 | PIT回测、体检及滚动验证 | 部分完成：研究内核/界面已有 | 真实生产仅FAC-R01 baseline；真实阶段序列适配及其他获批方法仍缺 |
| B02 | 本地模拟组合及AI增益 | 部分完成：账本与对照框架已有 | ai_variant保持disabled，不输出该腿收益或真实增益结论 |
| X01 | 双监听器、本机握手、CSRF | 工程已实现 | 历史Docker/TLS隔离已验证；实际公开部署未获准 |
| X02 | 提示注入及AI权限隔离 | 工程已实现 | 真实模型/数据许可与评测门仍须满足 |
| X03 | 留存、删除、许可到期 | 工程已实现 | N1B/N1C补齐报告与样本fsync后授权关闭 |
| V01 | 七页状态及快照一致性 | 工程已实现 | 有跨页E2E追溯 |
| V02 | 红涨绿跌与中性角色标签 | 工程已实现 | 不用颜色替代证据或监管结论 |
| V03 | 表格身份、单位、排序、分页 | 工程已实现 | 有列表及分析E2E追溯 |
| V04 | 单抽屉、Esc回焦及返回恢复 | 工程已实现 | 采用最新研究验收证据，不把旧焦点失败当作当前未实现 |
| V05 | 五标的与缺口曲线 | 工程已实现 | 基准/缺口/最多5标的约束保留 |
| V06 | 响应式、缩放与可读性 | 工程已实现，有历史实测 | 375/1440px、200%已有证据；本次未重跑视觉验收 |
| V07 | 真实进度与减少动效 | 工程已实现 | 不以装饰动画替代任务状态 |
| V08 | 引用定位与冲突提示 | 工程已实现 | 当前实际事实仍由授权读模型提供 |
| V09 | 深色主题 | 工程已实现 | P1偏好，不阻塞P0 |
| V10 | 紧凑/舒适密度 | 工程已实现 | P1偏好 |
| V11 | Ctrl+K命令面板 | 工程已实现 | P1前端命令，不能越过owner与数据权限 |

其他P1扩展“分时资金流”也有当前源码：
[实时路由](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/src/dragon_radar/http/routes/realtime.py#L372)、
[分时面板](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/web/src/features/realtime/RealtimePanel.tsx#L213)。
缺授权或不可计算的资金流显示不可用，不推定资金方向。

## 4. 最容易误判的完成项

### 4.1 Task 27已经有工程交付

2026-10-27终验正文记载“Task 27未实现、无0014_research、无研究路由”。
同一文件2026-11-16追加指引已明确解除该旧代码边界。
当前存在研究迁移、owner研究路由、worker/内核、回测和模拟面板及对应测试。
所以B01/B02不能继续简单标成“尚未开发”，应标成“工程已有、真实运行与AI腿部分受限”。

证据：[追加指引](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/docs/acceptance/reset-final.md#L251)、
[研究验收](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/docs/acceptance/task27-research.md)、
[研究路由](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/src/dragon_radar/http/routes/research.py)、
[研究迁移](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/src/dragon_radar/migrations/versions/0014_research.py)。

### 4.2 生产计算不是尚未从零开发

生产签章验证、不可变真实输入契约、FAC-R01物化、本机生产研究worker及影子记录已有实现。
当前代码明确拒绝未经批准的方法；真实walk-forward需要实际阶段序列适配，
生产实验仅允许baseline。研究测试中的多因子和滚动验证能力不能直接证明所有真实方法已可运行。

证据：[生产计算源码](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/src/dragon_radar/research/production.py#L354-L370)、
[方法限制](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/src/dragon_radar/research/production.py#L579-L585)、
[生产接入记录](file:///E:/ByteDance/Projects/Code/DragonRadar/docs/production-readiness-implementation.md)。

### 4.3 AI任务可用不等于真实AI质量通过

PRD要求至少100条人工标注样本、开发/冻结测试分离、原子事实支持率≥95%，
数字与确定性重算一致率100%，并阻断安全硬失败。工程校验与评估框架存在，
但没有对应真实输入输出和独立证明。研究对照中的AI腿仍固定disabled。

证据：PRD §8.3、
[AI对照约束](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/src/dragon_radar/research/ablation.py)、
[生产门禁记录](file:///E:/ByteDance/Projects/Code/DragonRadar/docs/production-readiness-implementation.md)。

### 4.4 标准样本合格不等于历史PIT或真实生产准入

N1/N1B/N1C已交付采集归档映射、不可变标准样本、独立核验及持久化授权关闭。
报告仍为retrospective、historical_pit_status=unverified、production_approved=false。
21会话的20日样本检查也不能证明60/120日方法所需61/121会话已经具备。

证据：[供应商交付记录](file:///E:/ByteDance/Projects/Code/DragonRadar/docs/supplier-ingestion-implementation.md)、
[下一阶段计划](file:///E:/ByteDance/Projects/Code/DragonRadar/docs/next-development-plan.md)。

## 5. 验证进度

以下是已有实际执行记录，不能相加为一个当前全量测试数。

| 验证层 | 已记录事实 | 当前解释 |
| --- | --- | --- |
| 2026-11-16研究阶段后端全量 | 4256 passed、0 skipped，合并覆盖率92% | 对应当时HEAD及manifest；当前后续文件有变化 |
| 同阶段前端与浏览器 | Vitest1237、Node28通过；五套E2E合计325通过，研究界面另4例有trace/截图 | 当时本机工程验收，不是今天重新运行 |
| 同阶段性能 | 普通GET整体P95 1.089s；研究并发三轮P95 1.075/1.037/1.159s，均≤2.0s | 有真实PG与合成研究负载证据；不证明真实供应商延迟 |
| 同阶段隔离/恢复 | 四构建、Docker隔离/API契约、TLS10例、指定恢复1例等通过 | 有本机证据，不等于部署获许可 |
| 2026-11-16生产接入相关 | Linux相关939项、最终生产套件235项等通过 | 范围有重叠，不能叠加；不构成生产外部证明 |
| 2026-11-17 N1C供应商回归 | Windows/Linux各412 passed、零跳过、零警告 | 最近切片范围通过；不是全项目当前全量 |
| N1C静态与构建 | Ruff零问题、338文件format、compileall和wheel实际入口通过 | 当前源码有静态/构建证据 |
| 正式CI/完整受信验收报告 | 未提供当前提交绑定的远程CI或正式verify_reset full受信报告 | automated_acceptance仍unverified |

依据：[Task 27门禁及原始证据索引](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/docs/acceptance/task27-research.md)、
[manifest](file:///E:/ByteDance/Projects/Code/DragonRadar/.worktrees/task27-research/docs/acceptance/evidence/task27/manifest.json)、
[生产接入验证](file:///E:/ByteDance/Projects/Code/DragonRadar/docs/production-readiness-implementation.md)、
[N1C验证](file:///E:/ByteDance/Projects/Code/DragonRadar/docs/supplier-ingestion-implementation.md)。

## 6. 仍未完成的生产条件

本次实际运行生产验证器：5项全部blocked。共有前置缺项为外部确认的发行方、
信任策略、公钥及独立保存的策略摘要。

| 门禁 | 当前缺项 | 完成所需 |
| --- | --- | --- |
| real_input | 无获许可的冻结真实输入及对应实际内核运行证据 | 真实许可/字段/日历/证券映射/额度，真实接收与PIT/版本血缘、获批方法和实际运行 |
| official_rules | 无适用官方归档、实际规则版本与独立批准 | 冻结适用范围、官方原文、规则生命周期、回归样例与批准 |
| real_model_evaluation | 无≥100个唯一真实人工样本及实际模型输入输出证明 | 无泄漏development/frozen_test、完整版本绑定、重新评估并通过质量门 |
| public_sharing_permit | 无目标范围对应的再分发许可 | 确定主体、用途、目标、受众、内容与数据并取得许可 |
| shadow_run_20_trading_days | 无连续20个真实交易日证据链 | 按批准日历逐日真实接收、实跑、复核和签章，不批造历史日期 |

本次生产诊断原始输出存于项目外
`E:\ByteDance\Projects\Code\.temp\DragonRadar-N1C\prd-progress-production.json`。
供应商申请投递成功只证明发送事实，本次未查邮箱，不判断是否收到新许可回复。

## 7. 后续工作顺序

1. 取得真实许可和实际字段/响应材料，配置现有采集与映射入口；先预检，再在真实调用获授权后小批采集。
2. 按所选方法完整窗口核验历史身份/状态、基准和PIT，接入已有生产计算入口。
3. 补齐实际方法与官方规则批准；如需真实walk-forward或其他因子，先冻结对应实际适配规格。
4. 有真实评测材料后验证AI质量及AI研究腿；缺材料时保持disabled。
5. 经明确授权提交/推送后形成当前代码绑定的正式CI及受信完整验收。
6. 满足运行前提后开展真实逐日影子观察，取得公开分享许可后再考虑相应发布。

这些工作多数是数据、证据或运行闭环，不应重复建设已存在的页面、回测内核或归档工具。
本次仅生成进度对照报告，未修改产品源码、PRD、既有验收记录或发布状态。
