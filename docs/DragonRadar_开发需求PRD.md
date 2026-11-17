# DragonRadar A股龙头复盘与量化研究平台 — 开发需求PRD


## 1. 项目概述

### 1.1 产品定位

DragonRadar 是一套**打开即用**的 A 股盘后复盘工作台，面向需要独立复盘的 A 股研究者。核心价值：

- 每个关键数字能定位到来源、时间、单位和版本
- 每个角色标签能打开支持证据、反证与缺失项
- 完成一次"看市场、查证据、记判断"的复盘任务，不依赖多个手工表格
- AI 失效、供应商断源时仍保留可读历史与用户笔记
- 无注册登录门槛，打开网址即用

### 1.2 技术栈建议

| 层 | 技术选型 | 说明 |
|---|---|---|
| 前端 | React 18 + TypeScript + Vite | 侧栏布局，浅色研究终端 |
| 图表 | ECharts | 折线/柱状/热力图/词云 |
| 状态 | Zustand + React Query | 快照数据缓存、筛选状态保持 |
| 后端 | Python + FastAPI | REST API，静态快照读取为主 |
| 行情/题材/角色/消息/监管库 | PostgreSQL | 只读读模型快照（服务端预计算） |
| **自选/笔记权威存储** | **浏览器 IndexedDB** | **watchlist + notes 唯一权威源（本版设计决策）** |
| 浏览器偏好 | localStorage（仅 UI 偏好/索引） | 主题/密度/筛选/上次日期/上次快照；**不存业务数据** |
| 计算 | pandas / Polars | 盘后批量计算，预生成快照 JSON |
| 部署 | Docker Compose + Caddy | 本机运行 |

> **生产证券 ID 映射（非 fixture）**：生产环境使用稳定内部 ID，格式为 `{交易所}.{本地代码}`，例如 `SZSE.300502`、`SSE.600519`，或服务端内部递增整数。本 PRD 所有 JSON 示例中的 `S00001`/`S00002` 等**仅为合成测试数据（fixture）**，不代表生产 security_id 格式。

### 1.3 免登录双入口与安全架构（H 类修正）

**双监听器设计（不能仅靠 Origin=localhost）：**

| 监听器 | 绑定 | 用途 | 对外可见性 |
|---|---|---|---|
| **私有 owner 路由** | 仅 `127.0.0.1` 回环地址，独立端口（如 `127.0.0.1:8001`） | 写操作、AI 触发、数据配置 | **公网任何方式都无法连接**（不监听外部网卡，不经过 Caddy 公网端口） |
| **公开只读网关** | `0.0.0.0:8000`（经 Caddy） | 只读已发布读模型 GET | guest_public 入口；POST/写一律 403 |

**安全要求：**

- **公网绝不连私有路由**：无论公网请求携带什么 `Host` / `X-Forwarded-*` / Cookie，公开只读网关都不会把请求转发到 `127.0.0.1:8001`。私有路由只接受来自本机回环的连接。
- **本机 owner 会话建立**：不使用用户名密码。首次启动由**本地操作系统权限启动器**（本机进程拉起时通过本机 IPC 或一次性握手）与浏览器建立会话，或通过一次性握手码（bootstrap token）完成；握手后颁发短期会话 cookie（HttpOnly + SameSite=Strict）。
- **bootstrap token 不发公网**：一次性握手码只在本机回环通道上交换，不写入公网响应、日志或 AI 提示词。
- **转发头不可信**：公开网关对 `X-Forwarded-For` / `X-Forwarded-Host` 等不做 owner 判定；owner 身份只由"连接是否来自本机回环 + 本地握手会话"决定。
- **CSRF 防护**：SameSite=Strict + 本机握手会话绑定来源。
- **guest_public**：仅 GET/HEAD 只读已发布读模型；POST/写/AI 触发无有效本机会话一律 403。
- **限流**：公网只读网关 20 并发/IP。
- **数据许可**：未授权数据字段不出现在公网响应中。

---

## 2. 功能架构与分期

### 2.0 2026-09-20 已批准的全范围修订

用户已批准本轮执行原 P0/P1/P2 的完整范围，无需再次审批。P0/P1/P2 仅保留为历史分类，不再表示延期：实时分钟异动、龙虎榜席位、板块分时资金流、词云、轮动时间轴、自然语言查询、深色主题、密度、命令面板、回测、模拟组合、因子体检/滚动验证/条件调权、AI 增益对照、0-100 情绪研究分与阶段标签均为本次必交。

研究输出必须公开公式、输入版本、覆盖率和限制；不是未来收益概率、投资承诺或官方监管结论。模拟组合仅研究账本，不接券商、不提交真实订单、不管理真实仓位。真实来源、授权、正式规则和模型评测分别是生产启用门禁，缺失时交付可运行合成测试链路与明确禁用原因，不把模拟数据宣称为真实接入。

原有附录 A 的 57 个 REQ 和 7.2/7.3 的 29 项编号保持不变；新增接口单列于 [工作台契约](contracts/workbench-api.md)。决策以 [DEC01-12](superpowers/specs/2026-09-20-prd-reset-decisions.md) 为准，执行任务与最终验收见 [实施计划](superpowers/plans/2026-09-20-prd-reset-refactor.md) Task21-29；Task20 仅为基础中期验收。

### 2.1 P0 / MVP（首期必须完成）

| # | 功能 | 来源 | 对应PRD需求ID |
|---|---|---|---|
| 1 | 市场总览页（指数、涨跌分布、涨停生态、原始情绪观测值） | PRD原创 + gushifupan借鉴 | REQ-M01, REQ-M02, FAC-M01~M04 |
| 2 | 市场消息页（新闻/公告列表、AI摘要、关联题材） | PRD原创 + gushifupan借鉴 | REQ-N01, REQ-N02, REQ-N03, REQ-A01, REQ-A02 |
| 3 | 龙头梯队页（六类角色、连板矩阵、梯队布局） | PRD原创 + gushifupan借鉴 | REQ-L01, REQ-L02, REQ-L03 |
| 4 | 涨幅分析页（区间收益、超额、回撤、多标的对比） | PRD原创 | REQ-P01, REQ-P02 |
| 5 | 个股详情抽屉（行情、角色证据、消息、封板信息） | PRD原创 + gushifupan借鉴 | REQ-S01, REQ-A03 |
| 6 | 监管观察页（三区域：数据完整性 / 数值计算状态 / 正式事件限制） | PRD差异化核心 | REQ-R01~R04（均P0） |
| 7 | 我的复盘页（自选股、笔记、AI复盘草稿） | PRD原创（**IndexedDB 本地存储**） | REQ-U01, REQ-U02, REQ-U03, REQ-U04 |
| 8 | 交易日切换 + 历史快照查看 | PRD原创 | REQ-G01 |
| 9 | 全局搜索（按代码/名称查股票） | PRD原创 | REQ-G02 |
| 10 | AI四功能（摘要、题材建议、候选解释、复盘增强）+ 任务轮询/取消/审核 | PRD原创 + gushifupan借鉴 | REQ-A01~A05 |
| 11 | 数据预计算与静态快照发布 | PRD原创 | REQ-D01~D04 |
| 12 | 数据质量降级（S04/S05状态） | PRD原创 | REQ-Q01~Q04 |

### 2.2 原 P1（本次必须完成）

| # | 功能 | 来源 | 对应PRD需求ID |
|---|---|---|---|
| 1 | 实时看盘与价量/盘口异动监控（含RT-01~RT-12） | PRD规划（原始PRD §11.1 为 P1） | **REQ-E01** |
| 2 | 龙虎榜与游资席位 | gushifupan借鉴 | REQ-E02 |
| 3 | 板块资金流分时曲线 | gushifupan借鉴 | —（P1 体验扩展） |
| 4 | 新闻词云（交互式） | gushifupan借鉴 | REQ-E03 |
| 5 | 题材轮动时间轴 / 题材演进 / 核心更替 | gushifupan借鉴 | REQ-E04 |
| 6 | 深色夜间主题 | PRD规划 | REQ-V09 |
| 7 | 列表密度切换（紧凑/舒适） | PRD规划 | REQ-V10 |
| 8 | 命令面板（Ctrl+K） | PRD规划 | REQ-V11 |
| 9 | 自然语言查询 | gushifupan借鉴 | REQ-E05 |

> 注：REQ-E01 为实时授权快照、分钟K与应用内异动监控，本次实现；生产启用仍须授权与延迟实测分项通过。RT-01~RT-12 的本轮可测试边界在 Task21 冻结，不据旧文档引用虚构实时供应商。

### 2.3 原 P2（本次必须完成，研究限定）

| # | 功能 | 来源 |
|---|---|---|
| 1 | 历史回测与模拟组合 | PRD规划 REQ-B01/B02 |
| 2 | 因子体检、滚动验证与条件调权（含市场状态五分类） | PRD规划 §11.6 |
| 3 | AI增益对照与模拟组合 | PRD规划 REQ-B02 |

> "冰点/修复/高潮/退潮"等阶段标签与 0-100 情绪分均为**研究假设**，本次在独立研究区输出；保留原始观测，不替代角色证据与监管状态，不称未来上涨概率。算法、阶段边界与缺失处理按 Task28 版本化公开。

---

## 3. 全局设计规范

### 3.1 状态体系（S01-S07）

| 状态 | 条件 | 页面行为 |
|---|---|---|
| **S01 正常** | 请求成功且关键数据完整 | 显示截至时间、快照、来源、覆盖率和证据 |
| **S02 加载** | 首次进入或刷新 | 骨架屏；刷新保留旧日期和旧版本，禁止重复提交 |
| **S03 无结果** | 无候选、历史不足或被过滤 | 列出原因、排除数量和重置入口；不造股票或填0 |
| **S04 降级** | 超时、过期、覆盖不足、冲突 | 只读最后有效快照，醒目标记旧时间；停用受影响的新分析 |
| **S05 禁用** | 无访问/展示权限、规则未验收 | 说明具体原因；不加载未授权内容，不标记风险正常 |
| **S06 保存成功** | 笔记/自选通过校验，**IndexedDB 事务提交成功并回读验证一致** | 显示保存时间和 revision；重新打开可恢复 |
| **S07 写入冲突** | 另一标签页/会话已修改同一笔记（revision 乐观锁不匹配） | 保留当前输入，提供对照与另存，不静默覆盖；多标签页经 BroadcastChannel 通知 |

### 3.2 视觉规范

**色彩体系：**

| 语义变量 | 浅色值 | 深色值 | 用途 |
|---|---|---|---|
| 页面背景 | #F6F8FA | #0B1017 | 主工作区背景 |
| 卡片表面 | #FFFFFF | #141B24 | 数据卡片 |
| 浮层 | #F2F5F8 | #1B2430 | 抽屉、弹窗 |
| 边框 | #E5EAF0 | #283442 | 分割线 |
| 主要文字 | #111827 | #F3F6FA | 正文 |
| 次要文字 | #4B5563 | #AAB6C4 | 副标题 |
| 操作色（蓝） | #2563EB | #3B82F6 | 按钮、链接、选中态 |
| AI点缀（青） | #0891B2 | #0891B2 | AI标签（只标来源类型，不代表正确性） |
| 上涨（红） | #D9363E | #E5484D | A股红涨 |
| 下跌（绿） | #11995A | #18A058 | A股绿跌 |

> 六种角色用中性文字标签区分，不配六种高饱和色；监管状态不等于涨跌方向。

**布局尺寸：**

| 项目 | 规格 |
|---|---|
| 侧栏宽度 | 220-240px（窄屏折叠） |
| 顶部状态栏 | 56-64px |
| 页边距 | 20-24px（移动端16px） |
| 表格行高 | 36px（紧凑）/ 44px（舒适），本次均交付 |
| 抽屉宽度 | 360-420px（手机全屏） |
| 正文字号 | 14px；辅助 12px；数字 tnum 右对齐 |

### 3.3 交互约定

- **股票单击**：打开详情抽屉；再次点击或按 Esc 关闭；全局只允许一个个股抽屉
- **返回列表**：保留筛选、排序、分页和可恢复滚动位置；未保存笔记先确认离开
- **分页**：默认50条/页，可切换20/50/100条；空值置后
- **图表对比**：最多同时比较5个标的；第6个被阻止
- **空值显示**：显示"—"，不用0替代
- **全局状态栏**：固定显示交易日、数据截至时间、股票池、数据质量状态
- **导出**：与当前筛选及版本一致；CSV 公式注入转义

---

## 4. 页面详细需求（7 页，指定顺序）

> 顺序：4.1 市场总览 → 4.2 市场消息 → 4.3 龙头梯队 → 4.4 涨幅分析 → 4.5 个股详情抽屉 → 4.6 监管观察 → 4.7 我的复盘。
> 每页给出：ASCII 布局图、组件字段、交互、状态（S01-S07）、关联 REQ-ID。

### 4.1 市场总览页

**ASCII 布局：**

```
+------------------------------------------------------------------+
| 顶部状态栏: 交易日 2026-09-18 | 截至 18:30 | 数据质量: 正常          |
+------------------------------------------------------------------+
| [指数横条 横向滚动] 沪深300+1.06% | 创业板+2.25% | 上证+0.94% ...     |
+------------------------------------------------------------------+
| 涨停生态: 2家涨停 / 0家跌停 / 最高2板 / 晋级率 1/2=50%             |
+-------------------+-------------------+----------------------------+
| 涨跌分布          | 情绪原始观测       | 题材热度TOP                |
| 涨3 跌1 平1       | 涨停数/跌停数     | 合成题材甲(1成员3)         |
| 封板率 2/3=66.67% | 连板高度/晋级率   | 合成题材乙(1成员2)         |
|                   | 昨涨今中位收益    |                            |
+-------------------+-------------------+----------------------------+
```

**组件清单：**

