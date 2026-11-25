# AI1 公开复核日报设计

日期：2026-11-24。状态：用户已统一批准，工程实施中；未验收。
目标：补齐 REQ-A04 的完整日报展开，同时保持私人任务与公开归档隔离。
源码调查基线：`d90a02e98abfd517a462c077a9f08bcbed32a347`。

## 方案选择

推荐独立、不可变的公开日报 sidecar，绑定已有精确 PUB。
行情发布无需等待 AI；后来公开的日报具有自身 published_at、version 和 sequence。
另一方案为第六统一发布域并生成新 PUB；它要求改动五域输入与水位协议，
并区分证据 P1 和承载 P2。当前需求只要求展开可追溯的日报，不采用此方案。

第三种仅展示私人 accepted 任务不满足公开归档：accepted 不代表验证通过，
任务可能包含笔记，也没有分享许可。因此不作为公开实现。
公共确定性模板继续作为无合格归档时的现有内容。

## 发布资格

必须同时满足：

- task_type=daily_review，task status=succeeded。
- 精确 selected revision 的服务端验证状态为 passed，task 摘要状态也为 passed。
- 接受审计 decision=accept、human_review_status=accepted、
  resulting_revision=selected revision，且审计验证状态为 passed。
- 当前草稿仍为该修订，内容规范 SHA256 与请求 expected_content_hash 相等。
- provider 来源，或有完整 provider 祖先链的 human_edit。
  单纯 deterministic_template 不登记为经模型复核的公开日报。
- 该修订通过与原任务证据绑定的独立验证。这里的独立是重新执行验证器，
  并非复用客户端布尔值，也不把人工接受当作验证通过。
- 原始 input.notes_context 必须精确为空串；曾经包含私人笔记的任务不能通过删输出发布。
- 明确发布同意 reference；有效来源 storage/display/AI/sharing 许可，
  以及独立模型输出 storage/display/sharing entitlement。

现有 accept 审计 draft_id 为 null，可通过 task_id+resulting_revision 唯一查出草稿；
不迁移旧审计或修改其不可变字段。缺失、歧义和错配均拒绝。
人工编辑后必须重新验证并接受新修订；迟到 provider 结果不能覆盖已归档内容。
资格失败返回结构化原因，但公共读者不得获知私人任务状态。

## 输出权与内容

新增本机 operator 登记的、版本化 `AIOutputEntitlement`：
reference、provider、model_version、starts_at/expires_at、
storage/display/sharing、audience、policy_reference。
它是外部许可文件的工程登记，不自动证明法律许可。
初版公开 audience 精确为 public；其他 audience 不自动升级为 public。
当前授权和嵌入发布授权必须均有效；缺失输出权默认拒绝。
登记由本机 CLI 完成，不提供公共或 Web 管理后台。

公开 payload 只含 facts、inferences、missing_evidence、citations 及非个人复核证明引用。
每个事实/推断的引用必须绑定该 PUB 的许可材料、明确 locator 和原文片段。
引用只能使用公开允许的 PUB/证券/消息定位，不能指向 /api/ai 或 /api/owner。
正文大小沿用现有 canonical_object 上限 32768 字节。

明确排除 task_id、owner_id、notes_context、完整私人 manifest、
个人自选/仓位/笔记、provider credentials、费用、token usage 和私人资源地址。
公开序列化采用允许字段投影；未知字段拒绝，不能把整个 draft JSON 原样透传。
姓名不公开，审计只显示 operator 登记的非个人 attestation_reference。
同一归档保存输入证据摘要、输出摘要、验证版本、模型/提示版本，
供授权审计使用；私人 link 表与公共 payload 分离。

## 数据和并发

新增 public_daily_ai_archives，字段包括 archive_id、base_publication_id、trade_date、
archive_version、publication_sequence、supersedes、published_at、content_hash、
schema/schema_version、safe_payload、rights_manifest、attestation_reference。
不可变私人 link 另表保存 owner/task/revision/review/验证审计。
新增 append-only revoke ledger；原归档不改写为另一内容。

以同一 PUB 的版本锁与源/输出权锁进行事务发布，
task/revision/review 校验及源许可重查在提交前完成。
expected_archive_version=0 表示无旧版本；不匹配为409。
唯一约束 PUB+archive_version、owner+idempotency_key；
同 key 同请求只返回原收据，同 key 异请求409。
幂等重放仍重查当前权限，不能借旧成功收据绕过撤权。
来源依赖写入现有 source_dependencies，使用新类型标签
archive_type=public_daily_ai、derived_type=daily_ai、derived_id=archive_id，
并绑定基础 publication_id，取得既有统一 dependency_sequence。
daily_ai 是保留分派类型，不加入 DOMAIN_NAMES 或五域 publication_results。
RetentionService 必须显式分派该类型至 sidecar handler：
停用/删除/去标识 sidecar，不能把它当作 publication_results 的第六域。
补齐反向依赖索引，沿用既有 cutoff 与 withdrawal_dependencies 绑定，
同一事务在完成 sidecar 绑定后才生成 binding seal。

