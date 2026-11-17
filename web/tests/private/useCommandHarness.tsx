import { useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { SessionContext } from '../../src/features/review/SessionContext';
import { SessionStore } from '../../src/features/review/session';
import { useCommand } from '../../src/features/review/useCommand';

type ContextChange = 'slot' | 'owner' | 'epoch';

interface HarnessState {
  store: SessionStore;
  deferred: Array<{
    resolve: (value: { source: string }) => void;
    reject: (reason: unknown) => void;
  }>;
  clearFeedbacks: Array<(() => void) | undefined>;
  accepted: string[];
  renders: Array<{ generation: number; busy: boolean; error: string; conflict: boolean }>;
}

declare global {
  interface Window {
    commandHarness: HarnessState;
  }
}

export async function mountUseCommandHarness(contextChange: ContextChange) {
  document.body.replaceChildren();
  const store = new SessionStore();
  store.authenticate({
    owner_id: 'owner-a',
    session_id: 'session-a',
    expires_at: '2099-01-01T00:00:00Z',
  });
  const deferred: HarnessState['deferred'] = [];
  const clearFeedbacks: HarnessState['clearFeedbacks'] = [];
  const accepted: string[] = [];
  const renders: HarnessState['renders'] = [];
  const send = <T,>() => new Promise<T>((resolve, reject) => deferred.push({
    resolve: value => resolve(value as T),
    reject,
  }));
  const command = (key: string, slot: string) => ({
    key, method: 'POST' as const, path: `/watchlist/${slot}`, body: '{}',
  });

  function Harness() {
    const [slot, setSlot] = useState('slot-a');
    const [generation, setGeneration] = useState(0);
    const retried = useRef(false);
    const mutation = useCommand<{ source: string }>(slot);
    clearFeedbacks[generation] = mutation.clearFeedback;
    renders.push({
      generation,
      busy: mutation.busy,
      error: mutation.error,
      conflict: mutation.conflict !== undefined,
    });
    useLayoutEffect(() => {
      if (generation === 1 && !retried.current) {
        retried.current = true;
        void mutation.execute(
          () => command('command-b', slot),
          data => accepted.push(data.source),
        );
      }
    }, [generation]);
    return <>
      <output id="command-state">{slot}:{mutation.busy ? 'busy' : 'idle'}</output>
      <button id="start" onClick={() => void mutation.execute(
        () => command('command-a', slot),
        data => accepted.push(data.source),
      )}>start</button>
      <button id="clear-feedback" onClick={mutation.clearFeedback}>clear feedback</button>
      <button id="change" onClick={() => {
        if (contextChange === 'slot') setSlot('slot-b');
        if (contextChange === 'owner') {
          store.owner = 'owner-b';
          store.commands.clear();
        }
        if (contextChange === 'epoch') store.epoch++;
        setGeneration(1);
      }}>change</button>
    </>;
  }

  const host = document.createElement('div');
  host.id = 'use-command-harness';
  document.body.append(host);
  createRoot(host).render(
    <SessionContext value={{
      enabled: true,
      store,
      version: 0,
      read: async () => { throw new Error('unused'); },
      send,
      check: async () => {},
      logout: async () => {},
    }}>
      <Harness />
    </SessionContext>,
  );
  window.commandHarness = { store, deferred, clearFeedbacks, accepted, renders };
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}
