# 供应商采集、映射与离线样本验收

本轮实现标准库 HTTPS 采集、采集预检 CLI、显式字段映射及样本验收 CLI。
代码已整合到本地 `main`。下列命令从项目根目录执行，使用已有 `uv` 环境。
真实 iFinD 账号、接口参数、字段定义、限额和存储权利须以供应商实际交付为准；
本轮未访问厂商服务，也未生成生产许可、签章、原始历史接收证明或数据库记录。

## 1. 采集适配层

本机命令行入口默认仅核验配置，不联网、不写采集档案：

```powershell
uv run --no-sync python -m dragon_radar.ingestion.collection_cli --request "<私有请求文件>" --grant "<私有授权文件>" --mapping "<私有映射文件>" --archive-root "<已存在的专用私有归档目录>" --output "<归档目录外的新报告文件>"
```

确认配置后，在同一命令追加 `--execute` 才采集。也可使用
`uv run --no-sync python scripts/collect_supplier_sample.py` 加相同参数。
以上路径为说明占位符，须换成操作者实际文件；不是厂商接口参数模板。
三个配置文件均限制4 MiB，要求私有常规文件，拒绝链接、重复 JSON 键和
非有限 JSON 数字。请求文件使用下列 `CollectionRequest` JSON 契约；
授权文件使用 `SourceGrant`，映射文件使用第2节的 `MappingProfile`。
request 的 profile_id/source_id/source_version 与 mapping 必须一致，
profile_sha256 必须是实际 mapping 文件字节的 SHA256，格式变化也须更新摘要。

预检检查当前 UTC 存储授权、归档根存在及私有权限、有界采集参数；
`checkpoint_status="not_checked"`，不声明已有断点已核验或字段获厂商验证。
显式执行才由 collect 核验全部断点并恢复。
不提供 `--now` 或直接令牌参数；令牌仍只读取环境变量。
`--token-env` 默认 IFIND_ACCESS_TOKEN，只允许大写字母开头的环境变量名。
`--timeout` 默认10秒、范围 (0,60]；`--max-attempts` 默认3、范围1–5；
`--retry-delay`、`--request-interval` 均默认1秒、范围 [0,60]。
未知或缩写参数均拒绝。预检不需要令牌，新分块发送前才检查凭据。

`--output` 可省略；指定时在采集前独占预留私有文件，
拒绝覆盖、输入路径碰撞或位于归档目录内。无法预留时发送次数为0。
报告 schema 为 `dragon-radar-supplier-collection-report/v1`；
只含固定状态码、配置摘要、证券/会话/分块数量及成功采集指纹，
不含证券代码、请求、行情、来源名、授权正文或令牌。
退出0为工程预检/采集成功，2为授权、凭据或服务可用性缺项，
1为配置、文件、映射身份、断点、响应或输出错误。
采集过程中失败会将安全错误报告写入已预留文件；输出本身失败时只报告安全错误。
报告固定 production_approved=false、historical_pit_status=unverified。

程序化入口为 `dragon_radar.ingestion.supplier_collection.collect`。
传输边界为 `IfindTransport`，默认只接收 HTTPS 主机
`quantapi.51ifind.com` 和 `quantapi.10jqka.com.cn`；拒绝端口、凭据 URL、
查询串、重定向及环境代理。接口路径必须由获授权的实际接口文档提供。
令牌从 `IFIND_ACCESS_TOKEN` 读取，或通过 `token_env` 指定环境变量名，
不会进入请求配置、归档或错误文本。

`CollectionRequest` 是严格不可变配置，字段如下：