| 组件 | 数据字段 | 交互 | 状态 | 关联REQ-ID |
|---|---|---|---|---|
| AI每日速览 | 事实摘要、解释、待验证事项分层 | 点击展开完整AI分析 | S04降级时显示确定性模板版 | A04/U03 |
| 指数卡片 | 指数代码(B前缀)、名称、最新价、涨跌幅(小数)、成交额 | 点击查看指数详情 | 缺指数显示"—" | M01 |
| 情绪观测曲线与独立研究区 | 日期、原始观测值；研究区另列0-100分、阶段、公式版本 | hover显示原始观测与研究分贡献明细 | 数据不足显示"—"；研究分/阶段不充当预测概率 | FAC-M04, M01/M02 |
| 涨停情绪热力图 | X轴=日期，Y轴=连板数，颜色=当日涨停家数 | 点击某天跳转龙头梯队页 | 无数据日期不渲染 | L01/L02 |
| 涨跌分布 | 上涨数、下跌数、平盘数、涨停数、跌停数、封板率(分子/分母) | 点击数字筛选 | 分母为0显示"不适用" | M01, FAC-M01 |
| 板块资金流TOP10 | 板块名称、涨跌幅、成分股数 | 点击下钻题材详情 | S04时显示旧时间标记 | M02 |
| 题材热度排行 | 题材名称、上涨占比、涨停占比、成员收益中位数、成员数 | 点击进入题材详情抽屉 | 成员不足5只标"样本不足" | M02, FAC-T01 |

**API需求**：`GET /api/market/overview`、`GET /api/market/sentiment`、`GET /api/market/heatmap`、`GET /api/market/sector-flow`

---

### 4.2 市场消息页

**ASCII 布局：**

```
+------------------------------------------------------------------+
| 工具栏: 日期[2026-09-18] 类型[全部▾] 来源[全部▾] 搜索[____]        |
+----------------------------+-------------------------------------+
| 消息列表(左)               | 消息详情 / AI摘要(右)               |
+----------------------------+-------------------------------------+
| [公告] 合成股份A涨停公告   | 原文标题 + URL                      |
| [新闻] 合成题材甲行业新闻   | AI摘要(事实/推断分层)               |
| [研报] 合成股份B研报       | 关联题材/个股 [确认关联][驳回]      |
+----------------------------+-------------------------------------+
```

**组件清单：**

| 组件 | 数据字段 | 交互 | 状态 | 关联REQ-ID |
|---|---|---|---|---|
| AI潜在题材 | 题材名、支持新闻数、候选个股 | 点击"确认加入"→进入审核；不自动生效 | 仅建议，未经确认不改正式池 | A02/N03 |
| 消息列表 | 标题、来源、发布/获取时间、类型(news/announcement/research)、AI摘要前100字 | 点击选中→右侧详情 | 无结果列原因；超出历史截止时点材料不进入历史分析 | N01/N02 |
| 消息详情 | 原文标题、URL、发布时间、AI摘要、关联题材/个股 | 展开原文、确认/驳回关联 | AI摘要失败显示"AI暂不可用"模板 | A01/N01 |
| 消息关联 | 题材名、关联个股、关联类型（直接证据/AI推断） | 确认/驳回按钮 | 驳回保留审计；AI建议未经确认不写正式成员池 | N03 |
| 词云（本次） | 关键词、频次、情感倾向及方法版本 | 点击词→筛选相关消息 | 无授权文本禁用；情感未计算时为null | E03 |

**API需求**：`GET /api/news`、`GET /api/news/{article_id}`、`POST /api/ai/summarize`、`POST /api/ai/topic-suggest`

---

### 4.3 龙头梯队页（核心页面）

**ASCII 布局：**

```
+------------------------------------------------------------------+
| 日期[2026-09-18] 题材[全部▾] 角色[全部▾]                          |
+------------------------------------------------------------------+
| 连板矩阵(行=日期, 列=板数)                                        |
|   板数      1板            2板            3板                     |
|   09-18    S00001(1)     S00002(1)      —                        |
|            合成股份A      合成股份B                                |
+------------------------------------------------------------------+
| 晋级率: 昨日≥2板集合 2 → 今日续封 1 / 可交易 2 = 50%             |
| 昨涨停今中位收益: +1.2%                                           |
+------------------------------------------------------------------+
```

**股票卡片内容**：合成证券 ID（S 开头六位，仅 fixture）+ 合成名称、涨跌幅（小数）、封板时间、题材标签（1-3个）、六类角色标签（情绪龙头/趋势龙头/中军/跟风/补涨/接力龙头）、标签旁标注证据状态（candidate / insufficient / waiting_verify）。

**组件清单：**

| 组件 | 数据字段 | 交互 | 状态 | 关联REQ-ID |
|---|---|---|---|---|
| 梯队矩阵（日期×板数） | 板数层级、单元格股票数、股票列表、角色标签、题材 | 点击股票→详情抽屉；点击题材→筛选；点击单元格→下钻个股 | 无候选层级显示"今日无"；允许无候选、允许并列 | L01/L02/L03 |
| 连板晋级率 | 昨日≥2板集合、今日继续封板数、今日有效可交易数（分子/分母） | hover显示计算明细 | 分母为0显示"不适用" | L02, FAC-M04 |
| 昨日涨停反馈 | 昨日涨停集合今日收益中位数（小数）、上涨/下跌数 | 点击查看明细 | 今日不可评价样本单独披露 | FAC-M04 |
| 角色筛选 | 六类角色多选 | 选中后高亮对应股票 | — | L01 |
| 角色变化 | 新增/退出/并列/不可比 | 切换日期对比 | 算法版本改变单独标记 | L03 |

**API需求**：`GET /api/limit-up/ladder`、`GET /api/role/candidates`、`GET /api/role/evidence`、`GET /api/limit-up/continuity`

---

### 4.4 涨幅分析页

**ASCII 布局：**

```
+------------------------------------------------------------------+
| 区间工具: [20日▾] 基准[沪深300▾] 复权[前复权▾] 对比槽[0/5]         |
+------------------------------------------------------------------+
| 排名 代码     名称       区间收益  超额收益  最大回撤  [加入对比]  |
| 1    S00001  合成股份A  +15.0%   +13.8%   3.0%      [+]         |
| 2    S00002  合成股份B  +10.0%   +8.8%     2.0%      [+]         |
| ...                                                              |
+------------------------------------------------------------------+
| 归一化对比曲线(100起点, 最多5条; 缺段中断不跨缺口连线)            |
+------------------------------------------------------------------+
```

**组件清单：**

| 组件 | 数据字段 | 交互 | 状态 | 关联REQ-ID |
|---|---|---|---|---|
| 区间工具栏 | N交易日(默认20,1~250)/自定义起止、基准指数、复权方式 | 切换窗口 | start>end 报错；休市自动回退并显示调整后窗口 | P01 |
| 排行表格 | 排名、S代码、名称、区间收益(小数)、超额收益(小数)、最大回撤(正值小数)、成交额变化、题材、[加入对比] | 排序、分页、加入对比 | 排除数量和原因可查；不前移基期凑数据 | P01/P02 |
| 对比曲线区 | 归一化收益曲线(100起点)、回撤曲线，最多5标的 | hover读数、切标的 | 第6个被阻止；缺失段中断不跨缺口连线 | P02/V05 |

**收益边界（按原始PRD §5.3 与 TCF-01）**：
- N交易日收益 = P(T)/P(T-N)-1，需 N+1 个日历位置，N=1~250，默认20。
- 自定义开始日遇休市顺延、结束日遇休市回退至最近交易日，显示调整后窗口。
- 自定义同一有效交易日起止 → 收益为 0。
- 停牌/异常缺价/起止价格无效的股票不进入有效排行，排除数与原因可查，不前移基期。
- 基准缺失时超额收益不可计算（不填0），单独标记。
- 最大回撤 = max_t[1 − P(t)/max(开始日至t) P]，结果为正值（0.05 = 5%）。
- 复权价用于研究收益，原始价+当日有效涨停价用于涨停判断，不混用。

**API需求**：`GET /api/rank/range`、`GET /api/compare/curves`

---

### 4.5 个股详情抽屉

**ASCII 布局：**

```
+--------------------------------------------------------------+
|  S00001 合成股份A            11.00  +10.00%            [ × ] |
+--------------------------------------------------------------+
|  角色证据: [情绪龙头·候选]  ▼展开证据链                        |
|    支持: 首板涨停, 封单 2.1e8                                  |
|    反证: 20日均线未突破                                        |
|    缺失: 盘中封单明细(来源未授权)                             |
+--------------------------------------------------------------+
|  封板: 首封09:35 末封14:55 开板0 封单2.1e8                    |
|  关联消息 2 条 | 题材 1 个                                    |
|  监管: 活跃事件 0                                             |
+--------------------------------------------------------------+
```

**组件清单：**

| 组件 | 数据字段 | 交互 | 状态 | 关联REQ-ID |
|---|---|---|---|---|
| 抽屉摘要 | 合成ID/名称、最新价、涨跌幅(小数)、缩略K线 | 单击预览；明确按钮进完整页；Esc回焦点 | 仅收盘数据画收盘线，不生成假蜡烛 | S01/V04 |
| 角色证据区 | 角色标签、因子值、支持证据、反证、缺失证据、规则版本 | 点击"证据▼"展开证据链 | 证据不足显示原因，不填0 | L01/A03 |
| 封板信息 | 首次封板时间、末次封板时间、开板次数、封单金额 | 展开明细 | 缺盘中字段标"未授权/不可用" | L02/TCF-03 |
| 关联消息/题材 | 消息数、题材数 | 点击展开 | 无消息显示空态 | N01/N03 |
| 监管状态 | 活跃事件数、事件类型 | 跳转监管观察页 | 未知始终可见，不合并为"安全分" | R01-R03 |
| 完整详情页 | 独立路由 `/stock/{security_id}?date=` | 完整K线、证据链、消息时间线、监管时间线 | 有OHLC才绘K线 | S01 |

**API需求**：`GET /api/stock/{security_id}`、`GET /api/stock/{security_id}/kline`、`POST /api/ai/explain-stock`

---

### 4.6 监管观察页（差异化核心）

**ASCII 布局：**

```
+------------------------------------------------------------------+
| 区域1 数据完整性: 预期5 有效4 排除0 缺失1  → partial             |
+------------------------------------------------------------------+
| 区域2 数值计算状态(阈值独立 + 评审开关)                          |
|  abnormal_10day  窗口10 阈值0.70 预警0.65 计算0.68 已评审→approaching |
|  abnormal_20day  窗口20 阈值0.70 预警0.65 计算0.62 待评审→pending     |
+------------------------------------------------------------------+
| 区域3 正式事件/限制: [监控] S00001 合成股份A 09-18~09-28        |
+------------------------------------------------------------------+
```

**组件清单：**

| 区域 | 数据字段 | 交互 | 状态枚举 | 关联REQ-ID |
|---|---|---|---|---|
| 区域1 数据完整性 | 覆盖率(expected/valid/excluded/missing)、缺失数、来源校验状态 | 展开分母与原因 | complete / partial / missing | R01, Q02/Q04 |
| 区域2 数值计算状态 | 规则ID+版本、窗口、基准、**threshold、warning_threshold、review_switch**、计算值、重置依据 | 查看规则原文与窗口 | 待核验pending / 未触发idle / 接近预警approaching_alert(仅已评审) / 计算触发triggered | R02 |
| 区域3 正式事件/限制 | 事件类型、对象、公告来源、有效起止时间、限制范围 | 查看原文、区分异常/严重异常/监控/实际限制 | disclosed / monitoring / restricted | R03 |

**阈值独立规则（G 类修正）：**
- `threshold`（法定/规则阈值）与 `warning_threshold`（产品自设提醒阈值）**分列**，不混用。
- `review_switch`（pending/approved/rejected）是独立评审开关；**未评审冻结时默认 `pending`，即使计算值 < threshold 也不输出 `approaching_alert`**。
- 只有 `review_switch=approved` 且 `warning_threshold ≤ computed_value < threshold` 时才标 `approaching_alert`。
- fixture 中的阈值为**合成示例，非监管真实条款**。
- 封板率/晋级率必须给分子与分母及计算精度（见 5.4）。

**规则配置字段（按原始PRD §7.3）**：适用交易所/板块/证券状态、生效与失效日期、计算窗口、方向和阈值、**预警阈值、评审开关**、基准指数、普通异动计数方式、重算条件、官方原文、复核记录、测试样例。

**复核与撤回**：规则须绑定官方来源+回归样例，记录编制/复核/发布；测试未通过无法启用。撤回只停用新计算，不删除旧结论。

**禁区**：不展示"安全再涨几个板"等规避监管建议；不展示"无风险"绝对化表述；不把监管状态与涨跌颜色混为一谈；数据完整不等于"无监管风险"。

**API需求**：`GET /api/regulation/status`、`GET /api/regulation/events`（P0 即返回真实正式事件；无事件时空数组）

---

### 4.7 我的复盘页

**ASCII 布局：**

```
+------------------------------------------------------------------+
| 自选股 (浏览器 IndexedDB)          | 复盘笔记 (浏览器 IndexedDB)   |
+------------------------------------+------------------------------+
| S00001 合成股份A  加入 09-18 19:00 | [复盘0918] 编辑中...          |
| S00002 合成股份B  加入 09-18 19:05 | 保存[revision 2] 自动回读验证 |
+------------------------------------+------------------------------+
| AI复盘草稿(分层: 事实/解释/待验证) [人工编辑确认] [重新触发校验]  |
+------------------------------------------------------------------+
```

**存储说明（v1.3 关键决策，A 类修正）**：

- **自选与笔记存储在浏览器 IndexedDB，是唯一权威源**。这是对原始 PRD 服务端语义的明确本版适配。
- **localStorage 仅用于 UI 偏好/索引**（主题/密度/筛选/上次日期/上次快照），不存业务数据。
- **S06 保存成功 = IndexedDB 事务提交成功后立即回读验证一致**。
- **S07 写入冲突 = revision 乐观锁不匹配**；多标签页通过 `BroadcastChannel('dr-sync')` 广播变更，另一标签页收到后重新拉取并提示。
- **guest_public 无法读取 owner 的私有 IndexedDB 数据**：浏览器 origin 隔离 + 私有路由不监听公网，二者独立保证。
- **owner 本机 AI 调用需要服务端许可**，但个人数据（自选/笔记）**不自动上传**；AI 请求只携带用户显式授权的片段与只读快照 ID。
- **不提供"默认 published 笔记分享"功能**：用户未授权分享私有自选/笔记；v1.2 的 `visibility=published` 字段与"guest 读已发布子集"已删除。
- notes/watchlist 的 GET/POST 在第 7 章标注为**浏览器 Repository 接口（IndexedDB），不是 HTTP 端点**。

