import { ExternalLink, FileText, Link2, Search } from 'lucide-react';
import { useEffect, useState } from 'react';

import { Pager, ResourceStatus, type Patch } from '../components/Common';
import {
  newsDetailPath,
  newsListPath,
} from '../features/news/api';
import type {
  NewsArticle,
  NewsDetailResponse,
  NewsListResponse,
  NewsType,
} from '../features/news/types';
import { useResource } from '../hooks/useResource';
import type { ResearchState } from '../state';

const TYPE_LABELS: Record<Exclude<NewsType, ''>, string> = {
  news: '新闻',
  announcement: '公告',
  research: '研报',
};

const METHOD_LABELS = {
  direct: '直接证据',
  manual: '人工判断',
  ai: 'AI 建议',
};

const STATUS_LABELS = {
  accepted: '已确认',
  unreviewed: '待审核',
  rejected: '已驳回',
};

interface NewsProps {
  state: ResearchState;
  patch: Patch;
  refreshToken: number;
  open: (id: string, sector: string | undefined, trigger: HTMLElement) => void;
}

function ArticleDetail({
  article,
  open,
}: {
  article: NewsArticle;
  open: NewsProps['open'];
}) {
  return <section className="news-detail" aria-label="消息详情">
    <p className="eyebrow">{TYPE_LABELS[article.type]} · {article.source.id} / {article.source.version}</p>
    <h2>{article.title}</h2>
    <dl className="metadata-grid news-metadata">
      <dt>发布时间</dt><dd>{article.published_at}</dd>
      <dt>首次获取</dt><dd>{article.received_at}</dd>
      <dt>可用时间</dt><dd>{article.available_at}</dd>
      <dt>文章修订</dt><dd>第 {article.revision} 版 · {article.revision_id}</dd>
    </dl>
    <a className="news-source-link" href={article.url} target="_blank" rel="noreferrer">
      <ExternalLink size={16} />查看原文
    </a>
    {article.summary && <p className="news-summary">{article.summary}</p>}
    {article.content
      ? <div className="news-content"><FileText size={18} /><p>{article.content}</p></div>
      : <p className="notice news-license"><strong>未获正文展示许可</strong>{article.summary ? '，仅展示授权摘要' : ''}</p>}

    <section className="news-associations" aria-label="关联证券与题材">
      <h3>关联证据</h3>
      {article.securities.length === 0 && article.topics.length === 0
        ? <p className="muted">当前文章没有已发布的关联证据。</p>
        : <>
          {article.securities.map(item => <div className="association-row" key={item.association_id}>
            <Link2 size={15} />
            <button
              className="security-button"
              onClick={event => open(item.security_id, undefined, event.currentTarget)}
            >{item.security_id === 'TEST001' ? '合成测试证券1' : item.security_id}</button>
            <span>{METHOD_LABELS[item.method]} · {STATUS_LABELS[item.status]}</span>
            <small>{item.evidence_snippets.join('；')}</small>
          </div>)}
          {article.topics.map(item => <div className="association-row" key={item.association_id}>
            <Link2 size={15} />
            <strong>{item.topic_id}</strong>
            <span>{METHOD_LABELS[item.method]} · {STATUS_LABELS[item.status]}</span>
            <small>{item.evidence_snippets.join('；')}{item.pending_topic_publication ? '；等待新题材快照发布' : ''}</small>
          </div>)}
        </>}
    </section>
  </section>;
}

export function News({ state, patch, refreshToken, open }: NewsProps) {
  const [query, setQuery] = useState(state.q);
  useEffect(() => setQuery(state.q), [state.q]);
  const request = {
    date: state.date,
    snapshot: state.snapshot,
    type: state.news_type,
    source: state.news_source,
    q: state.q,
    securityId: '',
    page: state.page,
    size: state.page_size,
  };
  const list = useResource<NewsListResponse>(
    newsListPath(request),
    state.snapshot,
    refreshToken,
  );
  const detail = useResource<NewsDetailResponse>(
    state.article
      ? newsDetailPath({
        articleId: state.article,
        date: state.date,
        snapshot: state.snapshot,
      })
      : null,
    state.snapshot,
    refreshToken,
  );
  const data = list.data?.data;
  const selected = detail.data?.data;
  const sources = Array.from(new Set([
    ...(data?.sources || []),
    ...(state.news_source ? [state.news_source] : []),
  ])).sort();

  return <>
    <div className="page-heading">
      <div><p className="eyebrow">消息来源 / 时点证据</p><h1>消息</h1></div>
      {data && <p className="muted">{data.total} 篇文章 · {data.event_count} 个事件</p>}
    </div>
    <div className="news-controls">
      <label>消息类型<select
        value={state.news_type}
        onChange={event => patch({
          news_type: event.target.value as NewsType,
          article: '',
          page: 1,
        })}
      >
        <option value="">全部类型</option>
        {Object.entries(TYPE_LABELS).map(([value, text]) =>
          <option value={value} key={value}>{text}</option>)}
      </select></label>
      <label>消息来源<select
        value={state.news_source}
        onChange={event => patch({
          news_source: event.target.value,
          article: '',
          page: 1,
        })}
      >
        <option value="">全部来源</option>
        {sources.map(source => <option value={source} key={source}>{source}</option>)}
      </select></label>
      <form onSubmit={event => {
        event.preventDefault();
        patch({ q: query.trim(), article: '', page: 1 });
      }}>
        <label className="search-label"><Search size={17} />
          <span className="sr-only">搜索消息</span>
          <input
            aria-label="搜索消息"
            value={query}
            maxLength={100}
            onChange={event => setQuery(event.target.value)}
            placeholder="搜索标题或摘要"
          />
        </label>
        <button type="submit">搜索消息</button>
      </form>
    </div>
    <ResourceStatus resource={list} name="消息列表" />
    <ResourceStatus resource={detail} name="消息详情" />
    {data && <div className="news-layout">
      <section className="news-list" aria-label="消息列表">
        {data.items.length === 0
          ? <p className="empty">当前筛选没有已发布消息。</p>
          : data.items.map(article => <button
            className="news-list-row"
            aria-pressed={state.article === article.article_id}
            key={article.article_id}
            onClick={() => patch({ article: article.article_id })}
          >
            <span>{TYPE_LABELS[article.type]} · {article.source.id}</span>
            <strong>{article.title}</strong>
            <small>{article.available_at}</small>
          </button>)}
        <Pager
          page={data.page}
          size={data.size}
          total={data.total}
          change={(page, page_size) => patch({ page, page_size, article: '' })}
        />
      </section>
      {selected
        ? <ArticleDetail article={selected} open={open} />
        : <section className="news-detail empty" aria-label="消息详情">
          <h2>选择一篇消息</h2><p>详情严格绑定当前快照，不读取最新文章表。</p>
        </section>}
    </div>}
  </>;
}
