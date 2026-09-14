# 私人复盘 API 契约

本文对应任务四的实际 `review/routes.py`、`review/repository.py`、`review/models.py`、
`review/tables.py` 和 `owner/security.py`，供前端实现，不是拟定接口。
示例全部为虚构数据；UUID 和时间只说明格式，不代表运行实例中的记录。

## 1. 入口与会话

- 唯一私人生产入口为 `create_owner_app(...)`；它安装全部私人路由。
  原 `create_app(...)` 仍只读，不安装配对、会话或复盘路由。
- 默认入口 `https://localhost:8030`，也可显式配置另一个精确的 localhost HTTPS 端口。
  Host、Origin、服务端会话中的 Origin 必须匹配；不信任 Forwarded 头替代身份。
- 下文业务路径统一加前缀 `/api/owner/v1`。成功状态均为 **200**，不是 201/204。
  JSON 响应统一 `Cache-Control: no-store`，不提供跨域许可。
- 每次读写都要求有效的 `__Host-dragon-private-session` Cookie。
  写请求还必须有精确 `Origin`、`Content-Type: application/json`、
  `__Host-dragon-private-csrf` Cookie 与相同的 `X-CSRF-Token`。
  token/CSRF 由 OwnerWrite 传入仓储，在业务事务内重新鉴权，不只依赖 HTTP 门禁。
- body 上限 131072 字节，先累计流式读取再解析；只接受 JSON 对象。
  重复 JSON 字段、NaN、非法编码、额外字段均拒绝。不要用字符串代替布尔值或整数。
- 业务写入要求 `Idempotency-Key`，见第 7 节。所有业务写路径禁止查询参数；
  详情读取也禁止查询参数，仅四种列表允许第 6 节列出的参数。
- 客户端不能声明 `owner_id`。响应中的 `owner_id` 仅用于绑定和隔离内存状态，
  不可用于切换身份；无效会话必须锁定并隐藏私人数据。

已有会话入口：

| 方法与路径 | 请求 | 响应 |
| --- | --- | --- |
| `POST /pair` | `{"code":"一次性码"}`；另需 `X-Dragon-Owner-Pair: 1`，不需要已有会话、CSRF 或幂等键 | `owner_id/session_id/expires_at`，并设置两个 Cookie |
| `GET /session` | 无 body、无查询 | `owner_id/session_id/expires_at` |
| `DELETE /session` | `{}`，需会话、Origin、CSRF，不需幂等键 | `{"status":"logged_out"}`，撤销当前会话并清 Cookie |

会话 Cookie 为 Secure、HttpOnly、SameSite=Strict、Path=/；CSRF Cookie 不设 HttpOnly，
其他属性相同，均无 Domain。会话绝对期限 8 小时。配对码、会话 token 不在 JSON 响应中。

## 2. 请求签名

以下 UUID 字段使用 JSON 字符串；路径 `version` 是整数，body 中 `version` 必须是
JSON 整数，不能是 `true`、`1.0` 或 `"1"`，范围 1～2147483647。
所有 body 禁止未知字段。标为可选的字段可省略，默认值如下表；`null` 仅用于明确可空字段。

| 方法与路径 | body / 参数 | 返回 |
| --- | --- | --- |
| `GET /notes` | `page/page_size/state` | 分页笔记 |
| `POST /notes` | `snapshot_id: UUID, content: NoteContent, security_id?: string|null = null` | 笔记 |
| `GET /notes/{note_id}` | 无查询 | 当前笔记 |
| `PATCH /notes/{note_id}` | `content: NoteContent`；需 `If-Match` | 新修订的笔记 |
| `POST /notes/{note_id}/confirm` | 必须 `{}`；需 `If-Match` | 新的 confirmed 修订 |
| `POST /notes/{note_id}/fork` | `version: int, content?: NoteContent|null = null, snapshot_id?: UUID|null = null` | 新独立草稿 |
| `GET /notes/{note_id}/revisions` | `page/page_size` | 分页原始修订行 |
| `GET /notes/{note_id}/revisions/{version}` | 无查询 | 指定修订的完整笔记 |
| `GET /watchlist` | `page/page_size` | 分页自选，包含已移除条目 |
| `POST /watchlist` | `snapshot_id: UUID, security_id: string` | 自选，含 committed |
| `PATCH /watchlist/{record_id}` | `active: boolean`；需 `If-Match` | 自选，含 committed |
| `POST /review-templates` | `snapshot_id: UUID` | kind=daily 的新草稿 |
| `GET /notes/{note_id}/verifications` | `page/page_size` | 分页验证记录 |
| `POST /notes/{note_id}/verifications` | `version: int, outcome: enum, evidence?: string = "", snapshot_id?: UUID|null = null, supersedes?: UUID|null = null` | 验证记录 |

