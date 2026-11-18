import { lazy, Suspense } from 'react';
import { LazyLoadBoundary } from '../components/LazyLoadBoundary';
import type { OverviewProps } from './OverviewContent';

const OverviewPage = lazy(async () => ({
  default: (await import('./OverviewContent')).OverviewContent,
}));

export function Overview(props: OverviewProps) {
  return <LazyLoadBoundary><Suspense fallback={<p role="status">正在加载市场总览…</p>}>
    <OverviewPage {...props} />
  </Suspense></LazyLoadBoundary>;
}
