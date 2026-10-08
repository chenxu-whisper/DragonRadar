import { ExternalLink, FileText, Link2, Search } from 'lucide-react';
import { lazy, Suspense, useEffect, useState } from 'react';

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
import { CsvExportButton } from '../export/CsvExportButton';

const NewsAITools = import.meta.env.VITE_PRIVATE_AI === '1'
  ? lazy(() => import('../features/ai/NewsAITools'))
  : null;
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
  publicationAsOf?: string;
  publicationVersion?: string;
}

function ArticleDetail({
  article,
  open,
  state,
  publicationAsOf,
  publicationVersion,
}: {
  article: NewsArticle;
  open: NewsProps['open'];
  state: ResearchState;
  publicationAsOf?: string;
  publicationVersion?: string;
}) {
  return <section className="news-detail" aria-label="消息详情">
    <p className="eyebrow">{TYPE_LABELS[article.article_type]} · {article.source} / {article.source_version}</p>
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
    {article.ai_summary && <p className="news-summary">AI 摘要：{article.ai_summary}</p>}
    {article.summary && <p className="news-summary">授权摘要：{article.summary}</p>}
    {article.content
      ? <div className="news-content"><FileText size={18} /><p>{article.content}</p></div>
      : <p className="notice news-license"><strong>未获正文展示许可</strong>{article.summary ? '，仅展示授权摘要' : ''}</p>}
    {NewsAITools && publicationAsOf && publicationVersion &&
      <Suspense fallback={<p role="status">正在加载 AI 工具…</p>}>
        <NewsAITools
          article={article}
          state={state}
          publicationAsOf={publicationAsOf}
          publicationVersion={publicationVersion}
        />
      </Suspense>
    }

    <section aria-label="事件新颖性证据">
      <h3>事件新颖性：{article.is_novel_event ? '新颖事件' : '非新颖事件'}</h3>
      <h4>支持证据</h4>
      {article.supporting_evidence.length
        ? <ul>{article.supporting_evidence.map(item => <li key={item}>{item}</li>)}</ul>
        : <p className="muted">无支持证据。</p>}
      <h4>反证</h4>
      {article.opposing_evidence.length
        ? <ul>{article.opposing_evidence.map(item => <li key={item}>{item}</li>)}</ul>
        : <p className="muted">无反证。</p>}
    </section>

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

export function News({
  state,
  patch,
  refreshToken,
  open,
  publicationAsOf,
  publicationVersion,
}: NewsProps) {
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
      {data && <div>
        <p className="muted">{data.total} 篇文章 · {data.event_count} 个事件</p>
        <CsvExportButton
          scope="news-current-page"
          label="导出当前页 CSV"
          envelope={list.data!}
          filters={{
            news_type: state.news_type,
            news_source: state.news_source,
            q: state.q,
            page: data.page,
            page_size: data.size,
          }}
          columns={[
            { key: 'article_id', header: 'article_id' },
            { key: 'article_type', header: 'article_type' },
            { key: 'source', header: 'source' },
            { key: 'source_version', header: 'source_version' },
            { key: 'title', header: 'title' },
            { key: 'published_at', header: 'published_at' },
            { key: 'available_at', header: 'available_at' },
            { key: 'revision', header: 'revision' },
          ]}
          rows={data.articles.map(article => ({
            snapshot_id: list.data!.snapshot_id,
            ...article,
          }))}
        />
      </div>}
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
        {data.articles.length === 0
          ? <p className="empty">当前筛选没有已发布消息。</p>
          : data.articles.map(article => <button
            className="news-list-row"
            aria-pressed={state.article === article.article_id}
            key={article.article_id}
            onClick={() => patch({ article: article.article_id })}
          >
            <span>{TYPE_LABELS[article.article_type]} · {article.source}</span>
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
        ? <ArticleDetail
          article={selected}
          open={open}
          state={state}
          publicationAsOf={publicationAsOf}
          publicationVersion={publicationVersion}
        />
        : <section className="news-detail empty" aria-label="消息详情">
          <h2>选择一篇消息</h2><p>详情严格绑定当前快照，不读取最新文章表。</p>
        </section>}
    </div>}
  </>;
}