`security_id` 为 1～200 个 `[A-Za-z0-9_.:-]` 字符，必须属于指定快照的预期证券池。
当前没有删除笔记、删除验证、GET 生成模板、自动验证或自动命中率接口。

### NoteContent

| 字段 | 类型和约束 |
| --- | --- |
| `title` | 必填字符串；1～100 个 Unicode 码点，不能全为空白 |
| `judgment` | 字符串，默认 `""` |
| `evidence` | 字符串，默认 `""` |
| `invalidation` | 字符串，默认 `""` |
| `verification_date` | `YYYY-MM-DD` 或 null，默认 null；不接受时间戳、数字或日期时间 |

三正文合计最多 20000 个 Unicode 码点；所有文字拒绝 NUL 和不可编码的 Unicode。
长度不是 UTF-16 code unit 或字节数。标题/正文不自动裁剪空白。
PATCH 的 content 是**完整替换**，省略的可选正文会恢复为默认空串，不是局部合并。
草稿可缺判断、依据、失效条件、验证日期；确认时三正文均须非空白，
验证日期不得早于笔记保存的快照交易日。

### 模板与另存

模板版本固定 `private-review-v1`。标题为 `YYYY-MM-DD 复盘`，判断为
`"市场观察\n\n待验证事项\n"`，依据和失效条件为空串，验证日期为 null。
无 AI、无行情原文/价格拷贝、无自动结论。每个**新幂等键**显式 POST 新建一份草稿；
同键同请求是重试，不会再次生成。

确认会追加修订并冻结整条笔记。不能再 PATCH，也不能用新键再次确认。
fork 的 `version` 选择源修订，不是对当前头版本的乐观锁；不要求 If-Match。
fork 默认复制该修订的内容、证券、类型、模板版本和原快照引用，记录
`derived_from_revision_id`，新笔记从 draft/version=1 开始。可用 content 替换文字。
不传 snapshot_id 或传 null 都继承旧引用，即使旧来源已失权也允许；
显式传 UUID（即使等于原快照 UUID）属于新引用，必须重新通过来源和证券池授权。
不能通过 PATCH 移动原笔记的快照。

## 3. 笔记与事实响应

所有笔记响应都包含以下字段：

| 字段 | 含义 |
| --- | --- |
| `id / owner_id` | 笔记 UUID / 当前主体 UUID |
| `snapshot_id / trade_date` | 原快照 UUID / 创建时保存的交易日 |
| `security_id` | 稳定证券 ID 或 null |
| `kind` | `note` 或 `daily` |
| `version / state / revision_id` | 本次返回修订的整数版本、draft/confirmed、修订 UUID |
| `derived_from_revision_id` | fork 的来源修订 UUID，否则 null |
| `template_version` | daily 模板版本，否则 null |
| `created_at / updated_at` | 笔记头的创建/最近修改时间 |
| `saved_at` | **本次返回修订**的保存时间 |
| `content` | 完整 NoteContent |
| `facts` | 每次按当前权限解析的事实状态，见下文 |

`created_at/updated_at/saved_at` 等仓储时间为带 UTC 偏移的字符串，
当前序列化形如 `"2026-09-11 12:00:00+00:00"`，中间是空格；
事实的 `as_of/source_time` 和会话 `expires_at` 使用带 `T` 的 ISO 日期时间。
前端应明确解析带偏移时间，以北京时间显示，不依赖浏览器对空格格式的隐式解析。
历史详情及重放中的 updated_at 仍是当前笔记头时间，不是该历史修订时间；
显示历史保存时间应取 saved_at。

创建请求示例：

```http
POST /api/owner/v1/notes
Origin: https://localhost:8030
Content-Type: application/json
Idempotency-Key: note-command-1
X-CSRF-Token: <从CSRF Cookie读取>
Cookie: <浏览器自动附带>

{
  "snapshot_id": "00000000-0000-4000-8000-000000000001",
  "content": {
    "title": "观察记录",
    "judgment": "待验证的人工判断",
    "evidence": "人工依据",
    "invalidation": "人工失效条件",
    "verification_date": "2026-09-11"
  }
}
```

同一笔记在来源失权后 GET 的完整示例：

