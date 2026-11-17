import { useEffect, useState } from 'react';
import BacktestPanel from './BacktestPanel';
import {
  EMPTY_MANIFEST_RESOURCE,
  subscribeBacktestManifest,
  type BacktestManifestResource,
} from './manifest-resource';

export default function OwnerBacktestWorkbench({ snapshotId }: { snapshotId: string }) {
  const [manifestResource, setManifestResource] = useState<BacktestManifestResource>(EMPTY_MANIFEST_RESOURCE);
  useEffect(() => {
    if (!snapshotId) {
      setManifestResource(EMPTY_MANIFEST_RESOURCE);
      return;
    }
    return subscribeBacktestManifest(snapshotId, setManifestResource);
  }, [snapshotId]);
  return <BacktestPanel manifest={manifestResource.manifest} />;
}
