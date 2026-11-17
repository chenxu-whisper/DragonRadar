# 供应商采集、数据映射与样本验收设计

日期：2026-11-16。用户已批准三项开发范围并要求开始实施。
代码工作树：`.worktrees/task27-research`；沿用其中上一阶段未提交的生产输入契约。
本轮不提交、推送、迁移数据库或发布，不安装厂商 SDK，不创建生产许可。

## 目标与边界

实现受授权约束的 HTTP 采集器、可配置字段映射和离线样本验收 CLI。
已有 MarketBatch、SourceGrant、质量检查、不可变输入归档及研究 worker 保持原契约。
真实厂商字段、账号额度和历史覆盖等待书面确认；配置不得假定厂商已经交付。
验收报告是工程诊断，不能通过五项生产门禁，也不发布或导入数据库。

比较过直接绑定 SDK、HTTP 采集和仅开发离线工具三种路径。
采用独立 HTTP 传输与配置驱动映射：无需新增运行依赖，便于离线验证；
SDK 后续可通过同一传输边界接入。本轮实现实际 HTTP 请求和失败恢复，
但不会使用未授权账号访问厂商服务。

## 组件

- `ingestion/supplier_transport.py`：有界 HTTPS JSON POST、只允许明确指定的
  iFinD 官方接口主机、拒绝重定向与凭据 URL；凭据从环境读取，不写进记录。
  网络错误仅输出固定错误码，不泄漏请求、响应或异常中的凭据。
- `ingestion/supplier_collection.py`：按显式证券清单及交易日历分批；
  有界重试、速率限制、原始响应摘要、实际接收时间及断点恢复。
  每次网络请求和持久化前检查 SourceGrant 存储权利及有效期。
  归档采用独占写入、私有文件权限及内容摘要；恢复时核验请求身份和归档完整性。
- `ingestion/supplier_mapping.py`：字段到 canonical 模型字段的显式映射；
  Decimal 精确保留，状态仅通过声明的枚举转换，单位只用显式倍率。
  不通过代码前缀猜交易所、股票名称猜 ST、周末猜日历或默认补复权因子。
- `ingestion/sample_acceptance.py`：复用 `validate_batch`，叠加首个候选
  SSE/main/common_stock 范围、历史状态、完整 OHLC/成交量金额/限价和预热覆盖检查。
- `ingestion/sample_cli.py` 与 `scripts/verify_supplier_sample.py`：
  独立离线入口，不加载数据库，不打印原始行情或输入值。

## 映射契约

MappingProfile 使用严格不可变 Pydantic 模型：
`schema="dragon-radar-supplier-mapping/v1"`，`profile_id`，`source_id`，
`source_version`，`bar_fields`，`state_fields`，`bar_constants`，
`state_constants`，`value_maps`，`decimal_scales`。
字段映射为 canonical 字段名到供应商行字段名；常量须显式填写。
两者不得覆盖同一个目标，未知目标拒绝。value_maps 为目标字段到输入字符串/
canonical 值的映射，未知值拒绝或保留为 null，不能推定为 false。
decimal_scales 仅适用于 Decimal 字段，必须是正的有限精确数。
稳定 security_id 必须来自明确映射或常量，不能由当前显示代码自动生成。
输入行中的未使用字段允许存在；不会进入 normalized payload 或报告。

`normalize_records(profile, *, bar_rows, state_rows)` 返回
`NormalizationResult(bars, states, issues)`。issues 只包含类别、行号、
目标字段、错误码，不包含原始值。异常行保留 issue，不默默删除并宣称完成。
模型结构错误、非有限值、未知枚举、缺字段及不精确浮点必须可定位。

## 离线样本契约与结果

SampleRequest 严格字段：
`schema="dragon-radar-supplier-sample/v1"`、`kind` (`test`/`supplier_sample`)、
`batch` (MarketBatch)、`benchmark_closes` (ISO 日期到精确 Decimal 字符串)、
`decision_sessions` (非空有序且唯一日期)、`window` (首版固定20)。
可另用 CLI 的 `--mapping`、`--bar-rows`、`--state-rows` 将供应商行转换后
替换 request.batch 对应记录再验收；映射 source 身份必须匹配 batch。
没有映射参数时直接验收 canonical 样本。

报告仅包含 schema、工程状态、input/profile 摘要、kind、逐项缺项、
覆盖计数、质量错误码和 `production_approved=false`。
不输出原始证券价格、供应商条款、文件内容、凭据或带值的 Pydantic 异常。
kind=test 必须明确 `test_only=true`；任何 kind 都不产生签章或生产记录。
历史当前获取的样本可以检查覆盖，但历史 PIT 可用性仍以原始接收证明核验；
报告不能把 retrospective 样本标为历史 PIT 已通过。

缺证/缺数据退出2；坏结构/冲突/非法文件退出1；工程样本合格退出0。
0仅表示样本工程检查通过，绝不表示真实输入或整体生产准入。
报告写入要拒绝覆盖及输入/输出路径碰撞。输入限大小、拒绝重复 JSON 键，
使用精确数解析；标准输出仅安全 JSON 摘要。

## 验收

TDD 验证成功映射、缺字段、未知状态、精度/单位、重复与版本冲突、
范围、预热21会话、基准缺项及报告不泄漏。
采集通过可注入离线传输测试分批、超时/429/5xx、有界重试、
认证失败不重试、授权到期、断点恢复及归档篡改。
本机核心相关回归、Ruff/format、compileall 均须通过，新增代码变更使既有
code 摘要成为历史身份；重新计算当前摘要并同步候选模板，不重写旧验证事实。
