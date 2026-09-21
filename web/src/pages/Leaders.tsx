import { ModuleState } from '../components/ModuleState';

export function Leaders() {
  return <>
    <div className="page-heading"><div><p className="eyebrow">梯队结构 / 角色证据</p><h1>梯队</h1></div></div>
    <ModuleState reason="新版角色计算与梯队发布读接口尚未接入。" missing={[
      '缺少绑定所选快照的板数矩阵、晋级率与候选结果。',
      '缺少已确认的角色规则版本及题材成员证据。',
      '无候选与输入缺失尚不能区分，不填充示例股票。',
    ]} />
  </>;
}
