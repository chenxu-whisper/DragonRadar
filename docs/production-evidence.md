# 生产证明接入契约

本机工具验证五项真实外部证明，开发验收与生产准入分别输出。
它不授权部署、发布、提交、合并或公开分享，也不授予正式 CI 的执行可信度。
测试中的密钥、供应商与人工样本只用于回归，不是生产资产。

## 运行入口

```text
uv run python scripts/verify_production.py \
  --workspace <用户根工作区> --output <已存在目录中的报告文件>
```

默认读取用户根工作区的 `data/suppliers.json` 与 `docs/proofs/`。
未显式指定外部信任策略时发行方集合为空；不会读取证明目录内的策略自行建立信任。
使用真实证明须另传 `--trust-policy <受保护外部策略文件>`、
`--trust-policy-sha256 <经外部批准的文件摘要>`、
`--openssl <配置好的绝对可执行文件路径>`。
可以通过 `--proofs`、`--suppliers`、`--candidate`、`--context` 覆盖材料入口。

报告退出码：0 为五项外部门禁均通过；1 为提供的配置或证据无效；
2 为证据或工具缺失。即使退出 0，`production_release` 仍 blocked，
还需当前可信自动化验收、其他发布门禁及独立发布授权。

`verify_reset.py --production-evidence` 薄接入同一验证流程。
`audit_reset_release.py --production-evidence-output <文件>` 生成独立外部报告；
历史发布审计 schema 的 BLOCKED/NO_GO 不接受调用者自行写入 VERIFIED。
`--validate-existing` 始终保持 INTEGRITY_ONLY。

## 证明与信任策略

所有 JSON 拒绝重复键、未知字段、NaN/Infinity 和越界结构。
签名记录必须是 UTF-8、无 BOM、无尾换行，按
`json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)`
编码。不是 RFC 8785。固定 RSA-PSS、SHA256、MGF1/SHA256、32-byte salt，
签名整个记录，包括 detached signature 的相对路径引用。

`dragon-radar-production-evidence/v1` 的记录字段：

```text
schema, gate_id, evidence_id, kind, issuer, key_id, approval_id, approver,
issued_at, approved_at, valid_from, expires_at, revoked, candidate,
scopes, materials, payload, signature
```

`kind=real`、`revoked=false`；所有时间为 UTC `...Z`，有效期半开。
签名者、批准身份、门禁和 scope 均须属于外部策略授权；旧签名批准可通过
策略撤销，不因记录内旧的 `revoked=false` 保持有效。
签名证明身份与完整性；门禁还独立核验真实材料的语义与适用性。

每个 material 为 `material_id/path/sha256`，每个 signature 为
`algorithm=RSA-PSS-SHA256/path`。材料、公钥及签名必须位于同一明确授权的
受保护本地资产根；拒绝绝对路径、越界、symlink/junction、硬链接与不安全权限。
公钥摘要是精确 PEM 文件字节的 SHA256。上限：记录 1MiB、单材料 16MiB、
材料总量 64MiB、512 个引用、公钥 16KiB、签名 1KiB、默认验证超时 5 秒。

外部策略 schema 为 `dragon-radar-production-trust/v1`，含
`issuers/revoked_approval_ids/revoked_evidence_ids`。
每个 issuer 含 `keys/gates/scopes/approvers/revoked`；
key 含 `public_key_path/sha256/revoked`。
scope 四项为 `subject/purpose/target/audience`，取明确非空字符串集合，无 wildcard。
策略由获授权人员从外部渠道确认；工具不生成、签署或自动安装生产身份。

## 候选与材料索引

`candidate.json`：

```text
versions, methods, regulation_rules, rule_scope, official_source_hosts
```

versions 恰为 `code/dataset/rules/model/prompt/retrieval`。
`code=source-sha256:<摘要>`；`source_identity` 绑定当前源码、测试、前端、工具链及
根目录 `compose.yaml`、`Dockerfile` 及 `containers/` 中的构建和网关配置，
包括未提交变化。忽略构建缓存与生成报告，不能用旧 commit 摘要证明新源码。
其他版本必须与实际候选配置一致。methods 为批准的方法映射；
regulation_rules 为排序后的 `rule_id:rule_version` 集合；
rule_scope 为 `exchange/board/security_status/trade_date`；
official_source_hosts 是由操作人员确认的官方来源主机集合。
语义 metadata 不允许覆盖已签名的六个版本。
当前验证器及生产 worker 必须运行于可绑定已加载代码的 `src/` 工作树布局。
只有安装后的 wheel 且无法核对完整源码和构建输入时拒绝验证，不接受空目录摘要。

