# 研究工作台只读接口

本批仅面向本机开发入口，不表示已实现匿名公网授权。每次读取都会检查当前来源授权，
响应中的所有内容来自选定快照，禁止通过日期不存在时回退最新数据。

## 公共元信息

响应封装保留 `trade_date`、`as_of`、`source_time`、`snapshot_id`、
`source`、`coverage`、`coverage_scope`、`analysis_coverage`、`units`、
`quality_flags`、`rule_version`、`basis`、`manifest`、`provenance` 和 `result`。
价格、金额和比率使用精确字符串或 `null`；计数和分页字段仍使用整数。
比率按小数传输，展示时才乘 100。
`source_time=null` 表示可靠源时间未知，不用请求时间代替。

## 证券列表

`GET /api/v1/snapshots/{snapshot_id}/securities`

| 参数 | 约束 |
| --- | --- |
| `q` | 名称或证券 ID 搜索，最长 100 字符 |
| `sort` | `security_id`、`name`、`close`、`change`、`amount` |
| `order` | `asc` 或 `desc` |
| `page` | 1～100000，默认 1 |
| `page_size` | 20、50 或 100，默认 50 |

`result` 包含 `items`、`total`、`page`、`page_size`、`basis`。
证券行包含 `security_id/name/exchange/board/close/change/amount/suspended`、
`missing_reasons` 和单位字段。`change` 为日收益比率，不是百分数字符串。
排序按原始 Decimal；空值始终在末尾，相同值按证券 ID 稳定排序。

## 个股详情

`GET /api/v1/snapshots/{snapshot_id}/securities/{security_id}`

`result` 包含 `security`、`history`、`chart_type`、`price_basis`、`basis`。
`history` 按快照内明确交易日列出原始 OHLC、成交量、成交额、停牌与缺失原因；
缺价或停牌是空点，不生成假蜡烛，不把缺口连成连续行情。
`chart_type` 为 `candlestick` 或 `line`；线图与 K 线均必须附等价数据表。
证券 ID 不属于快照预期池时返回 404，不从其他快照获取数据。
每个历史点有独立的币种与单位。`chart_type` 仅说明 OHLC 完整性，不保证跨日
单位一致；客户端不能将跨币种或跨价格单位数值直接连线，也不能自行换汇。

## 原有查询

- 快照列表：`GET /api/v1/snapshots?trade_date=YYYY-MM-DD`，可分页；无数据返回空列表。
- 快照详情：`GET /api/v1/snapshots/{snapshot_id}`，`result` 新增
  `calendar_sessions` 和 `earliest_date`。
- 市场广度：`GET /api/v1/snapshots/{snapshot_id}/overview`。
- 区间结果：`GET /api/v1/snapshots/{snapshot_id}/interval?n=20`，或使用
  `start`／`end`；`ids` 为最多五个不重复 ID 的逗号分隔字符串。

`coverage` 描述快照当日覆盖，`analysis_coverage` 描述所选分析窗口的资格，
两者不能混用。区间响应中的 `sessions` 是图表与比较的共同日期轴。

## 错误与页面处理

400／422 表示参数无效或样本不足，403 表示授权不可用，404 表示快照或证券不存在，
503 表示存储未就绪。页面应保留输入条件，并用中文提供重试或修改条件入口。
403 不继续展示缓存的数据；网络错误保留旧数据时必须保留原快照、原时间和降级标记。
前端不能自动调用管理、发布、采集或 AI 任务；本批接口没有这些写操作。
