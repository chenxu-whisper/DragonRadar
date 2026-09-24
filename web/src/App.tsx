import { lazy, Suspense, useEffect, useState, type ComponentType } from 'react';
import { ArrowLeft, BarChart3, ChartNoAxesCombined, ChevronLeft, Menu, Radar, Newspaper, Layers, ShieldCheck, NotebookPen } from 'lucide-react';
import { useLocation } from './hooks/useLocation';
import { AccessProvider, useAccess, useResource } from './hooks/useResource';
import { parseState, stateSearch, toggleCompare, type ResearchState } from './state';
import type { ResetSnapshotData, Snapshot, WorkbenchEnvelope } from './types';
import { ResourceStatus, shouldHandleSpaClick, SnapshotEvidence } from './components/Common';
import { SnapshotHeader } from './components/SnapshotHeader';
import { CompareMembership } from './components/CompareMembership';
import { LazyLoadBoundary } from './components/LazyLoadBoundary';
import type { ComparisonProps } from './components/Comparison';
import type { Patch } from './components/Common';
import { IntervalForm } from './components/IntervalForm';
import { Overview } from './pages/Overview';
import { Analysis } from './pages/Analysis';
import { Regulation } from './pages/Regulation';
import { verifiedSnapshotTradeDate } from './features/local-review/note-command';
import type { LeadersProps } from './pages/Leaders';
import type { SecurityDrawerProps, SecurityPageProps } from './pages/SecurityDetail';

const resetEnabled = import.meta.env.VITE_RESET_WORKBENCH === '1';
type ResetSnapshot = WorkbenchEnvelope<ResetSnapshotData>;
type ResetSnapshotList = WorkbenchEnvelope<{ snapshots: ResetSnapshot[] }>;

function legacySnapshot(value: ResetSnapshot): Snapshot {
  return {
    snapshot_id: value.snapshot_id, trade_date: value.trade_date, as_of: value.as_of,
    source_time: null, source: { id: value.source, version: value.source_version },
    coverage: { ...value.coverage, legal_excluded: value.coverage.excluded },
    coverage_scope: 'snapshot_current', analysis_coverage: null,
    units: { currency: value.basis.currency ? [value.basis.currency] : [], price_unit: [], volume_unit: [], amount_unit: [] },
    quality_flags: value.quality_flags, rule_version: value.rule_version, basis: 'published_market',
    manifest: { rule_version: value.rule_version }, provenance: 'contemporaneous',
    result: {
      ...value.data,
      calendar_sessions: value.data.calendar_sessions || [],
      earliest_date: value.data.earliest_date || null,
    },
  };
}
const LocalReview = import.meta.env.VITE_RESET_WORKBENCH === '1'
  ? lazy(() => import('./features/local-review/LocalReview'))
  : null;
const LocalReviewActions = import.meta.env.VITE_RESET_WORKBENCH === '1'
  ? lazy(() => import('./features/local-review/LocalReviewActions'))
  : null;
const Leaders = import.meta.env.VITE_RESET_WORKBENCH === '1'
  ? lazy(async () => ({ default: (await import('./pages/Leaders')).Leaders as ComponentType<LeadersProps> }))
  : null;
const News = import.meta.env.VITE_RESET_WORKBENCH === '1'
  ? lazy(async () => ({ default: (await import('./pages/News')).News }))
  : null;
const SecurityDrawer = lazy(async () => ({
  default: (await import('./pages/SecurityDetail')).SecurityDrawer as ComponentType<SecurityDrawerProps>,
}));
const SecurityPage = lazy(async () => ({
  default: (await import('./pages/SecurityDetail')).SecurityPage as ComponentType<SecurityPageProps>,
}));
const Comparison = lazy(async () => ({
  default: (await import('./components/Comparison')).Comparison as ComponentType<ComparisonProps>,
}));

function ResetReviewActions(props: { snapshot: string; security: string }) {
  return LocalReviewActions
    ? <Suspense fallback={<span className="muted">正在加载本地操作…</span>}><LocalReviewActions {...props} /></Suspense>
    : null;
}

interface PrivateFeatures {
  locked: boolean;
  navigationLabel: string;
  Toolbar: ComponentType;
  Workspace: ComponentType<{ state: ResearchState; snapshot?: Snapshot; patch: Patch }>;
  ReviewActions: ComponentType<{ snapshot: string; security: string }>;
}