**组件清单：**

| 组件 | 数据字段 | 交互 | 状态 | 关联REQ-ID |
|---|---|---|---|---|
| 自选股列表 | 合成ID/名称、加入时间、题材上下文、加入时快照 | 加入/移除（幂等）、撤销移除 | 重复加入不重复记录；改名不断关联 | U01 |
| 笔记编辑区 | 标题(1-100字)、正文(≤20000字)、判断/依据/失效条件、验证日期、绑定快照、revision | 保存(幂等键+revision 乐观锁)、历史对照 | 事务提交+回读一致；冲突保留输入并提示另存 | U02/U04/S06/S07 |
| AI复盘草稿 | 事实/解释/待验证事项分层草稿 | 旁置生成按钮，人工编辑确认 | 无AI仍可用确定性模板；晚到响应不覆盖已编辑内容 | U03/A04 |
| 验证记录 | 原判断、验证期事实、支持/削弱/否定/未到期 | 并排回看 | 主观判断不自动算命中率 | U04 |

**API需求**：Repository 接口（IndexedDB）：`listNotes`/`putNote`、`listWatchlist`/`putWatchlist`；`POST /api/ai/daily-review`（HTTP，仅 owner）。

---

## 5. 六类角色与因子体系

### 5.1 六类角色契约

角色主键 = **证券 × 题材 × 观察时点 × 角色类型 × 规则版本**。以下是证据框架，不是已验证选股公式。

| 角色 | 必要证据 | 候选逻辑 | 不成立/降级条件 |
|---|---|---|---|
| **情绪龙头** | 连板、短期相对强弱、成交关注、封板记录 | 比较题材内高度与活跃度，封板质量单独列因子 | 缺盘中字段→简化候选，不声称掌握封板质量 |
| **趋势龙头** | 20/60日相对收益、回撤、持续性 | 长期相对强且回撤约束通过，独立排序 | 历史不足、复权冲突→不评分 |
| **中军** | 多日成交份额、流通规模、持续活跃 | 识别容量与持续成交中心，不只看最大市值 | 题材成交覆盖不完整→暂停排名 |
| **跟风** | 同题材依据、晚于核心启动的时序 | 启动滞后且相对核心偏弱，保留观察区间 | 仅相关性→显示"跟涨观察"，不确认因果 |
| **补涨** | 前期相对落后、当前转强、共同事件依据 | 先落后后转强的两个不重叠窗口验证 | 低价小市值不能单独成立 |
| **接力龙头** | 旧核心转弱、新核心承接和后续持续 | 连续历史快照和时序证据验证 | 当日不能预知后续→P0显示"待验证" |

**规则**：无候选是有效结果；并列保留不强制每个题材选一只；候选分/分位数不是未来上涨概率。

### 5.2 核心因子目录（完整计算口径与数据前提）

| 因子ID | 名称 | 计算或观察口径 | 数据前提 |
|---|---|---|---|
| FAC-M01 | 市场广度 | 有效上涨数/有效可比数；高于MA20的有效证券数/MA20有效样本数 | P0日线；同时给出应有、有效、合法排除、缺失数 |
| FAC-M02 | 市场成交变化 | 同池当日总成交额/此前20个交易日总成交额均值−1，基期不含当日 | P0；池版本变化须可解释 |
| FAC-M03 | 指数趋势与波动 | 指数C/MA20−1、C/MA60−1；20日收益率标准差分别输出 | P0；基准ID、窗口、非年化口径明确 |
| FAC-M04 | 情绪生态 | 涨跌停数、连板高度、晋级率、昨日涨停集合今日有效收益中位数；独立附加0-100研究分与阶段标签，公开版本和贡献 | 固定昨日集合，披露今日不可评价样本；研究分不替代原始观测 |
| FAC-T01 | 题材扩散 | 题材广度、成交份额、成员收益中位数；连续活跃成员须先定义活跃阈值 | P0已知成员快照；不将跨题材重叠成交累加为市场总额 |
| FAC-R01 | 相对强弱 | RS_N=个股N日收益−同窗口市场基准收益，N=20/60/120；差值显示百分点 | P0 |
| FAC-R02 | 均线趋势 | C(T)/MA_k(T)−1；MA_k(T)/MA_k(T−5)−1，k=20/60 | P0；需 k+5 个有效日历位置 |
| FAC-R03 | 新高与回撤 | C(T)是否>此前60/120个交易日最高收盘；20/60日窗口收盘最大回撤另列 | P0；新高基期不含当日 |
| FAC-C01 | 容量与承接 | 截至T的20日平均成交额、题材成交份额、换手稳定性、可获得的流通规模 | P0；字段不足不替代 |
| FAC-C02 | 量价配合 | 成交额变化；窗口内上涨日/下跌日平均成交额分别观察 | P0；无某类样本时为"不适用" |
| FAC-L01 | 跟随与补涨 | 固定历史核心后的相对收益差、已知启动滞后；前期补涨缺口=题材收益中位数−个股收益 | P0日级候选 |
| FAC-N01 | 消息证据 | 来源、发布/首次可用时间、原文关联、新颖事件标记和反证分别记录 | P0原文证据 |

### 5.3 收益计算公式

```
N交易日收益   = P(T) / P(T-N) - 1            （N=1~250，需 N+1 个日历位置）
自定义区间收益 = P(结束日) / P(开始日) - 1
区间超额收益   = 个股区间收益 - 同窗口基准区间收益
归一化曲线     = 100 × P(t) / P(开始日)
最大回撤       = max_t[1 - P(t) / max(开始日至t) P]   （结果为正值）
```

**边界条件**：P 为相同复权因子版本收盘价；结束日遇休市回退最近交易日，开始日顺延；同有效交易日起止收益为0；停牌/缺价股票不入有效排行；基准缺失时超额不可计算；比率内部按小数存储、展示为百分数。

### 5.4 透明统计指标（含分子/分母）

| 指标 | 定义（分子/分母） | 边界 |
|---|---|---|
| 上涨/下跌/平盘 | 同一统计池内有效日涨跌幅 > / < / = 0 的证券数 | 停牌、缺价、不适用项另列；up+down+flat=有效总数 |
| 封板率 | 分子=收盘仍涨停数；分母=当日曾触板的去重证券数；比值，保留4位小数 | 同源同池；分母为0记"不适用"；P0仅当授权触板集合完整才启用 |
| 连板数 | 截止当日连续交易日收盘处于有效涨停价的次数 | 停牌日中断自算连续序列；缺数据时"未知" |
| 连板晋级率 | 分子=昨日≥2板集合中今日继续封板数；分母=该集合今日有效可交易数 | 排除项单列；非首板晋级率 |
| 题材成交份额 | 个股成交额/同日同题材有效成员成交额之和 | 跨题材重叠，不直接相加当市场总额 |
| 成交额变化 | 当日成交额/此前5个交易日平均成交额−1 | 基期不含当日；不足或基期为0记"不适用" |

---

## 6. 核心数据模型

### 6.1 数据域首期必需字段

| 数据域 | 首期必需字段 | 关键边界 |
|---|---|---|
| 证券与日历 | 证券标识、名称历史、交易所、板块、上市/退市、ST、停复牌、交易日历 | 历史状态而非当前状态；无状态不进入角色池 |
| 行情与指数 | OHLC、成交量/额、涨跌幅、复权因子、基准指数日线 | 价格版本和单位；交易日填充不能伪装成交 |
| 规模与流动性 | 流通市值、总市值、换手率、量比及适用口径 | 量比等字段有独立定义 |
| 涨停生态 | 有效涨跌停价、收盘封板、盘中触板、首末封、炸板次数、封单时点 | 缺盘中字段不从日线推造 |
| 题材成员 | 分类ID、成员、加入/退出、信息可用时间、来源和人工修订 | 无法取得历史成员时历史题材分析禁用 |
| 消息与公告 | 原文ID、标题、许可摘要/正文、URL、发布/首次获取时间、修订记录 | 文章数和事件数分开 |
| 监管 | 规则原文、有效区间、事件类型、公告ID、对象及限制范围 | 未披露不等于不存在 |

### 6.2 数据库表结构（服务端 PostgreSQL，只读读模型）

> 下列所有 JSON 示例中的 `S00001` 等为**合成测试数据（fixture）**；生产 security_id 用 `SZSE.300502` 式映射。每张表逐行给出：字段名 / 类型 / nullable / 约束 / 索引。

**表1：证券基础信息（security）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| security_id | VARCHAR(32) | NOT NULL | PK；生产格式 `{EXCHANGE}.{CODE}` 或内部递增 | PK |
| code | VARCHAR(10) | NOT NULL | 当前展示代码，不作为历史身份 | IDX(exchange, code) |
| name | VARCHAR(50) | NOT NULL | 当前名称 | — |
| exchange | VARCHAR(10) | NOT NULL | SSE/SZSE/BSE | IDX(exchange, board) |
| board | VARCHAR(20) | NOT NULL | 板块 | IDX(exchange, board) |
| list_date | DATE | NOT NULL | 上市日 | — |
| delist_date | DATE | NULLABLE | 退市日 | — |
| is_st | BOOLEAN | NOT NULL DEFAULT false | ST 标记 | — |
| is_suspended | BOOLEAN | NOT NULL DEFAULT false | 停牌标记 | — |
| source | VARCHAR(50) | NOT NULL | 数据来源 | — |

**唯一键**：`(security_id)` PK。security 不含 valid_from，不得引用不存在字段建唯一约束；代码可跨交易所重复，代码复用/名称变更不能合并历史证券。历史属性仅存入下表，稳定 ID 永不重新分配。

**表1b：证券状态历史（security_state_history，独立表）**

> 不能用单行 PK + valid_from 假装历史；状态变更逐条落表。

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| history_id | BIGINT | NOT NULL | PK（内部递增） | PK |
| security_id | VARCHAR(32) | NOT NULL | FK→security.security_id | IDX(security_id) |
| state_type | VARCHAR(20) | NOT NULL | code/name/is_st/is_suspended/board | IDX(security_id, state_type) |
| old_value | VARCHAR(100) | NULLABLE | 变更前值 | — |
| new_value | VARCHAR(100) | NULLABLE | 变更后值 | — |
| valid_from | DATE | NOT NULL | 生效起 | — |
| valid_to | DATE | NULLABLE | 生效止（NULL=当前） | — |
| source | VARCHAR(50) | NOT NULL | 来源 | — |

**复合唯一键**：`UNIQUE (security_id, state_type, valid_from)`；有效区间为 `[valid_from, valid_to)`，同证券同属性不得重叠，NULL 仅代表未结束。

**表2：行情日K（daily_kline）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| security_id | VARCHAR(32) | NOT NULL | FK | IDX(trade_date) |
| trade_date | DATE | NOT NULL | 交易日 | IDX(trade_date) |
| open | DECIMAL(10,3) | NOT NULL | 开盘价 | — |
| high | DECIMAL(10,3) | NOT NULL | 最高价（≥open/low/close） | — |
| low | DECIMAL(10,3) | NOT NULL | 最低价（≤open/high/close） | — |
| close | DECIMAL(10,3) | NOT NULL | 收盘价 | — |
| volume | BIGINT | NOT NULL | 成交量（股，≥0） | — |
| amount | DECIMAL(18,2) | NOT NULL | 成交额（元，≥0） | — |
| change_ratio | DECIMAL(8,6) | NOT NULL | 涨跌幅（小数） | — |
| adj_factor | DECIMAL(12,6) | NOT NULL | 复权因子（>0） | — |
| limit_up_price | DECIMAL(10,3) | NULLABLE | 有效涨停价 | — |
| limit_down_price | DECIMAL(10,3) | NULLABLE | 有效跌停价 | — |
| turnover_rate | DECIMAL(8,4) | NULLABLE | 换手率 | — |
| float_mv | DECIMAL(18,2) | NULLABLE | 流通市值 | — |
| total_mv | DECIMAL(18,2) | NULLABLE | 总市值 | — |
| source | VARCHAR(50) | NOT NULL | 来源 | — |
| version | VARCHAR(20) | NOT NULL | 快照版本（约束引用本列） | — |

**复合唯一键**：`UNIQUE (security_id, trade_date, source, version)`。

**表3：涨停/连板记录（limit_up_record）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| security_id | VARCHAR(32) | NOT NULL | FK | IDX(trade_date, continuous_boards) |
| trade_date | DATE | NOT NULL | 交易日 | IDX(trade_date, continuous_boards) |
| is_limit_up | BOOLEAN | NOT NULL | 收盘涨停 | — |
| touched_limit | BOOLEAN | NULLABLE | 盘中触板（无盘中数据时为空） | — |
| first_seal_time | TIME | NULLABLE | 首封时间 | — |
| last_seal_time | TIME | NULLABLE | 末封时间 | — |
| break_count | INT | NULLABLE | 开板次数（≥0） | — |
| seal_amount | DECIMAL(18,2) | NULLABLE | 封单金额 | — |
| continuous_boards | INT | NOT NULL DEFAULT 0 | 连续涨停板数（≥0） | IDX(trade_date, continuous_boards) |

**复合唯一键**：`UNIQUE (security_id, trade_date)`。

**表4：题材成员（sector_member）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| sector_id | VARCHAR(20) | NOT NULL | 题材ID | IDX(security_id) |
| security_id | VARCHAR(32) | NOT NULL | 成员 | IDX(security_id) |
| valid_from | DATE | NOT NULL | 业务加入日 | — |
| valid_to | DATE | NULLABLE | 业务退出日 | — |
| source | VARCHAR(20) | NOT NULL | official/manual/ai_suggest | — |

