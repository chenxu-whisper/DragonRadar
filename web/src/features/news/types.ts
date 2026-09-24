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

export interface NewsArticle {
  article_id: string;
  revision_id: string;
  revision: number;
  event_id: string;
  type: Exclude<NewsType, ''>;
  source: { id: string; version: string };
  title: string;
  url: string;
  published_at: string;
  received_at: string;
  available_at: string;
  content_scope: NewsContentScope;
  summary: string | null;
  content: string | null;
  securities: NewsSecurityAssociation[];
  topics: NewsTopicAssociation[];
}

export interface NewsList {
  items: NewsArticle[];
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
