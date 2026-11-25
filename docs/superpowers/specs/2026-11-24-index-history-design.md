# IH1 指数历史设计

日期：2026-11-24。状态：用户已统一批准，工程实施中；未验收。
目标：补齐 REQ-M01 的指数详情历史数据，不改变已有发布结果。
源码调查基线：`d90a02e98abfd517a462c077a9f08bcbed32a347`。

## 方案选择

推荐在既有市场物化 JSON 中增加可选、独立版本化 `index_history` 块。
发布时从同一市场归档生成，读取时只解析它。无需增加第六发布域。
另一方案是独立指数历史表与发布域；它适合不同更新周期，但需要新的发布协调、
迁移和水位协议，当前需求没有要求独立更新周期，故不采用。

旧物化结果不回填，新发布才有新块。当前指数报价及 FAC-M03 继续保留。
指数历史覆盖率使用交易日分母，与股票涨跌覆盖率、题材成员覆盖率分开。

## 输入与单位

身份使用当前 `SecurityState.security_type == "index"`、有效区间、
`source_id/source_version/pool_version` 和唯一日历。未知身份不能从代码猜测。
现有 `DailyBar` 的股票单位检查不能直接放宽为接受任意单位：

1. 股票继续使用现有 currency/share、share/lot、currency 单位检查。
2. 指数输入增加可选、冻结的 `index_basis`，包含 `series_type`、
   `series_reference`、`price_unit`、`amount_currency`、`volume_unit`。
3. `series_type` 精确枚举 `price`、`total_return`；必须由供应商明确提供。
   两种序列不能合并，不能从名称或数值推断。
4. 指数价的规范单位为 `point`；成交额仍为 currency，且实际货币必须明确。
   amount_currency 仅在 amount 全部缺失时可为 null，不能据指数点数推断货币。
   成交量可为供应商明确声明的 share/lot，或 `not_applicable` 且值必须为 null。
   不把指数点数当作每股价格，不给缺失量或金额填零。
5. 初版仅接受 raw。规范输入明确 `adjustment_factor=1`，
   `adjustment_version=raw-index-v1`，表示不应用股票复权。
   原供应商序列版本仍独立保存，不由 raw-index-v1 替代。
   新 index_basis 已明确的指数不使用股票 batch.adjustment_version 匹配规则；
   它使用自身 raw-index-v1 和供应商序列版本检查。因此混合股票/指数 batch
   仍允许股票保留原有复权版本。这个分支只适用于已知有效指数身份。
6. 历史股票式指数归档没有 index_basis 时，现有报价行为不改；
   新历史块记录 `index_basis_unavailable`，不能把它默认为点数价格指数。

非有限 Decimal、非正价格、负量额、重复键、错版本、未来可用时间以及倒置 OHLC
沿用隔离规则。缺 close 的已知交易日保留不可用原因；不能因此生成合法数值。
存在且无合法 close 的输入 bar 仍进入质量隔离并阻止对应输入发布；
成功归档中的历史 gap 来自规范日历内未提供的观测，不放行坏 bar。
当前 expected 池的完整性要求保持原契约，不因历史图支持缺口而放宽。
规范化 index_daily_kline 不是历史读取源；若其字段无法保存新 basis，
新 basis 保存在不可变输入 JSON，不在旧表中伪装股票单位。

## 物化契约

`index_history.schema = dragon-radar.index-history`，`schema_version = 1`。
块包含市场快照身份、源归档 ID/摘要、日历版本、归档截止和按 security_id 排序的 indexes。
每个指数保存身份、index_basis、源版本、price_type=raw、archive_start/archive_end、
points 和独立 coverage。

每个 point 包含 trade_date、nullable open/high/low/close/volume/amount、
available_at、source_id、source_version、reason、chart_type。
数值使用确切 Decimal/十进制字符串，不接受 float、NaN 或 Infinity。
完整合法 OHLC 为 candle；仅合法 close 为 line；缺失价格为 gap。
绘图不能跨 gap 连线。一个混合窗口可显示线与缺口；不能补造 OHLC。

