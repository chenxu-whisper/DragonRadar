# A股龙头复盘与量化研究平台 PRD

> **本版决策**
>
> - **首期**：完成可追溯的盘后复盘工作台。
> - **识别**：六类角色有独立证据，允许无候选。
> - **AI**：读资料、解释结果，不负责行情计算。
> - **风控**：监管状态和数据质量独立展示。
> - **交付**：需求文档与评审示意，不部署交易系统。

***

## 二、用户与目标

首个用户是需要独立复盘的A股研究者，不假定已掌握SQL、模型训练或量化工程。

### 2.1 用户场景

- 收盘后快速查看市场分布、强势题材和涨停梯队，再核查个股证据。
- 自定义区间比较持续强势股票，避免把单日涨幅第一等同于龙头。
- 从消息定位题材和个股，分清正式披露、媒体报道与AI推断。
- 查看监管预估与正式事件，明确风险未知和事实已确认的区别。
- 保存当天判断及失效条件，之后回看判断是否被事实支持。
- 以历史快照研究规则，后续再进入独立回测与模拟组合。

### 2.2 产品目标

1. 每个关键数字能定位到来源、时间、单位和版本。
2. 每个角色标签能打开支持证据、反证与缺失项。
3. 完成一次“看市场、查证据、记判断”的复盘任务，不依赖多个手工表格。
4. AI失效、供应商断源时仍保留可读历史与用户笔记。

复盘效率以同一批用户的人工流程作基线，比较完成相同任务的耗时；基线和改善幅度在试用后确定。收益率、推荐命中率及交易胜率不作为P0发布指标。

***

## 三、范围与分期

P0是首期发布必须满足的范围，P1需新增数据或稳定性验证，P2独立立项。分期不意味着已经获得数据、模型或展示许可。

### 3.1 发布范围

<table>
<tr><td align="center">阶段</td><td align="center">目标</td><td align="center">包含</td><td align="center">明确不含</td></tr>
<tr><td align="center">🔴 P0</td><td align="left">盘后研究</td><td align="left">七个页面、历史快照、角色候选、区间分析、监管观察、笔记、AI摘要／关联建议／证据解释／复盘增强、数据运营后台</td><td align="left">实盘交易、实时承诺、逐笔回放、策略收益承诺</td></tr>
<tr><td align="center">🟡 P1</td><td align="left">盘中与研究增强</td><td align="left">获授权的盘中行情与提醒、龙虎榜、词云、题材演进、自然语言筛选</td><td align="left">未经验证的因果判断与自动交易</td></tr>
<tr><td align="center">🔵 P2</td><td align="left">策略验证</td><td align="left">历史回测、滚动验证、模拟组合、AI增益对照</td><td align="left">券商接入和实盘下单；须另写PRD</td></tr>
</table>

P0内部先完成确定性数据链路，再上线AI摘要、关联建议、证据解释和复盘增强；允许单独关闭AI，不影响基础页面。跟风、接力在P0有标签入口与证据状态，但缺少时序数据时不得输出已确认结论，P1再增强其识别能力。

### 3.2 股票池与时间

- 行情浏览范围按供应商授权和实际覆盖展示，不能默认覆盖全部沪深北A股。
- 首期角色计算池建议为沪深主板普通股票；按历史时点排除ST、退市整理和上市不足120个交易日的股票。特殊状态缺失的股票暂不进入规则池。
- 市场统计池、题材成员池、角色计算池是不同集合，界面必须各自标示，不能共用一个含糊的“全市场”标签。
- 跨板块扩展必须适配对应规则。120个交易日是研究参数，不是监管要求。
- 行情主快照是选定交易日的正式盘后批次；新闻时间轴允许查看盘后新增消息，但须标明是否进入当前角色快照。

### 3.3 参考图与非目标

参考图片仅用于功能拆解和视觉方向，不用于复制未知算法、商标或未授权资讯。

<table>
<tr><td align="center">参考图</td><td align="center">采纳内容</td><td align="center">处理方式</td></tr>
<tr><td align="center">01</td><td align="left">侧栏、新闻摘要、AI报告</td><td align="left">来源优先；词云进入P1</td></tr>
<tr><td align="center">02</td><td align="left">题材梯队、消息与个股关系</td><td align="left">题材下钻；不用多重弹窗</td></tr>
<tr><td align="center">03</td><td align="left">市场情绪、领涨分析、龙虎榜</td><td align="left">透明指标进入P0；龙虎榜进入P1</td></tr>
<tr><td align="center">05</td><td align="left">每日涨停梯队、个股详情</td><td align="left">保存历史矩阵和事实；席位别名另核验</td></tr>
<tr><td align="center">06</td><td align="left">自定义涨幅、候选对照</td><td align="left">统一交易日与复权口径</td></tr>
<tr><td align="center">07</td><td align="left">题材趋势、成分股比较</td><td align="left">题材面板进入P0；复杂演进进入P1</td></tr>
</table>

不复原“火花买点”等未披露指标，不保证找到每个板块的唯一龙头，不训练自有大模型，不接收券商密码或交易验证码，不开发收费订阅、社区荐股或自动买卖。

***

## 四、页面与体验

主任务是从市场事实进入个股证据，再保存可验证的判断；不能只提供一张没有来源的排行榜。

### 4.1 页面导航

<table>
<tr><td align="center">页面</td><td align="center">主要内容</td><td align="center">操作与下钻</td></tr>
<tr><td align="center">市场总览</td><td align="left">指数、成交、涨跌分布、涨停生态</td><td align="left">切换日期、查看口径、打开题材</td></tr>
<tr><td align="center">市场消息</td><td align="left">新闻、公告、摘要、来源与关联</td><td align="left">检索、原文核验、纠正关联</td></tr>
<tr><td align="center">龙头梯队</td><td align="left">六类候选、连板矩阵、变化</td><td align="left">题材与角色筛选、查看因子和反证</td></tr>
<tr><td align="center">涨幅分析</td><td align="left">区间收益、超额收益、回撤</td><td align="left">选择窗口、排序、曲线比较</td></tr>
<tr><td align="center">个股详情</td><td align="left">行情、角色、事件、风险</td><td align="left">抽屉预览、完整详情、加入自选</td></tr>
<tr><td align="center">监管观察</td><td align="left">计算状态、正式事件、适用规则</td><td align="left">查看窗口、原始披露和待核验项</td></tr>
<tr><td align="center">我的复盘</td><td align="left">自选、笔记、AI草稿、验证记录</td><td align="left">保存、确认、历史对照、导出</td></tr>
</table>

后台只向管理员展示来源配置、权限状态、质量异常、任务重跑、规则版本和AI用量。普通用户不接触密钥与批量发布操作。

### 4.2 交互约定

- PC以浅色研究终端作为评审默认：白底、浅灰分区、蓝色操作入口及紧凑表格；行情红涨绿跌。角色采用中性文字标签，系统与监管状态使用独立的文字、图标及样式，不凭颜色推断风险或收益；深色参考与分期见4.5—4.9。
- 顶部固定交易日、数据截至时间、股票池和全局搜索；历史模式持续显示“历史回看”。
- 股票单击打开一个详情抽屉；完整详情可独立访问；Esc关闭、键盘可操作，返回保留筛选。
- 列表默认50条分页，可切换20／50／100条；排序相同值并列，证券代码仅用于稳定展示顺序，空值置后。
- 筛选条件变更后回到第1页；参数写入可分享的页面查询串，但私有笔记和凭证不进入URL。
- 自选使用证券唯一标识，改名后仍保留；重复加入不重复记录。
- 图表最多同时比较5个标的；明确单位、复权方式及基准，不使用双轴暗示相关性。
- 手机导航折叠、宽表横滑、详情全屏；按375px与1440px视口验收，不整体缩小桌面页面。
- 导出与当前筛选及版本一致；有授权的字段可导出，不默认导出全文资讯或批量原始行情。

### 4.3 业务流程

用户选择已发布快照后查看候选，核查数据与监管，再决定是否保存观察；任何证据不足都可以退出判断，而不是被迫选一只股票。