**复合唯一键**：`UNIQUE (sector_id, security_id, valid_from)`。

**表5：角色候选结果（role_candidate）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| security_id | VARCHAR(32) | NOT NULL | FK | IDX(as_of_date, role_type) |
| sector_id | VARCHAR(20) | NOT NULL | 题材 | IDX(as_of_date, role_type) |
| as_of_date | DATE | NOT NULL | 观察时点 | IDX(as_of_date, role_type) |
| role_type | VARCHAR(20) | NOT NULL | emotion_leader/trend_leader/zhongjun/genfeng/buZhang/jieli（同股多角色） | IDX(as_of_date, role_type) |
| evidence_status | VARCHAR(20) | NOT NULL | candidate/insufficient/waiting_verify | — |
| factor_values | JSONB | NOT NULL | 因子值 | — |
| supporting_evidence | JSONB | NULLABLE | 支持证据 | — |
| opposing_evidence | JSONB | NULLABLE | 反证 | — |
| missing_evidence | JSONB | NULLABLE | 缺失证据 | — |
| rule_version | VARCHAR(20) | NOT NULL | 规则版本 | — |
| snapshot_id | VARCHAR(32) | NOT NULL | 快照 | — |

**复合唯一键**：`UNIQUE (security_id, sector_id, as_of_date, role_type, rule_version)`（**含 role_type**，允许同股多角色）。

**表6：消息与公告（news_article）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| article_id | VARCHAR(32) | NOT NULL | PK | PK |
| title | VARCHAR(200) | NOT NULL | 标题 | — |
| content | TEXT | NULLABLE | 许可范围正文 | — |
| source | VARCHAR(50) | NOT NULL | 来源 | — |
| url | VARCHAR(500) | NOT NULL | 原文URL | — |
| published_at | TIMESTAMPTZ | NOT NULL | 发布时间 | IDX(event_id, published_at) |
| received_at | TIMESTAMPTZ | NOT NULL | 首次获取时间 | — |
| article_type | VARCHAR(20) | NOT NULL | news/announcement/research | — |
| event_id | VARCHAR(32) | NULLABLE | 聚类事件ID | IDX(event_id, published_at) |
| ai_summary | TEXT | NULLABLE | AI摘要 | — |
| ai_entities | JSONB | NULLABLE | AI实体/关联 | — |
| revision | INT | NOT NULL DEFAULT 1 | 修订号 | — |

**复合唯一键**：`(article_id)` PK；事件按 `event_id` 聚类。

**表7：监管规则表（regulation_rule）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| rule_id | VARCHAR(30) | NOT NULL | 规则ID | IDX(active, effective_from) |
| rule_version | VARCHAR(20) | NOT NULL | 版本（与 rule_id 联合唯一） | IDX(active, effective_from) |
| applicable_exchange | VARCHAR(10) | NULLABLE | 适用交易所 | — |
| applicable_board | VARCHAR(20) | NULLABLE | 适用板块 | — |
| security_status | VARCHAR(20) | NULLABLE | 证券状态 | — |
| effective_from | DATE | NOT NULL | 生效日 | — |
| effective_to | DATE | NULLABLE | 失效日 | — |
| window_days | INT | NOT NULL | 计算窗口（>0） | — |
| direction | VARCHAR(10) | NOT NULL | upper/lower | — |
| threshold | DECIMAL(8,4) | NOT NULL | 法定/规则阈值 | — |
| warning_threshold | DECIMAL(8,4) | NOT NULL | 产品自设预警阈值（独立列） | — |
| review_switch | VARCHAR(20) | NOT NULL DEFAULT 'pending' | pending/approved/rejected（评审开关） | — |
| benchmark_index | VARCHAR(20) | NULLABLE | 基准指数 | — |
| normal_anomaly_count_mode | VARCHAR(30) | NULLABLE | 普通异动计数方式 | — |
| recompute_condition | VARCHAR(100) | NULLABLE | 重算条件 | — |
| official_source_url | VARCHAR(500) | NULLABLE | 官方原文 | — |
| review_record | JSONB | NULLABLE | 编制/复核/发布 | — |
| test_cases | JSONB | NULLABLE | 回归测试样例 | — |
| active | BOOLEAN | NOT NULL DEFAULT false | 撤回只置 false，不删旧结论 | IDX(active, effective_from) |

**复合唯一键**：`UNIQUE (rule_id, rule_version)`。

**表8：监管事件表（regulation_event）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| event_id | VARCHAR(32) | NOT NULL | PK | PK |
| rule_id | VARCHAR(30) | NOT NULL | 关联规则 | IDX(security_id, effective_from) |
| rule_version | VARCHAR(20) | NOT NULL | 关联规则版本 | — |
| security_id | VARCHAR(32) | NOT NULL | 涉及证券 | IDX(security_id, effective_from) |
| event_type | VARCHAR(20) | NOT NULL | abnormal/serious_abnormal/monitoring/restriction | — |
| announcement_id | VARCHAR(50) | NULLABLE | 公告ID | — |
| announcement_url | VARCHAR(500) | NULLABLE | 公告原文 | — |
| effective_from | DATE | NOT NULL | 事件覆盖期起 | IDX(security_id, effective_from) |
| effective_to | DATE | NULLABLE | 事件覆盖期止 | — |

**复合唯一键**：`UNIQUE (event_id, announcement_id)`。

**表9：快照清单（snapshot）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| snapshot_id | VARCHAR(32) | NOT NULL | PK（不可变） | PK |
| trade_date | DATE | NOT NULL | 交易日 | IDX(trade_date, status) |
| version | VARCHAR(20) | NOT NULL | initial/revision | — |
| data_domains | JSONB | NOT NULL | 数据域 | — |
| quality_flags | JSONB | NOT NULL | 质量标记 | — |
| coverage_summary | JSONB | NOT NULL | 覆盖率汇总 | — |
| published_at | TIMESTAMPTZ | NOT NULL | 发布时间 | — |
| status | VARCHAR(20) | NOT NULL | draft/published/revoked | IDX(trade_date, status) |

**复合唯一键**：`(snapshot_id)` PK；旧复盘绑定旧快照，修订新建版本。

**表12：AI任务（ai_task，服务端）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| task_id | VARCHAR(40) | NOT NULL | PK | PK |
| task_type | VARCHAR(20) | NOT NULL | summarize/topic_suggest/explain_stock/daily_review | — |
| snapshot_id | VARCHAR(32) | NOT NULL | 输入快照 | IDX(snapshot_id) |
| input_manifest_id | VARCHAR(40) | NULLABLE | 输入清单 | — |
| status | VARCHAR(20) | NOT NULL | pending/running/succeeded/failed/cancelled | — |
| model_version | VARCHAR(30) | NULLABLE | 服务端填 | — |
| prompt_version | VARCHAR(30) | NULLABLE | 服务端填 | — |
| retrieval_version | VARCHAR(30) | NULLABLE | 服务端填 | — |
| verification_status | VARCHAR(20) | NULLABLE | pending/passed/failed/needs_human | — |
| verification_run_id | VARCHAR(40) | NULLABLE | 校验运行ID | — |
| human_review_status | VARCHAR(20) | NULLABLE | unreviewed/accepted/edited/rejected | — |
| result | JSONB | NULLABLE | content_draft | — |
| cost_tokens | INT | NULLABLE | 计费 | — |
| cost_cny | DECIMAL(12,6) | NULLABLE | 计费 | — |
| latency_ms | INT | NULLABLE | 耗时 | — |

**复合唯一键**：`(task_id)` PK。

### 6.3 浏览器 IndexedDB 与 localStorage（A 类关键决策）

> **本版将原始 PRD 的服务端语义（笔记/自选）适配为浏览器 IndexedDB 本地存储，这是明确的本版设计决策。** 服务端 PostgreSQL 不再保存 owner 的私有自选/笔记。

**IndexedDB object stores（唯一权威源）：**

**表10：复盘笔记（note，IndexedDB object store `notes`）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| note_id | string | NOT NULL | keyPath | keyPath |
| local_profile_id | string | NOT NULL | 稳定本地资料ID，不是HTTP会话或权限凭证 | IDX(local_profile_id, snapshot_id) |
| title | string | NOT NULL | 1-100字 | — |
| content | string | NOT NULL | ≤20000字 | — |
| judgment | string | NULLABLE | 判断 | — |
| evidence | string | NULLABLE | 依据 | — |
| invalid_condition | string | NULLABLE | 失效条件 | — |
| verify_date | string(DATE) | NULLABLE | 验证日 | — |
| snapshot_id | string | NOT NULL | 绑定快照 | IDX(local_profile_id, snapshot_id) |
| revision | number | NOT NULL | 乐观锁版本（S07） | — |
| idempotency_key | string | NOT NULL | 幂等键 | IDX(idempotency_key) |
| updated_at | string(ISO) | NOT NULL | 更新时间 | — |

> 无 `visibility` 字段；v1.2 的"published 分享子集"已删除。guest 浏览器因 origin 隔离 + 私有路由不监听公网，无法读取。

**表11：自选股（watchlist，IndexedDB object store `watchlist`）**

| 字段 | 类型 | nullable | 约束 | 索引 |
|---|---|---|---|---|
| local_profile_id | string | NOT NULL | 稳定本地资料ID | IDX(local_profile_id) |
| security_id | string | NOT NULL | 证券 | — |
| added_at | string(ISO) | NOT NULL | 加入时间 | — |
| context | object | NULLABLE | 题材上下文与加入时快照 | — |

**复合键**：`(local_profile_id, security_id)`，重复加入幂等。profiles store 原子创建稳定 ID；HTTP owner_session 过期、轮换、退出均不改变资料空间。notes 使用 `[local_profile_id, note_id]` 复合键；完整字段、修订、幂等摘要同事务提交，提交后独立只读事务回读，详见工作台契约 Task04。

**localStorage（仅 UI 偏好/索引，不存业务数据）：**

| Key | 类型 | 说明 |
|---|---|---|
| `dr:theme` | string | 主题（light/dark，本次） |
| `dr:density` | string | 密度（compact/comfortable，本次） |
| `dr:filters:{pageId}` | object | 各页面筛选条件（不含私有笔记/凭证） |
| `dr:date` | string | 当前选中交易日 |
| `dr:firstVisit` | boolean | 是否首次访问 |

**多标签页同步**：通过 `BroadcastChannel('dr-sync')` 广播 note/watchlist 变更；其他标签页收到后重新从 IndexedDB 读取。S06 = 事务提交 + 回读验证；S07 = 乐观锁 revision 不匹配。

---

## 7. API 接口设计

### 7.1 公共响应字段（读模型 HTTP 端点）