发布最多保存截至 batch.trade_date 的最后 250 个规范交易日。
日历存在而观测缺失的交易日也进入 points。
coverage 为 expected、valid、excluded、missing，满足分区恒等式；
valid 表示有合法 close，excluded 初版恒为 0。无规范日历时不猜分母，
返回整个历史不可用，而非 expected=0 的成功序列。

历史观测必须满足 available_at <= market as_of；回溯输入保留 retrospective 标签，
不能声称为当时已接收的数据。P2 不能改变 P1 的块、单位或身份。

## HTTP

新增 GET `/api/index/{security_id}/history`。
必填 date、snapshot_id、start、end，可选 adj=raw。
日期与 PUB 必须匹配；end 不得超过 PUB 交易日。start <= end。
按归档日历将 start 向后、end 向前调整，最多 250 个交易日。
超出归档范围的请求不从当前库重建；响应分别显示请求、调整、实际可用边界。
未知参数、重复参数、非法 UUID、非法日期和非 raw 复权在访问存储前拒绝。

响应沿用 daily envelope，data 保存指数身份、basis、三组边界、points、chart_type、
独立 coverage 与来源信息。PUB as_of 保持行情截止，不能换成 HTTP 请求时间。
显式未知 PUB/指数 ID 为 404；坐标错配为 400。
legacy 缺整个块返回 200/null + materialization_unavailable；
已知指数无历史为 200/null + index_history_unavailable；
无窗口交易日为 200/null + no_sessions。
存在但 schema、身份、日期、数值或覆盖不合法的块为 503 DATA_UNAVAILABLE。
禁止回退到股票 K 线、规范化表、当前报价拼接或读取时计算。

读取同时检查嵌入许可与当前许可；本机 display、公开 sharing 分别检查。
403 不返回受保护内容。初版无指数 CSV 导出按钮；后续导出必须独立检查 export。
public_app 与两套网关仅开放新增 GET，不扩大私人路径权限。

## 页面

ResearchDrawer 保留现有报价/FAC-M03，新增独立历史请求及 20/60/120/250 交易日窗口。
默认 60，边界从当前 PUB 日历产生，不能用自然日或今日重新定位。
显示单位、序列类型、实际范围、覆盖率、来源与缺失原因。
不足窗口如实缩短；未知 basis 继续显示明确不可用状态。
请求身份包含指数、PUB、日期和窗口；切换或撤权时清空旧图，
迟到响应不得重新填入。现有 Esc、焦点返回和单抽屉规则继续有效。

## 文件职责

| 文件 | 工作 |
| --- | --- |
| src/dragon_radar/domain/models.py | 可选冻结 index_basis |
| src/dragon_radar/domain/quality.py | 基于已知类型的单位验证，保留股票检查 |
| src/dragon_radar/readmodels/index_history.py | 新纯发布物化器与严格归档解析器 |
| src/dragon_radar/readmodels/market.py | 新发布调用物化器，旧块兼容 |
| src/dragon_radar/http/routes/indexes.py | 精确 PUB 选择和只读投影 |
| src/dragon_radar/http/app.py、http/public_app.py | 路由装配及显式 allowlist |
| containers/public.Caddyfile、owner.Caddyfile | GET 代理边界 |
| web/src/features/workbench/ResearchDrawer.tsx | 历史窗口、图表与状态 |
| web/src/features/workbench/index-history.ts | 独立请求/数据校验 |
| tests/test_index_history.py | 单位、归档、窗口、HTTP 和许可测试 |
| web/tests/unit、web/tests/e2e | 抽屉、窗口、缺口、撤权与迟到请求测试 |

## 验收

先用固定指数输入取得 RED，再实现 GREEN。
覆盖股票单位不退化、两种指数序列不可混用、旧 basis 降级、250 日边界、
休市调整、晚到观测、仅 close/缺口、Decimal 精度、P1/P2 不变、
无规范化/当前数据回退、400/403/404/503、焦点返回与旧响应清空。
增加原始命令日志和最终源码摘要；通过现有质量检查及适用构建/浏览器门禁。
真实指数授权、序列身份、历史完整性和 PIT 仍需真实输入才能验收。
