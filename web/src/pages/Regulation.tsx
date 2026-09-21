import { ModuleState } from '../components/ModuleState';

export function Regulation() {
  return <>
    <div className="page-heading"><div><p className="eyebrow">数据完整性 / 计算状态 / 正式事件</p><h1>监管</h1></div></div>
    <ModuleState reason="正式规则、监管事件来源与发布读接口尚未接入。" missing={[
      '数据完整性未知，不能将缺少事件解释为没有限制。',
      '数值计算待规则原文、有效期、复核与回归样例确认。',
      '正式事件需要官方公告证据，计算预警不等于已披露限制。',
    ]} />
  </>;
}