所有读模型 GET 返回公共 envelope；`data` 片段随端点不同。notes/watchlist 不是 HTTP 端点，见 7.4。

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 5, "valid": 5, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "adjusted", "currency": "CNY", "unit": "ratio", "benchmark": "B000300"},
  "data": {}
}
```

**覆盖率口径**：`expected`=统计池当日应有记录数（分母）；`excluded`=合法无记录（停牌/不适用）；`missing`=异常缺失。恒等式：`expected = valid + excluded + missing`。未知状态不得用于缩小分母。

新版由独立 FastAPI `create_workbench_app(repo)` 装配，Repository 显式注入；保留旧 `api.py` 应用与旧前端契约直到前端迁移。领域 `legal_excluded` 仅在新版边界映射为 `excluded`。公共 Decimal 按字段精度量化后直接编码为精确 JSON 数值，不经 float；额外十进制证据字符串供超 JS 精度验证，前端不承担财务复算。

日期读接口均支持可选 `snapshot_id`：给定 ID 后固定不可变发布版本，与 date 不一致返回400，不能静默换最新；无 ID 时解析一次并由导航携带返回 ID。区间端点12-14以调整后的 end 为选择日，显式 date 必须一致；17为清单发现，18可仅搜索基础证券，涉及历史证据时绑定选择上下文。具体错误与选择规则见工作台契约。

**错误约定**：无数据/未发布快照 → 200 + `data:null` + `quality_flags:["no_data"]`；参数错误 → 400 `INVALID_PARAM`；上游错误 → 502 `UPSTREAM_ERROR`；guest 写操作 → 403 `FORBIDDEN_GUEST`；版本冲突 → 409 `VERSION_CONFLICT`。错误响应不含密钥或原始堆栈。

### 7.2 29 个端点规格总表

> 列说明："guest可读"= guest_public 经 GET 已发布读模型是否可访问；POST/写/AI 一律仅 owner_local（经本机握手会话）。编号 26-29 为**浏览器 Repository 接口（IndexedDB），不是 HTTP 端点**。编号 23-25 为**本版工程设计**。

| # | 方法 | 路径 / 接口 | 参数（名/类型/默认/边界） | guest可读 | 失败状态 | 关联REQ-ID |
|---|---|---|---|---|---|---|
| 01 | GET | /api/market/overview | date=YYYY-MM-DD 必填 | ✓ | 400/200+no_data | M01,M02 |
| 02 | GET | /api/market/sector-flow | date 必填; top_n=int 默认10, 1-50 | ✓ | 400 | M02 |
| 03 | GET | /api/market/sentiment | date 必填; days=int 默认20, 1-120 | ✓ | 400 | FAC-M04,M02 |
| 04 | GET | /api/market/heatmap | date 必填; days=int 默认60, 1-250 | ✓ | 400 | M02,L02 |
| 05 | GET | /api/limit-up/ladder | date 必填; sector=string 可选 | ✓ | 400 | L01,L02,L03 |
| 06 | GET | /api/limit-up/continuity | date 必填 | ✓ | 400 | L02,FAC-M04 |
| 07 | GET | /api/role/candidates | date 必填; role=枚举 必填; sector 可选 | ✓ | 400 | L01,L03 |
| 08 | GET | /api/role/evidence | security_id 必填; sector 必填; date 必填 | ✓ | 400/404 | L01,S01,A03 |
| 09 | GET | /api/news | date 必填; type/source 可选; page=1,size=20(1-100) | ✓ | 400 | N01,N02 |
| 10 | GET | /api/news/{article_id} | path; date 必填 | ✓ | 403/404 | N01,A01 |
| 11 | GET | /api/stock/{security_id} | date 必填 | ✓ | 400/404 | S01,A03 |
| 12 | GET | /api/stock/{security_id}/kline | start,end 必填; adj=raw/qfq/hfq 默认qfq | ✓ | 400 | S01 |
| 13 | GET | /api/rank/range | start,end 必填; benchmark 默认B000300; sort 默认return; start≤end | ✓ | 400 | P01,P02 |
| 14 | GET | /api/compare/curves | symbols=逗号分隔≤5; start,end 必填 | ✓ | 400(>5)/403(权限) | P02 |
| 15 | GET | /api/regulation/status | date 必填 | ✓ | 400 | R01,R02 |
| 16 | GET | /api/regulation/events | date 必填; security_id 可选 | ✓ | 400 | R03 |
| 17 | GET | /api/snapshots/list | — | ✓(已发布) | — | G01,D03 |
| 18 | GET | /api/search | q 非空, 1-50字符 | ✓ | 400 | G02 |
| 19 | POST | /api/ai/summarize | body: article_id, snapshot_id, as_of, version | ✗ | 403/429 | A01 |
| 20 | POST | /api/ai/topic-suggest | body: date, snapshot_id, as_of, version | ✗ | 403/429 | A02 |
| 21 | POST | /api/ai/explain-stock | body: security_id, date, snapshot_id, as_of, version | ✗ | 403/429 | A03 |
| 22 | POST | /api/ai/daily-review | body: date, notes_context, snapshot_id, as_of, version | ✗ | 403/429 | A04 |
| 23 | GET | /api/ai/task/{task_id} | path（本版工程设计） | ✗(结果属发起者) | 404 | A01-A05 |
| 24 | POST | /api/ai/task/{task_id}/cancel | path; idempotency_key 必填（本版工程设计） | ✗ | 404/409 | A05 |
| 25 | POST | /api/ai/task/{task_id}/review | path; decision(accept/edit/reject), revision, idempotency_key（本版工程设计） | ✗ | 404/409(冲突) | A05,N03 |
| 26 | Repo | listNotes（IndexedDB，非 HTTP） | local_profile_id 隐式; snapshot_id 可选 | ✗(浏览器私有) | 只读失败 | U02,U04 |
| 27 | Repo | putNote（IndexedDB，非 HTTP） | note_id, revision, idempotency_key, payload | ✗(浏览器私有) | 409(S07冲突) | U02,U03,S06,S07 |
| 28 | Repo | listWatchlist（IndexedDB，非 HTTP） | local_profile_id 隐式 | ✗(浏览器私有) | 只读失败 | U01 |
| 29 | Repo | putWatchlist（IndexedDB，非 HTTP） | security_id, action(add/remove), idempotency_key | ✗(浏览器私有) | 幂等 | U01 |

> 4 个 AI POST（19-22）均为异步：立即返回 `task_id` 与状态字段；前端按端点 23 轮询。60s 提示处理中，120s 交付确定性模板。AI 输入必须携带 `snapshot_id`/`as_of`/`version`；异步状态字段（status/verification_status/human_review_status）与 content_draft 内容字段分离。

### 7.3 29 个端点成功响应示例（以下为合成测试数据，严格可解析）

> 以下 JSON 中 `S00001`/`SEC001` 等均为**合成测试数据（fixture）**；生产使用 `SZSE.300502` 式 ID。每个 JSON 的 coverage 均满足 expected=valid+excluded+missing。

#### 端点01 `GET /api/market/overview?date=2026-09-18`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 5, "valid": 5, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "adjusted", "currency": "CNY", "unit": "ratio", "benchmark": "B000300"},
  "data": {
    "indexes": [
      {"code": "B000300", "name": "合成沪深300指数", "last": 4507.39, "change_ratio": 0.0106, "amount": 5.2e11}
    ],
    "sentiment": {
      "limit_up_count": 2,
      "limit_down_count": 0,
      "max_boards": 2,
      "promote_rate": {"numerator": 1, "denominator": 2, "value": 0.5},
      "yesterday_limit_up_median_return": 0.012
    },
    "breadth": {"up": 3, "down": 1, "flat": 1, "valid_total": 5,
      "seal_rate": {"closed_limit_up": 2, "touched": 3, "value": 0.6667}},
    "hot_sectors": [
      {"sector_id": "SEC001", "name": "合成题材甲", "limit_up_count": 1, "member_count": 3},
      {"sector_id": "SEC002", "name": "合成题材乙", "limit_up_count": 1, "member_count": 2}
    ]
  }
}
```

#### 端点02 `GET /api/market/sector-flow?date=2026-09-18&top_n=10`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 5, "valid": 5, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "adjusted", "currency": "CNY", "unit": "ratio"},
  "data": {
    "sectors": [
      {"sector_id": "SEC001", "name": "合成题材甲", "change_ratio": 0.024, "member_count": 3, "net_inflow": 1.2e8},
      {"sector_id": "SEC002", "name": "合成题材乙", "change_ratio": -0.008, "member_count": 2, "net_inflow": -3.0e7}
    ]
  }
}
```

#### 端点03 `GET /api/market/sentiment?date=2026-09-18&days=2`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 5, "valid": 5, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "n/a", "currency": "CNY", "unit": "ratio"},
  "data": {
    "series": [
      {"trade_date": "2026-09-17", "limit_up_count": 3, "limit_down_count": 0, "max_boards": 1, "promote_rate": 0.3333, "yesterday_limit_up_median_return": -0.005},
      {"trade_date": "2026-09-18", "limit_up_count": 2, "limit_down_count": 0, "max_boards": 2, "promote_rate": 0.5, "yesterday_limit_up_median_return": 0.012}
    ]
  }
}
```

#### 端点04 `GET /api/market/heatmap?date=2026-09-18&days=2`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 5, "valid": 5, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "n/a", "currency": "CNY", "unit": "ratio"},
  "data": {
    "columns": [1, 2],
    "cells": [
      {"trade_date": "2026-09-18", "boards": 1, "count": 1},
      {"trade_date": "2026-09-18", "boards": 2, "count": 1}
    ]
  }
}
```

#### 端点05 `GET /api/limit-up/ladder?date=2026-09-18`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 2, "valid": 2, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "raw", "currency": "CNY", "unit": "ratio"},
  "data": {
    "columns": [1, 2],
    "total": 2,
    "cells": [
      {"boards": 1, "count": 1, "stocks": [{"security_id": "S00001", "name": "合成股份A", "roles": ["emotion_leader"], "sector_id": "SEC001"}]},
      {"boards": 2, "count": 1, "stocks": [{"security_id": "S00002", "name": "合成股份B", "roles": ["trend_leader"], "sector_id": "SEC002"}]}
    ],
    "promote_rate": {"yesterday_ge2": 2, "today_sealed": 1, "tradable": 2, "rate": 0.5}
  }
}
```

#### 端点06 `GET /api/limit-up/continuity?date=2026-09-18`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 2, "valid": 2, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "raw", "currency": "CNY", "unit": "ratio"},
  "data": {
    "promote_rate": {"yesterday_ge2": 2, "today_sealed": 1, "tradable": 2, "rate": 0.5},
    "yesterday_limit_up_median_return": 0.012,
    "tradable_count": 2
  }
}
```

#### 端点07 `GET /api/role/candidates?date=2026-09-18&role=emotion_leader`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 5, "valid": 5, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "role_v1.0",
  "basis": {"price_type": "adjusted", "currency": "CNY", "unit": "ratio"},
  "data": {
    "role": "emotion_leader",
    "candidates": [
      {"security_id": "S00001", "name": "合成股份A", "rank": 1, "evidence_status": "candidate", "sector_id": "SEC001", "boards": 1}
    ],
    "total": 1
  }
}
```

#### 端点08 `GET /api/role/evidence?security_id=S00001&sector=SEC001&date=2026-09-18`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 1, "valid": 1, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "role_v1.0",
  "basis": {"price_type": "adjusted", "currency": "CNY", "unit": "ratio"},
  "data": {
    "security_id": "S00001",
    "sector_id": "SEC001",
    "roles": [
      {"role_type": "emotion_leader", "evidence_status": "candidate",
       "factors": [{"factor_id": "FAC-R01", "value": 0.10, "status": "computed"},
                   {"factor_id": "FAC-C01", "value": 3.5e8, "status": "computed"}],
       "supporting_evidence": ["首板涨停，封单 2.1e8"],
       "opposing_evidence": ["20日均线未突破"],
       "missing_evidence": ["盘中封单明细（来源未授权）"]}
    ]
  }
}
```

#### 端点09 `GET /api/news?date=2026-09-18&page=1&size=20`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "media_adapter",
  "coverage": {"expected": 3, "valid": 3, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "n/a", "currency": "CNY", "unit": "n/a"},
  "data": {
    "articles": [
      {"article_id": "art_001", "title": "合成股份A涨停公告", "source": "exchange", "published_at": "2026-09-18T15:30:00+08:00", "article_type": "announcement", "summary": "公司发布涨幅偏离公告"},
      {"article_id": "art_002", "title": "合成题材甲行业新闻", "source": "media", "published_at": "2026-09-18T16:00:00+08:00", "article_type": "news", "summary": "行业政策预期"},
      {"article_id": "art_003", "title": "合成股份B研报", "source": "research", "published_at": "2026-09-18T17:00:00+08:00", "article_type": "research", "summary": "维持买入评级"}
    ],
    "total": 3
  }
}
```

#### 端点10 `GET /api/news/art_001?date=2026-09-18`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "exchange",
  "coverage": {"expected": 1, "valid": 1, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "n/a", "currency": "CNY", "unit": "n/a"},
  "data": {
    "article_id": "art_001",
    "title": "合成股份A涨停公告",
    "content": "公司股票连续两个交易日收盘价涨幅偏离值累计达到20%。",
    "source": "exchange",
    "url": "https://example.invalid/art/001",
    "published_at": "2026-09-18T15:30:00+08:00",
    "received_at": "2026-09-18T15:35:00+08:00",
    "article_type": "announcement",
    "ai_summary": "合成股份A因涨幅偏离触发公告。",
    "related_security_ids": ["S00001"]
  }
}
```

#### 端点11 `GET /api/stock/S00001?date=2026-09-18`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 1, "valid": 1, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "adjusted", "currency": "CNY", "unit": "yuan"},
  "data": {
    "security_id": "S00001",
    "name": "合成股份A",
    "quote": {"last": 11.00, "change_ratio": 0.10, "amount": 3.5e8},
    "roles": [{"role_type": "emotion_leader", "evidence_status": "candidate", "sector_id": "SEC001"}],
    "seal": {"first_seal_time": "09:35:00", "last_seal_time": "14:55:00", "break_count": 0, "seal_amount": 2.1e8},
    "regulation": {"active_events": 0}
  }
}
```

#### 端点12 `GET /api/stock/S00001/kline?start=2026-09-16&end=2026-09-18&adj=qfq`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 1, "valid": 1, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "adjusted", "currency": "CNY", "unit": "yuan"},
  "data": {
    "security_id": "S00001",
    "points": [
      {"trade_date": "2026-09-16", "close": 9.50},
      {"trade_date": "2026-09-17", "close": 10.00},
      {"trade_date": "2026-09-18", "close": 11.00}
    ]
  }
}
```

#### 端点13 `GET /api/rank/range?start=2026-08-19&end=2026-09-18&benchmark=B000300&sort=return`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 5, "valid": 5, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "adjusted", "currency": "CNY", "unit": "ratio", "benchmark": "B000300"},
  "data": {
    "window_days": 22,
    "benchmark_return": 0.012,
    "ranking": [
      {"rank": 1, "security_id": "S00001", "name": "合成股份A", "return_ratio": 0.15, "excess_return_ratio": 0.138, "max_drawdown_ratio": 0.03},
      {"rank": 2, "security_id": "S00002", "name": "合成股份B", "return_ratio": 0.10, "excess_return_ratio": 0.088, "max_drawdown_ratio": 0.02},
      {"rank": 3, "security_id": "S00003", "name": "合成股份C", "return_ratio": 0.05, "excess_return_ratio": 0.038, "max_drawdown_ratio": 0.01},
      {"rank": 4, "security_id": "S00004", "name": "合成股份D", "return_ratio": -0.02, "excess_return_ratio": -0.032, "max_drawdown_ratio": 0.04},
      {"rank": 5, "security_id": "S00005", "name": "合成股份E", "return_ratio": 0.00, "excess_return_ratio": -0.012, "max_drawdown_ratio": 0.00}
    ]
  }
}
```

#### 端点14 `GET /api/compare/curves?symbols=S00001,S00002&start=2026-09-16&end=2026-09-18`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 2, "valid": 2, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "adjusted", "currency": "CNY", "unit": "ratio"},
  "data": {
    "curves": [
      {"security_id": "S00001", "points": [
        {"trade_date": "2026-09-16", "normalized": 100.0},
        {"trade_date": "2026-09-17", "normalized": 105.26},
        {"trade_date": "2026-09-18", "normalized": 115.79}
      ]},
      {"security_id": "S00002", "points": [
        {"trade_date": "2026-09-16", "normalized": 100.0},
        {"trade_date": "2026-09-17", "normalized": 104.76},
        {"trade_date": "2026-09-18", "normalized": 109.52}
      ]}
    ]
  }
}
```

#### 端点15 `GET /api/regulation/status?date=2026-09-18`（阈值独立 + 评审开关）

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "exchange",
  "coverage": {"expected": 5, "valid": 4, "excluded": 0, "missing": 1},
  "quality_flags": ["partial_missing_1"],
  "rule_version": "reg_v1.0",
  "basis": {"price_type": "raw", "currency": "CNY", "unit": "ratio"},
  "data": {
    "region1_completeness": {"coverage_ratio": 0.8, "missing_count": 1, "source_check": "pass", "expected": 5, "valid": 4, "excluded": 0, "missing": 1},
    "region2_calc_state": [
      {"rule_id": "abnormal_10day", "rule_version": "reg_v1.0", "window_days": 10, "direction": "upper", "threshold": 0.70, "warning_threshold": 0.65, "review_switch": "approved", "benchmark_index": "B000300", "computed_value": 0.68, "state": "approaching_alert", "note": "合成示例，非监管真实条款"},
      {"rule_id": "abnormal_20day", "rule_version": "reg_v1.0", "window_days": 20, "direction": "upper", "threshold": 0.70, "warning_threshold": 0.65, "review_switch": "pending", "benchmark_index": "B000300", "computed_value": 0.62, "state": "pending", "note": "规则未评审冻结，0.62<0.70 不自动输出 approaching"}
    ],
    "region3_events": []
  }
}
```

