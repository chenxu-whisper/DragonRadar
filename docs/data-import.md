# 本机数据导入契约

后端接收显式提供的本机 JSON 数据批次，不自动下载行情、不推定供应商授权，
也不猜测历史节假日日历。操作者必须单独登记当前本机使用授权，导入批次不能代替授权登记。

## 输入内容

`dragon_radar.domain.models.MarketBatch` 是可执行的数据结构定义。
使用 `MarketBatch.model_json_schema()` 查看准确的必需字段，不再维护另一份手写结构。
Decimal 字段必须使用 JSON 字符串或整数，不能使用 JSON 浮点数。

每个批次必须提供：

- `trade_date` 和带时区、统一转为 UTC 的 `as_of`；观测数据的首次可用时间不能晚于截止时点。
- `provenance`：显式选择 `contemporaneous`（当时观测）或 `retrospective`（事后回补）。
- `source_id`, `source_version`, `adjustment_version`, `calendar_version`,
  `pool_version`：分别记录来源标识及来源、复权、日历、股票池版本。
- `grant`：已批准的来源标识、授权依据、生效与到期时间，以及独立的存储、展示、
  导出、AI 和分享权限。
- `calendar`：交易所、版本和明确列出的交易日；日期必须有序且不重复。
- `expected_security_ids`：显式声明当前覆盖率的分母集合，不能从实际到达的数据行反推。
- `security_states`：不可变的历史证券身份与状态区间，包含首次可用时间、上市日期和明确的停牌状态。
- `bars`：原始日线价格及其来源、复权因子版本、首次可用时间、复权因子、币种和单位。
  支持完整 OHLC（开、高、低、收）或仅收盘价数据。
- `complete_pages`：只有确认采集完整时才能显式设置为 `true`。

证券状态的生效区间包含 `effective_from`，不包含 `effective_to`。
授权有效期包含 `starts_at`，不包含 `expires_at`。不得用系统当前日期代替源事件时间。

研究收益使用“原始收盘价 × 复权因子”计算，整个批次必须使用同一复权因子版本。
涨跌停判断不能使用复权价格。`limit_status="limited"` 要求限价适用日期与交易日一致，
且上下限价格均为正数、互不相同并顺序正确。`unknown`（未知）不等于 `no_limit`（无限制）。

## 失败与修订

分页不完整、重复记录、无效价格、身份或版本不一致、异常缺失等情况都会阻断发布。
在允许存储的前提下，这些问题会保留为可检查的质量记录。
数据结构不符合 schema 时，会在业务校验前拒绝输入。没有存储权限时不能保存行情原文；
不得以调试为由留存未授权原始数据。

相同内容重试应使用稳定的幂等键。同一键用于不同内容时返回冲突，而不是覆盖。
快照发布后，仓储接口和普通数据库写入均不得编辑或删除它。
更正必须创建新批次与新修订版；读取旧快照仍需检查当前来源授权。

## 虚构测试材料

测试使用明确命名为 `SYNTH-*` 或仅存在于测试夹具中的证券，不下载真实价格。
这些材料仅用于测试，不是投资示例或生产内容。禁止在真实研究数据库执行测试初始化或导入命令。

`examples/synthetic-grant.json` 与 `examples/synthetic-batch.json` 是配套的本机测试文件。
批次刻意使用 `TEST` 交易所、一只虚构证券和两个明确的交易日。
收盘价从 100 变为 110，对应收益率 0.1、回撤 0，以及从 100 到 110 的归一化曲线。
两个交易日均明确无涨跌停限制，因此不能把 10% 收益识别成涨停事件。
源事件时间保持未知，不复制采集时间。请使用专用测试数据库并查询 `n=1`；
该样例无法满足默认 20 日窗口。

将 `DRAGON_DATABASE_URL` 仅指向可丢弃的测试数据库后执行：

```powershell
uv run dragon-radar migrate
uv run dragon-radar source-register examples/synthetic-grant.json
$import = uv run dragon-radar import examples/synthetic-batch.json --key synthetic-v1 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Import failed" }
$published = uv run dragon-radar publish $import.batch_id | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Publication failed" }
uv run dragon-radar serve --host 127.0.0.1 --port 8011
```

最后一条命令会持续占用该终端。在另一终端中，使用返回的快照 ID 查询
`GET /api/v1/snapshots/{snapshot_id}/interval?n=1`。
也可通过 `GET /api/v1/snapshots?trade_date=2026-09-08` 重新查找已发布的 ID。
执行 `source-revoke synthetic-example` 会阻止后续读取与发布，但不修改归档内容。
相同键和内容重复导入时返回原批次；重复发布时返回原快照。
内容变更必须使用新键，并创建新修订版。

为保证研究可复算，应保留来源内容、交易日历、因子版本、截止时点和软件版本。
今天取得的历史回补数据可以支持事后计算，但不能证明本系统在历史交易日已经知道这些信息。