| 字段 | 含义 |
|---|---|
| `source_id`, `source_version` | 与授权及后续 batch 完全一致的来源身份 |
| `profile_id`, `profile_sha256` | 已确认映射配置身份及文件摘要 |
| `endpoint` | 获授权的官方 HTTPS 接口地址 |
| `securities` | 明确的供应商证券代码列表，不自动构建证券池 |
| `calendar` | `ExchangeCalendar` 的 exchange/version/sessions |
| `sessions` | 日历中明确选定的有序唯一 ISO 日期 |
| `max_codes`, `max_sessions` | 每批证券、日期数量；默认100、20，上限均1000 |
| `payload_json` | 确认后的非敏感接口参数 JSON 字符串，默认 `{}` |
| `codes_field`, `sessions_field` | 供应商实际使用的两个列表字段名；不得与 payload 重复 |

调用方式：

```python
from pathlib import Path

from dragon_radar.domain.models import SourceGrant
from dragon_radar.ingestion.supplier_collection import CollectionRequest, collect
from dragon_radar.ingestion.supplier_transport import IfindTransport

# request_json / authorized_grant_json 由操作者从受保护配置加载。
# 它们必须使用已确认的供应商字段、日历和真实授权。
request = CollectionRequest.model_validate_json(request_json)
grant = SourceGrant.model_validate_json(authorized_grant_json)
result = collect(
    request,
    grant=grant,
    archive_root=Path(private_archive_directory),
    transport=IfindTransport(request.endpoint, timeout=10),
    max_attempts=3,
    retry_delay=1,
    request_interval=1,
)
```

`archive_root` 必须事先存在且仅当前用户可读写，一次采集计划使用一个专用目录。
本实现不改动操作者的目录权限。每次发送、实际接收和持久化都重新检查
`SourceGrant` 的来源、有效期和 `local_storage` 权利。
认证错误、重定向和非法数据不重试；429、5xx、超时在限定次数内重试。
最多5次尝试，单请求超时最多60秒，响应最多4 MiB；延迟/请求间隔最多60秒。

每个分块保留 `chunk-00000-<档案摘要>.raw.json` 私有不可覆盖档案，
以及 `chunk-00000.done.json` 标记。
raw 档案包含 schema、请求身份和摘要、base64 原始响应、原始响应摘要、
实际 `first_received_at` 及固定 `provenance="retrospective"`。
重启时使用完全一致的配置和目录，核验身份与所有摘要后复用已完成分块；
原始档案已写入但标记未写入的中断可安全恢复。损坏/冲突需人工处理，
不得删除档案后将后来的采集伪装成此前已接收的数据。
调用成功后，`result.chunks` 为有序不可变 tuple；每项包含 index、
fingerprint、相对 raw_path、raw_sha256、archive_sha256、
first_received_at 和 provenance。可据此绑定后续字段提取与样本输入。
失败抛出 `SupplierError`，仅固定 `code` 和 `retryable` 可用于诊断，
不应将环境令牌、原始响应或接口异常文本写入日志。

响应表结构仍由供应商交付定义。采集层保留响应，不自动推定分页已完成、
自动解析未经确认的 vendor 包装结构或生成 canonical 股票身份。
供应商字段确认后，按其真实结构提取 bar/state 行，并保留归档引用。
当前分块接口适用于显式证券和日期两个列表参数的接口；不同 SDK/分页形态
应在同一传输边界下单独适配，再声明 `complete_pages`。

## 2. 显式数据映射

`MappingProfile` 的 JSON 字段为：

```text
schema = "dragon-radar-supplier-mapping/v1"
profile_id, source_id, source_version
bar_fields, state_fields
bar_constants, state_constants
value_maps, decimal_scales
```

`bar_fields` / `state_fields` 为「canonical 字段名 → 真实供应商列名」。
目标字段分别取自 `DailyBar` / `SecurityState`；字段完整定义参见
`src/dragon_radar/domain/models.py` 和 `docs/production-inputs.md`。
常量使用 canonical 值，不能与同类字段映射重叠。
`source_id/source_version` 来自 profile；显式映射或常量不能覆盖来源身份。
`security_id` 必须明确映射或指定常量，不从当前显示代码推导。

