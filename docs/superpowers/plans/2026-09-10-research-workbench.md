# 第二批只读研究工作台实施计划

> 执行要求：使用 `subagent-driven-development`，逐任务实施，先规格复核后质量审查。

**目标：**交付连接真实快照 API 的本机只读研究工作台。
**架构：**证券读模型复用不可变快照；React 按快照键查询；Caddy 同源托管与代理。
**技术栈：**Python／FastAPI、React／TypeScript／Vite、成熟图表库、Playwright、PostgreSQL。

## 任务一：证券读模型

文件：新增 `src/dragon_radar/readmodels/securities.py`，
修改 `src/dragon_radar/api.py`，新增 `tests/test_securities.py`。

- [ ] 先写失败用例：证券名称与 ID 搜索、数值排序、空值后置、稳定并列、
  默认 50 行、20／50／100 页大小、越界参数、旧名与当前名称、停牌／缺价历史。
- [ ] 执行 `uv run pytest tests/test_securities.py -q -W error` 观察缺实现失败。
- [ ] 实现 `securities` 与 `securities/{security_id}` 快照读接口，复用授权与响应封装。
  只投影白名单，拒绝快照池外证券，不暴露授权依据。
- [ ] 添加授权撤销后列表和详情拒绝、非法查询、旧接口兼容测试。
- [ ] 执行全套 PostgreSQL 测试、Ruff、格式与 compileall；先规格后质量审查并修复。

```python
async def test_readonly_securities(client, snapshot):
    response = await client.get(f"/api/v1/snapshots/{snapshot.id}/securities")
    assert response.status_code == 200
    assert response.json()["result"]["page_size"] == 50
```

## 任务二：研究工作台

文件：`web/package.json`、`web/src/{api,types,state,format}.ts`、
`web/src/{App,main}.tsx`、`web/src/components/`、`web/src/pages/`、
`web/src/styles.css`、`web/tests/` 与构建配置。

- [ ] 先写查询串、数值格式、比较上限和请求错误状态测试，执行前端测试观察失败。
- [ ] 安装并锁定 React、TypeScript、Vite、lucide 和图表库；不安装外部设计脚本。
- [ ] 实现浅色导航和快照选择，元信息始终可查；无快照时不造数据。
- [ ] 实现市场总览、区间分析、证券抽屉与详情；图表同步数据表；不重复计算指标。
- [ ] 实现查询串恢复、取消旧请求、超时重试、403 清空、比较五标的上限。
- [ ] 执行 TypeScript、前端测试和构建；规格和质量审查通过后进入浏览器验证。

## 任务三：部署与验收

文件：新增工作台 Compose 叠加配置、前端 Dockerfile 和 Caddy 配置；
修改 README、依赖清单与 CI；新增中文验收记录。

- [ ] 配置独立 Compose 项目与新回环端口，保留首批 8010 服务及数据。
- [ ] Playwright 使用测试夹具覆盖正常、无快照、缺数据、断网、403、快速切换、
  抽屉 Esc／回焦点、对比上限、排序分页和四档视口。
- [ ] 核对图表绘制非空、中文标签不重叠、宽表只在容器滚动、200% 缩放。
- [ ] 运行 Python 全量回归、前端测试、Ruff／格式、compileall、构建和容器启动。
- [ ] 更新中文运行文档、实际测试数量及未覆盖边界，提交本批但不自动推送或合并。

本批不验收真实数据授权、龙头识别有效性、监管规则、笔记写入、AI 或公网部署。
