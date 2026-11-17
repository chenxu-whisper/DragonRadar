import { useState } from 'react';
import { localRepository } from '../../repositories/local/repository';

export default function LocalReviewActions({ snapshot, security }: { snapshot: string; security: string }) {
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const add = async () => {
    setError('');
    try {
      await localRepository.putWatchlist(security, 'add', crypto.randomUUID(), snapshot);
      setStatus('已加入本地自选');
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '加入自选失败');
    }
  };
  return <div className="local-review-actions">
    <button type="button" onClick={() => void add()}>加入本地自选</button>
    <button type="button" onClick={() => window.dispatchEvent(new CustomEvent('local-open-review', { detail: security }))}>记录本地判断</button>
    <span role="status">{status}</span>
    {error && <span role="alert">{error}</span>}
  </div>;
}
