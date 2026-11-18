import { useEffect, useState } from 'react';
import type { ResearchState, View } from '../../state';
import type { Patch } from '../../components/Common';
import { shouldHandleSpaClick } from '../../components/Common';
import './workbench.css';

const owner = import.meta.env.VITE_PUBLIC_SHARING !== '1';
interface Module { id: string; title: string; description: string }
const module = (id: string, title: string, description: string): Module => ({ id, title, description });
const modules: Partial<Record<View, Module[]>> = {
  overview: [
    module('indexes', '指数行情', '指数价格、涨跌幅与成交额'),
    module('ecology', '涨停生态', '涨停 / 跌停、最高板与晋级率'),
    module('breadth', '涨跌分布', '上涨、下跌、平盘与封板率'),
    module('sentiment', '情绪原始观测', '原始观测序列与覆盖率'),
    module('topics', '题材热度 TOP', '上涨占比、涨停占比与成员收益'),
    module('sector-flow', '板块资金流', '板块排行与授权分时资金流'),
    module('heatmap', '涨停情绪热力图', '日期 × 连板数 × 涨停家数'),
    module('daily-review', 'AI 每日速览', '事实、解释与待验证事项'),
    module('realtime', '实时看盘', '分钟行情与应用内异动'),
    module('sentiment-research', '情绪研究', '研究分、阶段与版本化公式'),
    module('factors', '市场因子', '广度、成交、指数趋势与波动'),
    module('lhb', '龙虎榜', '选择证券查看已披露席位证据'),
  ],
  news: [
    module('news-list', '消息列表', '按类型、来源、关键词筛选'),
    module('news-detail', '消息详情与 AI 摘要', '原文、来源与事实 / 推断分层'),
    module('wordcloud', '新闻词云', '关键词频次及计算方法'),
    module('topic-suggest', 'AI 题材建议', '支持新闻、关联证券与审核状态'),
  ],
  leaders: [
    module('ladder', '连板矩阵', '日期 × 板数与证券下钻'),
    module('continuity', '晋级与昨日反馈', '晋级分子 / 分母及昨日涨停反馈'),
    module('candidates', '六类角色候选', '候选、证据不足与待验证'),
    module('rotation', '题材轮动', '题材演进、核心更替与版本'),
  ],
  analysis: [
    module('ranking', '区间排行', '区间收益、超额收益、回撤与成交变化'),
    module('comparison', '多标的比较', '最多五个标的的同口径曲线'),
    module('query', '自然语言查询', '解析、预览并确认白名单查询'),
    ...(owner ? [module('backtest', '研究回测', '历史回测、因子体检、滚动验证与 AI 对照')] : []),
  ],
  regulation: [
    module('completeness', '数据完整性', '来源校验、覆盖率与缺失数量'),
    module('calculation', '数值计算状态', '版本化计算与独立阈值'),
    module('events', '正式事件与限制', '已披露事件、适用范围与生效时间'),
  ],
  stock: [module('lhb', '龙虎榜席位证据', '个股已披露席位、买卖金额与历史记录')],
  ...(owner ? { review: [
    module('notes', '自选与笔记', '浏览器保存、修订与历史验证'),
    module('review-ai', 'AI 复盘草稿', '明确选择片段后生成与人工确认'),
    module('simulation', '模拟组合', '本地模拟账本与研究约束'),
    module('backup', '备份与恢复', '导出本机资料与合并恢复'),
  ] } : {}),
};
const titles: Partial<Record<View, string>> = {
  overview: '市场总览', news: '市场消息', leaders: '龙头梯队',
  analysis: '涨幅分析', regulation: '监管观察', stock: '个股详情',
};
interface NavigationProps {
  state: ResearchState;
  destination: (view: View) => ResearchState;
  urlFor: (state: ResearchState) => string;
  patch: Patch;
  contentReady?: boolean;
}
function ModuleLink({ item, view, state, destination, urlFor, patch }: NavigationProps & { item: Module; view: View }) {
  const next = { ...(view === state.view ? state : destination(view)), section: item.id };
  return <a href={urlFor(next)} aria-current={state.view === view && state.section === item.id ? 'location' : undefined}
    onClick={event => {
      if (shouldHandleSpaClick(event)) { event.preventDefault(); patch(next); }
    }}>{item.title}</a>;
}
export function ModuleNavigation(props: NavigationProps) {
  const items = modules[props.state.view] || [];
  useEffect(() => {
    const id = props.state.section;
    if (!id || !items.some(item => item.id === id)) return;
    let positioned: HTMLElement | null = null;
    const find = () => {
      const target = document.getElementById(`workbench-${id}`);
      if (!target) return false;
      if (target === positioned) return true;
      positioned = target;
      const header = document.querySelector('.snapshot-header');
      const clearance = header && getComputedStyle(header).position === 'sticky' ? header.getBoundingClientRect().height : 0;
      window.scrollTo({ top: Math.max(0, window.scrollY + target.getBoundingClientRect().top - clearance - 16), behavior: 'instant' });
      target.focus({ preventScroll: true });
      return true;
    };
    find();
    const observer = new MutationObserver(() => { find(); });
    observer.observe(document.getElementById('main') || document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [props.state.section, props.state.view, props.state.snapshot, props.contentReady, items]);
  return <nav className="workbench-modules" aria-label="页面模块">
    {items.map(item => <ModuleLink key={item.id} {...props} item={item} view={props.state.view} />)}
  </nav>;
}
export function CapabilityNavigation(props: NavigationProps) {
  const tools: [View, string, string][] = [
    ['overview', 'realtime', '实时看盘'],
    ['overview', 'lhb', '龙虎榜'],
    ['leaders', 'rotation', '题材轮动'],
    ['analysis', 'query', '自然语言查询'],
    ...(owner ? [
      ['analysis', 'backtest', '研究回测'],
      ['review', 'simulation', '模拟组合'],
    ] as [View, string, string][] : []),
  ];
  return <nav className="workbench-tools" aria-label="研究工具">
    <span className="workbench-nav-label">研究工具</span>
    {tools.map(([view, id, title]) =>
      <ModuleLink key={id} {...props} view={view} item={module(id, title, '')} />)}
  </nav>;
}
export function EmptyPage({ state, loading, failed }: { state: ResearchState; loading: boolean; failed: boolean }) {
  return <div className="workbench-empty-page">
    <div className="page-heading"><div><p className="eyebrow">盘后复盘工作台</p><h1>{titles[state.view] || '市场总览'}</h1></div>
      <span className="workbench-state">{loading ? '正在读取快照' : failed ? '快照不可用' : '未选择快照'}</span></div>
    <div className="workbench-empty-notice" role="status">
      <strong>{loading ? '正在载入匹配快照' : state.date ? '该日期没有可用快照' : '暂无可用快照'}</strong>
      <p>{failed ? '快照读取失败，请通过上方重试恢复。' : '请选择有已发布快照的交易日，或在本机完成数据导入与发布后刷新。'}来源、覆盖率与质量未知。</p>
    </div>
    <div className={`workbench-empty-grid ${state.view === 'overview' ? 'workbench-empty-overview' : ''}`}>
      {(modules[state.view] || []).filter(item => !['query', 'backtest', 'lhb'].includes(item.id)).map(item =>
        <section key={item.id} id={`workbench-${item.id}`} tabIndex={-1} aria-label={item.title} className={`workbench-empty-module module-${item.id}`} aria-busy={loading}>
          <h2>{item.title}</h2><p className="muted">{item.description}</p>
          <div className="workbench-empty-value">—</div>
          <p className="muted">{loading ? '正在读取匹配快照' : '未选择可读快照，当前模块不可用。'}</p>
        </section>)}
    </div>
  </div>;
}
export function UnavailableModules({ view, ids, loading = false }: { view: View; ids: readonly string[]; loading?: boolean }) {
  return <div className="workbench-empty-grid">
    {(modules[view] || []).filter(item => ids.includes(item.id)).map(item =>
      <section key={item.id} id={`workbench-${item.id}`} tabIndex={-1} aria-label={item.title}
        className={`workbench-empty-module module-${item.id}`} aria-busy={loading}>
        <h2>{item.title}</h2><p className="muted">{item.description}</p>
        <p className="muted">{loading ? '正在读取当前快照的模块数据。' : '当前快照的模块数据不可用，来源、覆盖率与结果未知。'}</p>
      </section>)}
  </div>;
}
export function LhbEntry({ state, patch }: { state: ResearchState; patch: Patch }) {
  const [security, setSecurity] = useState('');
  return <section id="workbench-lhb" tabIndex={-1} aria-label="龙虎榜入口" className="workbench-lhb-selector">
    <h2>龙虎榜</h2><p className="muted">按所选快照查看个股已披露席位、买卖金额与历史记录。可从行情列表打开个股详情，或输入证券 ID。</p>
    <form className="search-form" onSubmit={event => {
      event.preventDefault();
      if (state.snapshot && security.trim()) patch({ view: 'stock', security: security.trim(), section: 'lhb' });
    }}>
      <label>证券 ID<input value={security} onChange={event => setSecurity(event.target.value)} maxLength={200} placeholder="如 SSE.600519" /></label>
      <button disabled={!state.snapshot || !security.trim()}>查看龙虎榜证据</button>
    </form>
    {!state.snapshot && <p className="muted">未选择可读快照，当前模块不可用。</p>}
  </section>;
}
