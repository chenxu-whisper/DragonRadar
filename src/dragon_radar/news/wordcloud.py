"""Deterministic word cloud over licensed news text (Task 23 / EX10).

This module is a synchronous, dependency-free pure function over
``NewsArticleRevision`` values. It performs no database access, no HTTP
concerns, no ``top_n`` bound, and no ``news_type`` filtering: those belong to
the EX10 route layer. Sentiment labels require an independent research
sentiment method, so every word carries ``sentiment=None`` here; word
frequencies are never presented as a sentiment signal.

Callers must supply at most one revision per ``article_id``. Choosing which
revision is visible at a point in time is the snapshot/route layer's job; the
function groups documents solely by ``article_id``.

Tokenizer ``wordcloud-tokenizer-v1`` is frozen:

* the only dictionary words are ``涨停`` and ``龙虎榜``, kept whole via
  greedy longest match inside contiguous CJK runs;
* every other character in the CJK Unified Ideographs block (U+4E00 to
  U+9FFF) is emitted as its own single-character token;
* contiguous Latin/digit runs are lowercased;
* punctuation, whitespace, emoji, CJK extension ideographs, kana and any
  character outside U+4E00..U+9FFF act as separators and never become
  tokens;
* stopwords are removed after tokenization by exact token equality.
"""

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Protocol, Self
from uuid import UUID

from pydantic import Field, StrictStr, model_validator

from dragon_radar.domain.models import FrozenModel, UTCDateTime
from dragon_radar.news.models import NewsType

TOKENIZER_V1 = "wordcloud-tokenizer-v1"

# Frozen read-side groups, in producer insertion order. ``all`` spans every
# news type and the other three match ``NewsType`` values exactly. The group
# set is part of the archived block contract: adding a group requires a news
# schema version bump, so both the materializer and the route validator
# derive their group sets from this tuple instead of redefining them.
WORDCLOUD_GROUPS: tuple[str, ...] = ("all", "announcement", "news", "research")

# Stopwords are deliberately minimal for v1 and are frozen together with the
# tokenizer: changing the production dictionary or stopword set is an external
# production gate and must ship as a new tokenizer version (v2), never as an
# in-place edit that would recompute history differently.
STOPWORDS_V1 = frozenset(
    {
        # 23 Chinese function words.
        "的",
        "了",
        "是",
        "在",
        "和",
        "与",
        "及",
        "或",
        "但",
        "也",
        "都",
        "就",
        "为",
        "对",
        "把",
        "被",
        "将",
        "从",
        "向",
        "到",
        "以",
        "于",
        "不",
        # 16 English function words (matched after lowercasing).
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "for",
        "on",
        "at",
        "by",
        "is",
        "are",
        "was",
        "were",
    }
)

# Frozen v1 vocabulary. It must not grow to cover sample words such as
# 快报/标题/摘要/利好/风险/涨跌: out-of-vocabulary CJK degrades to single
# characters so tokenization stays reproducible without a third-party engine.
_DICTIONARY_V1 = frozenset({"涨停", "龙虎榜"})
# Longest first (ties lexicographic) so the greedy scan prefers 龙虎榜 over
# any shorter prefix and stays independent of frozenset iteration order.
_DICTIONARY_WORDS = tuple(sorted(_DICTIONARY_V1, key=lambda word: (-len(word), word)))
# A run is either contiguous ASCII letters/digits or contiguous CJK Unified
# Ideographs (the class bounds are U+4E00 and U+9FFF). Full-width
# punctuation, whitespace, emoji and characters outside those classes never
# match and therefore act as separators.
_TOKEN_RUN = re.compile(r"[A-Za-z0-9]+|[一-鿿]+")


class WordCloudWord(FrozenModel):
    """One term and the distinct licensed articles containing it.

    ``sentiment`` is always ``None`` until an independent research sentiment
    method produces a labeled result.
    """

    term: Annotated[StrictStr, Field(min_length=1)]
    document_count: Annotated[int, Field(ge=1)]
    article_ids: Annotated[tuple[UUID, ...], Field(min_length=1)]
    sentiment: None = None

    @model_validator(mode="after")
    def validate_article_partition(self) -> Self:
        if self.document_count != len(self.article_ids):
            raise ValueError("document_count must match the number of article_ids")
        if len(self.article_ids) != len(set(self.article_ids)):
            raise ValueError("article_ids must not contain duplicates")
        if self.article_ids != tuple(sorted(self.article_ids)):
            raise ValueError("article_ids must be sorted ascending by UUID")
        return self


