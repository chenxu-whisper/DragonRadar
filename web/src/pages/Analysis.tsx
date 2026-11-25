import { lazy, Suspense, type ComponentType } from 'react';
import type { ResearchState } from '../state';
import type { Snapshot } from '../types';
import type { Patch } from '../components/Common';
import { LazyLoadBoundary } from '../components/LazyLoadBoundary';
import type { ResetAnalysisProps } from '../features/analysis/ResetAnalysis';
import type { QueryPanelProps } from '../features/query/QueryPanel';

const ResetAnalysis = lazy(async () => ({
  default: (await import('../features/analysis/ResetAnalysis')).ResetAnalysis as ComponentType<ResetAnalysisProps>,
}));
const LegacyAnalysis = lazy(() => import('./LegacyAnalysis'));
// 白名单查询面板仅 reset 构建接线：模块级 env 常量折叠，非 reset 构建下动态 import 死代码消除，
// 不产出 query chunk（对照 App.tsx CommandPalette；勿学 ResetAnalysis 的无条件 lazy）。
// 挂载点在 App 主区且不被 {snapshot && …} 门控卸载：结果绑定旧快照条必须在改日期/版本
// （Analysis 整树会随 snapshot 短暂卸载重建）后继续保留，故由 Workbench 持久挂载。
const resetWorkbenchEnabled = import.meta.env.VITE_RESET_WORKBENCH === '1';
export const ResetQueryPanel = resetWorkbenchEnabled
  ? lazy(async () => ({ default: (await import('../features/query/QueryPanel')).QueryPanel as ComponentType<QueryPanelProps> }))
  : null;
// Owner-only wrapper owns both the panel and manifest subscription. Default/sharing
// builds fold away this import, so neither research modules nor hooks reach them.
export const ResetBacktestPanel = resetWorkbenchEnabled && import.meta.env.VITE_PUBLIC_SHARING !== '1'
  ? lazy(() => import('../features/research/OwnerBacktestWorkbench'))
  : null;

export interface AnalysisProps {
  state: ResearchState; patch: Patch; refreshToken: number;
  open: (id: string, sector: string | undefined, trigger: HTMLElement) => void; compare: (id: string) => void;
  snapshot?: Snapshot; reset?: boolean;
  ReviewActions?: ComponentType<{ snapshot: string; security: string }>;
}

export function Analysis(props: AnalysisProps) {
  if (props.reset) return <LazyLoadBoundary><Suspense fallback={<p role="status" className="status">正在加载涨幅分析…</p>}>
    <ResetAnalysis {...props} />
  </Suspense></LazyLoadBoundary>;
  return <LazyLoadBoundary><Suspense fallback={<p role="status" className="status">正在加载区间分析…</p>}>
    <LegacyAnalysis {...props} />
  </Suspense></LazyLoadBoundary>;
}