#### 端点16 `GET /api/regulation/events?date=2026-09-18`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "exchange_announcement",
  "coverage": {"expected": 5, "valid": 5, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "reg_v1.0",
  "basis": {"price_type": "n/a", "currency": "CNY", "unit": "n/a"},
  "data": {
    "events": [
      {"event_id": "evt_001", "rule_id": "abnormal_10day", "rule_version": "reg_v1.0",
       "security_id": "S00001", "name": "合成股份A", "event_type": "monitoring",
       "announcement_url": "https://example.invalid/notice/evt001", "effective_from": "2026-09-18",
       "effective_to": "2026-09-28", "description": "10日涨幅偏离值达到监控标准（合成示例，非监管真实条款）"}
    ],
    "total": 1
  }
}
```

#### 端点17 `GET /api/snapshots/list`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "snapshot_store",
  "coverage": {"expected": 5, "valid": 5, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "n/a", "currency": "CNY", "unit": "n/a"},
  "data": {
    "snapshots": [
      {"snapshot_id": "snap_20260917_v1", "trade_date": "2026-09-17", "version": "v1", "status": "published", "published_at": "2026-09-17T18:30:00+08:00"},
      {"snapshot_id": "snap_20260918_v1", "trade_date": "2026-09-18", "version": "v1", "status": "published", "published_at": "2026-09-18T18:30:00+08:00"}
    ],
    "total": 2
  }
}
```

#### 端点18 `GET /api/search?q=S00001`

```json
{
  "trade_date": "2026-09-18",
  "as_of": "2026-09-18T18:30:00+08:00",
  "snapshot_id": "snap_20260918_v1",
  "source": "demo_adapter",
  "coverage": {"expected": 5, "valid": 5, "excluded": 0, "missing": 0},
  "quality_flags": [],
  "rule_version": "v2.1",
  "basis": {"price_type": "n/a", "currency": "CNY", "unit": "n/a"},
  "data": {
    "query": "S00001",
    "results": [
      {"security_id": "S00001", "name": "合成股份A", "match_field": "code", "exchange": "SZSE"}
    ],
    "total": 1
  }
}
```

#### 端点19 `POST /api/ai/summarize`（body: `{"article_id":"art_001","snapshot_id":"snap_20260918_v1","as_of":"2026-09-18T18:30:00+08:00","version":"v1"}`）

```json
{
  "task_id": "task_20260918_0001",
  "task_type": "summarize",
  "snapshot_id": "snap_20260918_v1",
  "as_of": "2026-09-18T18:30:00+08:00",
  "version": "v1",
  "status": "pending",
  "verification_status": "pending",
  "human_review_status": "unreviewed",
  "content_draft": null
}
```

#### 端点20 `POST /api/ai/topic-suggest`（body: `{"date":"2026-09-18","snapshot_id":"snap_20260918_v1","as_of":"2026-09-18T18:30:00+08:00","version":"v1"}`）

```json
{
  "task_id": "task_20260918_0002",
  "task_type": "topic_suggest",
  "snapshot_id": "snap_20260918_v1",
  "as_of": "2026-09-18T18:30:00+08:00",
  "version": "v1",
  "status": "pending",
  "verification_status": "pending",
  "human_review_status": "unreviewed",
  "content_draft": null
}
```

#### 端点21 `POST /api/ai/explain-stock`（body: `{"security_id":"S00001","date":"2026-09-18","snapshot_id":"snap_20260918_v1","as_of":"2026-09-18T18:30:00+08:00","version":"v1"}`）

```json
{
  "task_id": "task_20260918_0003",
  "task_type": "explain_stock",
  "snapshot_id": "snap_20260918_v1",
  "as_of": "2026-09-18T18:30:00+08:00",
  "version": "v1",
  "status": "pending",
  "verification_status": "pending",
  "human_review_status": "unreviewed",
  "content_draft": null
}
```

#### 端点22 `POST /api/ai/daily-review`（body: `{"date":"2026-09-18","notes_context":"...","snapshot_id":"snap_20260918_v1","as_of":"2026-09-18T18:30:00+08:00","version":"v1"}`）

```json
{
  "task_id": "task_20260918_0004",
  "task_type": "daily_review",
  "snapshot_id": "snap_20260918_v1",
  "as_of": "2026-09-18T18:30:00+08:00",
  "version": "v1",
  "status": "pending",
  "verification_status": "pending",
  "human_review_status": "unreviewed",
  "content_draft": null
}
```

#### 端点23 `GET /api/ai/task/{task_id}`（本版工程设计；状态字段与 content_draft 分离）

```json
{
  "task_id": "task_20260918_0001",
  "task_type": "summarize",
  "snapshot_id": "snap_20260918_v1",
  "as_of": "2026-09-18T18:30:00+08:00",
  "version": "v1",
  "status": "succeeded",
  "verification_status": "passed",
  "human_review_status": "unreviewed",
  "model_version": "server_model_x",
  "prompt_version": "v3",
  "retrieval_version": "r1",
  "content_draft": {
    "facts": [{"text": "合成股份A发布涨幅偏离公告", "evidence_id": "ev_001", "evidence_position": "paragraph_1"}],
    "inferences": [{"text": "该股当日为情绪候选", "confidence": 0.6}],
    "missing_evidence": ["盘中封单明细"],
    "citations": [{"evidence_id": "ev_001", "source": "公告", "url": "https://example.invalid/art/001"}]
  }
}
```

#### 端点24 `POST /api/ai/task/{task_id}/cancel`（本版工程设计；幂等键）

```json
{
  "task_id": "task_20260918_0002",
  "status": "cancelled",
  "idempotency_key": "cancel-task_20260918_0002-20260918",
  "accepted_at": "2026-09-18T18:35:00+08:00"
}
```

#### 端点25 `POST /api/ai/task/{task_id}/review`（本版工程设计；版本冲突 + 人工编辑后重新触发校验）

```json
{
  "task_id": "task_20260918_0001",
  "human_review_status": "edited",
  "revision": 2,
  "idempotency_key": "review-task_20260918_0001-r2",
  "verification_status": "needs_human",
  "note": "人工编辑后对 revision=2 重新触发校验；与服务端 revision 不一致返回 409"
}
```

#### 端点26 `listNotes`（浏览器 Repository 接口 IndexedDB，非 HTTP）

```json
{
  "repository": "IndexedDB",
  "object_store": "notes",
  "authoritative": true,
  "records": [
    {"note_id": "note_001", "title": "复盘0918", "content": "今日涨停2只，情绪一般。", "snapshot_id": "snap_20260918_v1", "revision": 1, "updated_at": "2026-09-18T20:00:00+08:00"}
  ],
  "total": 1
}
```

#### 端点27 `putNote`（浏览器 Repository 接口 IndexedDB，非 HTTP；S06=事务提交+回读验证）

```json
{
  "repository": "IndexedDB",
  "object_store": "notes",
  "note_id": "note_001",
  "revision": 2,
  "idempotency_key": "put-note_001-20260918T2000",
  "commit": "committed",
  "readback_verified": true,
  "updated_at": "2026-09-18T20:05:00+08:00"
}
```

#### 端点28 `listWatchlist`（浏览器 Repository 接口 IndexedDB，非 HTTP）

```json
{
  "repository": "IndexedDB",
  "object_store": "watchlist",
  "authoritative": true,
  "records": [
    {"security_id": "S00001", "added_at": "2026-09-18T19:00:00+08:00", "context": {"sector_id": "SEC001", "snapshot_id": "snap_20260918_v1"}},
    {"security_id": "S00002", "added_at": "2026-09-18T19:05:00+08:00", "context": {"sector_id": "SEC002", "snapshot_id": "snap_20260918_v1"}}
  ],
  "total": 2
}
```

#### 端点29 `putWatchlist`（浏览器 Repository 接口 IndexedDB，非 HTTP；幂等）

```json
{
  "repository": "IndexedDB",
  "object_store": "watchlist",
  "security_id": "S00001",
  "action": "add",
  "idempotency_key": "add-S00001-20260918T1900",
  "commit": "committed",
  "readback_verified": true,
  "added_at": "2026-09-18T19:00:00+08:00"
}
```

### 7.4 notes/watchlist 接口性质说明

端点 26-29 是**浏览器 Repository 接口（IndexedDB），不是 HTTP 端点**：它们由前端代码直接调用浏览器 IndexedDB object store，不经过 FastAPI 路由、不出现在 Caddy 公网网关、不分配 HTTP 路径。guest_public 浏览器因 origin 隔离无法访问 owner 的 IndexedDB。AI 触发（端点 19-22）是 HTTP，仅 owner_local 可调用，且只携带用户显式授权的片段，个人笔记/自选不自动上传。

---

## 8. AI 功能详细需求

### 8.1 A01~A05 逐项验收

| 需求ID | 优先级 | 触发/状态 | 预期行为 | 验收 |
|---|---|---|---|---|
| REQ-A01 摘要 | 🔴 P0 | 摘要授权文本；S01/S03 | 提取事件、主体、事实、不确定项和引用 | 无正文不声称全文解读；关键事实可定位原文片段 |
| REQ-A02 题材建议 | 🔴 P0 | 生成题材建议；S01/S05 | 建议消息—题材—证券关联，区分直接证据和推断 | 证券ID须在主数据存在；未经审核不修改正式成员池 |
| REQ-A03 解释候选 | 🔴 P0 | 解释候选；S01/S04 | 引用因子、反证、缺口和失效条件 | 引用数字必须匹配快照；错配被拦截 |
| REQ-A04 复盘增强 | 🔴 P0 | 增强复盘；S01/S04 | 生成事实/解释/待验证分层草稿，人工编辑确认 | 模型失败仍有模板；晚到响应不覆盖已编辑内容 |
| REQ-A05 模型发布 | 🔴 P0 | 模型发布/升级；S01/S05 | 固定评测集比较，支持回退 | 安全硬失败阻断上线；版本可追溯 |

### 8.2 输入输出契约

**输入**：选定快照 + 获许可的原文片段 + 用户授权的相关笔记 + 明确任务；不默认把整库发给模型。AI POST body 必须包含 `snapshot_id`/`as_of`/`version`。服务端对可计算数字调用白名单函数复算；异步状态字段与 content_draft 分离。

`verification_status` 四态：`pending` / `passed` / `failed` / `needs_human`。`human_review_status` 四态：`unreviewed` / `accepted` / `edited` / `rejected`。人工编辑后对新版本重新校验，旧记录不覆盖。

### 8.3 AI 评测标准

- 首期 ≥ 100 条人工标注样本；开发集与冻结测试集分离。
- 原子事实支持率目标 ≥95%；展示数字一致率与确定性指标重算一致率要求 100%。
- 硬失败阻断：越权调用、假监管认定、未来材料冒充历史事实、伪造校验结果。

### 8.4 降级策略

- 60s 提示"AI处理中"；120s 结束等待交付确定性模板；后台迟到结果另存草稿，不覆盖已保存文本。
- 模型权限限只读检索、调白名单计算函数、生成草稿；写笔记由用户动作触发的受控前端执行（IndexedDB 事务），模型不直连数据库。
- 每任务设 Token/并发/超时/费用上限。

---

## 9. 监管观察模块

### 9.1 三区域独立展示（P0 同时展示）

| 区域 | 内容 | 状态枚举 | 交付 |
|---|---|---|---|
| 数据完整性 | 覆盖率、缺失数量、来源校验状态 | complete / partial / missing | P0 |
| 数值计算状态 | 规则ID+版本、窗口、基准、threshold、warning_threshold、review_switch、计算值 | pending / idle / approaching_alert(仅已评审) / triggered | P0 |
| 正式事件/限制 | 异常、严重异常、公开监控、实际限制，附原文 | disclosed / monitoring / restricted | P0 |

### 9.2 规则配置与复核/撤回

- **启用**：须绑定官方来源+回归样例，记录编制/复核/发布；`review_switch=approved` 且测试通过才能输出 approaching。
- **撤回**：只停用新计算（`active=false`），不删除旧结论。
- **"接近预警"**：产品自设提醒，不是法定状态；阈值未经评审默认 pending，不输出 approaching。

### 9.3 禁区

不展示"安全再涨几个板"或规避监管建议；不展示"无风险"绝对化表述；不把监管状态与涨跌颜色混为一谈；数据完整不等于"无监管风险"。本次实现隔离的模拟研究账本，不管理真实仓位、不执行真实交易。

---

## 10. 数据质量与降级

### 10.1 时间标准

保留 6 种时间戳：源事件时间、供应商发布时间、首次接收时间、计算完成时间、快照发布时间、页面更新时间。服务器 UTC 存储、北京时间展示。盘后初版目标 18:30 前发布。

### 10.2 REQ-Q01~Q04 验收

| 需求ID | 触发/状态 | 预期行为 | 验收 |
|---|---|---|---|
| REQ-Q01 时效 | 展示/监控时效；S01/S04 | 展示各域截至时间和延迟 | 页面刷新不改变源时间；源时间未知不标实时 |
| REQ-Q02 数据校验 | 数据校验；S01/S04 | 字段、单位、唯一性、价格逻辑校验 | 高低价倒置、负成交量、重复键进隔离；停牌不填0 |
| REQ-Q03 抽样核验 | 抽样核验；S01/S04 | 同时间/单位/复权/统计池比较 | 不平均冲突值，不拼不同来源的分子分母 |
| REQ-Q04 降级 | 受影响模块降级；S04/S05 | 显示最后有效版本和影响范围 | 题材成交缺失停发中军排行，规则缺失显示待核验 |