`context.json` 恰为 `subject/purpose/target/audience/content/data`；
content/data 是实际拟发布内容与数据集合，不是“有许可”声明。
`index.json` 为 `schema=dragon-radar-production-index/v1/evidence`，
evidence 把五个 canonical gate ID 映射到相对签名记录路径。

## 五项 payload

- `real_input`：`bundle/run/supplier`，分别指向冻结生产输入、实际运行、
  用户配置供应商的 material ID。须通过真实接收、PIT、方法/快照/版本/授权核验，
  并以既有内核重新核对已归档结果。供应商配置必须与用户指定文件逐字节摘要一致。
- `official_rules`：`rules/lifecycle/official_archives/methods`。
  rules 材料为 `{"rules":[RegulationRule...]}`；lifecycle 为
  `{"records":[rule_id/rule_version/status/recorded_at...]}`。
  每个 official_archives 映射到归档证明，其 schema 为
  `dragon-radar-official-rule-archive/v1`，字段为
  `rule_id/rule_version/url/content_material/content_sha256/rule_sha256/scope`。
  独立签章绑定官方主机、原文摘要、规则定义及候选适用范围，并实际重跑冻结样例。
  最新追加生命周期须 active；泛化批准或任意 HTTPS 文件不能替代。
- `real_model_evaluation`：`manifest/model_identity/human_attestations`。
  复用 `dragon-radar-ai-eval/v2`；人工证明映射到真实 material，schema 为
  `dragon-radar-human-attestation/v1`，含 sample_id/sample_sha256，
  以及该样本完整 label_provenance 字段。证明须与样本、身份、时间和标签匹配，
  原始输入输出须在已认证材料中按 lineage SHA256 找到。
  100 个唯一人工样本、两个非空且不泄漏 split、各自事实支持率≥95%、
  数值/复算100%、安全硬失败0全部满足；外部发行方必须确证人工来源，
  助手不充当人工标注者或批准者。
- `public_sharing_permit`：`subject/purpose/target/audience/content/data/
  redistribution_allowed/restrictions`。主体与目标等四项精确匹配，
  实际 content/data 必须处于许可集合，redistribution_allowed=true。
  未批准适配的条件限制保持拒绝；内部研究授权不替代公开再分发许可。
- `shadow_run_20_trading_days`：`calendar/entries/calendar_evidence/daily_evidence`。
  除摘要链外，还逐日验证独立签名及真实 bundle/run/supplier，核对接收时间、
  可用时间、快照、运行时间与审核时间，不能靠新签一个 20 行声明解锁。

影子 calendar 材料为 `version/sessions`，session 为 `trade_date/close_at`；
entries 材料为 `{"entries":[日记录...]}`。calendar_evidence 为独立
`official_rules` 签章记录，payload 是
`type=trading_calendar/calendar/exchange/window_start/window_end/complete=true`，
在首日之前批准，覆盖完整适用交易窗口。daily_evidence 按日期映射原始逐日记录，
其门禁 ID 为 shadow，payload 是 `type=shadow_day/entry/bundle/run/supplier`。
签章发行及批准时间必须是实际交易日；审核身份与日记录一致。
逐日实跑的交易所、日历版本及适用窗口内的全部交易日必须与批准日历一致。
同时满足至少20个连续完成的官方交易日、同一语义版本、P95≤2秒、
GET零写、质量失败0、真实当日接收/执行及独立复核。

## 本机采集

```text
uv run python scripts/collect_shadow.py --record <受保护原始日记录> --store <本地目录>
uv run python scripts/collect_shadow.py --history <受保护原始历史集> --store <本地目录>
```

追加文件独占创建；同日重复幂等、冲突拒绝、摘要链保持。
失败日期不可覆盖成成功。输出 `UNVERIFIED_COLLECTION`，不授予 pass。
历史集必须来自实际当日记录，独立签章与底层实跑材料在生产准入时另外核验。
未批准的修订、日历缺口、语义版本变化、迟到数据及回填日志均不能凑满20日。