`value_maps` 为「canonical 字段 → 原始字符串 → canonical 值」，
例如供应商明确声明的停牌标志才能映射为 `true` / `false` / `null`。
未声明枚举值产生错误；未知状态不会补为 false。
`decimal_scales` 为「Decimal 字段 → 正的有限十进制字符串倍率」；
倍率对显式字段或常量精确相乘，必须同时声明正确的目标单位。
价格/金额等使用精确字符串或整数，拒绝 Python float/bool 及非有限数。
日期字段仅接受规范 `YYYY-MM-DD` 或 Python date，时间必须明确时区。
不推定交易所、日历、ST 状态、复权因子或单位。

调用 `normalize_records(profile, bar_rows=..., state_rows=...)` 返回
`NormalizationResult(bars, states, issues)`。每个 issue 只包含
`kind`、从0开始的 `row_index`、canonical `field`、固定 `code`。
必须检查 issues 再进行下游质量验收，不能只取成功行宣称完整。
输入中未使用的列不进入 normalized 数据。

### 从已核验归档映射

`read_collection` 提供离线完整归档读取：

```python
from dragon_radar.ingestion.supplier_collection import read_collection

verified = read_collection(
    request,
    grant=grant,
    archive_root=private_archive_directory,
)
```

要求计划全部原始分块齐备，并核验请求身份、原始/档案摘要、现有标记及
实际接收/当前时刻的存储授权。完整raw但缺done标记可读取；
该入口不会补写标记或发送请求，也不读取环境令牌。
返回不可变 VerifiedCollection；每个 VerifiedChunk 含 metadata 和 raw bytes，
raw不进入repr。返回的bytes就是本次核验读取的bytes。
默认最大档案总字节64 MiB（raw envelope及现有done标记均计入），
可用 `max_total_bytes` 在1–64 MiB范围降低；
部分归档、额外/损坏分块和超预算全部拒绝，不返回部分数据。

映射桥接通过显式对象键和数组索引选择对象行数组：

```python
from dragon_radar.ingestion.supplier_archive_mapping import (
    RowExtraction,
    normalize_collection,
)
from dragon_radar.production.trust import read_asset

# 实际路径必须根据厂商交付的响应结构配置；这里只展示路径语法。
extraction = RowExtraction(
    bar_path=("tables", 0, "bars"),
    state_path=("tables", 0, "states"),
)
mapping_json = read_asset(private_config_directory, "mapping.json", 4 * 1024 * 1024)
result = normalize_collection(
    request,
    grant=grant,
    archive_root=private_archive_directory,
    mapping_json=mapping_json,
    extraction=extraction,
)
if result.normalization.issues:
    # 保留全部问题；不能取成功行宣称完整验收。
    raise RuntimeError("normalization_failed")
bars = result.normalization.bars
states = result.normalization.states
```

两类路径可只声明一类，未声明类返回空tuple；至少声明一类。
路径非空、最多16段；字符串为精确键，整数为非负数组索引；
拒绝通配符、bool/负索引、缺路径及非对象数组。
每个分块使用同一显式结构配置；不自动拼接或推定供应商表/分页结构。
两类提取行累计最多100000。mapping JSON最多4 MiB且拒绝重复键、
非有限数；实际字节SHA256及profile/source身份绑定CollectionRequest。

桥接profile不得声明bar/state的 available_at 字段映射或常量。
trade_date、limit_date、effective_from、effective_to、listing_date
也不得配置为常量；日期须由供应商行显式提供或保持未知。
所有记录以所属归档块的实际 first_received_at 作为 available_at，
行中的供应商发布时间不会替代接收时间。字段有效日期仍由行显式给出。
profile与归档保持不可变；Normalizer问题的row_index为各类别在整个采集中的
全局行号（从0开始），包括失败行。
CollectionNormalization返回collection_fingerprint、profile_sha256、
不可变chunks元数据及normalization。固定provenance=retrospective、
historical_pit_status=unverified、production_approved=false。
结构映射之后仍须构造明确MarketBatch并执行样本/质量验收；
完整归档不表示供应商分页已完成，也不表示历史PIT证明成立。