```json
{
  "id": "00000000-0000-4000-8000-000000000002",
  "owner_id": "00000000-0000-4000-8000-000000000003",
  "snapshot_id": "00000000-0000-4000-8000-000000000001",
  "trade_date": "2026-09-08",
  "security_id": null,
  "kind": "note",
  "version": 1,
  "state": "draft",
  "derived_from_revision_id": null,
  "template_version": null,
  "created_at": "2026-09-11 12:00:00+00:00",
  "updated_at": "2026-09-11 12:00:00+00:00",
  "revision_id": "00000000-0000-4000-8000-000000000004",
  "saved_at": "2026-09-11 12:00:00+00:00",
  "content": {
    "title": "观察记录",
    "judgment": "待验证的人工判断",
    "evidence": "人工依据",
    "invalidation": "人工失效条件",
    "verification_date": "2026-09-11"
  },
  "facts": {"status": "unavailable"}
}
```

来源当前可读时，facts 的完整形状示例（无证券上下文）：

```json
{
  "status": "available",
  "snapshot_id": "00000000-0000-4000-8000-000000000001",
  "trade_date": "2026-09-08",
  "as_of": "2026-09-09T12:00:00+00:00",
  "source_time": null,
  "provenance": "contemporaneous",
  "manifest": {
    "domain_version": "market-batch-v1",
    "rule_version": "analytics-v1-decimal34-half-even",
    "source_version": "s1",
    "adjustment_version": "a1",
    "calendar_version": "c1",
    "pool_version": "p1",
    "payload_hash": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "result": null
}
```

`payload_hash` 示例仅表示 64 位十六进制摘要格式，不是上述示例正文的计算值。
provenance 为 `contemporaneous` 或 `retrospective`，后者应展示事后研究标记。
有 security_id 时 result 是证券行，**不是**市场接口的完整 envelope、历史 K 线或质量报告：

```json
{
  "security_id": "A",
  "name": "虚构证券",
  "exchange": "TEST",
  "board": "synthetic-board",
  "close": "10",
  "change": "0",
  "amount": "1000",
  "suspended": false,
  "missing_reasons": [],
  "change_unit": "fraction",
  "currency": "CNY",
  "price_unit": "currency/share",
  "volume_unit": "share",
  "amount_unit": "currency"
}
```

close/change/amount 是十进制字符串或 null；change 是比例，不是百分数。
name/exchange/board、suspended、四个单位字段均可能为 null；
missing_reasons 为字符串数组，不能把空值补零。
失权或引用无法解析时，facts **只有** `{"status":"unavailable"}`，没有 result、
缓存名称、价格或失权原因。笔记顶层仍保留原创文本、原 snapshot_id、trade_date、
security_id；这不代表原行情事实仍获授权。编辑既有草稿和自选状态不依赖来源仍有效。

### 修订列表不是笔记列表

`GET /notes/{id}/revisions` 的 items 每项只有：
`id, owner_id, note_id, version, state, content, saved_at`。
其中 id 是**修订 UUID**，不是笔记 UUID；不含 facts 或 snapshot_id。
需要事实时请求 `GET /notes/{id}/revisions/{version}`，它返回上面的完整笔记形状，
其中 id 才是笔记 UUID，revision_id 为所选修订。原文不会随后续保存被覆盖。

## 4. 自选响应与 committed

自选成功写响应示例：

```json
{
  "id": "00000000-0000-4000-8000-000000000005",
  "owner_id": "00000000-0000-4000-8000-000000000003",
  "security_id": "A",
  "snapshot_id": "00000000-0000-4000-8000-000000000001",
  "active": true,
  "version": 3,
  "added_at": "2026-09-11 12:00:00+00:00",
  "updated_at": "2026-09-11 12:02:00+00:00",
  "facts": {"status": "unavailable"},
  "committed": {
    "id": "00000000-0000-4000-8000-000000000005",
    "version": 2
  }
}
```

此例是移除命令（提交 version=2）在后来已经撤销移除（当前 version=3）之后的重放。
**committed 证明原命令提交到了哪个版本，不是当前状态**。顶层 active/version 是当前自选。
新操作的 If-Match 应用顶层 version，不能用旧 committed.version。
committed 仅在自选成功写响应中出现，自选列表项没有此字段。

同证券已处于 active 时新 POST 返回现有条目，不重置 added_at、快照或版本；
已移除时新 POST 返回 409/version_conflict，不隐式恢复。
明确恢复需要 PATCH `{"active":true}` 并使用当前版本。每次成功 PATCH 都增加版本，
即使目标 active 值与当前相同。状态修改和重放均按当前来源权限解析 facts。

