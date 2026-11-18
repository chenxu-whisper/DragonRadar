import { useEffect, useRef, useState } from 'react';

import { AdoptionCommandCache } from '../local-review/adoption-command';
import { localRepository } from '../../repositories/local/repository';
import TaskPanel, { type TaskPanelProps } from './TaskPanel';

export interface OverviewAIToolsProps {
  snapshotId: string;
  asOf: string;
  version: string;
  date: string;
  marketContext: string;
}

export function OverviewAITools({
  snapshotId,
  asOf,
  version,
  date,
  marketContext,
}: OverviewAIToolsProps) {
  const [expanded, setExpanded] = useState(false);
  const [hasExpanded, setHasExpanded] = useState(false);
  const commands = useRef(new AdoptionCommandCache());
  useEffect(() => () => commands.current.dispose(), []);

  const adopt: NonNullable<TaskPanelProps['onAdopt']> = async (text, context) => {
    if (
      context.date !== date
      || context.task.snapshot_id !== snapshotId
      || Date.parse(context.task.as_of) !== Date.parse(asOf)
      || context.task.version !== version
      || context.task.task_type !== 'daily_review'
    ) throw new Error('AI 草稿与当前日期或发布不一致，未保存。');
    if (context.task.revision === null) throw new Error('AI 草稿修订无效，未保存。');
    if (context.signal.aborted) throw new DOMException('采用操作已取消', 'AbortError');
    const result = await commands.current.execute(localRepository, {
      taskRevision: context.task.revision,
      strategy: 'save_as_new',
      payload: {
        expected_revision: 0,
        snapshot_id: snapshotId,
        trade_date: date,
        content: {
          title: `AI 每日速览 ${date}`,
          content: text,
          judgment: '',
          evidence: '',
          invalid_condition: '',
          verify_date: '',
        },
      },
    });
    if (context.signal.aborted) throw new DOMException('采用操作已取消', 'AbortError');
    return `已保存 · revision ${result.revision} · 回读验证通过`;
  };

  return <div className="overview-ai-tools">
    <button
      type="button"
      aria-expanded={expanded}
      onClick={() => {
        setHasExpanded(true);
        setExpanded(previous => !previous);
      }}
    >{expanded ? '收起完整 AI 分析' : '展开完整 AI 分析'}</button>
    {hasExpanded && <div hidden={!expanded}><TaskPanel
      taskType="daily_review"
      snapshotId={snapshotId}
      asOf={asOf}
      version={version}
      date={date}
      marketContext={marketContext}
      onAdopt={adopt}
    /></div>}
  </div>;
}

export default OverviewAITools;