class WordCloudTextScope(FrozenModel):
    """Visible documents actually tokenized, counted per licensed field."""

    titles: Annotated[int, Field(ge=0)]
    summaries: Annotated[int, Field(ge=0)]
    contents: Annotated[int, Field(ge=0)]


class WordCloudResult(FrozenModel):
    """Ordered word aggregation with the echoed tokenizer version."""

    words: tuple[WordCloudWord, ...]
    tokenizer_version: StrictStr
    text_scope: WordCloudTextScope


class WordCloudDocumentLike(Protocol):
    """Structural input contract for :func:`build_wordcloud`.

    ``NewsArticleRevision`` satisfies it directly; publication rows are
    projected through the concrete :class:`WordCloudDocument` adapter.
    """

    @property
    def article_id(self) -> UUID: ...

    @property
    def title(self) -> str: ...

    @property
    def summary(self) -> str | None: ...

    @property
    def content(self) -> str | None: ...

    @property
    def received_at(self) -> datetime: ...


class WordCloudDocument(FrozenModel):
    """Frozen snapshot-row adapter: only licensed fields needed for tokens."""

    article_id: UUID
    news_type: NewsType
    title: Annotated[StrictStr, Field(min_length=1)]
    summary: StrictStr | None
    content: StrictStr | None
    received_at: UTCDateTime


def _tokenize_v1(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RUN.finditer(text):
        run = match.group()
        if run[0].isascii():
            # Contiguous Latin/digit run: lowercased as one token.
            tokens.append(run.lower())
            continue
        position = 0
        while position < len(run):
            for word in _DICTIONARY_WORDS:
                if run.startswith(word, position):
                    tokens.append(word)
                    position += len(word)
                    break
            else:
                # Out-of-vocabulary CJK fragment: keep the single ideograph.
                tokens.append(run[position])
                position += 1
    return tokens


def build_wordcloud(
    articles: Iterable[WordCloudDocumentLike],
    tokenizer_version: str,
    stopwords: frozenset[str],
    *,
    as_of: datetime,
) -> WordCloudResult:
    """Aggregate term document counts over licensed, visible articles.

    Articles with ``received_at > as_of`` are excluded entirely; an article
    received exactly at ``as_of`` is visible. Every visible article always
    contributes its licensed title, plus the summary for ``summary``/``full``
    scopes and the body for ``full`` scope, even when stopwords remove every
    token from a field. Repeated occurrences within one article (across any
    field) count once for both ``document_count`` and ``article_ids``.

    The caller must pass at most one revision per ``article_id``. No
    ``top_n`` or ``news_type`` filtering happens here (EX10 route layer), and
    ``sentiment`` is always ``None``. Raises ``ValueError`` for any
    ``tokenizer_version`` other than ``wordcloud-tokenizer-v1``; the version
    is validated before the iterable is consumed.
    """
    if tokenizer_version != TOKENIZER_V1:
        raise ValueError(f"Unsupported tokenizer version: {tokenizer_version!r}")

    titles = 0
    summaries = 0
    contents = 0
    articles_by_term: dict[str, set[UUID]] = {}

    for article in articles:
        if article.received_at > as_of:
            continue
        titles += 1
        terms = set(_tokenize_v1(article.title))
        if article.summary is not None:
            summaries += 1
            terms.update(_tokenize_v1(article.summary))
        if article.content is not None:
            contents += 1
            terms.update(_tokenize_v1(article.content))
        terms.difference_update(stopwords)
        for term in terms:
            articles_by_term.setdefault(term, set()).add(article.article_id)

    ordered_terms = sorted(
        articles_by_term,
        key=lambda term: (-len(articles_by_term[term]), term),
    )
    words = tuple(
        WordCloudWord(
            term=term,
            document_count=len(articles_by_term[term]),
            article_ids=tuple(sorted(articles_by_term[term])),
        )
        for term in ordered_terms
    )
    return WordCloudResult(
        words=words,
        tokenizer_version=tokenizer_version,
        text_scope=WordCloudTextScope(
            titles=titles,
            summaries=summaries,
            contents=contents,
        ),
    )


__all__ = [
    "STOPWORDS_V1",
    "TOKENIZER_V1",
    "WORDCLOUD_GROUPS",
    "WordCloudDocument",
    "WordCloudDocumentLike",
    "WordCloudResult",
    "WordCloudTextScope",
    "WordCloudWord",
    "build_wordcloud",
]
