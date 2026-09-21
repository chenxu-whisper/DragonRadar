import { ModuleState } from '../components/ModuleState';

export function News() {
  return <>
    <div className="page-heading"><div><p className="eyebrow">消息来源 / 时点证据</p><h1>消息</h1></div></div>
    <ModuleState reason="消息来源与固定快照读接口尚未接入。" missing={[
      '缺少已发布的消息列表、发布时间与首次获取时间。',
      '缺少正文或摘要展示许可，尚不能提供全文解读。',
      '证券与题材关联需要可追溯证据及审核记录。',
    ]} />
  </>;
}
