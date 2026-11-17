import { Workbench } from '../../App';
import { AccessProvider, useAccess } from '../../hooks/useResource';
import type { ResearchState } from '../../state';
import type { Snapshot } from '../../types';
import type { Patch } from '../../components/Common';
import { ReviewActions } from './ReviewActions';
import { ReviewWorkspace } from './ReviewWorkspace';
import { SessionProvider } from './SessionProvider';
import { useSession } from './SessionContext';
import './review.css';

function PrivateToolbar() {
  const { check, logout } = useSession();
  return <section className="session-toolbar" aria-label="私人会话">
    <span>本机私人会话 · 仅内存草稿</span>
    <button onClick={() => void check()}>检查会话</button>
    <button onClick={() => void logout()}>退出私人会话</button>
  </section>;
}

function PrivateWorkspace({ state, snapshot, patch }: { state: ResearchState; snapshot?: Snapshot; patch: Patch }) {
  const { denied, retry } = useAccess();
  return <>
    {denied && <div className="notice"><span>来源不可用，原创内容仍可保存。</span><button onClick={retry}>重新检查访问权限</button></div>}
    <ReviewWorkspace state={state} snapshot={snapshot} patch={patch} />
  </>;
}

function PrivateWorkbench() {
  const { store } = useSession();
  return <Workbench privateFeatures={{
    locked: store.locked,
    navigationLabel: '我的复盘',
    Toolbar: PrivateToolbar,
    Workspace: PrivateWorkspace,
    ReviewActions,
  }} />;
}

export default function PrivateApp() {
  return <SessionProvider><AccessProvider><PrivateWorkbench /></AccessProvider></SessionProvider>;
}