库错误仍为固定SupplierError.code：collection_incomplete、
archive_budget_exceeded、invalid_extraction、extraction_failed、
mapping_binding_mismatch，或既有授权/断点/文件错误码。
不应将raw响应、原始行、供应商字段或带值异常打印到操作者报告。

## 3. 离线样本验收 CLI

输入 `sample.json` 顶层必须恰好包含：

```text
schema = "dragon-radar-supplier-sample/v1"
kind = "test" 或 "supplier_sample"
batch = MarketBatch
benchmark_closes = {"YYYY-MM-DD": "精确收盘价", ...}
decision_sessions = ["YYYY-MM-DD", ...]  # 非空、有序、唯一
window = 20                            # 严格整数
```

batch 使用现有完整契约：trade_date、as_of、source_id/source_version、
adjustment_version/calendar_version/pool_version、grant、calendar、
expected_security_ids、security_states、bars、complete_pages、provenance，
以及可选 source_time、limit_up_events。缺状态或行情可用空列表表达；
缺数据会报告 blocked，不能省略必填契约字段。
历史当前回补的 `as_of` / `available_at` 应记录真实接收事实，
不能回写到历史决策时点。

直接验收 canonical 样本：

```powershell
uv run --no-sync python -m dragon_radar.ingestion.sample_cli ".temp/sample.json" --output ".temp/sample-report.json"
```

或将已确认的供应商行映射后验收：

```powershell
uv run --no-sync python scripts/verify_supplier_sample.py ".temp/sample.json" --mapping ".temp/mapping.json" --bar-rows ".temp/bar-rows.json" --state-rows ".temp/state-rows.json" --output ".temp/sample-report.json"
```

映射三个参数须同时提供。行文件是 JSON 对象数组；映射来源与 batch 必须一致。
映射成功后替换 batch 的 bars/security_states；其余授权、日历、版本、范围
仍由 sample 提供。任意异常行使验收失败，不会退回 sample 原有正常行。
`--now` 仅用于明确的离线重放时间，如 `2026-11-16T12:00:00+00:00`；
省略时使用当前 UTC，不能通过旧时间参数替代实际生产授权检查。

首版候选范围为 SSE / sh_main / common_stock。
检查完整历史身份、ST/停牌/退市状态、OHLC、成交量/金额/单位、
明确限价信息及全部 OHLC 的涨跌停边界、每个决策日所需21会话和对应基准。
复用 `validate_batch` 检查重复、版本冲突、有效区间、迟到记录等。
报告包含摘要、覆盖数、缺项日期及错误码，不包含原始行情或输入值。
所有输入限16 MiB，行数组最多100000行，拒绝重复 JSON 键及非法非有限 JSON 数字。
输出私有独占写入，拒绝覆盖既有文件及输入/输出路径碰撞。

| 退出码 | 结果 |
|---|---|
| 0 | 工程样本检查合格 |
| 2 | 缺授权、缺证据或缺数据，blocked |
| 1 | 结构错误、冲突、非法文件或映射错误，fail |

所有报告固定 `production_approved=false`、
`historical_pit_status="unverified"`；`kind=test` 固定 `test_only=true`。
本 CLI 不加载数据库、不发布快照、不生成签章，也不能通过五项生产门禁。
真实输入准入继续使用独立受认证的生产证据链。

### 完整归档直接映射并验收

新离线入口将上述两个API与样本质量检查串联：

```powershell
uv run --no-sync python scripts/verify_supplier_archive.py --request "<私有request.json>" --grant "<私有grant.json>" --mapping "<私有mapping.json>" --extraction "<私有extraction.json>" --sample "<私有sample.json>" --archive-root "<专用私有归档目录>" --output "<归档外的新报告文件>"
```