## 5. 人工验证

outcome 精确取 `supported`（支持）、`weakened`（削弱）、`refuted`（否定）、
`not_due`（未到期）。无记录在界面显示“尚未验证”，不是第五种可提交 outcome。

- version 必须选中该笔记的已确认修订；不要求 If-Match。
- 到期判断采用服务端时钟的北京时间日期。未到期只允许 not_due，
  snapshot_id 必须省略或 null，不会生成虚假事实。evidence 可为空或人工文字。
- 到期后不能提交 not_due；其余三种结论必须有非空白 evidence 和显式可读 snapshot_id，
  快照交易日不得早于验证日期，若原笔记有证券 ID，验证快照也必须包含该证券。
- evidence 是严格字符串，最多 20000 个 Unicode 码点，同样禁止 NUL。
- 更正通过新的 POST、新的幂等键和 supersedes=原验证记录 UUID 追加；
  被引用记录必须属于同主体、同笔记、同确认修订，否则固定 404。
  旧记录仍在列表中；客户端按 supersedes 展示更正链，不自行删除历史。

完整验证响应示例：

```json
{
  "id": "00000000-0000-4000-8000-000000000006",
  "owner_id": "00000000-0000-4000-8000-000000000003",
  "note_id": "00000000-0000-4000-8000-000000000002",
  "revision_id": "00000000-0000-4000-8000-000000000004",
  "snapshot_id": "00000000-0000-4000-8000-000000000001",
  "outcome": "supported",
  "evidence": "人工填写的验证依据",
  "supersedes": null,
  "created_at": "2026-09-11 12:03:00+00:00"
}
```

验证响应/列表项**没有** version、facts 或 committed；关联的数字版本由请求指定，
响应保存 revision_id。snapshot_id 在 not_due 时为 null。
要并排展示验证事实，前端以记录的 snapshot_id 显式调用已有鉴权行情接口：
`GET /api/v1/snapshots/{snapshot_id}` 或
`GET /api/v1/snapshots/{snapshot_id}/securities/{security_id}`。
后者是已有行情 envelope，证券行为 result.security，质量字段在 envelope 中，
不是私人 facts.result 的形状。来源失权为 403/source_forbidden；清除旧事实，
保留人工验证文字与引用。重放只回读验证行，不内嵌历史行情缓存；
新建有事实依据的验证仍需当前来源授权，不自动改选其他日期。

## 6. 分页

四种列表使用 `page`（1～100000，默认 1）和 `page_size`（20/50/100，默认 50）。
URL 中显式 `?page_size=20`、`50`、`100` 都可用。只有笔记列表另支持
`state=draft` 或 `state=confirmed`，省略表示两者均返回。

```json
{"items":[],"page":1,"page_size":50,"has_more":false}
```

无 total、cursor、next_page。has_more=true 时可请求 page+1；越过末页返回空 items。
笔记/自选按 updated_at 降序，修订按 saved_at 降序，验证按 created_at 降序，
同时间再按 UUID 升序。是稳定排序的 offset 分页，不是跨请求快照隔离；
并发增改可能移动页边界，界面应刷新，不能承诺滚动加载永不重复。
未知或重复查询参数返回 400/invalid_query；非法值返回 422/invalid_request。
无效类型可在框架校验阶段先返回 422，不承诺多个错误同时存在时的优先级。

## 7. 版本、重试与冲突

Idempotency-Key 为 1～200 个 `[A-Za-z0-9_.:-]` 字符，建议每次用户确认的新命令生成 UUID。
作用域是整个主体，不是单一路径。缺失、空值、非法字符或过长均为 400/idempotency_required。
同键不同操作、目标、规范正文或预期版本返回 409/conflict；不要靠换键自动重试未知结果。
JSON 键顺序不影响摘要；省略可选字段与显式默认值经模型规范化后可能等价。

PATCH 笔记、自选状态和 POST 确认需要 `If-Match: "1"` 这种带双引号的十进制正整数，
缺失 428/version_required。弱 ETag、裸数字、0、负数、前导零、多个值或超过十位
为 400/invalid_version。符合头格式但超出仓储版本范围为 422；2147483647 不能继续递增。
服务端不提供 ETag 响应头，客户端用 JSON version 构造 If-Match。
重复安全头（含大小写不同的重复项）由 OwnerGate 在路由前拒绝为 403/origin_forbidden。

