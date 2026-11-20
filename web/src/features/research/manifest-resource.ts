/**
 * Task 27 — Task 17 fail-closed owner manifest subscription for the reset workbench.
 *
 * The App never fabricates research manifest UUIDs: it subscribes to the owner
 * endpoint for the selected publication and feeds the panel either a validated
 * manifest or null (loading / error / cancel), which keeps the BacktestPanel
 * submission gate closed. The subscription factory is hook-free so the node
 * vitest suite can drive its full fetch/abort lifecycle without a DOM renderer;
 * App.tsx reaches it through a folded dynamic import so no research runtime code
 * enters the default entry chunk.
 */
import {
  getBacktestManifest,
  type BacktestManifestContext,
} from './backtest-client';

export interface BacktestManifestResource {
  manifest: BacktestManifestContext | null;
  loading: boolean;
}

export const EMPTY_MANIFEST_RESOURCE: BacktestManifestResource = {
  manifest: null,
  loading: false,
};

/**
 * Loads one manifest and streams resource states to onResource. The returned
 * cancel handle aborts the in-flight request and freezes the stream: a late
 * resolution, including the abort rejection itself, never reaches the caller.
 */
export function subscribeBacktestManifest(
  snapshotId: string,
  onResource: (resource: BacktestManifestResource) => void,
): () => void {
  const controller = new AbortController();
  let active = true;
  onResource({ manifest: null, loading: true });
  getBacktestManifest(snapshotId, controller.signal)
    .then(manifest => {
      if (active) onResource({ manifest, loading: false });
    })
    .catch((error: unknown) => {
      if (active && !(error instanceof DOMException && error.name === 'AbortError')) {
        onResource(EMPTY_MANIFEST_RESOURCE);
      }
    });
  return () => {
    active = false;
    controller.abort();
  };
}