迁移追加 `0015_daily_ai`，不改旧迁移字节、不更改项目版本。
同步 release_manifest、schema_contract、verify_release_migrations、
release/package 测试及数据库支持 head。失败在数据库动作前关闭；
回滚沿用 backup_restore_only，不能执行降级破坏已有归档。

## 私人发布命令

POST `/api/ai/task/{task_id}/publish-daily`。
body 精确为 revision、expected_review_id、expected_content_hash、snapshot_id、date、
expected_archive_version、idempotency_key、publication_consent_reference、
output_entitlement_reference、attestation_reference。
主体只能来自有效 owner session，检查 CSRF 和当前任务 owner。
不增加自动接受、自动发布或新的匿名发布入口。

POST `/api/ai/task/{task_id}/published-daily/{archive_id}/revoke`。
body 为 expected_archive_version、idempotency_key、reason_code；
必须验证 owner、task 私人 link 和 CSRF。reason_code 取 withdrawn_by_owner、
rights_invalidated、verification_invalidated，公开只显示不可用。
发布与撤回分别保存审计，不修改原任务、草稿或行情 PUB。

## 公开读取和发现

GET `/api/market/daily-ai`：必填 date、snapshot_id，可选 archive_id。
不指定 archive_id 选择当前有权读取的最高版本；明确 ID 只能读该版本，不回退。
GET `/api/market/daily-ai/archives`：必填 date、snapshot_id，
可选 limit=1..100（默认50）、cursor。
排序 archive_version 降序、archive_id 升序。
游标绑定 PUB、date、首屏 publication_sequence 水位与上页最后排序键。
后续发布不插入本次翻页；撤权内容在分页前过滤。
仅发现当前 PUB 的已公开归档，不扩展全局搜索，不扫描私人 task。

响应沿用 daily PUB envelope，market as_of 不变；
另含 base_published_at、archive_published_at、archive_version 与安全 payload。
未知显式 PUB/archive_id 为404，日期/身份错配或重复/未知参数为400。
有合法 PUB、无合格归档为200/null + reviewed_daily_ai_unavailable；
可访问但无条目的发现为200/items=[]/total=0。
被明确撤回或许可拒绝为403，不返回正文；坏归档为503 DATA_UNAVAILABLE。
public_app 装配只依赖公共归档 repository，不能注入 AITaskRepository。
网关只开放两个 GET；/api/ai 和 /api/owner 仍不进入公共构建或公开入口。

## 撤权和保留

归档撤回、基础 PUB 撤回、来源 display/sharing/AI 到期、
输出 entitlement 到期/撤回均立即阻止读取、发现及缓存重用。
每次读取重查当前授权，进程缓存只能保存按所有依赖索引的不可变数据，
不能缓存“有权”结论。UI 403 后清空正文和引用，迟到响应不得恢复。
重新批准来源不自动恢复公开日报，需明确新的许可审计与重新发布。

storage 撤回需在撤权序号 cutoff 上绑定所有 sidecar/引用依赖，
同时给 binding seal 留下可核验证据。
没有合法删除政策时只阻断使用；经批准的 deidentify/delete 政策处理 payload，
保留最小 tombstone、摘要和必要审计。恢复不得复活被删除正文。
这里的法定删除是已有保留政策的例外，不是普通内容修订。

## UI 与包体

Overview 完整 AI 区域增加已公开版本列表及展开正文。
展示精确 PUB、归档版本、两种发布时间、验证/复核证明和缺失证据。
公共页面不出现生成或发布按钮。私人 owner AI 面板提供明确发布/撤回操作，
仅当本机 API 给出资格通过与许可有效时可提交；最终资格仍由服务端核验。
引用沿用现有定位/焦点返回；PUB 切换、撤权和请求失败清空旧归档。
不从私人任务 API 拼接公共界面。

私人的发布 UI 与 owner client 放入私人动态模块；
只读、公共和默认包必须物理排除私人 import、路径和 task 字段。
五构建与网关权限测试必须包含新 GET 和两条 owner 写路径。

## 文件职责与验收

新增 public_daily_ai/{models,tables,repository,service,entitlements}.py、
http/routes/daily_ai.py、迁移0015_daily_ai、web/features/public-daily-ai。
协调 ai/routes 与 owner security、publication retention/access、
public_app、两套 Caddy、schema/release/package 工具。

RED/GREEN 覆盖全部资格条件单项否决、精确修订审计/hash、
编辑重校验、私人输入拒绝、安全引用、权限/CSRF/并发/幂等、
P1/旧归档不变、独立 watermarked discovery、PUBLIC 不读 tasks、
来源/PUB/归档/输出权撤权及热缓存、保留 cutoff/seal/tombstone、
五包隔离、页面版本切换与迟到响应。
真实 provider、≥100 真实人工评估及真实分享许可缺失时保持生产不可启用。
工程合成用例不能替代真实许可或模型质量。
