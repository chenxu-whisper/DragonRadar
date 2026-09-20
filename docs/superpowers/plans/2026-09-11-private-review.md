# 第三批私人复盘实施计划

> 执行要求：使用 `subagent-driven-development`，逐任务先 TDD，后规格复核与质量审查。
> 用户已批准技术规格与本轮编码，无需再次请求执行方式确认。

**目标：**交付可鉴权、可保存、可回读与验证的本机私人复盘闭环。
**架构：**独立所有者应用复用快照读模型；不透明会话与业务修改在 PostgreSQL 中事务校验；
私人文稿修订不可变，行情事实按引用即时鉴权；React 分离会话、行情和未保存输入状态。
**技术栈：**Python 3.12、FastAPI、Pydantic、SQLAlchemy、PostgreSQL、React、TypeScript、
Caddy 内部 CA、pytest、Vitest、Playwright。

基线 `ef6854b`，工作树 `.worktrees/private-review`，不得修改其他工作树或现有实例。
规格：[已批准技术规格](../specs/2026-09-11-private-review-design.md)。
缓存、截图、测试记录均写 `.temp/`，新 Markdown 使用中文。

## 执行约定

测试数据库仅允许 `DRAGON_TEST_DATABASE_URL` 中数据库名为 `dragon_test` 的隔离 schema；
复用 `tests/conftest.py` 的 `database` fixture。不得测试真实研究库。

```powershell
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"
$env:PYTHONPYCACHEPREFIX = "$PWD/.temp/pycache"
$env:UV_CACHE_DIR = "$PWD/.temp/uv-cache"
uv run pytest -q -W error
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m compileall -q src tests
```

每个任务记录失败证据及最终命令、退出码、测试数。不把上一轮通过冒充修改后的验收。
数据库迁移只增加新版本；代码应用不自动迁移；数据库错误返回固定脱敏响应。

## 任务一：所有者存储、配对与会话

进度：`240ef9b` 已实现并提交，新增 59 项仓储测试；
基线加本任务 565 项通过，当时含笔记内容契约的整树 588 项通过。
独立规格复核与质量审查均通过，本任务完成；任务二HTTP/CLI开始实施。

新增 `src/dragon_radar/owner/{__init__,models,repository,errors}.py`、
`src/dragon_radar/migrations/versions/0002_owner.py`、
`tests/test_owner_repository.py`；修改 `storage/database.py` 的版本与就绪表检查。

- [x] 编写配对单次消费、到期、并发、撤销、限流、数据库不存明文的失败测试。
- [x] 运行 `uv run pytest tests/test_owner_repository.py -q -W error`，确认缺实现失败。
- [x] 使用标准库 `secrets.token_urlsafe(32)`、SHA-256 散列、注入 UTC clock；
  实现以下服务契约：

```python
OwnerRepository(database, clock=None)
repo.initialize()  # 单安装稳定主体 UUID
repo.issue_pairing(origin)  # 仅 CLI 调用，返回短期一次性明文
repo.exchange_pairing(code, origin)  # 返回 session token、csrf token、主体与期限
repo.authenticate(token)  # 返回服务端主体与会话身份
repo.revoke_all()  # 包括待用配对码
repo.logout(token)
```

- [x] 创建 `owner_principals`、`owner_pairings`、`owner_sessions` 与实例级配对失败计数；
  一次性码 5 分钟、会话 8 小时；限制第 6 次失败尝试，计数即使认证失败也提交。
- [x] 为后续私人事务提供 `authorize_connection(connection, token, csrf=None)`；
  使用同一主体锁串行化撤销和私人写入，期限在锁内重新检查；
  明确锁顺序“主体、幂等键、记录”，鉴权失败不得留下业务数据。
- [x] 执行专项、全套后端、Ruff/格式/compileall；先规格复核，再质量审查并提交。

测试核心：

```python
def test_pairing_single_use(database):
    repo = OwnerRepository(database)
    repo.initialize()
    code = repo.issue_pairing("https://localhost:8030")
    issued = repo.exchange_pairing(code, "https://localhost:8030")
    assert repo.authenticate(issued.token).owner_id == issued.owner_id
    with pytest.raises(InvalidPairing):
        repo.exchange_pairing(code, "https://localhost:8030")
```

对象属性以任务一最终契约为准，后续任务只通过公开方法接入，不传浏览器 owner_id。

## 任务二：所有者 HTTP 边界与 CLI

进度：`f982e42` 已实现；独立规格与质量复核均通过。质量复核对提交快照
重跑106项专项、694项当时全套，不与当前834项整树计数混用。

新增 `owner/{app,security,cli,files}.py`、`tests/test_owner_api.py`、
`tests/test_owner_cli.py`；小范围修改 `cli.py` 分派及 `api.py` 的只读复用入口。
保持 `create_app` 原默认行为与全部旧路由，所有者端显式 `create_owner_app`。

- [x] 写无会话、跨站 Origin、伪 Host/Forwarded、缺 CSRF、重复头、非法 JSON、
  超大 body、配对重放、只读应用无私有路由的失败 API 用例。