### 10.3 覆盖率分母与未知排除

- `coverage.expected` = 统计池当日应有记录数（分母）。
- `coverage.excluded` = 合法无记录（停牌/不适用）。
- `coverage.missing` = 异常缺失。
- 恒等式 `expected = valid + excluded + missing`。未知状态不得用于缩小分母。

### 10.4 降级策略

数据缺失不填0，显示"—"或"不适用"；显示缺失数量和原因；受影响模块标 S04 并停新分析。

---

## 11. "打开即用"技术方案

### 11.1 数据预计算流程

```
盘后采集当日行情 → 数据校验（字段/单位/价格逻辑）→ 计算涨停/连板/角色/情绪原始观测值
→ 生成不可变快照JSON → 质量检查通过后发布 → 前端加载快照渲染
```

### 11.2 存储分层（v1.3 关键决策）

- **自选/笔记**：浏览器 IndexedDB（唯一权威源，表10/表11）；S06=事务提交+回读验证；S07=revision 乐观锁 + BroadcastChannel 多标签同步。
- **localStorage**：仅 UI 偏好/索引，不存业务数据。
- **服务端 PostgreSQL**：只读读模型快照（行情/题材/角色/消息/监管），不存 owner 私有自选/笔记。
- **导出/导入**：从 IndexedDB 一键 JSON 备份/恢复。

### 11.3 owner 会话与双监听器（H 类）

- **私有 owner 路由**绑定 `127.0.0.1:8001`，仅回环可达；**公开只读网关**绑定 `0.0.0.0:8000`（Caddy）。公网请求无论 Host/Forwarded/Cookie 如何，都不会到达 8001。
- 本机会话经本地 OS 启动器或一次性握手码（bootstrap token，仅回环交换）建立，颁发短期会话 cookie（HttpOnly + SameSite=Strict）。
- 不使用用户名密码；不需要用户注册登录。
- 转发头不可信；owner 身份只由"回环连接 + 本机握手会话"决定。

### 11.4 安全要求

- CSP 头限制外部资源；XSS 转义；CSV 公式注入转义。
- 密钥不进前端构建产物、URL、错误堆栈或 AI 提示词。
- 个人数据（自选/笔记）不自动上传服务端；AI 调用仅携带显式授权片段。

---

## 12. 开发里程碑与验收

### 12.1 阶段划分

| 阶段 | 交付物 | 说明 |
|---|---|---|
| **M1 基础框架** | 前端骨架、路由、侧栏、全局状态栏、日期切换、部署骨架 | 确定性数据链路的地基 |
| **M2 数据管线** | 采集适配、字段映射、校验、快照生成/发布、质量降级 | 确定性数据链路核心 |
| **M3 确定性页面** | 市场总览、市场消息、龙头梯队、涨幅分析、个股抽屉、监管三区域 | 确定性页面闭环 |
| **M4 AI 能力** | 4 个 AI POST + 任务轮询/取消/审核、服务端复算与引用校验 | AI 叠加层，可单独关闭 |
| **M5 我的复盘** | IndexedDB 自选/笔记、AI 复盘草稿、历史对照 | 复盘闭环 |
| **M6 基础中期验收** | M1-M5集成、导出、恢复、性能 | 涨幅分析已在M3/Task13一次交付，Task20不是全范围终验 |
| **M7 扩展研究与体验** | 实时异动、龙虎榜、词云、轮动、自然语言、主题/密度/命令面板、回测/模拟组合、情绪研究分 | Task21-28，本次必交 |
| **M8 全范围最终验收** | 原57 REQ及扩展项、生产门禁状态、完整回归、影子运行 | Task29；外部条件未满足不得宣称生产全部通过 |

> 以上为单人开发估算，非用户审批工期，不承诺日期。真实排期以数据授权、接口试用与影子运行（至少 20 个交易日）结果为准。

### 12.2 非功能验收标准

| 维度 | 目标 |
|---|---|
| 普通查询 | P95 ≤ 2 秒（20 并发）；超 5 秒转异步 |
| 笔记保存 | IndexedDB 事务提交后回读一致；失败不丢输入；并发冲突不覆盖另一版本 |
| 模块隔离 | AI 或单源失败不影响历史浏览与笔记 |
| 兼容性 | 375px 手机 + 1440px 桌面主路径可完成；200% 缩放可读 |
| owner 鉴权 | 无有效本机握手会话的写请求一律 403；公网不连私有路由 |

---

## 附录A：REQ → 章节 → 字段 → API → 验收映射表

> 覆盖 57 个 REQ-ID。"—"表示该需求无独立公共 API。

| REQ-ID | 章节 | 关键数据字段 | API 端点 | 验收用例 |
|---|---|---|---|---|
| G01 | 2.1/4.x | trade_date, snapshot_id | GET /api/snapshots/list, /api/market/overview | AC-G01 |
| G02 | 2.1/4.x | q, 证券/题材/消息分组 | GET /api/search | AC-G02 |
| G03 | 3.3 | 日期/题材/角色/排序/分页保持 | 前端状态 | AC-G03 |
| G04 | 3.3 | 筛选+版本元信息导出 | 导出接口（预留） | AC-G04 |
| M01 | 4.1 | indexes, breadth, sentiment 原始观测 | GET /api/market/overview | AC-M01 |
| M02 | 4.1/4.3 | hot_sectors, 成员收益中位数 | /api/market/heatmap, /api/market/sector-flow | AC-M02 |
| N01 | 4.2 | article 发布/获取时间、正文摘录 | GET /api/news, /api/news/{id} | AC-N01 |
| N02 | 4.2 | event_id 去重聚类 | GET /api/news | AC-N02 |
| N03 | 4.2 | ai_entities 确认/驳回 | POST /api/ai/topic-suggest | AC-N03 |
| L01 | 4.3/5.1 | role_candidate, 证据/反证/缺失 | GET /api/role/candidates, /api/role/evidence | AC-L01 |
| L02 | 4.3 | limit_up_record, 连板/触板 | GET /api/limit-up/ladder, /api/limit-up/continuity | AC-L02 |
| L03 | 4.3 | rule_version 变化标记 | GET /api/role/candidates | AC-L03 |
| P01 | 4.4 | return_ratio, 窗口/排除原因 | GET /api/rank/range | AC-P01 |
| P02 | 4.4 | 归一化曲线、max_drawdown_ratio(正) | GET /api/compare/curves | AC-P02 |
| S01 | 4.5 | 个股行情/角色/封板/监管 | GET /api/stock/{id}, /kline | AC-S01 |
| U01 | 4.7 | watchlist(IndexedDB), 加入时间/上下文 | listWatchlist/putWatchlist (Repo) | AC-U01 |
| U02 | 4.7 | note 字段, revision 乐观锁 | listNotes/putNote (Repo) | AC-U02 |
| U03 | 4.7 | 确定性模板 + AI 草稿分层 | POST /api/ai/daily-review | AC-U03 |
| U04 | 4.7 | 验证期事实、支持/削弱/否定 | listNotes (Repo) | AC-U04 |
| R01 | 4.6/9.2 | regulation_rule 匹配 | GET /api/regulation/status | AC-R01 |
| R02 | 4.6/9.1 | computed_value, threshold/warning_threshold/review_switch | GET /api/regulation/status | AC-R02 |
| R03 | 4.6/7.3 | regulation_event, 公告原文 | GET /api/regulation/events | AC-R03 |
| R04 | 9.2 | 复核记录、回归样例、撤回不删旧 | 管理端（无公开 API） | AC-R04 |
| Q01 | 10.1/10.2 | as_of, 各域延迟 | 全部 GET | AC-Q01 |
| Q02 | 10.2 | quality_flags, 校验隔离 | 数据管线 | AC-Q02 |
| Q03 | 10.2 | 同口径抽样比较 | 数据管线 | AC-Q03 |
| Q04 | 10.2/10.3 | S04/S05 降级阻断 | GET 响应 quality_flags | AC-Q04 |
| D01 | 6.x/11.4 | 来源配置、授权、AI 许可 | 管理端 | AC-D01 |
| D02 | 11.1 | 采集、分页续传、幂等重试 | 数据管线 | AC-D02 |
| D03 | 6.2/6.3 | snapshot 不可变/修订新建 | GET /api/snapshots/list | AC-D03 |
| D04 | 10.2 | 批次重跑、回退不清笔记 | 管理端 | AC-D04 |
| A01 | 8.1 | 摘要 facts/citations | POST /api/ai/summarize | AC-A01 |
| A02 | 8.1 | 题材关联建议 | POST /api/ai/topic-suggest | AC-A02 |
| A03 | 8.1 | 解释引用因子/反证 | POST /api/ai/explain-stock | AC-A03 |
| A04 | 8.1 | 分层复盘草稿 | POST /api/ai/daily-review | AC-A04 |
| A05 | 8.3 | 评测集、版本追溯、回退 | GET /api/ai/task/{id} | AC-A05 |
| E01 | 2.0/2.2 | 实时快照/分钟K/异动 RT-01~RT-12 | 工程扩展EX04-07 | AC-E01 |
| E02 | 2.0/2.2 | 龙虎榜席位/金额 | 工程扩展EX08-09 | AC-E02 |
| E03 | 2.0/4.2 | 词云/主题频次 | 工程扩展EX10 | AC-E03 |
| E04 | 2.0/2.2 | 题材演进/核心更替 | 工程扩展EX11 | AC-E04 |
| E05 | 2.0/2.2 | 白名单结构化自然语言筛选 | 工程扩展EX12-13 | AC-E05 |
| B01 | 2.0/2.3 | 回测配置/防前视/滚动验证 | 工程扩展EX14-15 | AC-B01 |
| B02 | 2.0/2.3 | 模拟组合/T+1/涨跌停约束/AI增益对照 | 本地模拟Repository；EX14-15对照实验 | AC-B02 |
| X01 | 1.3/11.3 | 双监听器、本机握手、CSRF | 全部端点 | AC-X01 |
| X02 | 8.4/11.4 | 提示注入净化、出站控制 | POST ai 端点 | AC-X02 |
| X03 | 10.4 | 保留/删除/授权到期 | 管理端 | AC-X03 |
| V01 | 3.1/4.x | 七页同快照不丢上下文 | 前端 | AC-V01 |
| V02 | 3.2/3.3 | 红涨绿跌+符号、角色中性标签 | 前端 | AC-V02 |
| V03 | 3.3 | 身份列/单位/排序/分页 | 前端 | AC-V03 |
| V04 | 4.5 | 单抽屉/Esc回焦点/返回恢复 | 前端 | AC-V04 |
| V05 | 4.4 | 图表口径/最多5标的/缺口不补0 | GET /api/compare/curves | AC-V05 |
| V06 | 3.3 | 四档视口/200%缩放/对比度 | 前端 | AC-V06 |
| V07 | 3.1/10.4 | 真实进度状态/减少动效 | 前端 | AC-V07 |
| V08 | 4.5/8.2 | 引用定位/冲突持续提示 | GET /api/news/{id} | AC-V08 |
| V09 | 2.0/3.2 | 浅色/深色切换 | 前端 | AC-V09 |
| V10 | 2.0/3.2 | 密度紧凑/舒适 | 前端 | AC-V10 |
| V11 | 2.0/2.2 | 命令面板 Ctrl+K | 前端 | AC-V11 |

---

## 附录B：程序化验收方法

旧 `verify_prd_v18.py` 未随工作树提供，旧 PASS 文案不是当前证据，已撤销。执行 `uv run python scripts/check_prd_contracts.py` 直接读取当前 PRD，检查附录A的57个唯一需求、7.2与7.3的29个编号/方法/路径一一匹配、全部JSON解析、coverage分区、breadth/ladder/candidate数量及热力图映射。失败输出具体编号/位置并返回非零。

变异回归：`uv run pytest tests/test_prd_contracts.py -q`。此处只规定验收方法，不预填测试结果；实际命令、退出码由执行者另行报告。文档检查通过不等于产品开发、真实数据、官方规则、模型评测或影子运行通过。

---

## 附录C：竞品证据映射（gushifupan.vip screenshots_full_final：26 模块 / 68 tiles，全部 FULL（8px 容差，含暗盘资金 v02b/v02c 双补缝后连续覆盖）→ DragonRadar 模块）