[查看在线复盘流程画板](https://bytedance.larkoffice.com/docx/PojzdqI2coWdaJxDxRxcDKJKnNe)

离线完整包附带流程图源文件 `assets/review_flow.svg`；正文前后的业务路径说明可独立阅读。

### 4.4 状态与评审示意

<table>
<tr><td align="center">状态</td><td align="center">条件</td><td align="center">页面结果</td></tr>
<tr><td align="center">S01 正常</td><td align="left">请求成功且关键数据完整</td><td align="left">显示截至时间、快照、来源、覆盖率和证据</td></tr>
<tr><td align="center">S02 加载</td><td align="left">首次进入或刷新</td><td align="left">首次显示骨架；刷新保留旧日期和旧版本，禁止重复提交</td></tr>
<tr><td align="center">S03 无结果</td><td align="left">无候选、历史不足或被过滤</td><td align="left">列出原因、排除数量和重置入口；不造股票或填0</td></tr>
<tr><td align="center">S04 降级</td><td align="left">超时、过期、覆盖不足、冲突</td><td align="left">只读最后有效快照，醒目标记旧时间；停用受影响的新分析</td></tr>
<tr><td align="center">S05 禁用</td><td align="left">无访问／展示权限、规则未验收</td><td align="left">说明具体原因；不加载未授权内容，不标记风险正常</td></tr>
<tr><td align="center">S06 保存成功</td><td align="left">笔记通过校验并服务端提交</td><td align="left">显示保存时间和版本；重新打开可恢复</td></tr>
<tr><td align="center">S07 写入冲突</td><td align="left">另一会话已修改同一笔记</td><td align="left">保留当前输入，提供对照与另存，不静默覆盖</td></tr>
</table>

文档附带PROTO-A候选与证据、PROTO-B质量降级、PROTO-C笔记保存三个评审示意。A可切换正常、加载与无候选；B可切换过期、数据不完整和授权失效；C可模拟保存成功、冲突另存及AI超时降级。默认视图让评审者看到：候选必须有证据、旧数据必须标时间、笔记由用户确认保存。

示意使用虚构标的和固定演示内容，不连接行情、不持久保存笔记、不部署应用；完整七页视觉稿待设计评审。三幅等高示意横向排列，窄屏可左右滑动，每幅内容可独立上下滚动；交互用于验证禁用、冲突和降级行为，不作为真实系统验收结果。

[打开在线版查看交互评审示意](https://bytedance.larkoffice.com/docx/PojzdqI2coWdaJxDxRxcDKJKnNe)

离线完整包附带 `assets/leader_prd_prototype.html`，双击可用浏览器打开；仅演示行为，不联网、不持久存储。

即使不打开示意，S01—S07和下文需求表仍是完整的行为约定。表内A/S01为PROTO-A/S01的简称，B、C同理；单独S编号表示通用状态。示意对应REQ-L01、S01、Q01、Q04、R02、U02、U03、A04及同编号AC；未绘制页面在设计阶段补图。

### 4.5 视觉定位与参考取舍

**方向：简约、时尚、科技的研究终端。** 简约指入口清晰、减少装饰；时尚指克制的字体、留白与边界；科技感来自可追溯数据和直接交互，而非霓虹、粒子背景、玻璃叠层或大面积渐变。表格承担比较，图表承担趋势，少量指标卡承担概览；不做卡片墙。

用户明确了审美方向，并提出CEX示例供参考；默认主题、具体色值及尺寸仍是待评审建议，不代表已批准。P0沿用浅色默认，P1候选增加深色夜间主题、密度切换与命令面板；尚未实现的开关不出现在P0生产界面。P1视觉增强不以行情实时化为前提，也不随实时数据能力自动上线。

已查看的[CEX Trading示例](https://uupm.cc/demo/cex-trading)是营销落地页与简化交易组件的组合，不是本项目的信息架构模板；借鉴其深色层次、细边框、数字对齐和有限黄色焦点，不复制交易业务。

<table>
<tr>
<td align="center">参考要素</td>
<td align="center">保留或转译</td>
<td align="center">不采用</td>
</tr>
<tr>
<td align="left">层次与品牌色</td>
<td align="left">弱阴影、细边框、小圆角；深色作为夜间方向</td>
<td align="left">大面积黄色、强发光、每行黄色操作；黄色不用于代表候选更优</td>
</tr>
<tr>
<td align="left">营销与交易区</td>
<td align="left">改为市场事实、候选证据、查看监管、写复盘</td>
<td align="left">宣传大横幅、开户引导、Buy/Sell、杠杆、合约及无数据支持的盘口</td>
</tr>
<tr>
<td align="left">行情语义</td>
<td align="left">证券代码、交易日、盘后快照、红涨绿跌</td>
<td align="left">币种、24小时交易口径、绿涨红跌及示例行情数值</td>
</tr>
<tr>
<td align="left">可信度表达</td>
<td align="left">展示真实来源、覆盖率、授权与数据状态</td>
<td align="left">借用安全基金、认证、用户量等示例宣传作为本项目事实</td>
</tr>
</table>

### 4.6 布局、字体与设计变量

PC页面采用侧栏导航、全局状态栏、筛选工具栏、主工作区和单个按需详情抽屉；不把AI区固定占满首屏。日期、快照、覆盖与来源状态保留可见入口，展开后显示完整时间和版本；风险未知不得藏在悬浮提示里。

<table>
<tr>
<td align="center">项目</td>
<td align="center">建议基线</td>
<td align="center">使用限制</td>
</tr>
<tr>
<td align="left">框架</td>
<td align="left">侧栏220—240px；顶部56—64px</td>
<td align="left">窄屏折叠导航；状态行可换行，不裁掉时间和数据状态</td>
</tr>
<tr>
<td align="left">间距</td>
<td align="left">页边20—24px；区块12—16px</td>
<td align="left">基于4px间距步进；移动端建议16px页边距</td>
</tr>
<tr>
<td align="left">表格与抽屉</td>
<td align="left">PC默认行高36px；抽屉360—420px</td>
<td align="left">长内容展开，不强制单行裁切关键状态；P1舒适密度44px；手机详情全屏</td>
</tr>
<tr>
<td align="left">字体</td>
<td align="left">中文系统无衬线；正文14px；辅助12px；标题20—24px</td>
<td align="left">数字用等宽数字特性tnum，列右对齐；字体加载失败使用系统回退，不依赖付费字体</td>
</tr>
<tr>
<td align="left">形状</td>
<td align="left">圆角6／10／14px，细边框</td>
<td align="left">不同组件使用固定档位；不以大量阴影替代信息层级</td>
</tr>
</table>

以下是本项目拟定的设计变量，不是从参考站点提取的CSS，也尚未通过对比度验收。颜色、字号和响应式组合须设计评审及实际渲染验证后冻结；低对比色只能用于非关键信息或改色，不豁免正文可读性。

<table>
<tr>
<td align="center">语义变量</td>
<td align="center">P0浅色候选</td>
<td align="center">P1深色候选</td>
<td align="center">约束</td>
</tr>
<tr>
<td align="left">页面／表面／浮层</td>
<td align="left">#F6F8FA／#FFFFFF／#F2F5F8</td>
<td align="left">#0B1017／#141B24／#1B2430</td>
<td align="left">区分工作区，不依赖阴影</td>
</tr>
<tr>
<td align="left">边框／主要文字</td>
<td align="left">#E5EAF0／#111827</td>
<td align="left">#283442／#F3F6FA</td>
<td align="left">装饰线不替代控件可识别边界</td>
</tr>
<tr>
<td align="left">次要／辅助文字</td>
<td align="left">#4B5563／#6B7280</td>
<td align="left">#AAB6C4／#778596</td>
<td align="left">关键日期与风险不使用弱化文字</td>
</tr>
<tr>
<td align="left">操作／AI点缀</td>
<td align="left">#2563EB／#0891B2</td>
<td align="left">#3B82F6／#0891B2</td>
<td align="left">AI颜色只标来源类型，不代表正确性</td>
</tr>
<tr>
<td align="left">上涨／下跌</td>
<td align="left">#D9363E／#11995A</td>
<td align="left">#E5484D／#18A058</td>
<td align="left">同时显示正负号；文字与图线可分设深浅色</td>
</tr>
<tr>
<td align="left">黄色焦点</td>
<td align="left">不作默认品牌色</td>
<td align="left">#F0B90B，少量可选</td>
<td align="left">不与监管警示混用；需DEC-09确认</td>
</tr>
</table>

六种角色以中文名称和中性描边标签区分，不给六类角色配置六种高饱和色；标签旁单列“候选／证据不足”等证据状态。保存成功采用文字和确认图标，未知采用明确问号与原因，操作失败采用错误图标与说明；即使脱色也能区分。监管状态不等于涨跌方向，红色行情数字不等于正式监管事件。

### 4.7 七页布局与组件行为

<table>
<tr>
<td align="center">页面</td>
<td align="center">首屏与主工作区</td>
<td align="center">主要动作与必要状态</td>
</tr>
<tr>
<td align="left">市场总览</td>
<td align="left">市场范围与快照；少量关键指标；题材排序与涨跌分布</td>
<td align="left">由题材进入候选；缺失展示分母和原因，不用全屏装饰图</td>
</tr>
<tr>
<td align="left">市场消息</td>
<td align="left">时间／来源筛选；消息列表；按需原文与关联面板</td>
<td align="left">新闻、公告、AI推断清晰分开；关联建议可核验，不因摘要隐藏来源</td>
</tr>
<tr>
<td align="left">龙头梯队</td>
<td align="left">题材与角色筛选；梯队矩阵或候选表；证据抽屉</td>
<td align="left">角色标签查看证据；允许同股多标签与无候选，不设计冠军领奖台</td>
</tr>
<tr>
<td align="left">涨幅分析</td>
<td align="left">区间与口径工具栏；排行表；按需展开对比曲线</td>
<td align="left">最多5标的；标明基期、复权、基准和排除原因</td>
</tr>
<tr>
<td align="left">个股详情</td>
<td align="left">身份／快照；行情与角色证据；事件、监管和笔记入口</td>
<td align="left">抽屉只做摘要，明确按钮进入完整详情；不依赖双击</td>
</tr>
<tr>
<td align="left">监管观察</td>
<td align="left">数据完整性、数值计算、正式事件三个独立区域</td>
<td align="left">查看规则、窗口、原文；未知始终可见，不合并为“安全分”</td>
</tr>
<tr>
<td align="left">我的复盘</td>
<td align="left">记录列表与编辑区；引用证据；验证条件与版本</td>
<td align="left">显式保存与确认；AI草稿旁置；冲突保留输入并支持另存</td>
</tr>
</table>

- **表格**：证券名称与代码固定识别列，列头含单位；排序方向、已选条件与结果数量可见。空值显示“—”并可查原因，不用0替代；涨幅、成交额按列统一精度，排序按原始值，完整精度可查。
- **抽屉与导航**：全局最多一个个股抽屉，内部按钮不触发行点击；完整详情是独立入口。Esc关闭后焦点回到触发处，返回列表恢复筛选、分页及可恢复的滚动位置；笔记未保存时先确认离开。
- **图表**：有标题、单位、图例、日期与口径；颜色之外使用线型或标记区分曲线，并提供对应数据表。缺失点不补0、不无提示跨缺口连线；不展示无历史依据的拟真行情。
- **反馈与AI**：首次加载用骨架而非假数值；刷新显示旧快照时间；禁用按钮旁说明原因。保存成功短提示之外保留保存时间；冲突、权限失效和风险未知使用持续提示，不能自动消失。AI文字标明草稿／事实／推断，引用可点击定位证据片段。
- **响应式与无障碍**：375／768／1024／1440px逐档验证，宽表只在容器横滚；手机主要操作触控区至少44×44px。200%缩放后仍能完成筛选、读证据和保存；键盘焦点可见，图标按钮有名称，不能只靠hover获得关键内容。
- **动效**：常规过渡建议120—220ms，无循环闪烁和数字跳动装饰；开启减少动态效果后取消非必要位移。加载与断线依据真实状态，不用动画制造实时感。

### 4.8 UI与UX独立验收需求

下表补充第五章，不替换原需求；每条对应同编号AC。A／B／C继续指已有行为示意，尚未绘制的七页视觉与新增P1状态需在设计阶段补充，不把现有示意称为视觉验收通过。

<table>
<tr>
<td align="center">需求ID</td>
<td align="center">优先级</td>
<td align="center">触发／状态</td>
<td align="center">预期行为</td>
<td align="center">验收</td>
</tr>
<tr>
<td align="center">REQ-V01</td>
<td align="center">🔴 P0</td>
<td align="left">进入或切换七页；S01—S05</td>
<td align="left">统一浅色框架；日期、模式、覆盖与来源状态可见</td>
<td align="left">AC-V01：七页同一快照不丢上下文；任意状态均可查版本与完整时间，历史模式不隐藏</td>
</tr>
<tr>
<td align="center">REQ-V02</td>
<td align="center">🔴 P0</td>
<td align="left">阅读行情、角色与风险；A/B，S01、S03—S05</td>
<td align="left">红涨绿跌加符号；角色中性标签；未知与正式事件独立</td>
<td align="left">AC-V02：正、负、零、缺失各有测试；灰度截图仍可识别方向、角色及监管状态，不把缺失显示为0或安全</td>
</tr>
<tr>
<td align="center">REQ-V03</td>
<td align="center">🔴 P0</td>
<td align="left">排序、筛选、横滚列表；A/S01、S03</td>
<td align="left">身份列、单位、排序及结果数明确；统一数字格式</td>
<td align="left">AC-V03：100行测试含长名称、负数、并列与空值；横滚不丢身份，排序用原值，默认50条且空值置后</td>
</tr>
<tr>
<td align="center">REQ-V04</td>
<td align="center">🔴 P0</td>
<td align="left">开关抽屉或返回；A/C，S01、S07</td>
<td align="left">单击预览、显式完整详情；焦点与列表位置恢复</td>
<td align="left">AC-V04：只开一个抽屉；内部按钮不误触；Esc回焦点；返回保留条件，未保存离开有确认且输入不丢</td>
</tr>
<tr>
<td align="center">REQ-V05</td>
<td align="center">🔴 P0</td>
<td align="left">比较走势；S01、S03、S04</td>
<td align="left">图表有口径与等价数据表，缺失明确</td>
<td align="left">AC-V05：1及5标的正常，第6个被提示阻止；鼠标、触控与键盘均能读数；缺口不造值，切快照不混版本</td>
</tr>
<tr>
<td align="center">REQ-V06</td>
<td align="center">🔴 P0</td>
<td align="left">键盘、手机或缩放浏览；S01—S07</td>
<td align="left">关键操作可达，移动端不缩小整页</td>
<td align="left">AC-V06：四档视口和200%缩放完成主流程；普通文字对比度≥4.5:1，关键控件与焦点≥3:1；触控主入口≥44×44px，无焦点陷阱</td>
</tr>
<tr>
<td align="center">REQ-V07</td>
<td align="center">🔴 P0</td>
<td align="left">加载、刷新与减少动态效果；A/B，S02、S04</td>
<td align="left">只展示真实进度状态，非必要动效可消除</td>
<td align="left">AC-V07：减少动态效果时无非必要位移；断网不假装刷新成功；旧数据时间不随动画更新，无循环闪烁</td>
</tr>
<tr>
<td align="center">REQ-V08</td>
<td align="center">🔴 P0</td>
<td align="left">引用、保存与异常提示；B/C，S04—S07</td>
<td align="left">证据可定位；关键警告持续；保存结果可复查</td>
<td align="left">AC-V08：引用跳到匹配片段，失效时说明；冲突或权限提示不自动消失；读屏能获知保存结果，不把草稿渲染为已确认结论</td>
</tr>
<tr>
<td align="center">REQ-V09</td>
<td align="center">🟡 P1</td>
<td align="left">主动切换主题；S01—S07，设计阶段补图</td>
<td align="left">浅色／深色可选；显式选择优先于系统偏好</td>
<td align="left">AC-V09：刷新保留偏好；两主题均通过V02/V06；所有图表、浮层与导出预览可读，切换不改业务值</td>
</tr>
<tr>
<td align="center">REQ-V10</td>
<td align="center">🟡 P1</td>
<td align="left">调整列表密度；S01、S03，设计阶段补图</td>
<td align="left">紧凑36px／舒适44px，长内容可扩展</td>
<td align="left">AC-V10：切换不改筛选、排序和选择；刷新可恢复；手机仍满足触控尺寸，不因紧凑模式裁掉关键状态</td>
</tr>
<tr>
<td align="center">REQ-V11</td>
<td align="center">🟡 P1</td>
<td align="left">Ctrl/Cmd+K或点击搜索；S01、S03、S05，设计阶段补图</td>
<td align="left">命令面板检索股票、题材和允许访问的页面</td>
<td align="left">AC-V11：中文输入法组合态不误触；空结果不猜内容；Esc关闭并回焦点；无越权结果，不触发交易或隐式保存</td>
</tr>
</table>

### 4.9 设计辅助与交付边界

[UI/UX Pro Max项目](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)可作为设计规则与方案生成的辅助，不是运行时组件库，也不提供行情、量化或交易能力；是否使用不阻塞P0。目前未安装，本次增补不授权安装、执行外部脚本或上传私有数据。

如后续采用，应核验来源、固定版本与许可，审查脚本、依赖和联网行为；不向外部设计服务传入密钥、私人笔记或未授权行情。统一维护全局设计规则与页面差异，生成结果须经设计、数据和测试复核；同名技能不代表与上游仓库完全一致。

先以市场总览、龙头梯队和个股抽屉做视觉样板，覆盖正常、空、过期、无权限；确认后扩展七页与S01—S07。交付内容包括设计变量、组件变体、四档响应式、焦点与动效规范，以及REQ/AC/画面状态映射。现有三幅行为示意保持不变；本次只补需求，不声称已完成七页高保真稿。

***

## 五、业务需求

每个需求ID对应同编号的AC验收项；例如REQ-G01对应AC-G01。表内“验收”是必须满足的行为，不是已完成状态。

### 5.1 全局与总览

<table>
<tr><td align="center">需求ID</td><td align="center">优先级</td><td align="center">触发／状态</td><td align="center">预期行为</td><td align="center">验收</td></tr>
<tr><td align="center">REQ-G01</td><td align="center">🔴 P0</td><td align="left">选择交易日；A/S01、S03</td><td align="left">七页使用选定快照；显式选择初版、修订版或当时可见版</td><td align="left">AC-G01：无所选日期数据不跳今日；切页不混入其他快照</td></tr>
<tr><td align="center">REQ-G02</td><td align="center">🔴 P0</td><td align="left">全局搜索；A/S01、S03</td><td align="left">按代码／名称查询股票，题材与消息分组返回</td><td align="left">AC-G02：同名可消歧；历史改名保留证券身份；空结果不猜标的</td></tr>
<tr><td align="center">REQ-G03</td><td align="center">🔴 P0</td><td align="left">筛选、排序、返回；A/S01</td><td align="left">保留日期、题材、角色、排序和分页；来源改变整组重算</td><td align="left">AC-G03：返回列表状态恢复；过期请求不能覆盖新条件结果</td></tr>
<tr><td align="center">REQ-G04</td><td align="center">🔴 P0</td><td align="left">导出结果；S01、S05</td><td align="left">异步导出授权范围内筛选数据和版本元信息</td><td align="left">AC-G04：行数与筛选一致；越权字段不导出；CSV公式注入被转义</td></tr>
<tr><td align="center">REQ-M01</td><td align="center">🔴 P0</td><td align="left">打开总览；S01、S04</td><td align="left">展示指数、市场分布、成交与涨停统计，提供分子分母</td><td align="left">AC-M01：统计同源同池；分母为0显示不适用；缺失有数量</td></tr>
<tr><td align="center">REQ-M02</td><td align="center">🔴 P0</td><td align="left">打开题材；S01、S03</td><td align="left">查看成员、收益中位数、上涨占比、成交变化与事件</td><td align="left">AC-M02：标记成员生效时间和来源；只展示已知成员，不倒灌最新标签</td></tr>
</table>

### 5.2 消息与候选

<table>
<tr><td align="center">需求ID</td><td align="center">优先级</td><td align="center">触发／状态</td><td align="center">预期行为</td><td align="center">验收</td></tr>
<tr><td align="center">REQ-N01</td><td align="center">🔴 P0</td><td align="left">检索消息；S01、S03</td><td align="left">按类型、时间、来源、股票、题材检索，打开许可范围内原文</td><td align="left">AC-N01：每条有发布／获取时间；超出历史截止时点的材料不进入历史分析</td></tr>
<tr><td align="center">REQ-N02</td><td align="center">🔴 P0</td><td align="left">同一事件多篇报道；S01</td><td align="left">原文去重与事件聚类分层保存，可展开报道与更正</td><td align="left">AC-N02：摘要不计新文章；转载不计独立事件；撤回不抹掉历史记录</td></tr>
<tr><td align="center">REQ-N03</td><td align="center">🔴 P0</td><td align="left">核对消息关联；S01、S05</td><td align="left">区分来源标注、人工确认、AI建议；用户可确认或驳回</td><td align="left">AC-N03：驳回保留审计；AI建议未经确认不自动改写正式题材成员</td></tr>
<tr><td align="center">REQ-L01</td><td align="center">🔴 P0</td><td align="left">查看六类角色；A/S01、S03</td><td align="left">按题材与角色独立排序，展示证据、反证、缺失与版本</td><td align="left">AC-L01：同股可多标签；不足可无候选；人工确认不覆盖原规则结果</td></tr>
<tr><td align="center">REQ-L02</td><td align="center">🔴 P0</td><td align="left">查看连板矩阵；S01、S04</td><td align="left">按日期、板高查看连续涨停、断板和触板未封</td><td align="left">AC-L02：N天M板不等于M连板；无涨跌停限制的交易日不计普通涨停板</td></tr>
<tr><td align="center">REQ-L03</td><td align="center">🔴 P0</td><td align="left">比较角色变化；S01、S03</td><td align="left">展示新增、退出、并列与数据导致的不可比状态</td><td align="left">AC-L03：算法版本改变单独标记，不能当成行情驱动的角色变化</td></tr>
</table>

### 5.3 涨幅与个股

<table>
<tr><td align="center">需求ID</td><td align="center">优先级</td><td align="center">触发／状态</td><td align="center">预期行为</td><td align="center">验收</td></tr>
<tr><td align="center">REQ-P01</td><td align="center">🔴 P0</td><td align="left">选择区间；S01、S03</td><td align="left">支持N交易日／自定义起止日，展示实际窗口、价格口径与排除原因</td><td align="left">AC-P01：统一日历；收益不逐日相加；同日0收益；非法范围拒绝查询</td></tr>
<tr><td align="center">REQ-P02</td><td align="center">🔴 P0</td><td align="left">比较标的；S01、S03</td><td align="left">最多5个标的同基期归一化比较，提供收益与回撤</td><td align="left">AC-P02：所有曲线使用同日期；缺失段中断；超限给出明确提示</td></tr>
<tr><td align="center">REQ-S01</td><td align="center">🔴 P0</td><td align="left">打开个股；A/S01、S03</td><td align="left">抽屉汇集行情、题材、角色、消息和风险，可跳完整页</td><td align="left">AC-S01：有OHLC才绘K线；仅收盘数据则画收盘线；不生成假蜡烛</td></tr>
</table>

### 5.4 自选与复盘

<table>
<tr><td align="center">需求ID</td><td align="center">优先级</td><td align="center">触发／状态</td><td align="center">预期行为</td><td align="center">验收</td></tr>
<tr><td align="center">REQ-U01</td><td align="center">🔴 P0</td><td align="left">加入／移出自选；S01</td><td align="left">保存证券、题材上下文和加入时间，移除支持撤销</td><td align="left">AC-U01：重复提交幂等；改名不断关联；跨用户隔离</td></tr>
<tr><td align="center">REQ-U02</td><td align="center">🔴 P0</td><td align="left">保存笔记；C/S06、S07</td><td align="left">填写判断、依据、失效条件、验证日期，绑定快照</td><td align="left">AC-U02：保存后回读一致；失败保留输入；并发冲突不覆盖另一版本</td></tr>
<tr><td align="center">REQ-U03</td><td align="center">🔴 P0</td><td align="left">生成每日复盘；C/S01、S04</td><td align="left">先生成确定性模板，再可选AI增强；确认后形成只读版本</td><td align="left">AC-U03：无AI仍可使用；AI草稿与人工确认版分开；重新生成不覆盖人工文本</td></tr>
<tr><td align="center">REQ-U04</td><td align="center">🔴 P0</td><td align="left">回看历史判断；S01、S03</td><td align="left">并排看原判断和验证期事实，记录支持／削弱／否定／未到期</td><td align="left">AC-U04：主观判断不自动算命中率；验证条件未结构化时由人确认</td></tr>
</table>

笔记标题建议1—100字，正文最多20000字；保存前校验长度并提示，离开未保存页面需确认。数值型验证条件可由程序核对，叙述型条件只生成待确认建议。

***

## 六、指标与角色

计算是确定性的研究规则，AI不参与改写行情、板数、收益或监管阈值；所有参数都要版本化。

### 6.1 收益与样本

```text
N交易日收益 = P(T) / P(T-N) - 1
自定义区间收益 = P(结束日) / P(开始日) - 1
区间超额收益 = 个股区间收益 - 同窗口基准区间收益
归一化曲线 = 100 × P(t) / P(开始日)
最大回撤 = max_t[1 - P(t) / max_(开始日至t) P]
```

P为相同复权因子版本下的收盘价；最大回撤使用收盘序列，不宣称是盘中最大回撤。比率内部按小数存储，展示为百分数，两个收益率相减的差异标为百分点。

- N取1—250的整数，默认20；同一交易所日历确定T与T-N，需要N＋1个日历位置，不按每只股票有成交的行数倒数。
- N日结束日期遇休市，回退至最近交易日；自定义开始日期顺延、结束日期回退，显示调整后的窗口；开始晚于结束时报错。
- 自定义同一有效交易日起止返回0，但停牌、缺价仍不得参与有效排行。
- P0完整区间排名只纳入区间内无停牌、无异常缺价且起止价格有效的股票；排除数量和原因可查，不前移基期凑足数据。
- 比较曲线采用共同窗口；不存在完整共同窗口的标的不加入对比，并解释原因。个股原始图仍可保留已获得的真实片段。
- 复权价格用于研究收益，原始价格与当日有效涨停价用于涨停判断；不能混用。
- 市场指数、题材参考和监管基准是三种不同口径。首期题材参考使用相同窗口有效成员的区间收益中位数，并标“统计参考，非可投资组合”；历史成员必须在观察时点可知。

### 6.2 透明统计

<table>
<tr><td align="center">指标</td><td align="center">定义</td><td align="center">边界</td></tr>
<tr><td align="center">上涨／下跌／平盘</td><td align="left">同一统计池内有效日涨跌幅大于／小于／等于0的证券数</td><td align="left">停牌、缺价及不适用项另外列示</td></tr>
<tr><td align="center">封板率</td><td align="left">收盘仍涨停数／当日曾触及涨停的去重证券数</td><td align="left">同源同池；分母为0记不适用</td></tr>
<tr><td align="center">连板数</td><td align="left">截止当日连续交易日收盘处于有效涨停价的次数</td><td align="left">首期停牌日中断自算连续序列；缺数据时未知；与供应商口径并列不混算</td></tr>
<tr><td align="center">连板晋级率</td><td align="left">昨日至少2连板集合中今日继续封板数／该集合今日有效可交易数量</td><td align="left">停牌、退市等排除项单列；不是首板晋级率</td></tr>
<tr><td align="center">题材成交份额</td><td align="left">个股成交额／同日同题材有效成员成交额之和</td><td align="left">跨题材有重叠，题材成交额不得直接相加当市场总额</td></tr>
<tr><td align="center">成交额变化</td><td align="left">当日成交额／此前5个交易日平均成交额－1</td><td align="left">基期不含当日；历史不足或基期为0记不适用</td></tr>
<tr><td align="center">题材广度</td><td align="left">上涨占比、涨停占比、区间收益中位数分别展示</td><td align="left">缺成员不得假设为0收益；显示覆盖率</td></tr>
</table>

### 6.3 六类角色契约

角色主键为证券×题材×观察时点×规则版本。以下是可实现的证据框架，不是已验证有效的选股公式。

<table>
<tr><td align="center">角色</td><td align="center">必要证据</td><td align="center">候选逻辑</td><td align="center">不成立／降级</td></tr>
<tr><td align="center">情绪龙头</td><td align="left">连板、短期相对强弱、成交关注、封板记录</td><td align="left">比较题材内高度与活跃度，封板质量单独列因子</td><td align="left">缺关键盘中字段只能输出简化候选，不能声称掌握封板质量</td></tr>
<tr><td align="center">趋势龙头</td><td align="left">20／60日相对收益、回撤、持续性</td><td align="left">长期相对强且回撤约束通过，独立排序</td><td align="left">历史不足、复权冲突不评分</td></tr>
<tr><td align="center">中军</td><td align="left">多日成交份额、流通规模、持续活跃</td><td align="left">识别容量与持续成交中心，不只看最大市值</td><td align="left">题材成交覆盖不完整时暂停排名</td></tr>
<tr><td align="center">跟风</td><td align="left">同题材依据、晚于核心启动的时序</td><td align="left">启动滞后且相对核心偏弱，保留观察区间</td><td align="left">仅有相关性时显示跟涨观察，不确认因果</td></tr>
<tr><td align="center">补涨</td><td align="left">前期相对落后、当前转强、共同事件依据</td><td align="left">先落后后转强的两个不重叠窗口验证</td><td align="left">低价、小市值不能单独构成证据</td></tr>
<tr><td align="center">接力龙头</td><td align="left">旧核心转弱、新核心承接和后续持续</td><td align="left">使用连续历史快照和时序证据验证候选</td><td align="left">当日不能预知后续验证结果；P0显示待验证</td></tr>
</table>

- 初始先完成趋势龙头、中军，再接情绪与补涨；跟风与接力入口保留证据不足状态。
- 每类规则配置必需因子、阈值、比较范围、最少样本与失效条件；参数冻结前不能对外显示“正式算法”。最少有效成员建议5只，待算法评审确认。
- 关键因子缺失不得偷偷重分配权重；采用简化算法必须换规则ID并显示其差异。
- 无候选是有效结果；并列保留并列，不能强制每个题材选一只。候选分或分位数不是未来上涨概率。
- 人工确认层与规则层分开存储；确认只表明证据经人检查，不保证收益。更换规则可重算，但旧快照不覆盖。

### 6.4 因子分层与分期

**先用少量可解释因子建立证据，不把所有指标压成一个龙头总分。** 市场状态、角色证据、研究排序、研究资格、交易可达性和监管状态分别保存。风险提示不能抹掉历史角色事实，角色高分也不能抵消硬约束。

[查看在线因子动态调试画板](https://bytedance.larkoffice.com/docx/PojzdqI2coWdaJxDxRxcDKJKnNe)

离线完整包附带画板源文件 `assets/factor_lifecycle.svg`；日常计算与离线发布的边界说明可独立阅读。

<table>
<tr>
<td align="center">阶段</td>
<td align="center">纳入内容</td>
<td align="center">不默认纳入</td>
</tr>
<tr>
<td align="center">P0 盘后基线</td>
<td align="left">日线事实、固定角色规则、因子字典与快照证据</td>
<td align="left">不自动调权；不承诺盘中封单与事件时序；不新增因子管理页面</td>
</tr>
<tr>
<td align="center">P1 授权扩展</td>
<td align="left">合约覆盖的分钟／事件数据、封板时序、核心更替确认</td>
<td align="left">动态权重优化、因子绩效评估不提前到P1</td>
</tr>
<tr>
<td align="center">P2 研究验证</td>
<td align="left">状态识别、条件权重、因子体检、滚动检验与模拟对照</td>
<td align="left">结果先离线或影子运行；不直接接管正式排行，不接实盘</td>
</tr>
</table>

8.2中的涨停生态是字段需求清单，不表示所有字段均为P0必采。P0的收盘涨跌停和连板可用有效日线及对应涨跌停价计算；封板率只有在同源、同池的盘后触板集合确实获授权且完整时才可启用，否则为不可用。首末封、开板回封和同一时点封单放入P1，不能仅凭日线高点推断事件次数和顺序。

### 6.5 核心因子目录

以下是候选计算口径，不是已优化参数或收益结论。建议从目录中选8—12个低重复性因子建立首版固定规则；最终选用项及必需项由DEC-04冻结，未选择项保持停用。日线均线、收益、回撤使用6.1的统一价格与交易日历，历史不足不得缩短窗口冒充同一因子。

<table>
<tr>
<td align="center">因子编号</td>
<td align="center">名称</td>
<td align="center">计算或观察口径</td>
<td align="center">数据前提</td>
</tr>
<tr>
<td align="center">FAC-M01</td>
<td align="center">市场广度</td>
<td align="left">有效上涨数／有效可比数；高于MA20的有效证券数／MA20有效样本数，分别输出</td>
<td align="left">P0日线；同时给应有、有效、合法排除及缺失数</td>
</tr>
<tr>
<td align="center">FAC-M02</td>
<td align="center">市场成交变化</td>
<td align="left">同池当日总成交额／此前20个交易日总成交额均值－1，基期不含当日</td>
<td align="left">P0；池版本变化须可解释，不拼不完整市场总额</td>
</tr>
<tr>
<td align="center">FAC-M03</td>
<td align="center">指数趋势与波动</td>
<td align="left">指数C／MA20－1、C／MA60－1；20日收益率标准差分别输出</td>
<td align="left">P0；基准ID、窗口和非年化口径明确</td>
</tr>
<tr>
<td align="center">FAC-M04</td>
<td align="center">情绪生态</td>
<td align="left">涨跌停数、连板高度、6.2晋级率；昨日涨停集合今日有效收益中位数</td>
<td align="left">P0条件启用；固定昨日集合，披露今日不可评价样本</td>
</tr>
<tr>
<td align="center">FAC-T01</td>
<td align="center">题材扩散</td>
<td align="left">6.2题材广度、成交份额、成员收益中位数；连续活跃成员须先定义活跃阈值</td>
<td align="left">P0已知成员快照；不将跨题材重叠成交累加为市场总额</td>
</tr>
<tr>
<td align="center">FAC-R01</td>
<td align="center">相对强弱</td>
<td align="left">RS_N＝个股N日收益－同窗口市场基准收益，N候选20／60／120；题材差额另列</td>
<td align="left">P0；差值显示百分点，题材参考不替代市场基准</td>
</tr>
<tr>
<td align="center">FAC-R02</td>
<td align="center">均线趋势</td>
<td align="left">C(T)／MA_k(T)－1；MA_k(T)／MA_k(T－5)－1，k候选20／60</td>
<td align="left">P0；k＋5个有效日历位置及完整输入</td>
</tr>
<tr>
<td align="center">FAC-R03</td>
<td align="center">新高与回撤</td>
<td align="left">C(T)是否大于此前60／120个交易日最高收盘价；20／60日窗口收盘最大回撤另列</td>
<td align="left">P0；新高基期不含当日；当前回撤与最大回撤不混称</td>
</tr>
<tr>
<td align="center">FAC-C01</td>
<td align="center">容量与承接</td>
<td align="left">截至T的20日平均成交额、题材成交份额、换手稳定性、可获得的流通规模</td>
<td align="left">P0；自由流通与普通流通口径不同，字段不足不替代</td>
</tr>
<tr>
<td align="center">FAC-C02</td>
<td align="center">量价配合</td>
<td align="left">6.2成交额变化；窗口内上涨日与下跌日平均成交额分别观察</td>
<td align="left">P0；无某类样本时为不适用，不除零</td>
</tr>
<tr>
<td align="center">FAC-L01</td>
<td align="center">跟随与补涨</td>
<td align="left">固定历史核心后的相对收益差、已知启动滞后；前期补涨缺口＝题材收益中位数－个股收益</td>
<td align="left">P0日级候选；当前激活窗口与前期窗口不重叠</td>
</tr>
<tr>
<td align="center">FAC-N01</td>
<td align="center">消息证据</td>
<td align="left">来源、发布／首次可用时间、原文关联、新颖事件标记和反证分别记录</td>
<td align="left">P0原文证据；自动情绪／新颖度模型另评估，不默认加入数值评分</td>
</tr>
</table>

计数和占比按已定义统计池计算，有效分母与质量覆盖分母分别披露。异常缺失可以支持标注清楚的部分观察，不能通过缩小分母获得正式全市场或题材排名资格。样本标准差的自由度、停牌处理、极值处理、窗口闭区间均需进入因子定义，不能依赖计算库默认值。

<table>
<tr>
<td align="center">角色</td>
<td align="center">主导证据</td>
<td align="center">扩展与限制</td>
</tr>
<tr>
<td align="center">情绪龙头</td>
<td align="left">连板高度、短期相对强弱、成交关注、题材扩散及催化</td>
<td align="left">P1再核验首次封板、回封与封单；最高板不是充分条件</td>
</tr>
<tr>
<td align="center">趋势龙头</td>
<td align="left">FAC-R01—R03、量价配合、持续性与回撤</td>
<td align="left">先20／60日；120日和K线结构按样本与研究需要启用；CZSC仍属P2可选实验</td>
</tr>
<tr>
<td align="center">中军</td>
<td align="left">FAC-C01、多日题材成交份额、容量和稳定性</td>
<td align="left">大市值不是充分条件；Beta／相关性可作P2辅助，不能替代成交中心证据</td>
</tr>
<tr>
<td align="center">跟风</td>
<td align="left">同题材证据、相对核心落后、启动滞后和催化检索结果</td>
<td align="left">“未找到独立催化”不等于不存在；相关性不证明带动，历史核心不能事后择优</td>
</tr>
<tr>
<td align="center">补涨</td>
<td align="left">FAC-L01前期落后＋当前放量／突破激活＋真实题材关联</td>
<td align="left">缺口大不是买入依据；必须有两个不重叠窗口，低价或小市值不能单独成立</td>
</tr>
<tr>
<td align="center">接力龙头</td>
<td align="left">旧核心转弱、新核心承接、题材延续和后续确认</td>
<td align="left">沿用6.3新老交接定义；高板延续另作观察标签，不扩大接力角色含义；P1补时序验证</td>
</tr>
</table>

辅助背景包括财报、估值、业务关联、减持等已披露事件；按照实际披露和首次可用时间接入，不提前使用报告期结束时尚未公布的财报。北向、机构持仓和资金流口径另行核验，不能假定实时可得。上市／ST／停牌、流动性门槛、质量和监管按各自规则保存，不作为可被加权抵消的负分项。

### 6.6 因子数据契约

沿用13.1核心对象，扩展以下版本信息；这是计算和研究产物契约，不新增数据库平台或管理Web。

- 因子定义：`factor_id`、`factor_version`、公式、原始单位、方向、频率、窗口、股票池与基准、最低历史、合法排除、缺失规则、来源权利和生效时间。
- 因子观测：实体／题材、`as_of`、`available_at`、`snapshot_id`、原始值、标准化值、质量、样本量、覆盖率、输入版本和失败原因；只有在观察截止前实际可用的输入才可进入当时可见计算。
- 角色结果：固定角色规则版与因子贡献；P2研究排序另存`regime_version`、`weight_set_version`、状态判定依据和研究分，不冒用原角色分字段。
- 实验与发布：`experiment_id`关联数据、因子、标准化、标签、状态、权重、成本、代码／依赖和评审记录；`effective_from`不得早于批准生效时间。回滚保留原发布历史和笔记引用。
  标准化建议先用同一已知样本横截面分位数，并保留原值；并列使用平均秩，最低样本在实验前冻结。量纲、正负方向、极值裁剪、对数处理和中性化均有独立版本。需要拟合的缩放参数只从训练窗口估计，不能利用全历史分布；缺失必需因子输出不可计算，缺失可选因子也不得静默重分配权重，简化配置须另有规则ID。

***

## 七、监管观察

监管结果独立于龙头角色，数据完整也不能证明“无监管风险”；本系统只展示公开信息与规则计算，不掌握非公开监管全貌。

### 7.1 状态分层

页面同时展示三个维度，不压缩成一个分数：数据完整性、数值计算状态、正式事件／限制状态。

- 数值计算：待核验、未触发、自设接近预警、计算触发。
- 正式事件：尚未获取相关披露、已披露异常／严重异常事件、公开监控信息等，保留原文。
- 实际限制：按披露显示对象、范围、起止时间；证券停牌与特定账户限制不可混为一谈。

“接近”是产品自设提醒，不是法定状态；预警比例可配置，未经评审默认关闭。数值触发不等于自动停牌，窗口重置也不自动解除正式限制。

### 7.2 监管需求

<table>
<tr><td align="center">需求ID</td><td align="center">优先级</td><td align="center">触发／状态</td><td align="center">预期行为</td><td align="center">验收</td></tr>
<tr><td align="center">REQ-R01</td><td align="center">🔴 P0</td><td align="left">匹配监管规则；S01、S05</td><td align="left">按交易所、板块、证券状态和生效区间匹配已复核版本</td><td align="left">AC-R01：无匹配、冲突或未验收时待核验，不套其他板块规则</td></tr>
<tr><td align="center">REQ-R02</td><td align="center">🔴 P0</td><td align="left">计算数值；B/S01、S05</td><td align="left">展示窗口、基准、阈值、计算值和重置依据</td><td align="left">AC-R02：基准缺失停算；同一滚动命中不重复计正式事件；临界值按规则处理</td></tr>
<tr><td align="center">REQ-R03</td><td align="center">🔴 P0</td><td align="left">查看正式披露；S01、S03</td><td align="left">按原文区分异常事件、监控和实际限制，列有效时间</td><td align="left">AC-R03：重复公告关联同一事件；账户限制不扩展成全市场禁止交易</td></tr>
<tr><td align="center">REQ-R04</td><td align="center">🔴 P0</td><td align="left">管理员启用规则；S01、S05</td><td align="left">绑定官方来源和回归样例，记录编制／复核及发布记录</td><td align="left">AC-R04：测试未通过无法启用；撤回只停用新计算，不删除旧结论</td></tr>
</table>

### 7.3 规则配置与禁区

规则版本至少包含：适用交易所／板块／证券状态、生效与失效日期、计算窗口、方向和阈值、基准指数、普通异动计数方式、重算条件、官方原文、复核记录、测试样例。

普通异动、严重异动、风险提示、窗口重算和交易限制分别保存；公告发布时间与事件所覆盖的交易区间分别记录。上线前核对当时有效的官方规则，本PRD不代替法律规则清单。

禁止展示“安全再涨几个板”、保证不被监管或规避监管的操作建议。P0不管理真实或模拟仓位，监管模块只提示研究观察限制。

***

## 八、数据服务

采用一个主行情源、一个题材体系及获授权的消息渠道；其他来源用于核验，不能把不同口径拼成单一指标。

### 8.1 来源候选

- 同花顺iFinD可作为正式接口候选，其手册区分实时行情、分钟行情和历史查询；常规接口与逐笔产品不能等同。实际权限、部署方式与展示范围需书面确认。\[^1][^2]
- 东方财富Choice提供数据服务和量化API；A股实时范围、延迟及服务器部署权限以实际试用和合同为准，不把网页请求地址当成授权API。[^3]
- Tushare可作个人日频研究候选；其日线未复权，停牌不提供行，新涨跌停列表不提供ST统计。服务协议限制用途，不能默认对外Web再分发。[^4][^5][^6]
- 公告批量接入可核验深证信／巨潮数据服务；交易所和公司正式披露用于监管原文核对。公开可查看不等于获准全文转载或批量存储。[^7]

AI可使用什么数据需单独确认，不能从“允许个人查看”推导出“允许发送给外部模型”。本轮对话查询能力、截图和私人订单页都不是网站生产数据源。

### 8.2 数据清单

<table>
<tr><td align='center'>数据域</td><td align='center'>首期必需字段</td><td align='center'>关键边界</td></tr>
<tr><td align='center'>证券与日历</td><td align='left'>证券标识、名称历史、交易所、板块、上市／退市、ST、停复牌、交易日历</td><td align='left'>历史状态而非当前状态；无状态不进入角色池</td></tr>
<tr><td align='center'>行情与指数</td><td align='left'>OHLC、成交量／额、涨跌幅、复权因子、基准指数日线</td><td align='left'>价格版本和单位；交易日填充不能伪装成交</td></tr>
<tr><td align='center'>规模与流动性</td><td align='left'>流通市值、总市值、换手率、量比及适用口径</td><td align='left'>量比等字段有独立定义，不自行按名称猜公式</td></tr>
<tr><td align='center'>涨停生态</td><td align='left'>有效涨跌停价、收盘封板、盘中触板、首末封、炸板次数、封单时点</td><td align='left'>缺盘中字段不从日线推造；封单需同一时点比较</td></tr>
<tr><td align='center'>题材成员</td><td align='left'>分类ID、成员、加入／退出、信息可用时间、来源和人工修订</td><td align='left'>无法取得历史成员时，历史题材分析禁用</td></tr>
<tr><td align='center'>消息与公告</td><td align='left'>原文ID、标题、许可摘要／正文、URL、发布／首次获取时间、修订记录</td><td align='left'>文章数和事件数分开；标题不能当作完整正文</td></tr>
<tr><td align='center'>监管</td><td align='left'>规则原文、有效区间、事件类型、公告ID、对象及限制范围</td><td align='left'>未披露不等于不存在；正式状态不靠AI推断</td></tr>
</table>

### 8.3 数据运营需求

<table>
<tr><td align='center'>需求ID</td><td align='center'>优先级</td><td align='center'>触发／状态</td><td align='center'>预期行为</td><td align='center'>验收</td></tr>
<tr><td align='center'>REQ-D01</td><td align='center'>🔴 P0</td><td align='left'>来源配置；S01、S05</td><td align='left'>管理授权、字段映射、到期时间、限额、服务器部署及AI使用许可</td><td align='left'>AC-D01：缺授权禁止启用；Token不在页面、日志或导出出现</td></tr>
<tr><td align='center'>REQ-D02</td><td align='center'>🔴 P0</td><td align='left'>历史补齐／增量采集；S02、S04</td><td align='left'>原始留档、分页续传、限频退避、任务幂等和失败重试</td><td align='left'>AC-D02：重复任务无重复行；缺页批次不能伪装完整</td></tr>
<tr><td align='center'>REQ-D03</td><td align='center'>🔴 P0</td><td align='left'>发布／修订；S01、S04</td><td align='left'>校验通过后发布不可变快照；修订新建版本并提示影响范围</td><td align='left'>AC-D03：旧复盘仍绑定旧版；中断发布不产生半新半旧页面</td></tr>
<tr><td align='center'>REQ-D04</td><td align='center'>🔴 P0</td><td align='left'>管理员处理故障；S04</td><td align='left'>查看批次、缺失行、重跑范围；支持关闭模块与回退快照</td><td align='left'>AC-D04：操作有审计；回退不清空自选、笔记和人工确认</td></tr>
</table>

首次接入建议先用少量股票和若干交易日核对字段，再补到许可范围内的历史。为支持最长250交易日收益，初始历史建议覆盖至少251个交易日历位置及对应有效价格；实际不足须显示覆盖起点，不能承诺全历史。60日角色等要求也必须满足自身样本门槛。首次系统留档之前的回补数据可用于事后研究，但不能冒充该系统当时已经可见的快照。

***

## 九、质量与时效

实时性与准确性分别度量；系统的目标是及时识别问题并限制误导性输出，不承诺永不出错。

### 9.1 时间标准

保留源事件时间、供应商发布时间（若有）、首次接收时间、计算完成时间、快照发布时间和页面更新时间。服务器统一UTC存储、北京时间展示，并保留原始时区；缺少可靠源时间时，只能描述采集频率，不能证明端到端实时。

- P0默认展示最新已发布盘后快照，未生成当日批次时显示“当日待更新”，不把上一日标成今日。
- 在上游按约就绪条件下，盘后初版目标18:30前发布；18:30后仍不满足质量门槛则保留待更新并告警，不强制发布。该时刻是产品目标，需接口试用确认。
- 消息按授权配置15—30分钟量级增量检查，标“定期更新”。公告、龙虎榜、财报各按其披露／入库节奏更新，不与行情假定同步。
- P1实时行情需供应商时间戳、心跳和实测延迟；阈值由合同SLA和测试制定，没冻结前不能启用“实时”标识。
- 股票无成交并不必然断流，要结合行情消息时间、心跳和横截面更新判断。休市和午间暂停按交易日历及交易时段处理。

### 9.2 质量需求

<table>
<tr><td align='center'>需求ID</td><td align='center'>优先级</td><td align='center'>触发／状态</td><td align='center'>预期行为</td><td align='center'>验收</td></tr>
<tr><td align='center'>REQ-Q01</td><td align='center'>🔴 P0</td><td align='left'>展示／监控时效；B/S01、S04</td><td align='left'>展示各域截至时间和延迟，记录端到端及各阶段耗时</td><td align='left'>AC-Q01：页面刷新不改变源时间；源时间未知不标实时</td></tr>
<tr><td align='center'>REQ-Q02</td><td align='center'>🔴 P0</td><td align='left'>数据校验；S01、S04</td><td align='left'>字段、单位、唯一性、价格逻辑、证券状态、分页与覆盖校验</td><td align='left'>AC-Q02：高低价倒置、负成交量和重复键进入隔离；停牌不填0</td></tr>
<tr><td align='center'>REQ-Q03</td><td align='center'>🔴 P0</td><td align='left'>抽样核验；S01、S04</td><td align='left'>同时间、单位、复权与统计池比较；保留差异及处理结论</td><td align='left'>AC-Q03：不平均冲突值，不拼不同来源的分子分母</td></tr>
<tr><td align='center'>REQ-Q04</td><td align='center'>🔴 P0</td><td align='left'>受影响模块降级；B/S04、S05</td><td align='left'>显示最后有效版本和影响范围，阻断受污染的新排名／提醒</td><td align='left'>AC-Q04：题材成交缺失停发中军排行，规则缺失显示待核验</td></tr>
</table>

数据覆盖率分母为所选统计池中当日应有该类记录的证券数，停牌等合法无记录需以可验证状态排除；未知状态不得用于缩小分母。首期正式市场汇总要求预期集合全部核对，不完整可发布明确标记的部分浏览版，但不得称全市场完整排行。题材正式排名要求该题材必要数据完整且满足最少样本，否则只提供部分样本浏览。

### 9.3 非功能目标

下列数值均为待评审的验收目标，不是已测性能。

<table>
<tr><td align='center'>维度</td><td align='center'>目标</td><td align='center'>测试口径</td></tr>
<tr><td align='center'>普通查询</td><td align='left'>20并发下P95≤2秒；超5秒长查询转异步</td><td align='left'>记录股票池、历史深度、硬件、缓存冷热和网络</td></tr>
<tr><td align='center'>笔记保存</td><td align='left'>请求成功后回读一致；失败不丢输入</td><td align='left'>本机所有者场景下的断网、重试、并发编辑和操作上下文失效；匿名访客不开放云端保存</td></tr>
<tr><td align='center'>模块隔离</td><td align='left'>AI或单源失败不影响历史浏览与笔记</td><td align='left'>故障注入、来源超时和质量开关</td></tr>
<tr><td align='center'>备份恢复</td><td align='left'>用户内容建议RPO≤1小时、RTO≤4小时</td><td align='left'>隔离环境恢复演练；最终依部署成本确认</td></tr>
<tr><td align='center'>告警</td><td align='left'>关键批次失败／质量阻断后5分钟内通知值守角色</td><td align='left'>检测、投递、确认和恢复时间均留档</td></tr>
<tr><td align='center'>兼容体验</td><td align='left'>375px手机和1440px桌面主路径可完成</td><td align='left'>键盘、空态、错误态和横向滚动测试</td></tr>
</table>

断线、乱序、迟到消息按源序列或事件键去重；接收顺序不等于事件顺序。重试采用限次退避，达到限额进入失败状态由管理员处理，不无限重试消耗额度。授权到期时停止新采集及不被许可的展示，历史存储是否可保留按合同执行。

***

## 十、AI能力

AI读取经过校验、允许用于模型处理的事实，输出有来源的草稿；默认不训练自己的大模型，不让AI重新发明价格、规则或排名。

### 10.1 功能与验收

<table>
<tr><td align='center'>需求ID</td><td align='center'>优先级</td><td align='center'>触发／状态</td><td align='center'>预期行为</td><td align='center'>验收</td></tr>
<tr><td align='center'>REQ-A01</td><td align='center'>🔴 P0</td><td align='left'>摘要授权文本；S01、S03</td><td align='left'>提取事件、主体、事实、不确定项和引用；只有标题时标标题级摘要</td><td align='left'>AC-A01：无正文不声称全文解读；关键事实可定位具体原文片段</td></tr>
<tr><td align='center'>REQ-A02</td><td align='center'>🔴 P0</td><td align='left'>生成题材建议；S01、S05</td><td align='left'>建议消息—题材—证券关联，区分直接证据和推断，等待确认</td><td align='left'>AC-A02：证券ID须在主数据存在；未经审核不修改正式成员池</td></tr>
<tr><td align='center'>REQ-A03</td><td align='center'>🔴 P0</td><td align='left'>解释候选；A/S01、S04</td><td align='left'>引用因子、反证、缺口和失效条件，不改变规则输出</td><td align='left'>AC-A03：引用数字必须匹配快照；错配输出被拦截并回退结构化证据</td></tr>
<tr><td align='center'>REQ-A04</td><td align='center'>🔴 P0</td><td align='left'>增强复盘；C/S01、S04</td><td align='left'>生成事实、解释、待验证事项分层草稿，人工编辑确认</td><td align='left'>AC-A04：模型失败仍有模板；晚到响应不覆盖用户已编辑内容</td></tr>
<tr><td align='center'>REQ-A05</td><td align='center'>🔴 P0</td><td align='left'>模型发布／升级；S01、S05</td><td align='left'>固定评测集比较事实一致、引用、拒答、延迟与成本，支持回退</td><td align='left'>AC-A05：安全硬失败阻断上线；模型／提示词／检索版本可追溯</td></tr>
</table>

### 10.2 输入输出契约

输入是选定快照、获许可的原文片段、用户授权的相关笔记和明确任务，不默认把整个数据库发送给模型。先用证券ID、题材、时间筛选，再按关键词或向量检索；向量库不是第一版必备。

输出至少包含以下结构，所有标识只能从输入材料中引用：

```json
{
  "task_id": "task_demo",
  "snapshot_id": "snapshot_demo",
  "status": "draft",
  "facts": [],
  "inferences": [],
  "supporting_evidence_ids": [],
  "opposing_evidence_ids": [],
  "missing_evidence": [],
  "citations": [],
  "model_version": "version_from_server",
  "prompt_version": "version_from_server"
}
```

每个fact需要证据ID与片段位置；inferences不能混进facts。引用检查不仅验证URL存在，还要检查材料是否支持所述主体、日期和数字。数值优先引用后端字段ID，再由模板渲染，减少模型抄错数字。

### 10.3 历史、权限与降级

- 当时可见模式以首次可用时间和当时已冻结快照为界；源发布时间用于核对，不能用后来获取的材料冒充当时已知。事后修订模式单独标识。
- 现在的大模型可能掌握历史截止时点之后的知识，RAG不能消除这一风险。历史重生成只允许引用检索证据并标“事后生成解释”；P0不得把这类文本用于证明当时可交易的AI信号。P2使用AI信号需另验证时间污染。
- 新闻正文和检索片段均是不可信输入；其中的命令、外链指令或要求读取凭证的内容不能成为工具调用。
- 模型权限限只读检索、调用白名单计算函数和生成草稿；写笔记、确认关联由用户动作触发的受控服务执行，不能让模型直连数据库写SQL。
- 每任务设置Token、并发、超时和费用上限；同一快照＋材料版本＋模型版本＋提示词版本可复用缓存，私有笔记缓存按用户隔离。
- 建议60秒提示处理中，120秒结束本次等待并交付模板；后台迟到结果可另存草稿，但必须重新提示用户，不能覆盖已保存文本。
- schema不合法可尝试一次受控修复；仍失败、越权、引用错误或触及预算上限时回退模板，不无限重试。
- 原文撤回或授权收回后，相关AI输出标记待复核或按许可下线；引用材料是否留存以合同和隐私规则为准。

### 10.4 AI评测标准

首期建立建议不少于100条人工标注样本，包含澄清、同名公司、转载、数值单位、时间错位、无正文、反向证据及提示注入，分离开发集与冻结测试集。

评测维度：原子事实支持率、实体匹配率、引用覆盖率、未知正确拒答率、人工采纳／编辑／驳回比例、单任务费用和P95耗时。事实支持率建议目标≥95%，展示数字一致率要求100%；出现越权调用、假监管认定、未来材料冒充历史事实等硬失败，即使平均分达标也不得发布。百分比是待评测目标，不是模型能力声明。

***

## 十一、研究与回测

P1扩展盘中研究，P2再验证交易策略；网页候选识别准确不等于策略能够盈利。

### 11.1 P1扩展

<table>
<tr><td align='center'>需求ID</td><td align='center'>优先级</td><td align='center'>触发／前置</td><td align='center'>预期行为</td><td align='center'>验收</td></tr>
<tr><td align='center'>REQ-E01</td><td align='center'>🟡 P1</td><td align='left'>实时授权及延迟实测通过</td><td align='left'>实时快照、分钟K、规则异动提醒，区分暂态与终态</td><td align='left'>AC-E01：按事件键去重、重连补数；过期数据不触发新提醒</td></tr>
<tr><td align='center'>REQ-E02</td><td align='center'>🟡 P1</td><td align='left'>龙虎榜明细授权可用</td><td align='left'>披露原因、买卖金额、官方席位名与历史记录</td><td align='left'>AC-E02：席位别名单独标来源，不推断真实账户持仓</td></tr>
<tr><td align='center'>REQ-E03</td><td align='center'>🟡 P1</td><td align='left'>去重语料及标签稳定</td><td align='left'>词云、主题频次、消息情绪及原文下钻</td><td align='left'>AC-E03：计数可回查；情绪标签不等同股价预测</td></tr>
<tr><td align='center'>REQ-E04</td><td align='center'>🟡 P1</td><td align='left'>历史题材和时序数据可用</td><td align='left'>题材演进、核心更替、跟风与接力验证</td><td align='left'>AC-E04：后续验证只能在后续时点出现，旧判断保留</td></tr>
<tr><td align='center'>REQ-E05</td><td align='center'>🟡 P1</td><td align='left'>自然语言查询</td><td align='left'>转为白名单结构化筛选，显示日期、阈值、基准，经确认后执行</td><td align='left'>AC-E05：模糊词需澄清；参数校验失败不执行；不生成任意SQL</td></tr>
</table>

P1提醒是应用内消息，站外推送须单独授权；用户能启停规则、设置冷却时间并查看触发依据。重连补到的旧事件标“补录”，不伪装成当前异动。数据质量恢复不自动重发全部历史提醒。

### 11.2 P2需求

<table>
<tr><td align='center'>需求ID</td><td align='center'>优先级</td><td align='center'>触发／前置</td><td align='center'>预期行为</td><td align='center'>验收</td></tr>
<tr><td align='center'>REQ-B01</td><td align='center'>🔵 P2</td><td align='left'>冻结研究配置后运行回测</td><td align='left'>记录股票池、信号时间、调参／检验区间、基准、成本和成交模型</td><td align='left'>AC-B01：防未来信息及幸存者偏差；输出样本外表现、成本敏感性和不可成交比例</td></tr>
<tr><td align='center'>REQ-B02</td><td align='center'>🔵 P2</td><td align='left'>模拟组合与对照研究</td><td align='left'>模拟买卖、仓位和风险限制；对比无AI／有AI与不同规则版本</td><td align='left'>AC-B02：同数据同成本；T+1、停牌及涨跌停约束生效；无真实委托</td></tr>
</table>

收盘后产生的信号不能默认按同日收盘价成交；停牌、涨停买不到、跌停卖不出和交易费用必须显式建模。无逐笔数据不宣称还原队列撮合。研究至少报告收益、超额、回撤、换手、成本、成交率和阶段稳定性，不能只报最高收益曲线。

### 11.3 框架借鉴与复用

**采用模块化思路，不整体搬入交易平台。** 当前基线仍是轻量后端、授权数据适配、不可变快照和确定性角色规则；更换框架不能改变证券身份、时间口径、证据、历史版本和监管三维状态。下表是基于上一轮官方资料核验形成的设计参考，不是依赖安装清单、性能测评或已批准选型。

<table>
<tr>
<td align="center">项目与来源</td>
<td align="center">借鉴点</td>
<td align="center">本项目转化方式</td>
<td align="center">阶段与边界</td>
</tr>
<tr>
<td align="left">[LEAN](https://github.com/QuantConnect/Lean)</td>
<td align="left">股票池、信号、组合、执行、风控分层</td>
<td align="left">候选识别与组合决策、撮合、风控分开；P0不创建真实订单</td>
<td align="left">P0借鉴边界；P2评估引擎；A股制度另行适配</td>
</tr>
<tr>
<td align="left">[RQAlpha](https://rqalpha.readthedocs.io/zh-cn/develop/development/basic_concept.html)</td>
<td align="left">DataSource、Broker、PriceBoard、前置校验与交易成本扩展</td>
<td align="left">把交易日历、T+1、停牌、有效涨跌停价与税费从角色评分中剥离</td>
<td align="left">P2候选；商用授权待确认，不默认可商用</td>
</tr>
<tr>
<td align="left">[VeighNa / vn.py](https://github.com/vnpy/vnpy)</td>
<td align="left">事件驱动、网关、数据适配、策略和风控模块</td>
<td align="left">数据、计算、发布事件职责清晰；同一事件重放不重复发布快照</td>
<td align="left">P0借鉴解耦；券商网关与实盘仍在范围外</td>
</tr>
<tr>
<td align="left">[Qlib](https://qlib.readthedocs.io/en/latest/component/workflow.html)</td>
<td align="left">配置化研究、实验记录、模型与分析流程</td>
<td align="left">P0保留规则和快照版本；P2补齐实验配置、分段检验和可复现报告</td>
<td align="left">不是大模型聊天平台；不把示例Alpha直接当龙头定义</td>
</tr>
<tr>
<td align="left">[Alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded)</td>
<td align="left">因子分组收益、IC、换手和分组分析</td>
<td align="left">为情绪、趋势、容量与补涨特征输出独立的因子体检</td>
<td align="left">P2；因子统计收益不等于可成交策略收益</td>
</tr>
<tr>
<td align="left">[vectorbt](https://github.com/polakowo/vectorbt)</td>
<td align="left">向量化研究与批量参数扫描</td>
<td align="left">比较窗口、阈值、权重和成本敏感性，保留完整试验结果</td>
<td align="left">P2候选；免费版与PRO分开，附加许可限制需审查</td>
</tr>
<tr>
<td align="left">[Freqtrade前视检测](https://www.freqtrade.io/en/latest/lookahead-analysis/)、[递归检测](https://www.freqtrade.io/en/latest/recursive-analysis/)</td>
<td align="left">前视偏差与启动历史长度稳定性检查</td>
<td align="left">按截止时点重算并对照结果，检测未来数据污染及窗口敏感性</td>
<td align="left">P0质量思想、P2策略检验；不引入加密交易执行模块</td>
</tr>
<tr>
<td align="left">[OpenBB ODP](https://openbb.co/products/odp/)</td>
<td align="left">供应商适配与统一消费接口</td>
<td align="left">行情页面、AI和未来回测共用标准化事实，不直接读取供应商原始响应</td>
<td align="left">P0借鉴；ODP不等于商业Workspace，也不是A股数据授权</td>
</tr>
<tr>
<td align="left">[AKShare](https://github.com/akfamily/akshare)</td>
<td align="left">公开数据接口探索的候选工具</td>
<td align="left">用于字段原型与数据源适配验证，生产来源需重新完成许可和质量验收</td>
<td align="left">非默认生产源；不承诺接口覆盖、稳定或实时</td>
</tr>
<tr>
<td align="left">[CZSC](https://github.com/waditu/czsc)</td>
<td align="left">K线结构、多周期信号与可视化调试</td>
<td align="left">可选趋势结构因子，输出形成时点与确认时点，保留结构修订</td>
<td align="left">P2实验候选；缠论信号不替代六种角色规则</td>
</tr>
<tr>
<td align="left">[聚宽 / JQData](https://github.com/JoinQuant/jqdatasdk)</td>
<td align="left">商业数据、研究环境与模拟环境分层</td>
<td align="left">参考认证、配额与数据接入方式，采购前确认Web展示和再分发权限</td>
<td align="left">商业平台参考，不归类为免费开源数据源</td>
</tr>
<tr>
<td align="left">[Backtrader](https://www.backtrader.com/docu/)</td>
<td align="left">策略、Data Feed、Broker与回测入口</td>
<td align="left">作为小样本回测接口设计的对照，不绑定主系统</td>
<td align="left">P2可选基线；GPL义务、依赖兼容与维护状态另审</td>
</tr>
</table>

**保持既有分期**：P0只落地现有数据、快照、角色、权限和质量要求中的工程约束；P1仍以既有评审稿列出的盘中与体验扩展为准，正式范围待评审确认；因子体检、参数扫描、滚动样本外检验、回测与模拟组合仍归P2。此前借鉴讨论中的“研究实验层”不是另一次产品分期批准。任何新增范围或工期由DEC-11重新评审。

### 11.4 需求映射与补充验收

以下验收细化既有REQ和AC，不增加需求编号或新页面；P0后台核验沿用现有快照与质量页面。P2实验报告允许先交付离线结果，不因此新增完整实验管理Web；若需要该界面，另补设计与需求。

<table>
<tr>
<td align="center">补充用例</td>
<td align="center">关联需求／阶段</td>
<td align="center">触发与检查</td>
<td align="center">通过条件与输出</td>
</tr>
<tr>
<td align="center">TCR-01</td>
<td align="left">D01、D02、Q02、Q03／P0</td>
<td align="left">对两个来源的同口径固定样本执行映射与异常注入</td>
<td align="left">证券、时区、单位、复权及源时间均可追溯；切源生成新来源版本；冲突隔离，不拼接数值或绕过许可</td>
</tr>
<tr>
<td align="center">TCR-02</td>
<td align="left">D02、D03、L01、L03／P0</td>
<td align="left">同数据、规则、参数与截止时点重复计算；修改一项规则后重算</td>
<td align="left">固定环境下候选ID、排序及证据一致；规则变更新建版本，旧笔记引用不漂移；重复事件不重复发布</td>
</tr>
<tr>
<td align="center">TCR-03</td>
<td align="left">D03、L01／P0；B01／P2</td>
<td align="left">只改变截止时点之后的价格、消息或题材成员，重算该时点结果</td>
<td align="left">固定当时可用版本时结果不变；正式数据修订另建版本。历史回补与当时可见快照分开；检测通过不等于消除所有偏差</td>
</tr>
<tr>
<td align="center">TCR-04</td>
<td align="left">B01／P2</td>
<td align="left">冻结数据、因子、代码、依赖、参数、成本、训练／验证／测试区间及随机种子</td>
<td align="left">实验ID可定位全部配置及产物；随机算法记录预先约定的误差容限；换配置另开实验，失败任务留状态且不覆盖成功结果</td>
</tr>
<tr>
<td align="center">TCR-05</td>
<td align="left">B01／P2</td>
<td align="left">在共同样本池检验因子分组收益、IC、换手和时间稳定性</td>
<td align="left">展示覆盖率、缺失／剔除原因及未来收益窗口；IC定义、分组方式和最小样本先冻结；未来收益只作检验标签，不进入当期特征</td>
</tr>
<tr>
<td align="center">TCR-06</td>
<td align="left">B01／P2</td>
<td align="left">参数扫描、滚动检验与不同预热历史长度对照</td>
<td align="left">保存全部参数试验，不只保留最佳曲线；冻结留出集，不反复按测试结果调参；重叠标签区间有隔离措施；指标差异可定位并据此设预热门槛</td>
</tr>
<tr>
<td align="center">TCR-07</td>
<td align="left">B01、B02／P2</td>
<td align="left">T+1、停牌、涨跌停、费用、滑点和信号可用时点边界测试</td>
<td align="left">未到可用时点不能成交；无成交证据不能因价格触及即视为买到；按适用证券规则处理，输出拒单／未成交原因与成本，不产生真实委托</td>
</tr>
<tr>
<td align="center">TCR-08</td>
<td align="left">X02、D01、X03／实际接入阶段</td>
<td align="left">引入或升级外部库、数据服务、模型消费接口</td>
<td align="left">版本、许可、数据权利、依赖及出站行为完成审查；无密钥泄露；可关闭适配器并回退，不能恢复已失权数据；未通过不启用该依赖</td>
</tr>
</table>

因子体检建议观察未来1／3／5／10／20个交易日，具体窗口及最小样本量在实验开始前冻结；它们是研究参数，不是收益承诺。换手和成本敏感性不能替代成交可得性模拟，只有通过B01、B02的A股约束检验，才可将结果称为该模拟条件下的策略表现。

技术结构识别须区分“结构起点”和“当时能够确认的时间”。例如一个后来确认的分型或中枢，不能被回填成起点当天已知的信号；跨周期指标只使用当时已完成或明确标记未完成的K线。指标预热不足时标不可用，不靠补0或缩短窗口制造信号。

AI继续只负责获准材料的摘要、关联建议和解释，不修改行情事实、确定性计算或已冻结研究基线。LLM历史重述还受模型本身的未来知识影响，不能因通过数值前视测试而宣称其可用于无泄漏的历史交易验证；无AI／有AI对照必须记录材料、模型、提示词和人工介入差异。

### 11.5 依赖选择与许可

**开源代码许可、数据使用权和交易授权是三件独立的事。** 正式依赖清单为空并不阻止学习公开文档中的架构；代码复制、库集成、网络服务、分发或采购则须在对应动作前完成审查。本次PRD同步不授权安装、执行外部脚本、购买数据或连接账户。

- VeighNa、Qlib的MIT及LEAN、Alphalens-reloaded、CZSC的Apache 2.0可作为候选许可线索，但仍需核验采用版本、依赖许可证和通知义务；不能凭主仓库许可推断所有扩展均相同。
- RQAlpha的非商业使用提示、vectorbt的附加Commons Clause及PRO条款须单独评估；不能把它们当作无商业限制的标准宽松开源组件。免费版没有的能力不写成已具备。
- Backtrader、Freqtrade的GPL与OpenBB的AGPL不等于禁止商用；应按实际修改、组合、分发和网络服务方式评估源码提供及其他义务。闭源Web集成未通过审查前，只借鉴设计，不直接纳入生产依赖；是否需要商业许可由授权审核确定。
- AKShare的代码许可不自动授予底层数据商业展示、再分发或模型输入权；聚宽SDK与JQData服务授权也须分开。AKShare完整接口和用途声明仍需接入前复核，本版不据此保证生产可用性。
  依赖登记至少包含仓库／服务、版本或commit、许可证与NOTICE、直接及传递依赖、使用和部署方式、允许的数据用途、已知漏洞、维护责任人、测试产物与回退路径。未锁版本前，框架功能和许可说明均是参考线索，不是采购或法务批准。

研发在P2选型前使用同一组受许可的固定样本，对比“现有轻量方案”“RQAlpha适配”“LEAN适配”，评估A股规则覆盖、可复现性、集成成本、许可与运行成本；指标及测试环境一致，不用GitHub星标或项目宣传替代验证。能满足研究需要时保留轻量方案，不为使用框架而扩建多资产交易系统。

### 11.6 市场状态与调权

> **调试原则**：每日更新观测，离线验证参数。固定角色分回答“符合哪类角色证据”；状态条件研究分回答“当前优先研究什么”。后者不得改写角色历史、自动生成交易指令或放松数据与监管约束。

<table>
<tr>
<td align="center">候选状态</td>
<td align="center">量化规则的输入方向</td>
<td align="center">待验证的观察侧重</td>
</tr>
<tr>
<td align="center">强趋势</td>
<td align="left">指数均线位置、市场广度、成交变化、创新高占比</td>
<td align="left">相对强弱、趋势持续性、容量承接；不放松回撤与质量检查</td>
</tr>
<tr>
<td align="center">情绪高潮</td>
<td align="left">涨停数量、连板高度、晋级率、昨日强势股反馈</td>
<td align="left">启动与题材扩散，并列展示高位失败证据；不自动提高追高偏好</td>
</tr>
<tr>
<td align="center">轮动震荡</td>
<td align="left">指数趋势弱、题材成交份额切换、核心分化</td>
<td align="left">补涨激活与新核心观察，不将单日异动确认为接力</td>
</tr>
<tr>
<td align="center">退潮分歧</td>
<td align="left">晋级率下降、前期强势股回撤、题材广度收缩；炸板需完整事件数据</td>
<td align="left">降低高位延续研究权重，提高流动性与失效证据关注</td>
</tr>
<tr>
<td align="center">弱势冰点</td>
<td align="left">广度、成交和涨停生态持续偏弱</td>
<td align="left">收缩研究候选，保留有充分证据的样本，允许无候选</td>
</tr>
</table>

五类名称只是假设，不是已运行的分类器，也不表示当前市场处于其中某类。P2首版优先使用少量可解释的确定性条件。每个状态必须先冻结输入、回看窗口、阈值／历史分位、最少持续期、状态冲突优先级、退出条件和缺失处理；未冻结则输出“尚未启用状态识别”，不由AI临时判断。

状态重叠、样本不足或关键输入缺失时输出“状态不确定”。可回退预先批准的固定基线，但必须声明回退；若基线必需输入也缺失，则停止新研究排序。不能无限沿用过期状态，也不能用未来行情平滑过去状态。状态切换频次、最小持续期或迟滞机制只能作为预注册参数验证。

```plaintext
固定角色证据分(i,r,t) = sum_f[固定角色权重(r,f) × 合格标准化因子(i,f,t)]
P2研究分(i,t) = sum_f[已发布状态权重(当时状态,f) × 合格标准化因子(i,f,t)]
可选概率混合权重(f,t) = sum_s[p(s,t) × 已发布权重(s,f)]
```

默认使用确定性状态选择，不强制概率模型。概率混合仅作为P2后续实验：非负状态权重和为1，需检查校准、稳定性与样本量；它不是股票上涨概率。因子方向统一后可从非负且和为1的简单权重起步，等权只作基线，具体数值不能宣称已经优化。市场级因子用于状态识别，不当作同一横截面所有股票相同的加分项来伪造区分度。

监管规则按官方版本和生效区间更新；研究池门槛、数据许可、停牌、T+1与成交约束按各自制度生效。它们不进入上述权重求和，不会因行情强而放松。交易不可达限制的是模拟成交，不能反向删除当时已经成立的描述性角色记录。

### 11.7 验证与版本生命周期

每日计算只消费已发布配置；新配置先离线验证，再进入不影响正式结果的影子对照。以下生命周期用于P2研究配置，不能用“每天自动学习”替代审核。

**建议频率**：日级更新因子观测与质量；周级检查覆盖、缺失、漂移和状态切换；月级评估是否有必要开启新一轮参数实验。该频率是待评审方案，不代表创建实际定时任务，也不意味着每月必须发布新权重。标签尚未成熟时不计算该窗口绩效；重大异常可立即停用配置，但不能紧急按当日赢家重新拟合。

- 训练／验证／冻结测试按时间前后切分，股票池含历史状态与退市样本。滚动训练只能使用该时点已经成熟的标签，验证期选择参数，最终测试用于一次性评估；看过测试结果再修改后，不得继续称该区间为未触及样本外。
- 先冻结未来收益标签窗口、可用时点和基准。与相邻分段重叠的持有期标签必须清除或隔离，并记录隔离长度；不随机打乱日期，不只按起始日切分。
- 对比固定基线、状态条件规则与简单等权方案，保持数据、股票池、成本、调仓频率和可交易条件一致；动态方案没有稳定改善时保留固定方案。
- 输出IC／Rank IC、分位分组收益及单调性、高低分位差、换手、覆盖／缺失、因子相关性、分时段稳定性和成本敏感性。市场级状态因子另看状态区分及对应样本表现，不强行计算无横截面差异的股票IC。
- 按每个状态报告交易日数、有效证券数、独立事件数、标签窗口和不确定区间；高度重叠的股票—日期行数不冒充独立样本量。门槛未满足只标“样本不足”，不能宣称只在某状态有效。
- 参数扫描保留全部尝试、失败和筛选记录，控制试验数量，检查参数附近是否稳定，避免只选单点最优；一次优先改一个因子组，并做剔除该组的对照。
- 影子对照检查角色解释、候选更替、状态抖动和质量降级；配置生效只作用于之后新计算，回滚只切换生效配置，不覆盖旧快照。14.2的20个交易日影子运行是工程门槛，不证明因子统计有效。
- 样本外退化先排查数据延迟、授权／字段变化、股票池漂移和成交假设，再讨论模型失效。触发停用阈值、最少样本、影子时长和统计评价方法在DEC-12冻结；未通过时维持原基线或停止排序。
  [Qlib OnlineManager](https://qlib.readthedocs.io/en/latest/component/online.html)可借鉴模型上线时段记录，[Qlib Recorder](https://qlib.readthedocs.io/en/latest/component/recorder.html)可借鉴参数、指标和产物追踪；不因此引入自动批准。[Alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded)用于理解因子收益、IC与换手体检，不代表可交易策略收益。

[Freqtrade前视检测](https://docs.freqtrade.io/en/2025.5/lookahead-analysis/)提醒未来位移及全局统计造成时间污染，未触发信号仍有漏检边界；[递归检测](https://docs.freqtrade.io/en/2026.3/recursive-analysis/)比较不同预热长度下末行指标，不能替代整段策略和成交验证。P2应同时做截止时点截断重算、未来输入扰动和预热稳定性测试，而非仅凭一个工具通过就宣布无泄漏。

### 11.8 因子补充验收

以下TCF用例细化既有需求，不新增REQ或独立页面。P0仅验证已启用固定因子及数据契约；状态调权与统计有效性验证仍属P2。

<table>
<tr>
<td align="center">用例</td>
<td align="center">关联需求／阶段</td>
<td align="center">输入或操作</td>
<td align="center">通过条件</td>
</tr>
<tr>
<td align="center">TCF-01</td>
<td align="left">L01、D03、Q02／P0</td>
<td align="left">固定数据复算、端点缺失、历史不足、零分母、常数序列</td>
<td align="left">原值、单位、窗口与独立样例一致；容限预先记录；不能计算时返回原因而非0；已发布配置可定位</td>
</tr>
<tr>
<td align="center">TCF-02</td>
<td align="left">L01、L03、Q04／P0；B01／P2</td>
<td align="left">删除必需／可选因子，修改监管或研究资格状态</td>
<td align="left">无静默重分配权重；降级另有规则ID；描述性历史角色保留，当前资格、风险和可交易性分别变化</td>
</tr>
<tr>
<td align="center">TCF-03</td>
<td align="left">L02、D01／P0；E01、E04／P1</td>
<td align="left">仅有日线，却请求首封、开板和新老核心交接</td>
<td align="left">时序因子不可用或待验证；盘后封板率只在授权触板集合完整时启用；后续确认不回填起点</td>
</tr>
<tr>
<td align="center">TCF-04</td>
<td align="left">B01／P2</td>
<td align="left">状态边界、重叠、缺失、过期、阈值附近往复波动</td>
<td align="left">选择或回退严格符合冻结规则；解释输入与状态版；只改未来数据时过去状态和权重不变</td>
</tr>
<tr>
<td align="center">TCF-05</td>
<td align="left">B01／P2</td>
<td align="left">注入未成熟标签、重叠窗口、全样本缩放、未来成员和事后择优核心</td>
<td align="left">违规样本被阻断或隔离；预处理只用训练／截止前数据；保留未触及测试集和完整试验日志</td>
</tr>
<tr>
<td align="center">TCF-06</td>
<td align="left">B01、B02／P2</td>
<td align="left">固定与动态方案同条件对照、状态样本不足、费用加压</td>
<td align="left">报告样本数、覆盖、IC／分组、换手、成本及不可成交比例；不足不宣称有效，不仅展示最优曲线</td>
</tr>
<tr>
<td align="center">TCF-07</td>
<td align="left">D03、D04／P0；B01／P2</td>
<td align="left">因子版本变更、影子配置发布、失败停用和回滚</td>
<td align="left">P0版本可复现；P2未评审配置不接管正式输出；生效时间受控，旧快照与笔记引用不漂移</td>
</tr>
<tr>
<td align="center">TCF-08</td>
<td align="left">A03、X02／P0；B01／P2</td>
<td align="left">AI尝试改因子值、调权、补造缺失或把分数写成上涨概率</td>
<td align="left">写操作与误导解释被拒绝；回退确定性证据；AI不得批准研究配置或调用交易指令</td>
</tr>
</table>

***

## 十二、权限与安全

本期不做用户注册与登录；采用本机所有者入口和匿名公开只读入口，服务端隔离私有研究、共享内容与管理操作。免登录不免访问控制，向他人展示仍须取得相应数据许可。

### 12.1 权限矩阵

<table>
<tr><td align='center'>角色</td><td align='center'>允许</td><td align='center'>默认禁止</td></tr>
<tr><td align='center'>研究用户</td><td align='left'>查看已授权快照、管理自己的笔记和自选</td><td align='left'>查看他人笔记、改正式行情、管理密钥</td></tr>
<tr><td align='center'>数据管理员</td><td align='left'>来源、任务、质量、快照发布和回退</td><td align='left'>读取私有笔记、无审计修改历史值</td></tr>
<tr><td align='center'>规则复核员</td><td align='left'>规则审核、样例测试与发布审批</td><td align='left'>跳过测试、套用未核验的板块规则</td></tr>
<tr><td align='center'>系统管理员</td><td align='left'>本机操作权限、环境、审计与恢复；本期不建设账号系统</td><td align='left'>借管理员身份默认获得私有研究内容</td></tr>
</table>

上表是所有者侧的操作职责，不代表匿名访客具有研究用户身份。首期可以一人承担多个操作角色，但要记录每次动作；正式监管规则仍需具备独立复核记录。若只能自审，则标“实验规则，未独立复核”，不可显示为已正式验收。

### 12.2 安全需求

<table>
<tr><td align='center'>需求ID</td><td align='center'>优先级</td><td align='center'>触发／状态</td><td align='center'>预期行为</td><td align='center'>验收</td></tr>
<tr><td align='center'>REQ-X01</td><td align='center'>🔴 P0</td><td align='left'>本机所有者／匿名访客访问及资源读写；S01、S05</td><td align='left'>不要求注册登录；服务端逐请求校验入口、操作权限、资源归属及导出范围。公开入口仅允许已发布读模型，匿名访客不得映射为本机所有者</td><td align='left'>AC-X01：伪造Host／Forwarded、用户ID、任务ID或导出URL不能越界读取私有内容；公开写入和管理请求被拒绝；所有者主流程无需注册登录</td></tr>
<tr><td align='center'>REQ-X02</td><td align='center'>🔴 P0</td><td align='left'>外部文本与模型调用；S01、S05</td><td align='left'>凭证服务端保管、文本净化、安全外链、AI权限与脱敏控制</td><td align='left'>AC-X02：提示注入和脚本不能执行；未许可数据不出站</td></tr>
<tr><td align='center'>REQ-X03</td><td align='center'>🔴 P0</td><td align='left'>删除、导出、恢复与授权到期</td><td align='left'>按数据域实施保留策略，记录删除／恢复和版权下线操作</td><td align='left'>AC-X03：撤回后不可再导出；恢复不重新暴露已删除或已失权内容</td></tr>
</table>

采用HTTPS、依赖扫描、请求限流和CSRF／XSS防护；本机操作上下文或令牌须可失效、可撤销，匿名访客不建立登录会话。私人导出仅供本机所有者，链接短期有效并校验操作权限；公开导出默认关闭，另行批准后也仅含已发布且许可可导出的字段。密钥不能进入前端构建产物、URL、错误堆栈或AI提示词。

保留策略建议：普通运维日志30天、管理审计180天、笔记软删除回收期30天；原始商业数据按合同最短许可保留，研究快照按许可与项目需要归档。个人信息删除、备份滚动清理和版权撤回以法务／安全确认的正式政策为准，以上不是法律规定。

### 12.3 免登录分享边界

**两种入口，不是所有访客共用一个管理员账号。** 本机所有者使用本机控制入口；他人打开公网HTTPS网址即可浏览，不注册、不登录、不安装VPN或本地证书。默认不加入Access验证码或Basic Auth；若未来要求“只有指定人可用”，须另评审认证或可撤销分享能力，不能同时承诺匿名网址只对朋友有效。

<table>
<tr>
<td align="center">访问模式</td>
<td align="center">允许</td>
<td align="center">禁止／限制</td>
</tr>
<tr>
<td align="center">owner_local</td>
<td align="left">在本机管理自选、私人笔记、数据源、采集、规则、发布、回滚与限额内AI任务</td>
<td align="left">不可绕过数据许可、质量门槛与审计；远程管理不在本期范围</td>
</tr>
<tr>
<td align="center">guest_public</td>
<td align="left">查看获许可且已发布的快照、消息、角色证据和经审核发布的AI报告；筛选、排序、查看详情</td>
<td align="left">不写笔记、不上传、不调参、不触发采集或AI生成、不读私人归档；公开导出默认关闭</td>
</tr>
</table>

- **公开接口**：采用精确路由、查询条件与字段白名单，数据库或服务层仅返回已发布且当前许可有效的公开读模型；未列入白名单的API默认拒绝。GET／HEAD也不得触发写库、采集、发布或计费任务。
- **管理隔离**：Caddy区分本机入口和分享入口，后端再次校验操作权限与资源归属；不能仅靠隐藏按钮、Host字段或回环地址认定所有者。采用仅本机建立的操作上下文，校验Origin／Host、防CSRF及高风险操作确认；上下文不得通过分享入口签发。
- **页面适用范围**：原笔记、自选编辑、AI生成和管理需求保留给所有者；访客只展示发布内容，相关写入控件不提供。无发布数据显示“暂无可分享内容”，下线内容不得从旧链接读取；未授权访问显示无权限或不存在，不泄漏资源是否存在。
- **分享不是保密措施**：网址可被转发；HTTPS、noindex、robots和不公开链接均不构成身份验证。匿名访问不创建云端用户归属，也不得绑定所有者的笔记、自选或任务历史。
- **AI成本**：访客只读已生成、已审核且许可可共享的报告；刷新、筛选和重复访问不产生新的模型调用。本机AI任务仍需预算、限次重试、取消与降级控制。

***

## 十三、数据契约

PRD约束接口含义、状态和权限；本期部署边界在13.4确认，具体配置引用部署详案。数据库索引、任务实现和具体SDK仍由技术设计评审。

### 13.1 核心对象

<table>
<tr><td align='center'>对象</td><td align='center'>唯一性／关联</td><td align='center'>必保留信息</td></tr>
<tr><td align='center'>证券状态</td><td align='left'>证券ID＋有效区间</td><td align='left'>交易所、板块、名称、特殊状态、来源</td></tr>
<tr><td align='center'>行情记录</td><td align='left'>证券＋源时点＋来源＋版本</td><td align='left'>原始价、成交、单位、复权因子版本、质量</td></tr>
<tr><td align='center'>题材成员</td><td align='left'>题材＋证券＋有效区间</td><td align='left'>业务生效与首次可知时间、分类来源、确认方式</td></tr>
<tr><td align='center'>文章与事件</td><td align='left'>文章ID／聚类事件ID</td><td align='left'>原文、许可、发布、获取、更正与撤回</td></tr>
<tr><td align='center'>角色快照</td><td align='left'>证券＋题材＋观察时点＋规则版</td><td align='left'>角色、排名、样本、因子、证据与缺口</td></tr>
<tr><td align='center'>监管规则</td><td align='left'>规则ID＋版本</td><td align='left'>生效范围、公式、基准、重置、原文、复核</td></tr>
<tr><td align='center'>监管事件</td><td align='left'>事件ID＋来源公告</td><td align='left'>事件覆盖期、披露期、对象、限制与解除依据</td></tr>
<tr><td align='center'>复盘与笔记</td><td align='left'>用户＋记录ID＋修订版本</td><td align='left'>判断、验证条件、快照引用、确认状态与创建时间</td></tr>
<tr><td align='center'>AI任务</td><td align='left'>任务ID＋用户／共享范围</td><td align='left'>输入材料ID、模型／提示版本、费用、引用、审核状态</td></tr>
<tr><td align='center'>快照清单</td><td align='left'>snapshot_id</td><td align='left'>各数据域版本、计算截止、质量、许可和发布记录</td></tr>
</table>

### 13.2 API公共字段

所有读模型至少返回：`trade_date`、`as_of`、`snapshot_id`、`source`、`coverage`、`quality_flags`、`rule_version`、`basis`。跨数据域返回各自版本的清单，前端不能用一个页面更新时间掩盖不同域的延迟。

- `coverage` 包括预期／有效／合法排除／异常缺失数量及池定义；缺失原因不能只返回一个百分数。
- `basis` 包括币种、价格口径、单位、样本与比较基准；对数值型字段提供单位元数据。
- 错误区分参数无效、身份失效、无权限、数据不足、源超时、质量阻断、预算上限和系统错误；错误响应不含密钥或原始堆栈。
- 笔记写入要求幂等键和版本条件；冲突返回当前服务端版本，用户选择合并或另存。
- 长任务返回任务ID、状态、结果快照和错误信息；取消或超时后结果不能覆盖更新的请求。
- 导出CSV／Markdown附筛选条件、源时间、快照、规则和质量状态；指标导出支持，商业原文导出默认关闭。

### 13.3 技术建议

建议React＋TypeScript前端、Python＋FastAPI后端、PostgreSQL主存储、对象存储留档、pandas或Polars计算。先建立模块化单体，任务增多时再引入Celery＋Redis；容器部署、测试／生产分离、Git版本和自动化测试应从第一阶段开始。

已有实现方案提供数据处理架构，PRD以需求ID及验收为主，具体架构沿技术评审调整。[^8]

### 13.4 本机部署与公网分享

**已选基线**：使用一台自己的电脑运行Docker Compose；前端、FastAPI与PostgreSQL是三层业务组件，Caddy负责静态前端托管及反向代理。当前配置参考把前端构建产物挂载给Caddy，因此基础运行服务为db、api、caddy，并非强制四个常驻容器；构建流程与运行流程分开。

部署步骤、配置参考及已绘制的入口架构见[本地托管与免登录分享部署详案](https://bytedance.larkoffice.com/docx/RVmCdyjw2oPWiNx2WYXccmVynxf)。详案细化本节，不替代PRD的数据许可、访问控制与发布门槛。

#### 13.4.1 本机与公网连接

<table>
<tr>
<td align="center">组件／入口</td>
<td align="center">约束</td>
<td align="center">验证要点</td>
</tr>
<tr>
<td align="center">本机所有者</td>
<td align="left">Caddy的80／443宿主映射仅绑定127.0.0.1；本机入口使用localhost</td>
<td align="left">首次信任本机CA；不能把localhost发给异地访客</td>
</tr>
<tr>
<td align="center">匿名分享入口</td>
<td align="left">受控域名＋出站隧道；优先试用Cloudflare Named／远程管理Tunnel，连接到Caddy独立8443入口</td>
<td align="left">不需家用公网IP或路由器入站转发；实际网络须测试</td>
</tr>
<tr>
<td align="center">Caddy分享监听</td>
<td align="left">share.internal:8443只在Docker网络监听，不映射宿主端口；对公开API精确白名单代理</td>
<td align="left">不得让隧道连接localhost所有者入口</td>
</tr>
<tr>
<td align="center">API与数据库</td>
<td align="left">API:8000、PostgreSQL:5432不发布宿主端口；仅在backend网络通信</td>
<td align="left">公网无法直连API、数据库和管理文档</td>
</tr>
<tr>
<td align="center">网络分层</td>
<td align="left">cloudflared仅加入edge；Caddy加入edge与backend；API、数据库仅加入backend</td>
<td align="left">网络分层不等于禁止出站；数据及AI出站仍按许可限制</td>
</tr>
</table>

**公网前置条件**：域名及DNS可控、入口服务可用、证书签发与续期正常、本机持续联网、数据向他人展示及第三方处理获许可。Cloudflare是待实测的优先方案，不是已购买的服务或唯一指定供应商；中国大陆访客须在实际网络核验。失败时评估合适的国内中转、公网直连或云部署，不以更换部署位置规避适用义务。

#### 13.4.2 HTTPS与凭据

- 访客到公网边缘使用浏览器可信证书；隧道连接器到Caddy使用内部证书并严格验证。访客无需导入Caddy CA；只有本机管理员需要完成本机证书信任。
- 回源参数固定为Service＝<https://caddy:8443、originServerName＝share.internal、httpHostHeader＝share.internal、caPool＝/etc/cloudflared/certs/caddy-root.crt、noTLSVerify＝false；错误CA或证书名称必须导致连接失败，禁止跳过校验。>
- 连接器只读挂载CA根证书公钥，不挂载含CA私钥的整个Caddy数据目录。隧道Token通过secret文件与--token-file传入，不写进前端、URL或命令行参数；宿主文件仍须ACL保护，文件式secrets不等于磁盘加密。
- 使用支持--token-file且处于供应商支持窗口的cloudflared版本（该参数要求至少2025.4.0）；试验后锁定版本或digest并定期升级，不默认使用失去支持的旧版，也不盲目长期跟随latest。
- 公网边缘终止访客TLS；该方案不是只有浏览器与本机能解密的直连式端到端加密。受限数据是否允许经过第三方服务必须纳入授权检查。

#### 13.4.3 启停、备份与恢复

- **默认仅本机**：基础docker-compose.yml不包含隧道；分享通过compose.share.yml显式叠加。未配置隧道Token或镜像时不影响基础本机启动；关闭连接器应只停止公网分享。
- **一键启动的范围**：先安装Docker、填写非弱口令、构建前端、准备业务镜像并完成数据库迁移及本机证书信任，之后Compose一条命令启动。域名、隧道和首次证书配置不是零准备自动完成；当前参考包仅是占位脚手架，不是完整业务系统。
- **运行连续性**：关机、休眠、断网或Docker退出会中断服务；restart: unless-stopped不唤醒电脑，也不保证Docker Desktop开机即就绪。需验证宿主开机启动、关闭休眠、容器重启、健康检查与恢复；存活检查不等于数据库及业务就绪。
- **备份目标不降级**：数据库卷和归档卷不是备份。保留9.3的用户内容RPO≤1小时、RTO≤4小时建议目标，结合DEC-07确认备份方式与成本；每日备份通常只能支持约24小时损失窗口，不能声称达标。采用足够频率的备份或连续归档，并保留独立于本机的恢复副本及演练记录。
- **更新与回退**：升级前备份，记录镜像、数据库迁移和配置版本；不可兼容的数据库迁移必须有恢复方案，不能仅回滚应用镜像。日常更新禁止使用docker compose down -v清库；密钥泄漏须在服务后台轮换并断开旧连接。
- **本次验证状态**：配置参考完成静态检查；尚未在用户电脑执行Docker／Caddy运行测试、数据库读写、域名接入或异地访问，不把静态检查作为上线证据。

***

## 十四、验收与埋点

所有P0需求必须有正常、边界和失败用例；功能正确、数据可信与业务价值分别评估，不用一个收益指标替代发布验收。

### 14.1 关键测试集

<table>
<tr><td align='center'>用例</td><td align='center'>输入／故障</td><td align='center'>期望结果</td><td align='center'>关联需求</td></tr>
<tr><td align='center'>TC-01</td><td align='left'>历史日期无快照</td><td align='left'>进入S03，不切换今天；原选择保留</td><td align='center'>G01</td></tr>
<tr><td align='center'>TC-02</td><td align='left'>休市起止、停牌、样本不足</td><td align='left'>显示调整日期；不足样本排除；不改变比较窗口</td><td align='center'>P01、P02</td></tr>
<tr><td align='center'>TC-03</td><td align='left'>100条区间收益独立复算</td><td align='left'>显示差异≤0.01个百分点，基期和版本完全相同</td><td align='center'>P01、Q03</td></tr>
<tr><td align='center'>TC-04</td><td align='left'>8天5板、冲板失败、特殊板块</td><td align='left'>不误算连板；有效涨停价按规则精度判断</td><td align='center'>L02、Q02</td></tr>
<tr><td align='center'>TC-05</td><td align='left'>分页缺失、单位翻倍、重复行</td><td align='left'>隔离错误批次，停发受影响排行</td><td align='center'>D02、Q02、Q04</td></tr>
<tr><td align='center'>TC-06</td><td align='left'>后补公告、题材成员变更</td><td align='left'>当时可见版不变，修订版另列</td><td align='center'>N01、M02、D03</td></tr>
<tr><td align='center'>TC-07</td><td align='left'>监管临界值、复牌、除权、规则切换、重复公告</td><td align='left'>计算与事件计数正确；未知不转正常</td><td align='center'>R01—R04</td></tr>
<tr><td align='center'>TC-08</td><td align='left'>AI错数字、错实体、无引文、提示注入</td><td align='left'>阻断问题输出或拒答；不执行写入和外部指令</td><td align='center'>A01—A05、X02</td></tr>
<tr><td align='center'>TC-09</td><td align='left'>AI超时及晚到、用户已编辑</td><td align='left'>模板可用；晚到草稿不覆盖用户内容</td><td align='center'>U03、A04</td></tr>
<tr><td align='center'>TC-10</td><td align='left'>两个窗口修改同一笔记、断网重试</td><td align='left'>冲突提示且输入不丢；重复请求幂等</td><td align='center'>U02、X01</td></tr>
<tr><td align='center'>TC-11</td><td align='left'>越权ID、失效导出链接、授权到期</td><td align='left'>服务端拒绝；日志无敏感数据</td><td align='center'>G04、X01—X03</td></tr>
<tr><td align='center'>TC-12</td><td align='left'>数据库恢复、应用和快照回退</td><td align='left'>用户内容保留；删除／许可状态仍有效</td><td align='center'>D04、X03</td></tr>
</table>

#### 网页视觉与交互回归

<table>
<tr>
<td align="center">用例</td>
<td align="center">操作／输入</td>
<td align="center">通过条件</td>
<td align="center">关联需求</td>
</tr>
<tr>
<td align="center">TC-13</td>
<td align="left">四档视口、200%缩放、长名称与大数字</td>
<td align="left">主流程可完成；页面不整体横溢，表格容器可滚动，身份与关键状态可读</td>
<td align="center">V01、V03、V06</td>
</tr>
<tr>
<td align="center">TC-14</td>
<td align="left">纯键盘筛选、抽屉、返回、编辑与保存</td>
<td align="left">无焦点陷阱，关闭回原入口；未保存内容有保护，保存结果可被读屏感知</td>
<td align="center">V04、V06、V08</td>
</tr>
<tr>
<td align="center">TC-15</td>
<td align="left">灰度、对比度、涨跌零值缺失、风险未知</td>
<td align="left">符号／文字足以判别；实际文字和控件达到V06阈值，不以配色图代替测量</td>
<td align="center">V02、V06</td>
</tr>
<tr>
<td align="center">TC-16</td>
<td align="left">断网、AI超时、失效引用、减少动态效果</td>
<td align="left">旧时间清晰；关键提示保留；模板可用；无伪实时动画或晚到覆盖</td>
<td align="center">V05、V07、V08</td>
</tr>
<tr>
<td align="center">TC-17</td>
<td align="left">P1主题／密度／命令面板回归</td>
<td align="left">两主题及偏好恢复通过，数据值不变，输入法与权限测试通过；仅对应功能发布时执行</td>
<td align="center">V09—V11</td>
</tr>
</table>

**框架借鉴补充验收**：11.4的TCR-01—TCR-08并入关联REQ／AC的用例库。P0执行TCR-01、02、03的当期部分；P2执行TCR-03的策略部分及04—07；TCR-08在实际引入、升级或启用依赖前执行。未引入候选库不因其未安装而阻塞上线，P2研究报告不成为P0发布条件。

**因子方案补充验收**：11.8的TCF-01—TCF-08并入对应REQ／AC。P0执行01、02、03、07、08中固定因子、授权和版本安全的当期部分；P1执行03的时序扩展；P2执行04—06及其余用例的状态调权部分。因子目录不意味着全部因子必须进入P0，参数、统计门槛和动态排序启用必须经DEC-04／DEC-12评审。

### 14.2 发布条件

- 连续至少20个交易日影子运行，保留每日核验和问题单；关键规则变更后重验受影响范围。
- P0需求验收全部通过；未来信息泄漏、证券错配、数值单位错误、越权和假监管状态为阻断级，不能豁免。
- AI评测、安全测试、授权检查及数据库恢复演练通过；模型故障降级路径经过实测。
- 真实数据、示意数据和测试数据隔离，生产页面禁止保留演示标的或伪造曲线。
- 网页P0视觉与交互用例TC-13—TC-16通过；冻结设计变量并完成键盘、读屏、对比度与响应式检查。新增设计工作量需重新估算，不默认原排期自动覆盖；P1视觉功能不得作为P0上线前置条件。
- 先在受控测试环境试用，验收后才开启匿名公网分享；公开链接不保证仅指定朋友访问。行情、角色、监管、AI与分享入口分别有开关；问题回退不清库。
- 开启公网分享前，14.4的DEP-01—DEP-08、数据展示许可和第三方处理检查必须通过；未通过时保持本机模式或使用明确标记且可合法展示的测试材料，不开放真实研究数据。

### 14.3 埋点字典

公共字段为匿名化用户或会话标识、access\_mode（owner\_local／guest\_public）、页面、需求ID、事件时间、快照、模式、结果状态、耗时。匿名会话标识仅用于统计，不是认证身份，不用IP认定用户；不要记录笔记正文、Token或整段原文。

<table>
<tr><td align='center'>事件名</td><td align='center'>触发／需求</td><td align='center'>扩展字段与用途</td></tr>
<tr><td align='center'>review_open</td><td align='left'>打开复盘页／G01</td><td align='left'>日期、数据状态、加载耗时</td></tr>
<tr><td align='center'>snapshot_switch</td><td align='left'>切换版本／G01</td><td align='left'>原／目标版本、当时可见或修订模式</td></tr>
<tr><td align='center'>news_source_open</td><td align='left'>查看原文／N01</td><td align='left'>文章ID、来源、外跳成功与否</td></tr>
<tr><td align='center'>role_evidence_open</td><td align='left'>查看候选证据／L01</td><td align='left'>角色、题材、规则版、缺失状态</td></tr>
<tr><td align='center'>interval_query</td><td align='left'>区间查询／P01</td><td align='left'>窗口、样本量、排除数、计算耗时</td></tr>
<tr><td align='center'>regulation_detail_open</td><td align='left'>规则详情／R01</td><td align='left'>规则版、计算状态、是否待核验</td></tr>
<tr><td align='center'>ai_task_complete</td><td align='left'>AI结束／A01—A05</td><td align='left'>任务类型、模型版、耗时、成本、降级原因</td></tr>
<tr><td align='center'>ai_suggestion_review</td><td align='left'>人工审核／N03、U03</td><td align='left'>接受／编辑／驳回、原因类别</td></tr>
<tr><td align='center'>note_save</td><td align='left'>保存笔记／U02</td><td align='left'>成功、冲突、版本、耗时</td></tr>
<tr><td align='center'>report_export</td><td align='left'>导出／G04</td><td align='left'>格式、行数、权限阻断原因</td></tr>
</table>

**网页体验补充埋点**：`stock_drawer_open`／V04记录入口与证券ID；`evidence_reference_open`／V08记录证据ID和定位成功与否；`ui_preference_change`／V09—V10记录主题／密度及切换前后值；`command_palette_use`／V11记录触发方式、结果类型与成功状态。继承公共字段，禁止记录笔记正文、搜索原文和凭证；P1事件只随功能启用。以查证据耗时、回退恢复成功率和保存任务完成率评估体验，不用点击次数或动画数量证明改进。

**因子运行与研究审计**：P0的`factor_compute_complete`记录因子版、快照、有效／缺失数量、耗时和失败原因；P2的`regime_evaluated`记录状态版、判定／回退与输入快照，`factor_experiment_complete`记录实验ID、标签成熟范围及评价状态，`weight_version_change`记录原／新版本、生效时间、审核依据和发布／停用／回滚动作。采用现有日志与实验产物，不新增管理界面，不记录凭证、私有笔记或未获准原文。

任务完成率仅统计owner\_local中开始复盘后形成已保存笔记或已确认复盘版本的适用会话；guest\_public只读会话不进入该分母，单独统计浏览、筛选与证据查看成功率。会话建议30分钟无活动结束，默认基线待试用确定。人工采纳率按已处理建议中的接受数量计算，编辑单列，不把尚未审核算成拒绝。质检指标按明确分母、样本数和时间窗口披露。

### 14.4 部署补充验收

DEP用例细化现有P0需求及9.3非功能目标，不新增正式REQ、不改变P1／P2边界；以下为待执行验收，不是已测结果。5并发测试仅是公网分享专项，不替代原20并发普通查询目标。

<table>
<tr>
<td align="center">用例</td>
<td align="center">关联需求／条款</td>
<td align="center">输入／场景</td>
<td align="center">通过标准</td>
</tr>
<tr>
<td align="center">DEP-01</td>
<td align="left">X01、D01；13.4</td>
<td align="left">异地两种实际网络打开分享域名</td>
<td align="left">无需注册、登录、VPN或证书安装即可读取许可范围内已发布内容；无证书告警、无登录跳转；记录日期、网络、设备与域名，localhost不作为分享URL</td>
</tr>
<tr>
<td align="center">DEP-02</td>
<td align="left">X01、X02；13.4</td>
<td align="left">正常回源、测试环境替换错误CA／名称</td>
<td align="left">正常严格TLS成功，错误CA或名称失败；noTLSVerify=false；连接器无CA私钥；错误配置不得回落到不校验证书</td>
</tr>
<tr>
<td align="center">DEP-03</td>
<td align="left">X01、G04；12.3</td>
<td align="left">枚举管理路由、任务／导出ID；伪造Host／Forwarded；POST／PUT／DELETE</td>
<td align="left">公网管理、私有资源、API文档均不可读；非GET／HEAD被拒绝，GET／HEAD无写入或计费副作用；公开字段白名单生效，后端二次校验通过，不能成为owner_local</td>
</tr>
<tr>
<td align="center">DEP-04</td>
<td align="left">Q01；9.3</td>
<td align="left">预热后5并发公开读请求，持续至少10分钟</td>
<td align="left">建议P95≤3秒；记录请求集、样本数、错误率、硬件、数据量、缓存和网络；20并发P95≤2秒的原普通查询验收另做，失败不能以本项通过替代</td>
</tr>
<tr>
<td align="center">DEP-05</td>
<td align="left">D03、D04、Q01、Q04</td>
<td align="left">容器／宿主重启、断网后恢复、旧快照访问</td>
<td align="left">数据与笔记持久化，恢复过程可观测；只提供完整已发布版本；旧数据保留源时间与待更新状态，不伪装今日或实时；记录恢复耗时</td>
</tr>
<tr>
<td align="center">DEP-06</td>
<td align="left">X02、D01、A05；10.3</td>
<td align="left">刷新分享报告、超额请求、扫描日志与前端产物</td>
<td align="left">访客访问不新增模型任务；超限返回429且有合理重试提示；凭据不泄露；本机AI超预算受阻、重试有限、降级可用，费用可追溯</td>
</tr>
<tr>
<td align="center">DEP-07</td>
<td align="left">X03、D04；9.3</td>
<td align="left">从独立备份恢复到隔离环境并验证</td>
<td align="left">核对笔记、自选、快照、版本与权限；已撤回或失权内容不重新公开；记录实际RPO／RTO并对照已批准目标，不以卷存在或备份命令成功替代恢复验证</td>
</tr>
<tr>
<td align="center">DEP-08</td>
<td align="left">X01、X02、D04</td>
<td align="left">停止分享、轮换泄漏Token、应用版本回退</td>
<td align="left">公网入口关闭，本机仍可使用且数据不丢；旧凭据及旧连接失效；不执行清卷；不兼容迁移按恢复方案处理，审计记录完整</td>
</tr>
</table>

**部署观测**：share\_access\_denied记录入口、路由类别、拒绝原因及request\_id；share\_switch记录开启／关闭、操作者控制上下文与结果；backup\_restore\_check记录备份版本、恢复对象、实际RPO／RTO与核对结果。日志不含Token、私人正文或原始凭据；这些审计事件不构成访客身份验证。

***

## 参考来源

需求依据为本次会话讨论、六张用户参考图及既有实现方案；供应商网页仅证明已披露的产品能力，不证明本项目已经拥有权限。正文未指定具体报价或当前监管数值阈值。\[^1]: <https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/manual.html> 同花顺iFinD｜数据接口手册

[^2]: <https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/faq.html> 同花顺iFinD｜数据接口常见问题

[^3]: <https://choice.eastmoney.com/> 东方财富Choice｜数据服务与量化API

[^4]: <https://tushare.pro/document/1?doc_id=27> Tushare｜A股日线行情

[^5]: <https://tushare.pro/document/2?doc_id=298> Tushare｜涨跌停列表（新）

[^6]: <https://tushare.pro/document/1?doc_id=405> Tushare｜服务协议

[^7]: <https://webapi.cninfo.com.cn/#/technology> 深证信数据服务平台｜技术支持

[^8]: <https://bytedance.larkoffice.com/docx/EpT2dUlyYoGrrexLVCqcaHztnUe> 龙头战法复盘Web实现方案

[^9]: <https://bytedance.larkoffice.com/docx/SukidDEyPowG0gx6LEzc94rlnjh> 龙头战法量化开发方案

***