- [x] 使用 HTTPX ASGITransport 与现有 lifespan 测试模式，不引入 TestClient 警告屏蔽。
- [x] 将配对 POST、会话 GET/DELETE 与已鉴权行情 GET 装配到独立应用；
  明确精确 Origin/Host，只允许 `/health/live` 与 `/health/ready` 不鉴权。
- [x] Secure、HttpOnly、SameSite Strict 的 session Cookie 与非 HttpOnly CSRF Cookie；
  写操作用 JSON 和 CSRF 头，配对改用明确的同源自定义头。
  不信任 forwarded proxy 标识身份；固定 code/message 错误，不回显输入。
- [x] 128 KiB body 限制必须在 JSON 解析前流式累计，不能只信 Content-Length。
- [x] CLI 增加 `owner-init`、`owner-pair --origin ... --output ...`、
  `owner-revoke`、`serve-owner --origin ...`；配对文件禁止覆盖已有文件、
  禁止软链接，先保护目录/ACL后写明文，Unix 使用 0600；stdout 只输出路径与期限。
- [x] 回归精确 Cookie 属性、撤销、状态码、CLI 文件保护与脱敏；专项与全量通过后两阶段审查。

```python
async def test_read_app_never_issues_owner_context(read_client):
    response = await read_client.post("/api/owner/v1/pair", json={"code": "invalid"})
    assert response.status_code == 404
    assert "set-cookie" not in response.headers
```

## 任务三：自选与不可变私人记录

前置纯内容契约已在 `72e9c05` 提交：`NoteContent`、
`validate_confirmation`、`template_content`，23 项测试及两阶段独立审查通过。
其中字段为 `title/judgment/evidence/invalidation/verification_date`，后续仓储与API复用。
私人仓储现已实现并通过规格复核；质量审查发现同快照列表重复解析，
已抽出 `review/facts.py` 实现事务局部复用，批量查询当前修订，独立复审PASS。
修复后的仓储及私人API联合回归140项、完整后端834项通过。
本任务已提交为 `7dca8e5`；任务四API及私人前端仍单独验收。

新增 `review/{__init__,models,tables,repository,errors}.py`、
`migrations/versions/0003_private_review.py`、
`tests/test_review_repository.py`；更新就绪 schema 版本。

- [x] 先失败测试：两主体读写同 ID、重复幂等键、并发编辑、确认后编辑、
  移除后重新 POST、撤销旧版本、事务失败回滚。
- [x] 按规格创建自选、笔记头、修订、验证、命令账本与私人审计；
  复合外键绑定 owner 与 record/revision；不可变表添加 UPDATE/DELETE/TRUNCATE 保护。
- [x] `ReviewRepository(database, owner_repository, market_repository, clock=None)` 的写入口
  接受会话与 CSRF，不接受客户端主体；所有私人写在同一连接重验会话。
- [x] 新建时检查获授权快照和池内证券；编辑原文不依赖来源仍有效；
  只存引用，不复制行情到人工字段。当前权限读取失败的引用单独返回不可用。
- [x] 实现 `add_watch/set_watch/list_watch`、`create_note/save_note/get_note/list_notes`、
  `list_revisions/get_revision/confirm_note/fork_note`。
- [x] 幂等摘要覆盖方法、路径、正文、预期版本，先查同键提交再判版本；
  重放只返回原资源/修订引用并重验当前会话，不存明文响应。
- [x] 标题按 Unicode 码点 1～100，三正文合计 20000；草稿允许条件缺失，
  确认要求正文与验证日期完整；每次保存追加修订、原快照不可漂移。
- [x] 更新条件同时匹配 owner、ID、版本；409返回当前主体的服务端修订，
  不返回另一主体存在性。验证撤销竞态和 DB trigger，完成两阶段审查。

关键事务顺序：

```python
with database.engine.begin() as connection:
    identity = owners.authorize_connection(connection, token, csrf)
    # 主体锁已持有，然后获取命令键与目标记录锁。
    # 同键同请求返回既有修订标识，否则检查预期版本并追加修订。
    # 头指针、修订、命令账本与私人审计一起提交。
```

## 任务四：模板、人工验证及私人 API

进度：生产装配、分页和错误隔离已实现，中文实际契约见
`docs/private-review-api.md`。104项私人API、联合owner/review321项通过；
独立规格与质量复核PASS，提交 `91de787`。质量复核另执行138项联合市场回归，
并对八类写操作分别验证审计失败回滚、原键重试与重放。

新增 `review/{routes,templates}.py`、`tests/test_review_api.py`、
`tests/test_review_templates.py`；所有者应用挂载显式私人 router。

- [x] 先写模板重生成不覆盖、确认冻结、验证未到期、跨主体 fork、
  来源失权保留原创文稿但事实不可用的失败测试。
- [x] 固定模板版本，市场观察/待验证事项是判断字段文本小节，不添加无限正文；
  显式 POST 新建独立草稿，不调用 AI、不以 GET 写库。