响应丢失、超时或 503 后，保存原来的 key、方法、路径、正文、If-Match。
会话恢复后按原命令重试；同键已提交会先回读结果，不先因旧版本而冲突，也不会重复写审计。
未提交的失败不留下业务、命令或审计，原键可重试。
重放仍重验会话、主体归属与当前事实许可，不是历史响应正文缓存。
笔记重放返回原提交修订而非当前头修订；自选的可变头与原提交另见 committed；
验证重放返回原验证行。模板和 fork 重试也返回原新建笔记，不再创建另一份。

版本冲突示意：

```json
{
  "code": "version_conflict",
  "message": "Resource version conflict",
  "current": {"id":"当前自有资源UUID","version":3}
}
```

此处 current 仅为节选，不是完整响应示例：
笔记冲突包含完整当前笔记（含当前授权 facts）；自选冲突包含
`id/owner_id/security_id/snapshot_id/active/version/added_at/updated_at`，
不包含 facts 或 committed。current 永远限定当前主体；跨主体目标不是冲突，而是固定 404。
前端保留原输入和基线，与 current 比较；手工合并后使用**新键和当前版本**，
或显式 fork。不得静默覆盖、自动合并正文。

## 8. 错误与隐私

除 version_conflict 带 current 外，错误只有固定 code/message，不返回验证细节或输入。

| HTTP | code | message |
| --- | --- | --- |
| 400 | `idempotency_required` | `Idempotency key required` |
| 400 | `invalid_version` | `Invalid expected version` |
| 400 | `invalid_query` | `Invalid query` |
| 401 | `session_unauthorized` | `Owner session required` |
| 401 | `invalid_pairing` | `Invalid pairing` |
| 403 | `origin_forbidden` | `Owner request forbidden` |
| 403 | `csrf_forbidden` | `Invalid CSRF token` |
| 403 | `source_forbidden` | `Source access denied` |
| 404 | `not_found` | `Not found` |
| 409 | `conflict` | `Conflict` |
| 409 | `version_conflict` | `Resource version conflict` |
| 413 | `body_too_large` | `Request body too large` |
| 422 | `invalid_request` | `Invalid request` |
| 428 | `version_required` | `Expected version required` |
| 429 | `pairing_rate_limited` | `Pairing rate limit exceeded` |
| 503 | `storage_unavailable` | `Storage unavailable` |

私人不存在、另一主体资源、不属于指定笔记的修订/更正引用统一 not_found。
行情路由仍保留 404/snapshot_not_found (`Snapshot not found`)，以及已有
422/analysis_unavailable、quality_rejected 契约；私人路由不会覆盖行情异常映射。
未知路径或不支持的方法固定 404，不自动重定向尾斜杠。
多个门禁/参数错误同时出现时不要依赖优先级；有效入口下的无会话是 401，
来源拒绝和 CSRF 拒绝是不同的 403，不能统一当作“行情撤权”。

“错误不回显正文”指拒绝请求的错误不复制提交内容、SQL、连接串、凭据或框架校验 input。
正常成功读取和 409/current 的**当前自有已保存文稿**是业务契约的显式例外，
不是原样回显本次失败输入。正文只是 JSON 字符串，不做 HTML 清洗也不执行；
前端必须使用纯文本控件/渲染，不使用 innerHTML 或可执行 Markdown。
不得把正文、token、配对码、幂等键加入 URL、浏览器持久存储、访问日志或埋点。

## 9. 装配依赖与验证边界

`install_review_routes(app)` 用 `app.state.repository`（行情仓储）和
`app.state.owner_repository`（所有者仓储）构造并设置 `app.state.review_repository`。
两者应使用同一个已迁移数据库，由 create_owner_app 设置；缺失任一依赖时 review_repository
为 None，路由依赖显式返回固定 503，不依靠 AttributeError 或 None 方法调用兜底。
重复安装不重复添加路由或重建仓储。安装本身不建库、不迁移、不初始化主体。
该安装器必须在带 OwnerGate、OwnerRead/OwnerWrite 和固定异常映射的所有者应用中使用，
不是独立公开应用的快捷装配器。无会话时门禁可先返回 401，而不是暴露存储状态。

HTTP 契约由 `tests/test_review_api*.py` 的 HTTPX ASGITransport 集成测试覆盖，
仓储事务使用现有随机 schema fixture。此文不代表浏览器纯文本渲染、真实 HTTPS、
证书信任、前端交互、容器重启或完整 P0 已验收。
