# DragonRadar

面向本机使用、支持数据追溯的盘后市场研究平台。已实现数据底座、确定性计算及
只读研究工作台，尚不是包含七个页面的完整 P0 应用。
真实行情源、AI 服务、券商连接和公网分享均未启用。

## 当前开发进度

截至 2026-09-15，首批“数据底座＋确定性计算”及中文文档已推送至
`origin/feature/data-foundation`（`3fef804`），尚未合并到 `main`。
第二批位于工程根目录 `.worktrees/research-workbench`，
分支为 `feature/research-workbench`，本批独立审查、回归和容器验收已完成；
代码保留在该开发分支，尚未推送或合并。
第三批位于 `.worktrees/private-review`，分支为 `feature/private-review`，
已完成本机私人复盘闭环；所有者入口、私人仓储、私人前端自动化验收
、最终镜像重建及真实 HTTPS 浏览器验收已完成，未推送或合并。

| 模块 | 进度 |
| --- | --- |
| 数据契约、来源授权、质量校验与问题隔离 | 已实现 |
| 交易日窗口、区间收益、回撤、归一化比较、市场广度与收盘涨跌停计算 | 已实现 |
| PostgreSQL 迁移、幂等导入、不可变快照、修订与审计 | 已实现 |
| 本机管理 CLI、只读 API、Docker 启动脚本 | 已实现并完成本机联调 |
| 证券列表与详情只读 API、搜索排序分页、历史缺口与单位 | 已实现 |
| 市场总览、区间分析、个股抽屉及完整详情 | 已实现并完成第二批本机验收 |
| 本机所有者配对、会话、CSRF 与 CLI 文件保护 | 已实现并通过独立审查 |
| 私人笔记、自选、不可变修订、模板、确认与人工验证仓储 | 已实现并通过独立审查，提交 `7dca8e5` |
| 私人业务 API 与私人复盘界面 | API 已通过两阶段审查并提交 `91de787`；前端规格与质量复审通过，待提交 |
| 自动化验证 | 后端含真实TLS边界844项通过、总语句覆盖率96%；前端97项单测、80项只读E2E、64项私人E2E及双构建通过 |
| 其余页面、真实数据源、正式角色规则、监管规则与 AI | 尚未实现 |
| 公网分享、备份恢复验收、性能指标与影子运行 | 尚未完成 |

以上不代表完整 P0 已验收。首批历史结果见
[数据底座验收记录](docs/verification-data-foundation.md)，
第二批范围和验证命令见[工作台测试说明](docs/workbench-testing.md)，
实际测试和审查状态见[第二批验收记录](docs/verification-research-workbench.md)。
第三批阶段证据见[私人复盘验证记录](docs/verification-private-review.md)，
接口见[私人 API 契约](docs/private-review-api.md)，
运行与证书步骤见[私人实例说明](docs/private-review-running.md)。
本批独立入口为 `https://localhost:8030`，最终私人镜像已启动且正常库为空；
项目 CA 已在明确授权后安装到当前用户 Windows Root 信任库；
不得忽略证书警告或将该入口公开到网络。

## 开发环境

环境要求：Python 3.12、uv 0.8.2 或兼容版本、Docker Compose v2；
前端本机开发还需 Node.js 22.12 或以上版本。
在开发工作树目录中使用 PowerShell 执行：

```powershell
$env:PYTHONPYCACHEPREFIX = "$PWD/.temp/pycache"
$env:UV_CACHE_DIR = "$PWD/.temp/uv-cache"
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python -m compileall -q src tests
```

数据库集成测试需要将 `DRAGON_TEST_DATABASE_URL` 指向专用 PostgreSQL 测试数据库。
禁止使用真实研究数据库执行测试。未设置该变量时，测试结果不能作为数据库验收依据。
如果测试容器已经存在，应执行 `docker start dragon-radar-foundation-test-db`，
不要重复创建。

```powershell
docker run --detach --name dragon-radar-foundation-test-db `
  --publish 127.0.0.1:55432:5432 `
  --env POSTGRES_DB=dragon_test --env POSTGRES_USER=dragon_test `
  --env POSTGRES_PASSWORD=dragon-test-local-only `
  postgres:17-alpine
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"
uv run pytest -q --cov=dragon_radar --cov-report=term-missing
docker stop dragon-radar-foundation-test-db
```

上述明文密码仅用于隔离的测试数据库。正常运行的 Compose 实例必须另设强密码。

Task20 默认由有界编排器执行完整后端、前端、性能、恢复与证据校验：

```powershell
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"
uv run python scripts/verify_reset.py --mode full --output .temp/acceptance/reset-base.json
```

`--mode focused` 只运行 CSV、Reset 验收、恢复、性能与 Python 静态门禁，
缺少完整门禁时必然返回阻塞。已有证据只能使用
`--validate-existing <path>` 校验 v2 封闭证据结构；即使完整性校验通过也返回
exit 3，且 integrity-only (`INTEGRITY_ONLY`) evidence cannot satisfy automated
acceptance。验证器要求完整且不重复的门禁 ID、精确允许的
argv/cwd/环境类别、正通过数、零失败与零跳过、日志及性能原始产物 SHA-256，
并从原始字段推导状态和性能结论，不接受调用方填写状态自证通过。
`tested_commit` 必须等于当前 `HEAD`；仅当当前 `HEAD` 只改动
`docs/acceptance/` 或证据元数据时，才允许等于 `HEAD^` 并记录父提交关系。

实测合成结果、恢复演练和独立阻塞门禁见
[Reset 基础验收](docs/acceptance/reset-base.md)。真实输入、正式规则、真实模型评测、
公网分享许可及至少 20 个交易日影子运行未由自动化结果替代。

