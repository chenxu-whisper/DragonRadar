import { Component, type ErrorInfo, type ReactNode } from 'react';

// Section-scoped fallback for lazy chunk rejection (network failure or stale
// deployment hash). Kept outside components/Common.tsx on purpose: Common is
// statically reachable from the entry bundle, while this file only loads with
// role-feature lazy chunks. The fallback never replaces the whole page and
// never renders private-workbench copy.
export function SectionLoadError({ name }: { name: string }) {
  return <div className="notice error" role="alert">
    <span>{name}加载失败，请检查本机服务后重试。</span>
    <button type="button" onClick={() => window.location.reload()}>重新加载并重试</button>
  </div>;
}

export class SectionBoundary extends Component<
  { name: string; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(_error: unknown, _info: ErrorInfo) {
    // The underlying module error is intentionally neither retained nor rendered.
  }

  render() {
    if (this.state.failed) return <SectionLoadError name={this.props.name} />;
    return this.props.children;
  }
}
