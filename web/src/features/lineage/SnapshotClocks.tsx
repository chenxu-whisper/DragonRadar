import { useEffect, useRef, useState } from 'react';
import { useAccess } from '../../hooks/useResource';
import { fetchLineage, LineageHttpError } from './api';
import { DomainClocks } from './DomainClocks';
import { LineageLifecycle } from './lifecycle';
import type { AcceptedLineage } from './types';

export default function SnapshotClocks({ snapshotId, date, refreshToken }: {
  snapshotId: string; date: string; refreshToken: number;
}) {
  const { denied, revoke, epoch } = useAccess();
  const lifecycle = useRef(new LineageLifecycle());
  const [accepted, setAccepted] = useState<AcceptedLineage | null>(null);
  const key = `${snapshotId}:${date}:${epoch}:${refreshToken}`;
  const [visibleKey, setVisibleKey] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    lifecycle.current.reset();
    setAccepted(null);
    setVisibleKey(key);
    if (denied || !snapshotId || !date) return;
    const selection = { type: 'daily' as const, snapshot_id: snapshotId, trade_date: date };
    const ticket = lifecycle.current.begin(selection);
    Promise.resolve().then(() => {
      if (controller.signal.aborted) throw new DOMException('请求已取消', 'AbortError');
      return fetchLineage(selection, controller.signal);
    }).then(value => {
      if (lifecycle.current.accept(ticket, value, new Date())) {
        setAccepted(lifecycle.current.current);
      }
    }).catch(error => {
      if (controller.signal.aborted) return;
      lifecycle.current.deny(ticket);
      setAccepted(null);
      if (error instanceof LineageHttpError && error.status === 403) revoke();
    });
    return () => { controller.abort(); lifecycle.current.reset(); };
  }, [snapshotId, date, key, denied, revoke]);
  return <DomainClocks value={!denied && visibleKey === key ? accepted : null} />;
}