## 第二批工作台

第二批工作树位于 `.worktrees/research-workbench`，分支为 `feature/research-workbench`。
范围为市场总览、涨幅分析、个股抽屉和详情，全部只读。证券查询接口见
[接口说明](docs/workbench-api.md)，运行与测试要求见[工作台测试说明](docs/workbench-testing.md)。

工作台使用独立容器项目，不复用或覆盖首批数据库：

```powershell
.\scripts\start-local.ps1 -Workbench -Port 8020
```

入口为 `http://127.0.0.1:8020`，Caddy 同源托管页面并代理 API；
数据库和 API 本身不发布宿主端口。新实例默认空库，不自动加载虚构行情。
开发凭据位于 `.temp/workbench-compose.env`，不要与首批凭据混用。

## 首批 API 容器

在 Windows 上，启动脚本会在 `.temp/local-compose.env` 生成凭据文件，
将访问权限限制为当前所有者与 SYSTEM，然后构建镜像、执行迁移并等待服务就绪：

```powershell
.\scripts\start-local.ps1 -Port 8000
```

保留数据库卷时，也应保留该凭据文件。脚本不会读取项目已有的 `.env`。
停止由该脚本管理的服务：

```powershell
docker compose --env-file .temp/local-compose.env -f compose.yaml -f compose.dev.yaml stop
```

若不用脚本而是手动配置，请使用另一个全新的 Compose 项目。
通过密码管理器生成十六进制密码，或在 PowerShell 中执行：

```powershell
$env:COMPOSE_PROJECT_NAME = "dragon-radar-manual"
$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$env:DRAGON_DB_PASSWORD = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
docker compose build
docker compose up -d db
docker compose run --rm api migrate
docker compose -f compose.yaml -f compose.dev.yaml up -d
```

关闭终端前，应将密码保存到本机凭据管理器。后续启动必须使用相同的数据库密码；
修改环境变量不会自动轮换已初始化的 PostgreSQL 账户密码。

开发模式只读 API 默认地址为 `http://127.0.0.1:8000`。
手动启动时，如端口被占用，可提前设置 `DRAGON_API_PORT`；使用启动脚本时，
通过 `-Port` 指定端口。本次本机联调使用 `8010`，再次启动可执行
`.\scripts\start-local.ps1 -Port 8010`，相应查询地址也需改为 `8010`。

基础 Compose 配置不映射数据库或 API 的宿主端口；开发叠加配置仅将 API
绑定到回环地址，不是已经实现身份认证的分享服务器。基础后端网络是内部网络，
开发配置为 Docker Desktop 回环端口转发增加了一个网络，因此 API 容器具备出站能力，
但尚未实现数据供应商或模型客户端。数据库始终只连接内部后端网络。

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
Invoke-RestMethod http://127.0.0.1:8000/api/v1/snapshots
docker compose -f compose.yaml -f compose.dev.yaml stop
```

启动不会插入演示数据或发布快照。来源登记、数据导入与发布都需要通过本机命令显式执行，
具体见 `dragon-radar --help` 和[数据导入契约](docs/data-import.md)。
原只读浏览器 API 不提供来源登记、导入、发布、AI 调用或笔记写入能力。
第三批独立所有者应用才装配私人写入接口；原只读入口不因本批开发而开放写入。

## 数据规则

- 每个快照记录来源、截止时点、股票池／日历／复权版本和明确的覆盖情况。
  所选历史日期不存在时，不自动跳到今天的快照。
- 来源授权必须允许请求的本机操作，并在读取时仍然有效。
  授权到期或撤销后，之前发布的数据也不得继续读取。
- 不完整或无效批次保留问题记录并禁止发布；不补零、不对不完整股票池生成正式排名，
  不静默混用不同来源。
- N 日收益需要所提供交易日历中的 N＋1 个位置，不猜测节假日。
  区间内缺价或停牌的证券会被排除。
- 研究收益统一使用同一复权版本；收盘涨跌停判断使用原始价格与明确提供的有效限价，
  不硬编码“涨跌 10%”。
- 已发布快照不可修改；更正会创建新修订版。
- 历史回补数据只能用于事后研究，不能冒充当时已知的证据。

## 目录与边界

`src/dragon_radar/domain` 定义数据与质量契约，`analytics` 实现纯计算，
`storage` 负责 PostgreSQL 事务，`owner` 管理本机操作上下文，
`review` 管理私人修订及事务局部事实投影。本机 CLI 执行数据管理，
FastAPI 分别装配只读与所有者应用。
迁移脚本随包放在 `src/dragon_radar/migrations`，测试产物与缓存统一放在 `.temp/`。

当前交付尚不满足 P0 发布条件。待完成内容包括其余页面、经批准的角色阈值、
真实数据源接入、监管复核、AI 评测、公网分享、备份恢复演练、
完整无障碍验收和规定的影子运行周期。
本机 Caddy 同源网关和本批自动化无障碍扫描不替代这些发布条件。

数据库卷不是备份。禁止使用 `docker compose down -v` 更新或回退应用。
应用镜像回退无法撤销不兼容的数据库迁移；今后升级前应备份并验证恢复流程。

相关文档：[已批准的技术设计](docs/superpowers/specs/2026-09-09-data-foundation-design.md)、
[实施计划](docs/superpowers/plans/2026-09-09-data-foundation.md)、
[验收记录](docs/verification-data-foundation.md)、
[依赖清单](docs/dependencies.md)、[产品需求](docs/leader_quant_prd.md)。