> 本附录在 v1.9 基础上由 v2.0 修订（按 `_coverage_meta.json` 真实元数据校准 tiles 计数为 68：25_limitup_switch 删除 v1.9 重复计入的 v04，由 5 个 tile 更正为 4 个；08_darkpool 仍含补缝 tile `v02b`、`v02c` 共 7 个）。逐行说明 DragonRadar 每个模块对应竞品哪一组真实 tile 文件，以及该依据的性质：**实测所见**（tile 中直接可见）、**站方宣称**（仅页面文案）、**未测**（积分墙/会员墙/子菜单未展开）。截图位于同级 `screenshots_full_final/` 目录，文件命名 `NN_name_vXX_hYY.png`；本表证据 ID 写作 `evidence:NN_name_vXX.png`，表示该 vXX 行 tile 组（其下真实文件为 `NN_name_vXX_hYY.png`）。本附录不引入 ```json 代码块，不影响附录 A 的 57 REQ 映射与 6 项程序化验收。
>
> 覆盖口径：viewport 1780×1188；v2.0 **全部 26 模块覆盖状态为 FULL（8px 容差，含暗盘资金 v02b/v02c 双补缝后连续覆盖）**。多 tile 模块为同一模块纵向/横向多屏连续覆盖，**不等于单屏完整**；分页/长表仅覆盖当前页/当前视口（如龙虎榜共 43 条、截图仅前 20 条；暗盘资金共 5353 条、仅第 1 页；概念题材 406 个、仅当前视图）。**截图证据只证明页面可见外观，不能证明其背后算法正确性，也不证明未截图的全站页面；当前页不等于全量。**

### C.0 模块 → 真实 tile 文件对照（26 模块 / 68 个真实 PNG；标注分页边界与权限墙）

> 下表 68 个文件名与 `_coverage_meta.json` 逐 tile 记录及 `screenshots_full_final/` 目录实有 PNG 一致（已实盘复核）。"分页/权限墙"列标注两类硬边界：**【分页】**=长表/分页仅当前页或当前视口，非全量；**【权限墙】**=正文被会员/积分墙挡住，视口虽 FULL 但证据状态为未测。

| 模块前缀 | 真实 tile 文件（screenshots_full_final/，共 68） | 分页边界 / 权限墙 |
|---|---|---|
| 01_home | `01_home_v00_h00.png`、`01_home_v01_h00.png`（2） | — |
| 02_wordcloud | `02_wordcloud_v00_h00.png`、`02_wordcloud_v01_h00.png`（2） | —（附 Invalid Date 实测 bug） |
| 03_ai_news | `03_ai_news_v00_h00.png`（1） | **【权限墙】**会员墙，正文未测 |
| 04_ai_analyst | `04_ai_analyst_v00_h00.png`（1） | **【权限墙】**积分=0，仅计费 UI |
| 05_ai_stock | `05_ai_stock_v00_h00.png`（1） | **【权限墙】**共用积分=0，仅表头 |
| 06_popularity | `06_popularity_v00_h00.png`（1） | **【分页】**排行仅当前视口 |
| 07_sector_flow | `07_sector_flow_v00_h00.png`、`07_sector_flow_v01_h00.png`（2） | — |
| 08_darkpool | `08_darkpool_v00_h00.png`、`v01_h00`、`v02b_h00`（补缝）、`v02c_h00`（补缝）、`v02_h00`、`v03_h00`、`v04_h00`（7） | **【分页】**共 5353 条仅第 1 页；`v02b`/`v02c` 双补缝填补 v01~v02 纵向空隙后连续覆盖（0 gap）；纵向已到底（8px 容差）；不借鉴 |
| 09_anomaly | `09_anomaly_v00_h00.png`、`09_anomaly_v01_h00.png`（2） | — |
| 10_market | `10_market_v00_h00.png`（1） | — |
| 11_industry | `11_industry_v00_h00.png`（1） | — |
| 12_leading | `12_leading_v00_h00.png`、`12_leading_v01_h00.png`（2） | **【分页】**仅当前视口 |
| 13_concept | `13_concept_v00_h00.png` … `13_concept_v15_h00.png`（v00~v15，16） | **【分页】**406 概念非全量/仅当前视图 |
| 14_chain | `14_chain_v00_h00.png`、`14_chain_v01_h00.png`（2） | — |
| 15_ladder_leader | `15_ladder_leader_v00_h00.png`、`v01_h00`（2） | — |
| 16_ladder | `16_ladder_v00_h00.png`、`16_ladder_v01_h00.png`（2） | — |
| 17_hot_sector | `17_hot_sector_v00_h00.png`、`v01_h00`、`v02_h00`（3） | — |
| 18_leader | `18_leader_v00_h00.png`（1） | —（"基因分"不照搬） |
| 19_rotation | `19_rotation_v00_h00.png`、`v01_h00`、`v02_h00`（3） | — |
| 20_watchlist | `20_watchlist_v00_h00.png`（1） | — |
| 21_lhb | `21_lhb_v00_h00.png`、`21_lhb_v01_h00.png`（2） | **【分页】**共 43 条仅前 20 条 |
| 22_yz_direction | `22_yz_direction_v00_h00.png`、`v00_h01.png`、`v01_h00.png`、`v01_h01.png`（纵向 v00/v01 × 横向 h00/h01，共 4） | —（唯一横向双块模块） |
| 23_sentiment_chart | `23_sentiment_chart_v00_h00.png`、`v01_h00`（2） | — |
| 24_short_term_sentiment | `24_short_term_sentiment_v00_h00.png`、`v01_h00`（2） | — |
| 25_limitup_switch | `25_limitup_switch_v00_h00.png`、`v01_h00`、`v02_h00`、`v03_h00`（4） | — |
| 26_sentiment_heatmap | `26_sentiment_heatmap_v00_h00.png`（1） | — |

> 合计：2+2+1+1+1+1+2+7+2+1+1+2+16+2+2+2+3+1+3+1+2+4+2+2+4+1 = **68**，与 `_coverage_meta.json` 逐 tile 实数及目录实有 PNG 一致（08 含补缝 tile v02b/v02c 计 7；15 实为 2；22 实为 4；25 实为 4，已删除重复计的 v04）。权限墙=03/04/05（共 3 tile）；分页/长表边界=06/08/12/13/21。

### C.1 DragonRadar 七页 + AI + P1 + 情绪模块 → 竞品 tile 证据

| DragonRadar 模块（章节） | 借鉴/对照的竞品模块 | 证据 ID（screenshots_full_final/） | tiles / 覆盖范围 | 证据性质 |
|---|---|---|---|---|
| 4.1 市场总览·指数横条 | 首页指数横条横向滚动 | evidence:01_home_v00.png | 01_home 共 2 tiles | 实测所见 |
| 4.1 市场总览·题材热度 TOP | 首页涨停题材 chips（按家数排序） | evidence:01_home_v00.png | 同上（2 tiles） | 实测所见 |
| 4.1 市场总览·情绪原始观测 | 市场分析日频情绪表（涨停/炸板/连板率/20CM） | evidence:10_market_v00.png | 10_market 1 tile | 实测所见 |
| 4.1 市场总览·涨停情绪热力图 | 日期×板数来自本PRD定义与16梯队布局；26仅为不同口径的热力图参考 | evidence:16_ladder_v00.png；evidence:26_sentiment_heatmap_v00.png | 16共2 tiles；26共1 tile | 26的概念×日期涨幅不能证明日期×连板数×家数；不据截图推定算法 |
| 4.1 情绪周期图 | 情绪周期图 | evidence:23_sentiment_chart_v00.png | 23 共 2 tiles（**v1.5 新增实测**） | 实测所见 |
| 4.1 市场总览·板块资金流 TOP10 | 板块资金流（5 分钟分时/缺失补全） | evidence:07_sector_flow_v00.png | 07 共 2 tiles | 实测所见 |
| 4.1 市场总览·题材热度排行 | 概念题材（406 概念/5 日涨幅/涨跌家数） | evidence:13_concept_v00.png | **13 共 16 tiles 多屏**（v00~v15），仍非全量 406 概念/仅当前视图 | 实测所见 |
| 4.1 市场总览·板块下钻 | 领涨股分析（板块涨跌家数趋势/领涨股） | evidence:12_leading_v00.png | 12 共 2 tiles，仅当前视口 | 实测所见 |
| P1·短线交易情绪 | 短线交易情绪 | evidence:24_short_term_sentiment_v00.png | 24 共 2 tiles（**v1.5 新增实测**） | 实测所见 |
| P1·涨停情绪与切换节点 | 涨停情绪与切换节点 | evidence:25_limitup_switch_v00.png | 25 共 4 tiles 多屏（**v1.5 新增实测**） | 实测所见 |
| 4.2 市场消息·词云（P1/REQ-E03） | 新闻词云图（词云+词根按钮） | evidence:02_wordcloud_v00.png | 02 共 2 tiles | 实测所见（附 Invalid Date bug） |
| 4.2 市场消息·AI 摘要（REQ-A01） | AI 新闻分析 | evidence:03_ai_news_v00.png | 03 共 1 tile | **未测**（会员墙） |
| 4.3 龙头梯队·连板矩阵（核心） | 涨停梯队（日期×板数矩阵+反包配色） | evidence:16_ladder_v00.png | 16 共 2 tiles | 实测所见 |
| 4.3 龙头梯队·角色演化 | 连板龙头股票（龙头随日期演化折线） | evidence:15_ladder_leader_v00.png | 15 共 2 tiles（v00_h00、v01_h00） | 实测所见 |
| 4.3 龙头梯队·股票卡片题材标签 | 热点板块涨幅数据（涨停解读文案） | evidence:17_hot_sector_v00.png | 17 共 3 tiles 多屏 | 实测所见 |
| 4.3 龙头梯队·六类角色 | 涨停龙头（8 日列布局/基因分） | evidence:18_leader_v00.png | 18 共 1 tile | 实测所见（基因分不照搬） |
| 4.5 个股详情抽屉·字段密度 | AI 总结个股（22 列表头） | evidence:05_ai_stock_v00.png | 05 共 1 tile | **未测正文**（积分=0，仅表头） |
| 4.6 监管观察·区域2/区域3（REQ-R02/R03） | 严重异动提醒（偏离值/触发/监管期/多次异动） | evidence:09_anomaly_v00.png | 09 共 2 tiles | 实测所见（推翻旧"竞品无监管"断言） |
| 4.7 我的复盘·自选（REQ-U01） | 自选个股（账号同步） | evidence:20_watchlist_v00.png | 20 共 1 tile | 实测所见（DR 反其道用本地 IndexedDB） |
| 4.1 AI 每日速览（REQ-A04） | AI 总结涨停分析师（按交易日/积分制） | evidence:04_ai_analyst_v00.png | 04 共 1 tile | **未测正文**（积分=0，仅计费 UI） |
| P1·龙虎榜与游资席位（REQ-E02） | 龙虎榜数据（买卖席位/净买入/CSV） | evidence:21_lhb_v00.png | 21 共 2 tiles；**分页表共 43 条、截图仅前 20 条** | 实测所见（P1） |
| P1·游资方向/板块资金 | 游资方向（营业部数/游资占比/净额） | evidence:22_yz_direction_v00.png | 22 共 **4 tiles（纵向 v00/v01 × 横向 h00/h01）** | 实测所见（P1） |
| P1·题材轮动时间轴（REQ-E04） | 题材轮动（5 分钟涨停时间分布/多日对比） | evidence:19_rotation_v00.png | 19 共 3 tiles | 实测所见（P1） |
| P1·行业/产业链视图 | 行业全景（A股/美股/港股对比）；产业链树 | evidence:11_industry_v00.png；evidence:14_chain_v00.png | 11 共 1 tile；14 共 2 tiles | 实测所见（P1） |
| P1·人气关注 | 人气排名（东财+同花顺双源） | evidence:06_popularity_v00.png | 06 共 1 tile，仅当前视口 | 实测所见（P1） |
| —（不借鉴） | 暗盘资金（暗盘/明盘/信号） | evidence:08_darkpool_v00.png | 08 共 **7 tiles（v2.0 FULL，8px 容差，v00/v01/v02b补缝/v02c补缝/v02/v03/v04）**；**暗盘资金共 5353 条、仅第 1 页** | 实测所见，判定不照搬（算法不透明） |

### C.2 证据性质汇总（按 26 模块 / 68 tiles 口径）

- **实测所见模块（23 个，68 tiles）**：01、02、06、07、08、09、10、11、12、13、14、15、16、17、18、19、20、21、22、23、24、25、26。其中 08 实测但判定**不借鉴**；v1.7 中 08 已由 PARTIAL 改判 **FULL（8px 容差）**，至此 **26 个模块覆盖状态全部 FULL、无 PARTIAL**；23/24/25/26 为情绪/复盘模块（合计 9 tiles：23=2、24=2、25=4、26=1）。注意"覆盖 FULL"与"证据状态"正交：03/04/05 视口 FULL 但正文仍未测。
- **未测（3 个模块，均为 AI 正文/权限墙，共 3 tiles）**：03（会员墙）、04（积分=0）、05（共用积分=0）——仅观测到权限/计费 UI 与字段表头，**未观测 AI 输出与引用链**；此三模块证据不能证明竞品 AI 的算法/质量。
- **仍未展开父菜单（无 tile）**：潜伏新闻、每日新闻概念、趋势复盘；龙虎榜下"游资龙虎榜"及"上榜统计/游资统计/席位活跃度"未单独取证。（注：v1.4 列为未展开的"情绪周期"本轮已实测，对应 23/24/25/26。）
- **多 tile 覆盖（非单屏完整，按 tiles 数降序）**：13_concept=16、08_darkpool=7（含补缝 tile v02b/v02c）、25_limitup_switch=4、22_yz_direction=4（纵向 2 × 横向 2）、17_hot_sector=3、19_rotation=3，其余为 1~2 tiles（15_ladder_leader=2）。逐行相加 = 68，与 `_coverage_meta.json` 逐 tile 实数及 `screenshots_full_final/` 实有 PNG 一致。
- **分页/长表仅当前页/当前视口（不假装全量）**：21_lhb（共 43 条，截图仅前 20 条）；08_darkpool（共 5353 条，截图仅第 1 页）；13_concept（16 tiles 多屏仍非全量 406 概念/仅当前视图）；06_popularity、12_leading 排行类同理。**当前页不等于全量。**
- **站方宣称（文案层）**：首页"数据都为网上公开获取，不构成投资建议"、积分制定价（1 元=1 积分/新用户送 5 积分）等为页面文案，DragonRadar 不据此承诺同等数据覆盖或计费模式。

### C.3 对 v1.3/v1.4 结论的影响

- **保留并强化**：三区域监管差异化（REQ-R01~R04）——竞品虽有异动监控（evidence:09_anomaly_v00.png，2 tiles），但为表格堆叠、无数据完整性/计算状态区域，DragonRadar 的三区域独立 + 阈值独立 + 评审开关仍为差异化核心。
- **保留并强化**：本地 IndexedDB 路线——竞品自选为账号同步（evidence:20_watchlist_v00.png），DragonRadar"打开即用、免登录、本地唯一权威源"构成明确差异。
- **情绪与热力图边界修订**：23/24/25/26只提供视觉参照，不证明公式有效；26不能作为日期×板数家数口径的实测证据。按2.0本次输出独立且透明的0-100研究分与阶段标签，禁止解释为未来概率。
- **不改动**：29 端点、13 表 schema、57 REQ 映射、七页 ASCII、合成 fixture、数学自洽——本附录仅修订证据映射与计数，不触碰上述内容。
