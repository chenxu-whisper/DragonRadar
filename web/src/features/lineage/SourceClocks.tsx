import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { fetchLineage, LineageHttpError } from './api';
import { useSourceClockDenial } from './SourceClockBoundary';
import { useAccess } from '../../hooks/useResource';
import { LineageLifecycle } from './lifecycle';
import type { AcceptedLineage, LineageSelection } from './types';
import { selectionKey } from './validation';

const DomainClocks = lazy(() => import('./DomainClocks').then(module => ({
  default: module.DomainClocks,
})));

export interface SourceClocksProps {
  selection: Exclude<LineageSelection, { type: 'daily' }>;
  label?: string;
  refreshToken?: number;
}

function SourceClockDetails({ selection, refreshToken = 0 }: SourceClocksProps) {
  const denyParent = useSourceClockDenial();
  const { revoke } = useAccess();
  const lifecycle = useRef(new LineageLifecycle());
  const [accepted, setAccepted] = useState<AcceptedLineage | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(false);
  const key = selectionKey(selection);
  useEffect(() => {
    const controller = new AbortController();
    lifecycle.current.reset();
    setAccepted(null);
    setLoading(true);
    const ticket = lifecycle.current.begin(selection);
    const denied = () => {
      controller.abort();
      lifecycle.current.deny(ticket);
      setAccepted(null);
      setLoading(false);
    };
    const events = ['private-source-revoked', 'private-session-lock', 'dragon-source-revoked'];
    events.forEach(event => window.addEventListener(event, denied));
    Promise.resolve().then(() => {
      if (controller.signal.aborted) throw new DOMException('Cancelled', 'AbortError');
      return fetchLineage(ticket.selection, controller.signal);
    }).then(value => {
      if (!controller.signal.aborted && lifecycle.current.accept(ticket, value, new Date())) {
        setAccepted(lifecycle.current.current);
      }
    }).catch(error => {
      if (controller.signal.aborted) return;
      lifecycle.current.deny(ticket);
      setAccepted(null);
      if (error instanceof LineageHttpError && [401, 403].includes(error.status)) {
        const status = error.status as 401 | 403;
        denyParent?.(status);
        if (status === 401) window.dispatchEvent(new Event('private-session-lock'));
        else if (!denyParent) {
          revoke();
          window.dispatchEvent(new Event('private-source-revoked'));
        }
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => {
      controller.abort();
      lifecycle.current.reset();
      events.forEach(event => window.removeEventListener(event, denied));
    };
  }, [key, refreshToken, attempt, denyParent, revoke]);
  return <>
    {loading ? <p role="status">Loading clock details…</p> : <DomainClocks value={accepted} />}
    <button type="button" disabled={loading} onClick={() => setAttempt(value => value + 1)}>
      Refresh clock details
    </button>
  </>;
}

export default function SourceClocks(props: SourceClocksProps) {
  const [open, setOpen] = useState(false);
  if (import.meta.env.VITE_PUBLIC_SHARING === '1') return null;
  let key: string;
  try {
    if (props.selection.type !== 'realtime' && props.selection.type !== 'lhb') {
      throw new Error('Specialized identity required');
    }
    key = selectionKey(props.selection);
  } catch {
    return <p role="status">Source identity unavailable.</p>;
  }
  return <details open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{props.label || 'Source clock details'}</summary>
    {open && <Suspense fallback={<p role="status">Loading clock details…</p>}>
      <SourceClockDetails key={key} {...props} />
    </Suspense>}
  </details>;
}