- [x] 验证服务端北京时间到期、已确认修订、明确且可读的验证快照；
  支持/削弱/否定需依据和不早于验证日的交易日；未到期无假事实；
  更正仅追加带替代引用的记录，禁止自动命中率。
- [x] 实现规格第八节所有路径，读分页与参数白名单；
  `If-Match` 缺失 428、冲突 409、Idempotency-Key 缺失 400；
  不可见 404、会话 401、入口/CSRF 403、限流429、存储503。
- [x] 同条件重试不重复写入；头部重复/非法版本/未知字段拒绝。
  API集成确认纯文本脚本不执行、脱敏、无受限行情响应缓存。
- [x] 运行全套与审查，输出中文 `docs/private-review-api.md` 作为前端字段契约。

## 任务五：前端私人工作流

进度：实现、规格复审和质量复审已完成。最终自动化门禁为97项单测、
80项只读浏览器回归、64项私人浏览器回归及默认/私人双构建通过；
最终镜像与真实 HTTPS 浏览器验收归任务六。

新增 `web/src/features/review/{api,types,draft}.ts`、
`{SessionProvider,Pairing,ReviewWorkspace,Watchlist,NoteEditor,ConflictDialog,Verification}.tsx`；
按责任拆分；改 `App.tsx`、`state.ts`、行情详情动作及局部样式，
保留旧只读构建和既有78项浏览器回归。

- [x] 先写会话锁定/原主体恢复、幂等重试、Unicode长度、并发409、不安全参数测试。
- [x] 独立私人请求器读取 CSRF Cookie，保留固定保存命令键，
  401锁定会话不误当行情403；只读构建不请求私人接口。
- [x] 受控配对表单，提交后清码；会话/来源/草稿分别存于内存。
  草稿键为主体+笔记ID+基线修订，401时隐藏且锁定，同主体恢复，退出清除。
- [x] “我的复盘”包含自选、草稿/确认版与历史验证；
  名称取当前授权快照，缺失不造数；当前日期无快照仍可管理已有原创笔记。
- [x] 标题与三正文纯文本编辑，保存回读版本/时间；409三栏对照，
  手工合并需新预期版本，另存记录来源；确认后禁原地编辑。
- [x] 注册应用内离开防护及 beforeunload，取消离开保持URL与输入；
  行情切换/来源失权不卸载编辑状态，迟到请求不覆盖人工文本。
- [x] 生成模板按钮每次明确新建，历史验证绑定确认修订和用户所选快照，
  主观结论用选项与依据输入，不出现绩效/命中率。
- [x] Playwright覆盖两窗口冲突、断网提交结果未知后重试、配对失效、
  草稿恢复、焦点、四档视口、200%与axe扫描；所有测试夹具仅在tests。

```ts
// 只把首次保存命令保留到其结论明确；重试不得产生第二条记录。
const command = { key: crypto.randomUUID(), expectedVersion, payload };
// 恢复请求仍发送 command.key / expectedVersion / payload 的相同值。
```

## 任务六：HTTPS 部署与最终交付

进度：独立8030实例已启动且正常库为空，0003迁移和TLS健康检查通过。
重复目录初始化的ACL错误已按红绿测试修复并经独立复核。
项目CA已在用户明确授权后安装到当前用户信任库，真实浏览器配对验收已完成。

新增 `compose.private.yaml`、`deploy/private.Caddyfile`、`deploy/private.Dockerfile`、
`scripts/start-private.ps1`、`scripts/private-owner.ps1`、
`docs/private-review-running.md`、`docs/verification-private-review.md`；
更新 README、依赖、CI、忽略配置与本计划。

- [x] 验证配置只回环8030、独立项目卷、API/DB不发布、无隧道、正文凭据不进镜像。
- [x] 启动脚本只建库迁移和启动，不导入行情；随机凭据写入预保护.temp目录，
  新 CA 私钥不导出，只提供公钥证书/指纹及精确安装、卸载命令。
- [x] Caddy `tls internal`，禁自动HTTP公开入口，明确API方法白名单，
  no-store/CSP/no-referrer，无访问日志。健康探针验证专属CA，不跳证书验证。
- [x] Windows信任库变更先单独征求用户同意；获准后仅信任本项目CA，
  不绕警告、不用ignoreHTTPSErrors，不删除其它证书。
- [x] 实际 HTTPS 配对、保存、冲突、确认、撤销、重启回读；测试主体与样例隔离，
  正常库为空，记录证书和域名校验，不记录码/Token/正文。
- [x] 后端全量、Ruff/格式/compileall、前端全量/构建、镜像构建与只读兼容回归。
- [x] 先最终审查、修复闭环，再更新真实计数、限制和地址并提交；
  不自动推送/合并，不删除前两批数据库卷，不宣称公网和完整P0已验收。

## 计划自查

规格三对应任务一/二/六，四/五对应三，六对应四，七/八对应四/五，
九的跨层验收由六收口。批准后新增方法的字段以任务验收输出契约为准同步更新本文，
不允许前后端各自猜测结构。系统证书授权是执行阶段唯一预期需用户介入的操作，
在此之前持续推进全部可独立验证的代码与测试。
