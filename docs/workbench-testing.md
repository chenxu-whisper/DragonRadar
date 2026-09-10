# 第二批测试与运行说明

## 后端回归

在 `.worktrees/research-workbench` 中执行，测试数据库不得指向真实研究库：

```powershell
docker start dragon-radar-foundation-test-db
$env:DRAGON_TEST_DATABASE_URL = "postgresql+psycopg://dragon_test:dragon-test-local-only@127.0.0.1:55432/dragon_test"
$env:PYTHONPYCACHEPREFIX = "$PWD/.temp/pycache"
uv sync --locked
uv run pytest -q -W error
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m compileall -q src tests
```

## 前端验证

前端位于 `web/`，安装版本由锁文件固定。
测试使用专用虚构 API 响应，正常构建不含测试行情，不连接外部数据或模型。
TypeScript、单元测试、构建与 Playwright 各自通过后才视为本批验证完成。

在工作树的 `web/` 目录执行：

```powershell
npm ci --ignore-scripts
npm test
npm run build
npm run test:e2e
```

本机 Playwright 默认使用已安装的 Microsoft Edge；Linux CI 设置
`PLAYWRIGHT_CHANNEL=chromium` 并先执行 `npx playwright install --with-deps chromium`。
测试自动启动并关闭 `127.0.0.1:5173`，执行前需确保该端口空闲。
测试报告与截图位于 `web/.temp/playwright/`。
200% 验证采用 CSS `zoom: 2`，不等同于所有浏览器原生缩放模式的完整验收。

本机交互开发时先启动下面的工作台容器，再在 `web/` 执行 `npm run dev`。
Vite 通过 `DRAGON_API_PROXY` 选择后端，默认使用第二批的 `http://127.0.0.1:8020`。
首批 8010 API 不包含第二批证券接口，不应作为本工作台的联调后端。
正常工作台为空库，因此页面无行情是预期状态；不要为开发预览向正常库导入虚构数据。

浏览器验收覆盖：

- 市场总览、涨幅分析、个股抽屉及完整页的往返。
- 所选日期无快照时保持原条件，不自动回退到其他日期。
- 切快照或快速筛选时，迟到响应不覆盖当前结果。
- 默认 50 行分页、空值末尾、比较最多五个标的。
- 缺价不补零、无 OHLC 不画 K 线、图表存在等价数据表。
- 断网／存储错误保留正确旧时间，403 清除受限内容。
- 四档视口、200% 缩放、抽屉 Esc 与焦点恢复、减少动态效果。

## 本机容器

工作台使用独立项目 `dragon-radar-workbench`，不要与首批项目共用卷或凭据。
以下命令完成前后端构建、迁移和健康检查：

```powershell
.\scripts\start-local.ps1 -Workbench -Port 8020
```

访问 `http://127.0.0.1:8020`。首次启动为空库，需要本机 CLI 在获得授权后显式导入。
脚本不会读用户已有 `.env`，不会自动导入虚构数据。
凭据保存到已忽略的 `.temp/workbench-compose.env`。

```powershell
docker compose -p dragon-radar-workbench --env-file .temp/workbench-compose.env -f compose.yaml -f compose.workbench.yaml stop
```

本批采用本机 HTTP，只读网关不是公网分享实现，不满足正式 HTTPS、所有者身份、
数据展示许可或外网访问验收。数据库与 API 没有宿主端口，只有 Caddy 的页面入口映射回环地址。
