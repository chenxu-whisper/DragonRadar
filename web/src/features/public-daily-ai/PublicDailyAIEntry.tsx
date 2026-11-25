import { lazy, Suspense, useState } from 'react';

const PublicDailyAI = lazy(() => import('./PublicDailyAI'));

export default function PublicDailyAIEntry({ snapshotId, date }: {
  snapshotId: string; date: string;
}) {
  const [open, setOpen] = useState(false);
  return <details onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>复核日报 · 公开 AI 归档</summary>
    {open && <Suspense fallback={<p role="status">正在加载复核日报…</p>}>
      <PublicDailyAI key={`${snapshotId}:${date}`} snapshotId={snapshotId} date={date} />
    </Suspense>}
  </details>;
}
