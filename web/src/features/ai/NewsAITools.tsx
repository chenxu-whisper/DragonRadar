import { useEffect, useRef } from 'react';

import type { NewsArticle } from '../news/types';
import {
  AdoptionCommandCache,
  type AdoptionStrategy,
} from '../local-review/adoption-command';
import { localRepository } from '../../repositories/local/repository';
import type { ResearchState } from '../../state';
import type { AITask } from './client';
import TaskPanel from './TaskPanel';

export function NewsAITools({
  article,
  state,
  publicationAsOf,
  publicationVersion,
}: {
  article: NewsArticle;
  state: ResearchState;
  publicationAsOf: string;
  publicationVersion: string;
}) {
  const commands = useRef(new AdoptionCommandCache());
  useEffect(() => () => commands.current.dispose(), []);

  const adopt = async (
    text: string,
    label: string,
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
          title: `${label} · ${article.title}`.slice(0, 100),
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

  return <div className="ai-entry-group">
    <TaskPanel
      taskType="summarize"
      snapshotId={state.snapshot}
      asOf={publicationAsOf}
      version={publicationVersion}
      date={state.date}
      articleId={article.article_id}
      securityId={article.securities[0]?.security_id}
      onAdopt={(text, context) => adopt(text, 'AI 消息摘要', context)}
    />
    <TaskPanel
      taskType="topic_suggest"
      snapshotId={state.snapshot}
      asOf={publicationAsOf}
      version={publicationVersion}
      date={state.date}
      articleId={article.article_id}
      securityId={article.securities[0]?.security_id}
      onAdopt={(text, context) => adopt(text, 'AI 题材建议', context)}
    />
  </div>;
}

export default NewsAITools;
