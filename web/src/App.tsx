import { useEffect, useState } from 'react';
import { BarChart3, ChartNoAxesCombined, ChevronLeft, Menu, Radar } from 'lucide-react';
import { useLocation } from './hooks/useLocation';
import { AccessProvider, useAccess, useResource } from './hooks/useResource';
import { parseState, stateSearch, toggleCompare, type ResearchState } from './state';
import type { Snapshot } from './types';
import { ResourceStatus, SnapshotEvidence } from './components/Common';
import { SnapshotHeader } from './components/SnapshotHeader';
import { Comparison } from './components/Comparison';
import { CompareMembership } from './components/CompareMembership';
import { Overview } from './pages/Overview';
import { Analysis, IntervalForm } from './pages/Analysis';
import { SecurityDrawer, SecurityPage } from './pages/SecurityDetail';

function Workbench() {
  const { state, patch } = useLocation();
  const { denied, retry } = useAccess();
  const [notice, setNotice] = useState(() => parseState(window.location.search).notices.join(' '));
  const [menu, setMenu] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [drawer, setDrawer] = useState<{ id: string; trigger: HTMLElement; context: string } | null>(null);
  const context = stateSearch(state);
  const list = useResource<Snapshot[]>(!state.snapshot ? `/api/v1/snapshots?${new URLSearchParams({
    ...(state.date ? { trade_date: state.date } : {}), limit: '100', offset: '0',
  })}` : null, undefined, refreshToken);
  const summary = useResource<Snapshot>(state.snapshot ? `/api/v1/snapshots/${state.snapshot}` : null, state.snapshot, refreshToken);
  const snapshot = summary.data && (!state.date || state.date === summary.data.trade_date) ? summary.data : undefined;
  useEffect(() => {
    if (!state.snapshot && list.data?.length && !denied) {
      const chosen = list.data[0];
      patch({ snapshot: chosen.snapshot_id, date: chosen.trade_date }, true);
    }
  }, [list.data, state.snapshot, denied, patch]);
  useEffect(() => {
    if (summary.data && state.date !== summary.data.trade_date) {
      if (state.date) setNotice('网址日期与快照不一致，已按指定快照的实际交易日期纠正。');
      patch({ date: summary.data.trade_date }, true);
    }
  }, [summary.data, state.date, patch]);
  useEffect(() => { setDrawer(null); }, [context, denied]);
  useEffect(() => {
    if (denied) { setNotice(''); patch({ compare: [], security: '' }, true); }
  }, [denied, context, patch]);
  const refresh = () => { if (denied) retry(); else setRefreshToken(v => v + 1); };
  const compare = (id: string) => {
    const result = toggleCompare(state.compare, id);
    if (result.error) setNotice(result.error);
    else { setNotice(''); patch({ compare: result.ids }); }
  };
  const open = (id: string, trigger: HTMLElement) => setDrawer({ id, trigger, context });
  const navigate = (view: 'overview' | 'analysis'): Partial<ResearchState> => ({
    view, security: '', page: 1, sort: 'security_id', order: 'asc',
  });
  return <div className="app-shell">
    <a className="skip-link" href="#main">跳至研究内容</a>
    <aside className={`sidebar ${menu ? 'expanded' : ''}`}>
      <div className="brand"><Radar size={24} /><span>龙头雷达<small>盘后研究工作台</small></span>
        <button className="mobile-menu" aria-expanded={menu} aria-controls="primary-nav" aria-label={menu ? '收起导航' : '展开导航'} onClick={() => setMenu(v => !v)}>{menu ? <ChevronLeft size={20} /> : <Menu size={20} />}</button>
      </div>
      <nav id="primary-nav" aria-label="主导航">
        {([{ view: 'overview', text: '市场总览', Icon: BarChart3 }, { view: 'analysis', text: '区间分析', Icon: ChartNoAxesCombined }] as const).map(({ view, text, Icon }) =>
          <a key={view} href={stateSearch({ ...state, ...navigate(view) })} aria-current={state.view === view ? 'page' : undefined} onClick={e => {
            if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); patch(navigate(view)); setMenu(false); }
          }}><Icon size={19} /><span>{text}</span></a>)}
      </nav>
      <div className="sidebar-note"><strong>只读 · 快照研究</strong><p>以指定版本为依据。<br />未知不等于零。</p><span>不含实时交易与投资建议</span></div>
    </aside>
    <div className="workspace">
      <SnapshotHeader state={state} snapshot={snapshot} patch={patch} refresh={refresh} refreshing={summary.loading || list.loading} refreshToken={refreshToken} />
      <main id="main" tabIndex={-1}>
        {notice && <div className="notice" role="alert"><span>{notice}</span><button onClick={() => setNotice('')}>关闭提示</button></div>}
        {denied ? <div className="empty access-denied" role="alert"><h1>来源访问已被拒绝</h1><p>已清除所有受保护内容与内存中的来源数据。恢复授权后可重新请求。</p><button className="primary" onClick={retry}>重新检查访问权限</button></div> : <>
          <ResourceStatus resource={list} name="快照列表" />
          <ResourceStatus resource={summary} name="快照" />
          {!state.snapshot && list.data?.length === 0 && <div className="empty"><h1>{state.date ? '该日期没有可用快照' : '暂无可用快照'}</h1><p>请选择其他日期，或在数据准备完成后刷新。不会使用其他日期替代。</p></div>}
          {snapshot && <>
            <CompareMembership state={state} patch={patch} refreshToken={refreshToken} />
            {state.view === 'overview' && <Overview state={state} patch={patch} refreshToken={refreshToken} open={open} compare={compare} />}
            {state.view === 'analysis' && <Analysis state={state} patch={patch} refreshToken={refreshToken} open={open} compare={compare} />}
            {state.view === 'stock' && <SecurityPage state={state} patch={patch} refreshToken={refreshToken} />}
            {state.view !== 'stock' && <>
              {state.view === 'overview' && state.compare.length > 0 && <IntervalForm state={state} patch={patch} />}
              <Comparison state={state} patch={patch} refreshToken={refreshToken} />
            </>}
            <SnapshotEvidence snapshot={snapshot} />
            {drawer && drawer.context === context && state.view !== 'stock' && <SecurityDrawer state={state} id={drawer.id} trigger={drawer.trigger} close={() => setDrawer(null)} patch={patch} refreshToken={refreshToken} />}
          </>}
        </>}
      </main>
      {!denied && snapshot && state.view !== 'stock' && state.compare.length > 0 && <div className="compare-shortcut" aria-label="比较快捷操作">
        <span role="status">已选 {state.compare.length} / 5 个标的{notice.includes('最多比较') ? '，已达上限，请先移除一个' : ''}</span>
        <button className="primary" onClick={() => {
          const target = document.getElementById('comparison-title');
          if (!target) return;
          const headerHeight = document.querySelector('.snapshot-header')?.getBoundingClientRect().height || 0;
          window.scrollTo({ top: window.scrollY + target.getBoundingClientRect().top - headerHeight - 16 });
          target.focus({ preventScroll: true });
        }}>前往比较</button>
        <button onClick={() => { setNotice(''); patch({ compare: [] }); }}>取消选择</button>
      </div>}
      <footer>只读研究 · 指标来自所选快照 · 数据缺失与排除单独披露</footer>
    </div>
  </div>;
}
function Session() {
  const { epoch } = useAccess();
  return <Workbench key={epoch} />;
}
export default function App() {
  return <AccessProvider><Session /></AccessProvider>;
}