也可使用 `python -m dragon_radar.ingestion.archive_sample_cli` 加相同参数。
该入口完全离线；没有 `--execute`、令牌参数或数据库依赖。
五项配置必需且须为私有普通文件，每项最多4 MiB，拒绝重复JSON键、
非有限JSON数字、不安全链接和目录。归档沿用64 MiB总预算。

extraction.json只声明已确认响应结构的精确行路径，例如测试结构：

```json
{"bar_path": ["data", "bars"], "state_path": ["data", "states"]}
```

这只是路径语法，不是iFinD真实响应模板。至少声明一类，未声明类为空。
sample.json沿用上文SampleRequest完整结构，但
`batch.bars`、`batch.security_states`、`batch.limit_up_events`必须为空列表；
`batch.provenance`必须为`retrospective`。
batch来源及版本、完整grant、完整calendar须与采集配置相同，
calendar_version须等于calendar.version，trade_date须为采集最后一个session。
expected_security_ids、adjustment_version、pool_version、benchmark_closes、
decision_sessions、window和complete_pages由操作者明确给出。
不自动推定供应商代码到canonical security_id的关系。
完整归档仅说明计划分块齐备；不能替代complete_pages分页声明。

batch.as_of保持样本中明确声明的时间，必须不早于所有分块实际接收时间；
每行available_at仍按实际first_received_at注入，不回写、不自动调整as_of。
任何映射问题直接fail并保留normalization_issues（类别全局行号及canonical字段），
不会取成功行继续验收；成功映射后检查bar日期属于采集sessions，再调用完整
样本质量验收，包括来源/复权/池版本、状态区间、重复记录、历史覆盖及基准。
授权/数据缺项为blocked，非法结构/冲突为fail；退出码仍为0/2/1。

默认使用当前UTC并在归档处理和最终验收时重新检查操作时间与授权。
`--now`只用于显式离线时间重放，不能代替生产当前授权或历史PIT证明。
报告包含collection_fingerprint、chunks、五项配置实际SHA256、
覆盖数与安全问题；不包含raw行、股票标识、厂商字段、授权reference或路径。
固定provenance=retrospective、production_approved=false、
historical_pit_status=unverified，test样本保持test_only=true。
输出须在归档目录外且不得等于任何输入，私有独占预留失败时不开始归档处理。
stdout和输出文件包含同一报告。

### 不可变标准样本包

在上述离线命令追加 `--sample-output "<归档外的新私有样本文件>"` 才保存标准样本。
默认仍只输出诊断；只有映射和质量均pass时生成包，blocked/fail只保留诊断。
包为 `dragon-radar-normalized-sample-archive/v1`，最多16 MiB，
包含完整SampleRequest与五配置实际字节摘要、collection_fingerprint、
每块index/fingerprint/raw_sha256/archive_sha256/first_received_at。
完整样本含授权字段、证券和行情，须私有保存；包不含原始响应、令牌、归档路径
或整份授权证明。报告只增加安全 `sample_archive_sha256`，其值为整份canonical
UTF-8文件字节SHA256，不嵌入包自身。

两个输出都先独占预留，之后才读输入。报告写入并完成fsync/关闭后，再写入样本；
写入前后重新检查授权；合格结果的报告及样本均在各自最终flush/fsync后、
关闭文件前再次检查当前授权。report-only、sample-only及两输出模式均适用。
任何处理/写入/fsync失败或持久化期间授权过期都会清理相应预留。
报告不是完成标记：样本最终写入或fsync期间失败/失效时报告可能已保存，
样本必须不存在，消费必须以实际样本读取核验为准。
强制终止可能留下空/部分文件，读取拒绝，重跑拒绝覆盖；人工核对后处理残留。
输出互相、输入文件和采集归档目录冲突均拒绝。

