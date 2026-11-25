import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import SourceClocks from '../../src/features/lineage/SourceClocks';
import type { LineageSelection } from '../../src/features/lineage/types';
import RealtimePanel from '../../src/features/realtime/RealtimePanel';

const asOf = '2026-09-10T02:00:00Z';
function Fixture() {
  const [selection, setSelection] = useState<Exclude<LineageSelection, { type: 'daily' }>>({
    type: 'realtime', source_id: 'vendor', source_version: 'v1',
    kind: 'minutes', batch_id: 'batch-1', as_of: asOf,
  });
  const [shown, setShown] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const realtime = new URLSearchParams(location.search).get('surface') === 'realtime';
  return <main>
    <h1>Source clock integration fixture</h1>
    <button onClick={() => setShown(value => !value)}>Toggle mount</button>
    <button onClick={() => setRefresh(value => value + 1)}>Refresh context</button>
    <button onClick={() => setSelection({
      type: 'realtime', source_id: 'vendor', source_version: 'v2',
      kind: 'minutes', batch_id: 'batch-2', as_of: '2026-09-10T03:00:00Z',
    })}>Switch batch</button>
    <button onClick={() => setSelection({
      type: 'lhb', source_id: 'vendor', source_version: 'v3',
      disclosure_id: 'disclosure-1', revision: 3, as_of: asOf,
    })}>Show LHB</button>
    <button onClick={() => window.dispatchEvent(new Event('private-source-revoked'))}>Revoke source</button>
    {shown && (realtime ? <RealtimePanel date="2026-09-10" securityIds={['TEST001']}
      sectorId="SEC001" refreshToken={refresh} />
      : <SourceClocks selection={selection} refreshToken={refresh} />)}
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