export function Workbench({ privateFeatures }: { privateFeatures?: PrivateFeatures }) {
  const privateEnabled = Boolean(privateFeatures);
  const PrivateToolbar = privateFeatures?.Toolbar;
  const PrivateWorkspace = privateFeatures?.Workspace;
  const ReviewActions = privateFeatures?.ReviewActions;
  const { state, patch, destination, urlFor } = useLocation(privateEnabled, resetEnabled);
  const { denied, retry } = useAccess();
  const [notice, setNotice] = useState(() => parseState(window.location.search, privateEnabled, resetEnabled, window.location.pathname).notices.join(' '));
  const [menu, setMenu] = useState(false);
  const [refreshToken, setRefreshToken] = useState(0);
  const [drawer, setDrawer] = useState<{ id: string; sector?: string; trigger: HTMLElement; context: string } | null>(null);
  const context = stateSearch(state, privateEnabled, resetEnabled);
  const PendingPage = resetEnabled ? ({ regulation: Regulation } as Partial<Record<ResearchState['view'], ComponentType>>)[state.view] : undefined;
  const list = useResource<Snapshot[] | ResetSnapshotList>(resetEnabled
    ? `/api/snapshots/list?${new URLSearchParams({ ...(state.date ? { date: state.date } : {}), limit: '100', offset: '0' })}`
    : !state.snapshot ? `/api/v1/snapshots?${new URLSearchParams({
      ...(state.date ? { trade_date: state.date } : {}), limit: '100', offset: '0',
    })}` : null, undefined, refreshToken);
  const resetSnapshots = resetEnabled && list.data && !Array.isArray(list.data)
    ? list.data.data.snapshots.map(legacySnapshot) : [];
  const legacySnapshots = Array.isArray(list.data) ? list.data : [];
  const availableSnapshots = resetEnabled ? resetSnapshots : legacySnapshots;
  const summary = useResource<Snapshot>(!resetEnabled && state.snapshot ? `/api/v1/snapshots/${state.snapshot}` : null, state.snapshot, refreshToken);
  const resetSummary = useResource<ResetSnapshot>(resetEnabled && state.snapshot ? `/api/snapshots/${state.snapshot}` : null, state.snapshot, refreshToken);
  const selectedSummary = resetEnabled
    ? resetSummary.data ? legacySnapshot(resetSummary.data) : undefined
    : summary.data;
  const snapshot = selectedSummary && (!state.date || state.date === selectedSummary.trade_date) ? selectedSummary : undefined;
  useEffect(() => {
    if (!privateEnabled) return;
    const openNote = (event: Event) => { setDrawer(null); patch({ view: 'review', note: (event as CustomEvent<string>).detail }, true); };
    window.addEventListener('private-open-note', openNote);
    return () => window.removeEventListener('private-open-note', openNote);
  }, [patch, privateEnabled]);
  useEffect(() => {
    if (!resetEnabled) return;
    const openReview = (event: Event) => {
      setDrawer(null);
      patch({
        view: 'review',
        security: (event as CustomEvent<string>).detail,
        return_view: state.view === 'leaders' ? 'leaders' : state.view === 'analysis' ? 'analysis' : state.view === 'news' ? 'news' : 'overview',
      });
    };
    window.addEventListener('local-open-review', openReview);
    return () => window.removeEventListener('local-open-review', openReview);
  }, [patch, state.view]);
  useEffect(() => {
    if (!state.snapshot && availableSnapshots.length && !denied) {
      const chosen = availableSnapshots[0];
      patch({ snapshot: chosen.snapshot_id, date: chosen.trade_date }, true);
    }
  }, [availableSnapshots, state.snapshot, denied, patch]);
  useEffect(() => {
    if (selectedSummary && state.date !== selectedSummary.trade_date) {
      if (state.date) setNotice('网址日期与快照不一致，已按指定快照的实际交易日期纠正。');
      patch({ date: selectedSummary.trade_date }, true);
    }
  }, [selectedSummary, state.date, patch]);
  useEffect(() => { setDrawer(null); }, [context, denied, privateFeatures?.locked]);
  useEffect(() => {
    if (denied) { setNotice(''); patch({ compare: [], ...(resetEnabled && state.view === 'review' ? {} : { security: '' }) }, true); }
  }, [denied, context, patch, state.view]);
  const refresh = () => { if (denied) retry(); else setRefreshToken(v => v + 1); };
  const compare = (id: string) => {
    const result = toggleCompare(state.compare, id);
    if (result.error) setNotice(result.error);
    else if (patch({ compare: result.ids })) setNotice('');
  };
  const open = (id: string, sector: string | undefined, trigger: HTMLElement) =>
    setDrawer({ id, sector, trigger, context });
  const ActiveReviewActions = resetEnabled ? ResetReviewActions : ReviewActions;
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
        {resetEnabled ? ([
          { view: 'overview', text: '总览', Icon: BarChart3 },
          { view: 'news', text: '消息', Icon: Newspaper },
          { view: 'leaders', text: '梯队', Icon: Layers },
          { view: 'analysis', text: '涨幅', Icon: ChartNoAxesCombined },
          { view: 'regulation', text: '监管', Icon: ShieldCheck },
          { view: 'review', text: '我的复盘', Icon: NotebookPen },
        ] as const).map(({ view, text, Icon }) =>
          <a key={view} href={urlFor(destination(view))} aria-current={state.view === view ? 'page' : undefined} onClick={event => {
            if (shouldHandleSpaClick(event)) { event.preventDefault(); if (patch(destination(view))) setMenu(false); }
          }}><Icon size={19} /><span>{text}</span></a>)
        : ([{ view: 'overview', text: '市场总览', Icon: BarChart3 }, { view: 'analysis', text: '区间分析', Icon: ChartNoAxesCombined }] as const).map(({ view, text, Icon }) =>
          <a key={view} href={stateSearch({ ...state, ...navigate(view) }, privateEnabled)} aria-current={state.view === view ? 'page' : undefined} onClick={e => {
            if (shouldHandleSpaClick(e)) { e.preventDefault(); if (patch(navigate(view))) setMenu(false); }
          }}><Icon size={19} /><span>{text}</span></a>)}
        {!resetEnabled && privateFeatures && <a href={stateSearch({ ...state, view: 'review' }, privateEnabled)} aria-current={state.view === 'review' ? 'page' : undefined} onClick={event => {
          if (shouldHandleSpaClick(event)) { event.preventDefault(); if (patch({ view: 'review' })) setMenu(false); }
        }}>{privateFeatures.navigationLabel}</a>}
      </nav>
      <div className="sidebar-note"><strong>只读 · 快照研究</strong><p>以指定版本为依据。<br />未知不等于零。</p><span>不含实时交易与投资建议</span></div>
    </aside>
    <div className="workspace">
      {PrivateToolbar && <PrivateToolbar />}
      <SnapshotHeader state={state} snapshot={snapshot} patch={patch} refresh={refresh} refreshing={summary.loading || resetSummary.loading || list.loading} refreshToken={refreshToken} reset={resetEnabled} />
      <main id="main" tabIndex={-1}>
        {notice && <div className="notice" role="alert"><span>{notice}</span><button onClick={() => setNotice('')}>关闭提示</button></div>}
        {LocalReview && state.view === 'review'
          ? <section aria-label="本地复盘">
            {state.return_view === 'leaders' && <a className="back-link" href={urlFor({ ...state, view: 'leaders', security: '', return_view: 'overview' })}
              onClick={event => {
                if (shouldHandleSpaClick(event)) {
                  event.preventDefault();
                  patch({ view: 'leaders', security: '', return_view: 'overview' });
                }
              }}><ArrowLeft size={18} />返回梯队</a>}
            <LazyLoadBoundary><Suspense fallback={<p role="status" className="status">正在加载本地复盘…</p>}>
              <LocalReview
                snapshot={state.snapshot || undefined}
                tradeDate={verifiedSnapshotTradeDate(state.snapshot, selectedSummary)}
                security={state.security || undefined}
              />
            </Suspense></LazyLoadBoundary>
          </section>
          : PrivateWorkspace && state.view === 'review'
          ? <PrivateWorkspace state={state} snapshot={denied ? undefined : snapshot} patch={patch} />
          : denied ? <div className="empty access-denied" role="alert"><h1>来源访问已被拒绝</h1><p>已清除所有受保护内容与内存中的来源数据。恢复授权后可重新请求。</p><button className="primary" onClick={retry}>重新检查访问权限</button></div> : PendingPage ? <PendingPage /> : <>
          <ResourceStatus resource={list} name="快照列表" />
          {resetEnabled
            ? <ResourceStatus resource={resetSummary} name="快照" />
            : <ResourceStatus resource={summary} name="快照" />}
          {!state.snapshot && availableSnapshots.length === 0 && !list.loading && <div className="empty"><h1>{state.date ? '该日期没有可用快照' : '暂无可用快照'}</h1><p>请选择其他日期，或在数据准备完成后刷新。不会使用其他日期替代。</p></div>}
          {snapshot && <>
            {!resetEnabled && <CompareMembership state={state} patch={patch} refreshToken={refreshToken} allowPrivate={privateEnabled} />}
            {state.view === 'overview' && <Overview state={state} patch={patch} refreshToken={refreshToken} open={open} compare={compare} ReviewActions={ActiveReviewActions} reset={resetEnabled} />}
            {state.view === 'analysis' && <Analysis state={state} patch={patch} refreshToken={refreshToken} open={open} compare={compare} snapshot={snapshot} reset={resetEnabled} ReviewActions={ActiveReviewActions} />}
            {News && state.view === 'news' && <LazyLoadBoundary><Suspense fallback={<p role="status" className="status">正在加载消息…</p>}>
              <News state={state} patch={patch} refreshToken={refreshToken} open={open} />
            </Suspense></LazyLoadBoundary>}
            {Leaders && state.view === 'leaders' && <LazyLoadBoundary><Suspense fallback={<p role="status" className="status">正在加载梯队…</p>}>
              <Leaders state={state} patch={patch} open={open} refreshToken={refreshToken} ReviewActions={ActiveReviewActions} />
            </Suspense></LazyLoadBoundary>}
            {state.view === 'stock' && <LazyLoadBoundary><Suspense fallback={<p role="status" className="status">正在加载证券详情…</p>}>
              <SecurityPage state={state} snapshot={snapshot} patch={patch} refreshToken={refreshToken} allowPrivate={privateEnabled} ReviewActions={ActiveReviewActions} />
            </Suspense></LazyLoadBoundary>}
            {['overview', 'analysis'].includes(state.view) && <>
              {state.view === 'overview' && state.compare.length > 0 && <IntervalForm state={state} patch={patch} reset={resetEnabled} />}
              <LazyLoadBoundary><Suspense fallback={<p role="status" className="status">正在加载比较…</p>}>
                <Comparison state={state} patch={patch} refreshToken={refreshToken} snapshot={snapshot} reset={resetEnabled} />
              </Suspense></LazyLoadBoundary>
            </>}
            <SnapshotEvidence snapshot={snapshot} />
            {drawer && drawer.context === context && state.view !== 'stock' && <LazyLoadBoundary><Suspense fallback={<p role="status" className="status">正在加载证券详情…</p>}>
              <SecurityDrawer state={state} snapshot={snapshot} id={drawer.id} sector={drawer.sector} trigger={drawer.trigger} close={() => setDrawer(null)}
                patch={patch} refreshToken={refreshToken} allowPrivate={privateEnabled} ReviewActions={ActiveReviewActions} />
            </Suspense></LazyLoadBoundary>}
          </>}
        </>}
      </main>
      {!denied && snapshot && (resetEnabled ? ['overview', 'analysis'].includes(state.view) : state.view !== 'stock') && state.compare.length > 0 && <div className="compare-shortcut" aria-label="比较快捷操作">
        <span role="status">已选 {state.compare.length} / 5 个标的{notice.includes('最多比较') ? '，已达上限，请先移除一个' : ''}</span>
        <button className="primary" onClick={() => {
          const target = document.getElementById('comparison-title');
          if (!target) return;
          const headerHeight = document.querySelector('.snapshot-header')?.getBoundingClientRect().height || 0;
          window.scrollTo({ top: window.scrollY + target.getBoundingClientRect().top - headerHeight - 16 });
          target.focus({ preventScroll: true });
        }}>前往比较</button>
        <button onClick={() => { if (patch({ compare: [] })) setNotice(''); }}>取消选择</button>
      </div>}
      <footer>只读研究 · 指标来自所选快照 · 数据缺失与排除单独披露</footer>
    </div>
  </div>;
}
function Session() {
  const { epoch } = useAccess();
  return <Workbench key={epoch} />;
}
const PrivateApp = import.meta.env.VITE_RESET_WORKBENCH !== '1' && import.meta.env.VITE_PRIVATE_REVIEW === '1'
  ? lazy(() => import('./features/review/PrivateApp'))
  : null;
export default function App() {
  return PrivateApp
    ? <LazyLoadBoundary>
      <Suspense fallback={<p role="status" className="status">正在加载私人复盘…</p>}><PrivateApp /></Suspense>
    </LazyLoadBoundary>
    : <AccessProvider><Session /></AccessProvider>;
}
