import { useState } from 'react';
import { localRepository, type WatchContext } from '../../repositories/local/repository';

export default function LocalReviewActions({ snapshot, security, context }: {
  snapshot: string; security: string; context?: WatchContext;
}) {
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const add = async () => {
    setError('');
    try {
      await localRepository.putWatchlist(security, 'add', crypto.randomUUID(), snapshot, context);
      setStatus('已加入本地自选');
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '加入自选失败');
    }
  };
  return <div className="local-review-actions">
    <button type="button" onClick={() => void add()}>加入本地自选</button>
    <button type="button" onClick={() => window.dispatchEvent(new CustomEvent('local-open-review', {
      detail: context?.trade_date ? {
        security, snapshot, date: context.trade_date, ...(context.sector_id ? { sector: context.sector_id } : {}),
      } : security,
    }))}>记录本地判断</button>
    <span role="status">{status}</span>
    {error && <span role="alert">{error}</span>}
  </div>;
}
