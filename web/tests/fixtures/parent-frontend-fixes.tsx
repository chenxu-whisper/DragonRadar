import { useEffect, useState, useSyncExternalStore } from 'react';
import { createRoot } from 'react-dom/client';
import OverviewAITools from '../../src/features/ai/OverviewAITools';
import TaskPanel from '../../src/features/ai/TaskPanel';
import PublicationPanel from '../../src/features/ai/PublicationPanel';
import { SessionStore } from '../../src/features/review/session';
import { SessionContext } from '../../src/features/review/SessionContext';
import { AccessProvider } from '../../src/hooks/useResource';
import RealtimePanel from '../../src/features/realtime/RealtimePanel';
import { SeatsPanel } from '../../src/features/lhb/SeatsPanel';
import { Comparison } from '../../src/components/Comparison';
import { News } from '../../src/pages/News';
import { parseState } from '../../src/state';
import { PUB, OWNER, SESSION, TASK, DATE, AS_OF, calendar } from './parent-frontend-fixes-data';

function Fixture() {
  const surface = new URLSearchParams(location.search).get('surface') || 'daily';
  const [store] = useState(() => {
    const value = new SessionStore();
    value.authenticate({ owner_id: OWNER, session_id: SESSION, expires_at: '2099-01-01T00:00:00Z' });
    return value;
  });
  const version = useSyncExternalStore(store.subscribe, store.snapshot);
  const [refresh, setRefresh] = useState(0);
  const [state, setState] = useState(() => parseState(
    `?view=overview&snapshot=${PUB}&date=${DATE}&compare=A&n=20&adj=raw&page_size=20`, false, true,
  ).state);
  useEffect(() => {
    window.addEventListener('private-session-lock', store.lock);
    return () => window.removeEventListener('private-session-lock', store.lock);
  }, [store]);
  const patch = (change: Partial<typeof state>) => { setState(previous => ({ ...previous, ...change })); return true; };
  const props = { snapshotId: PUB, date: DATE, asOf: AS_OF, version: 'v1' };
  const content = <main>
    <h1>Frontend boundary regressions</h1>
    <p data-testid="principal">{store.locked ? 'locked' : `${store.owner}:${store.session?.session_id}`}</p>
    <button onClick={() => store.authenticate({
      owner_id: TASK, session_id: SESSION, expires_at: '2099-01-01T00:00:00Z',
    })}>Switch owner</button>
    <button onClick={() => store.authenticate({
      owner_id: OWNER, session_id: TASK, expires_at: '2099-01-01T00:00:00Z',
    })}>Switch session</button>
    <button onClick={() => setRefresh(value => value + 1)}>Refresh context</button>
    <button onClick={() => patch({ view: 'analysis' })}>Navigate analysis</button>
    <button onClick={() => patch({ return_view: 'news', return_snapshot: TASK, return_date: DATE, return_sector: 'SEC001' })}>
      Change return context
    </button>
    {surface === 'daily' && <OverviewAITools {...props} marketContext="Private local market notes" />}
    {surface === 'notes' && <TaskPanel {...props} taskType="daily_review" note={{
      title: 'Private original note', content: '', judgment: '', evidence: '', invalid_condition: '', verify_date: '',
    }} />}
    {surface.startsWith('publication') && <PublicationPanel taskId={TASK} revision={3} snapshotId={PUB} date={DATE} />}
    {surface === 'realtime' && <RealtimePanel date={DATE} securityIds={['TEST001']} refreshToken={refresh} />}
    {surface === 'lhb' && <SeatsPanel date={DATE} securityId="TEST001" asOf={AS_OF} refreshToken={refresh} />}
    {surface === 'comparison' && <Comparison state={state} patch={patch} reset refreshToken={refresh}
      snapshot={{ result: { calendar_sessions: calendar } } as never} />}
    {surface === 'news' && <News state={state} patch={patch} refreshToken={refresh} open={() => {}} />}
  </main>;
  return <AccessProvider>{surface === 'publication-reset' ? content : <SessionContext value={{
    enabled: true, store, version, read: async () => { throw new Error('Not used'); },
    send: async () => { throw new Error('Not used'); }, check: async () => {}, logout: async () => {},
  }}>{content}</SessionContext>}</AccessProvider>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
