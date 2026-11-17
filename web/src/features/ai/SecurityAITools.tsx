import { useEffect, useRef } from 'react';

import {
  AdoptionCommandCache,
  type AdoptionStrategy,
} from '../local-review/adoption-command';
import { localRepository } from '../../repositories/local/repository';
import type { AITask } from './client';
import TaskPanel from './TaskPanel';

export function SecurityAITools({
  snapshotId,
  asOf,
  version,
  date,
  securityId,
  securityName,
}: {
  snapshotId: string;
  asOf: string;
  version: string;
  date: string;
  securityId: string;
  securityName: string | null;
}) {
  const commands = useRef(new AdoptionCommandCache());
  useEffect(() => () => commands.current.dispose(), []);

  const adopt = async (
    text: string,
    context: {
      strategy: AdoptionStrategy;
      task: AITask;
      date: string;
      securityId?: string;
      signal: AbortSignal;
    },
  ) => {
    if (context.task.revision === null) throw new Error('AI 草稿修订无效，未保存。');
    if (context.signal.aborted) throw new DOMException('采用操作已取消', 'AbortError');
    const result = await commands.current.execute(localRepository, {
      taskRevision: context.task.revision,
      strategy: context.strategy,
      payload: {
        expected_revision: 0,
        snapshot_id: context.task.snapshot_id,
        trade_date: context.date,
        security_id: context.securityId,
        content: {
          title: `AI 个股解释 · ${securityName || securityId}`.slice(0, 100),
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

  return <TaskPanel
    taskType="explain_stock"
    snapshotId={snapshotId}
    asOf={asOf}
    version={version}
    date={date}
    securityId={securityId}
    onAdopt={adopt}
  />;
}

export default SecurityAITools;