读取使用调用方独立保存的预期摘要和当前适用完整授权：

```python
from pathlib import Path
from dragon_radar.ingestion.sample_archive import read_sample_archive

package = read_sample_archive(
    Path(private_sample_path),
    expected_sha256=retained_digest,
    grant=current_grant,
)
sample = package.sample
```

读取拒绝摘要不符、非canonical/重复键、超预算、链接或非私有文件，
重新校验质量、来源及实际接收时间绑定，并在读取前和返回前检查当前存储授权。
当前grant必须与保存内容完全一致；授权内容变化应重新验收归档，不能替换旧包。
available_at、as_of、日历、记录顺序、精确Decimal与complete_pages保持原值。
`write_sample_archive`在最终flush/fsync后、仍可安全回滚时再次检查授权；
期间失效抛storage_denied并清理文件，不返回可供消费的摘要。
共享服务 `archive_sample.evaluate_archive` 接受五项配置bytes、archive_root和clock，
返回安全report及只在pass时存在的archive；`write_sample_archive`独占保存该对象。
服务在返回前再次检查当前授权；只出诊断时不序列化样本包，
16 MiB的包字节预算只用于显式保存，不改变合格大样本的诊断结果。

固定 `production_approved=false`、`historical_pit_status=unverified`、
retrospective provenance。外部预期摘要提供本机完整性检查，不是供应商签章、
历史PIT证明或生产准入。`--now`仅作离线重放；实际使用默认当前UTC。
本机存储许可不等于展示、数据导出、AI或公开分享许可，使用方仍需分别核验。

### 独立核验已保存的标准样本

无需原始采集归档或五项映射配置：

```powershell
uv run --no-sync python scripts/verify_normalized_sample.py --sample "<私有标准样本包>" --sha256 "<独立保存的64位小写SHA256>" --grant "<当前私有授权文件>" --output "<新私有报告文件>"
```

模块入口为 `dragon_radar.ingestion.sample_archive_cli`，前三项必需，
output可选；stdout输出安全JSON。该命令使用实际当前UTC，不提供`--now`。
grant最多4 MiB，样本最多16 MiB；两者须为私有普通文件，
拒绝链接、重复JSON键、非法模型、摘要或canonical不一致。
当前完整grant必须与样本中保存的内容相同。

质量检查只执行一次；报告保留coverage、issues、kind/test_only、
input_sha256、sample_archive_sha256及lineage安全摘要，固定retrospective、
production_approved=false、historical_pit_status=unverified。
不输出完整样本、证券、价格、授权reference、凭据或输入路径。
退出0表示当前工程核验pass，2表示存储授权拒绝或质量缺项blocked，
1表示结构/完整性/权限/冲突fail；保留原质量问题码。

output不得等于任一输入，且在读取输入前独占预留。
失败诊断可以保存，文件JSON与stdout一致；写入/flush/fsync失败会清理预留，
stdout返回fail。成功和失败诊断均在最终flush/fsync后重新授权，
仍在安全回滚作用域内；期间授权失效会清理报告并返回blocked。
既有文件永不覆盖。输入不会修改，也不构造传输、读取令牌或连接数据库。

共享 `verify_sample_archive` 返回安全report，仅pass时返回archive；
`read_sample_archive`的参数不变，非pass继续抛错而不返回私有包。
本机完整性与质量核验不改变原始接收事实，也不替代生产签章或历史PIT证明。

## 4. 本地验证

```powershell
uv run --no-sync pytest tests/test_supplier_collection.py tests/test_supplier_collection_cli.py tests/test_supplier_archive_sample_cli.py tests/test_supplier_sample_archive.py tests/test_supplier_sample_archive_cli.py tests/test_supplier_sample.py -q -W error
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync python -m compileall -q src scripts tests
```

测试全部使用离线注入传输和 synthetic 数据，不是实际供应商字段模板。
