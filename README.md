# DragonRadar

面向本机使用、支持数据追溯的盘后市场研究平台。已实现复盘工作台、
确定性研究回测和纯本地模拟账本。真实数据与模型有效性、正式规则、
公开部署许可及影子运行须独立验收。

## 当前开发进度

2026-11-17：主要功能整合到本地 `main`，后续从工程根目录开发。
真实数据接入与生产验收环境暂缓，具体条件见 [后续待办](docs/TODO.md)。
Task 17 历史实现基线为 `1adda8d`。
Task 18 已完成本机开发验收：全量回归、研究并发性能门及需求追溯均通过，
实际证据见 [Task 27 验收记录](docs/acceptance/task27-research.md)。
本次工程整合记录见 [主分支整合记录](docs/integration/2026-11-17-main.md)。
其他工程资料见 [文档入口](docs/README.md)。

| 模块 | 进度 |
| --- | --- |
| 数据底座、不可变发布、CLI、API 与部署隔离 | 已实现 |
| 总览、消息、梯队、涨幅分析、个股、监管与本地复盘 | 已实现；正式数据/规则另验 |
| 笔记、自选、备份恢复 | IndexedDB 为权威源，按本地 profile 分区 |
| 实时行情展示、龙虎榜、词云、轮动、自然语言查询、情绪研究 | 已实现；真实输入另验 |
| PIT 回测、因子体检、walk-forward、异步运行与取消 | 已实现；生产输入装配仍失败闭合 |
| 本地模拟账本 | 已实现；仅研究模拟，无真实订单 |
| AI 增益对照 | 同池实验框架已实现；真实评测缺失时 ai_variant 固定 disabled |
| Task 18 最终验收 | 开发验收完成；详见新的验收记录 |
| 真实数据、正式规则、真实模型评测、公开许可、20 交易日影子运行 | 五项独立外部门禁 blocked |

上述是实现状态，不代表生产就绪。历史阶段结果见
[数据底座](docs/verification-data-foundation.md)、
[早期工作台](docs/verification-research-workbench.md)、
[早期私人复盘](docs/verification-private-review.md)。
Reset 当前运行方式见 [运行说明](docs/reset-running.md)，
2026-10-27 历史终验见 [Reset 全范围终验](docs/acceptance/reset-final.md)；
其 Task 27 未实现结论由后续验收记录补充，不覆盖原始历史记录。

## 开发环境

环境要求：Python 3.12、uv 0.8.2 或兼容版本、Docker Compose v2；
前端本机开发还需 Node.js 22.12 或以上版本。
在开发工作树目录中使用 PowerShell 执行：

```powershell
$env:PYTHONPYCACHEPREFIX = "$PWD/.temp/pycache"
$env:UV_CACHE_DIR = "$PWD/.temp/uv-cache"
uv sync --locked
uv run python scripts/check_prd_contracts.py
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

`--mode focused` 只运行 PRD 契约、CSV、Reset 验收、恢复、性能与 Python 静态门禁，
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

Task29 全范围终验在编排器首位新增 `prd_contracts` 门禁
（`uv run python scripts/check_prd_contracts.py`，同时纳入 full 与 focused），
并新增 `tests/test_reset_full_scope.py`（57 项 REQ 与 EX01-16 逐项追溯、只读应用面
与 Task 27 研究实现边界）和 `web/tests/e2e/reset-full-scope.spec.ts`（固定快照跨导航、
实时批次独立、龙虎榜合成授权、词云、轮动、NL 查询、离线笔记、授权撤回、主题/密度/
命令面板；Task 27 段为通过性边界断言，不使用 `test.skip()`）。七个独立状态台账与
真实门禁退出码见 [Reset 全范围终验](docs/acceptance/reset-final.md)。
**开发完成不等于生产就绪**：Task 27 的实现与后续验收见
[研究验收记录](docs/acceptance/task27-research.md)。真实输入、官方规则、
真实模型评测、公开部署许可、≥20 交易日影子运行五项外部门禁保持 `blocked`。
平台相关检查按实际执行环境登记，合成回归不得替代真实材料。

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

当前交付尚不满足生产发布条件。真实数据源与生产研究输入装配、
正式规则复核、真实 AI 评测、公开许可和规定的影子运行须分别完成。
本机 Caddy 网关、合成回归和自动化无障碍扫描不替代这些发布条件。

数据库卷不是备份。禁止使用 `docker compose down -v` 更新或回退应用。
应用镜像回退无法撤销不兼容的数据库迁移；今后升级前应备份并验证恢复流程。

相关文档：[已批准的技术设计](docs/superpowers/specs/2026-09-09-data-foundation-design.md)、
[实施计划](docs/superpowers/plans/2026-09-09-data-foundation.md)、
[验收记录](docs/verification-data-foundation.md)、
[依赖清单](docs/dependencies.md)、[产品需求](docs/DragonRadar_开发需求PRD.md)。
