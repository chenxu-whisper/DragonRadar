import type { WorkbenchEnvelope } from '../../types';

export type NewsType = '' | 'news' | 'announcement' | 'research';
export type NewsContentScope = 'none' | 'summary' | 'full';
export type NewsAssociationMethod = 'direct' | 'manual' | 'ai';
export type NewsAssociationStatus = 'accepted' | 'unreviewed' | 'rejected';

export interface NewsSecurityAssociation {
  association_id: string;
  security_id: string;
  method: NewsAssociationMethod;
  status: NewsAssociationStatus;
  evidence_snippets: string[];
  effective: boolean;
  pending_topic_publication: boolean;
}

export interface NewsTopicAssociation {
  association_id: string;
  topic_id: string;
  method: NewsAssociationMethod;
  status: NewsAssociationStatus;
  evidence_snippets: string[];
  effective: boolean;
  pending_topic_publication: boolean;
}

export interface NewsListArticle {
  article_id: string;
  title: string;
  source: string;
  source_version: string;
  published_at: string;
  available_at: string;
  article_type: Exclude<NewsType, ''>;
  summary: string | null;
  event_id: string;
  revision: number;
}

export interface NewsArticle extends NewsListArticle {
  revision_id: string;
  url: string;
  received_at: string;
  content_scope: NewsContentScope;
  content: string | null;
  ai_summary: string | null;
  related_security_ids: string[];
  is_novel_event: boolean;
  supporting_evidence: string[];
  opposing_evidence: string[];
  securities: NewsSecurityAssociation[];
  topics: NewsTopicAssociation[];
}

export interface NewsList {
  articles: NewsListArticle[];
  page: number;
  size: number;
  total: number;
  sources: string[];
  event_count: number;
}

export type NewsListResponse = WorkbenchEnvelope<NewsList | null>;
export type NewsDetailResponse = WorkbenchEnvelope<NewsArticle | null>;

export interface NewsListRequest {
  date: string;
  snapshot: string;
  type: NewsType;
  source: string;
  q: string;
  securityId: string;
  page: number;
  size: number;
}

export interface NewsDetailRequest {
  articleId: string;
  date: string;
  snapshot: string;
}

export type WordCloudGroup = 'all' | 'news' | 'announcement' | 'research';

export interface WordCloudWord {
  term: string;
  document_count: number;
  article_ids: string[];
  sentiment: null;
}

export interface WordCloudTextScope {
  titles: number;
  summaries: number;
  contents: number;
}

export interface WordCloud {
  type: WordCloudGroup;
  top_n: number;
  total: number;
  words: WordCloudWord[];
  text_scope: WordCloudTextScope;
  tokenizer_version: string;
}

export type WordCloudResponse = WorkbenchEnvelope<WordCloud | null>;

export interface WordCloudRequest {
  date: string;
  snapshot: string;
  group: WordCloudGroup;
  topN: number;
}
