import { lazy, Suspense } from 'react';

import type { ResearchState } from '../state';

const RegulationContent = lazy(async () => {
  const module = await import('../features/regulation/RegulationContent');
  return { default: module.RegulationContent };
});

export function Regulation({
  state,
  refreshToken,
}: {
  state: ResearchState;
  refreshToken: number;
}) {
  return <Suspense fallback={<p className="muted">加载监管视图...</p>}>
    <RegulationContent state={state} refreshToken={refreshToken} />
  </Suspense>;
}
