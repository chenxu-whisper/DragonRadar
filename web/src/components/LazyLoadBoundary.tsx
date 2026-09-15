import { Component, type ErrorInfo, type ReactNode } from 'react';

export class LazyLoadBoundary extends Component<{
  children: ReactNode;
}, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(_error: unknown, _info: ErrorInfo) {
    // The underlying module error is intentionally not rendered or retained.
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <main className="empty" role="alert">
      <h1>私人复盘加载失败</h1>
      <p>私人功能暂时无法加载。请检查本机服务后重试，已输入内容不会发送到其他位置。</p>
      <div className="toolbar">
        <button className="primary" onClick={() => window.location.reload()}>重新加载并重试</button>
      </div>
    </main>;
  }
}
