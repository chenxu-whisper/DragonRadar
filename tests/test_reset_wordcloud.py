"""Pure-function tests for the deterministic, license-scoped news word cloud.

These tests pin the ``build_wordcloud`` contract (Task 23 / HTTP contract
EX10) without a database, HTTP client, ``top_n`` bound, or ``news_type``
filter: those belong to the route layer. Only licensed text is tokenized —
the title for every scope, the summary for ``summary``/``full`` scopes, and
the content for ``full`` scope. Future revisions (``received_at > as_of``)
never enter the corpus, so future data cannot flow back into history.

Tokenizer ``wordcloud-tokenizer-v1`` behavior pinned here: ``涨停`` and
``龙虎榜`` are frozen dictionary words kept whole; every other Chinese
fragment in the samples below is out of vocabulary and degrades to single
characters. Latin/digit runs are lowercased, punctuation and whitespace
never become terms, and stopwords are removed after tokenization.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from factories import NOW
from news_factories import article_revision
from pydantic import ValidationError

from dragon_radar.news.wordcloud import WordCloudWord, build_wordcloud

TOKENIZER_VERSION = "wordcloud-tokenizer-v1"
STOPWORDS = frozenset({"的", "了", "the"})

ARTICLE_1 = UUID("00000000-0000-4000-8000-000000000201")
ARTICLE_2 = UUID("00000000-0000-4000-8000-000000000202")
ARTICLE_3 = UUID("00000000-0000-4000-8000-000000000203")
REVISION_1 = UUID("00000000-0000-4000-8000-000000000211")
REVISION_2 = UUID("00000000-0000-4000-8000-000000000212")
REVISION_3 = UUID("00000000-0000-4000-8000-000000000213")


def _revision(article_uuid, revision_uuid, source_ref, **changes):
    return article_revision(
        id=revision_uuid,
        article_id=article_uuid,
        source_article_id=source_ref,
        url=f"https://example.test/news/{source_ref}",
        **changes,
    )


def _word(result, term):
    return next(word for word in result.words if word.term == term)


def _terms(result):
    return {word.term for word in result.words}


def _scope_counts(result):
    scope = result.text_scope
    return scope.titles, scope.summaries, scope.contents


@pytest.mark.parametrize(
    ("document_count", "article_ids"),
    [
        (2, (ARTICLE_1,)),
        (2, (ARTICLE_1, ARTICLE_1)),
        (2, (ARTICLE_2, ARTICLE_1)),
    ],
)
def test_wordcloud_word_rejects_count_mismatch_duplicates_and_unsorted_ids(
    document_count, article_ids
):
    with pytest.raises(ValidationError):
        WordCloudWord(
            term="涨停",
            document_count=document_count,
            article_ids=article_ids,
        )


def test_wordcloud_word_accepts_consistent_sorted_unique_article_ids():
    word = WordCloudWord(
        term="涨停",
        document_count=2,
        article_ids=(ARTICLE_1, ARTICLE_2),
    )

    assert word.sentiment is None


def test_same_term_repeated_in_one_article_counts_the_document_once():
    article = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="涨停复盘 涨停",
        summary="涨停摘要",
        content="涨停细节 涨停 涨停",
    )

    result = build_wordcloud((article,), TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    word = _word(result, "涨停")
    assert word.document_count == 1
    assert word.article_ids == (ARTICLE_1,)


def test_shared_term_counts_each_article_once_and_sorts_article_ids():
    higher_uuid_first = _revision(
        ARTICLE_2,
        REVISION_2,
        "wc-2",
        title="涨停",
        content_scope="none",
        summary=None,
        content=None,
    )
    lower_uuid_second = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="涨停",
        content_scope="none",
        summary=None,
        content=None,
    )

    result = build_wordcloud(
        (higher_uuid_first, lower_uuid_second),
        TOKENIZER_VERSION,
        STOPWORDS,
        as_of=NOW,
    )

    word = _word(result, "涨停")
    assert word.document_count == 2
    assert isinstance(word.article_ids, tuple)
    assert word.article_ids == (ARTICLE_1, ARTICLE_2)


def test_future_articles_are_excluded_while_cutoff_boundary_is_included():
    visible = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="涨停 alpha",
        content_scope="none",
        summary=None,
        content=None,
    )
    future = _revision(
        ARTICLE_3,
        REVISION_3,
        "wc-3",
        title="later 涨停",
        received_at=NOW + timedelta(hours=1),
        available_at=NOW + timedelta(hours=1),
        content_scope="none",
        summary=None,
        content=None,
    )
    boundary = _revision(
        ARTICLE_2,
        REVISION_2,
        "wc-2",
        title="edge 涨停",
        received_at=NOW,
        available_at=NOW,
        content_scope="none",
        summary=None,
        content=None,
    )

    result = build_wordcloud(
        (visible, future, boundary),
        TOKENIZER_VERSION,
        STOPWORDS,
        as_of=NOW,
    )

    terms = _terms(result)
    assert "later" not in terms
    assert "edge" in terms
    word = _word(result, "涨停")
    assert word.document_count == 2
    assert word.article_ids == (ARTICLE_1, ARTICLE_2)
    assert _scope_counts(result) == (2, 0, 0)


def test_scope_none_tokenizes_only_the_licensed_title():
    article = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="涨停快报",
        content_scope="none",
        summary=None,
        content=None,
    )

    result = build_wordcloud((article,), TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    assert _terms(result) == {"涨停", "快", "报"}
    assert _scope_counts(result) == (1, 0, 0)


def test_scope_summary_tokenizes_title_and_summary_but_never_content():
    article = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="涨停标题",
        content_scope="summary",
        summary="摘要含龙虎榜",
        content=None,
    )

    result = build_wordcloud((article,), TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    terms = _terms(result)
    assert "龙虎榜" in terms
    assert "摘" in terms
    assert "详" not in terms
    assert _scope_counts(result) == (1, 1, 0)


def test_scope_full_tokenizes_title_summary_and_content():
    article = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="涨停标题",
        content_scope="full",
        summary="摘要片段",
        content="详情正文 龙虎榜",
    )

    result = build_wordcloud((article,), TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    assert {"涨停", "摘", "详", "龙虎榜"} <= _terms(result)
    assert _scope_counts(result) == (1, 1, 1)


def test_words_sort_by_document_count_then_term():
    articles = (
        _revision(
            ARTICLE_1,
            REVISION_1,
            "wc-1",
            title="涨停 alpha beta",
            content_scope="none",
            summary=None,
            content=None,
        ),
        _revision(
            ARTICLE_2,
            REVISION_2,
            "wc-2",
            title="涨停 alpha",
            content_scope="none",
            summary=None,
            content=None,
        ),
        _revision(
            ARTICLE_3,
            REVISION_3,
            "wc-3",
            title="涨停 beta",
            content_scope="none",
            summary=None,
            content=None,
        ),
    )

    result = build_wordcloud(articles, TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    head = [(word.term, word.document_count) for word in result.words[:3]]
    assert head == [("涨停", 3), ("alpha", 2), ("beta", 2)]
    ordering = [(word.document_count, word.term) for word in result.words]
    assert ordering == sorted(ordering, key=lambda item: (-item[0], item[1]))


def test_sentiment_is_always_null_without_a_research_sentiment_method():
    article = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="利好兑现 风险提示 涨停",
        content_scope="none",
        summary=None,
        content=None,
    )

    result = build_wordcloud((article,), TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    assert result.words
    assert all(word.sentiment is None for word in result.words)
    assert _word(result, "涨停").sentiment is None


def test_tokenizer_version_is_echoed_unchanged_on_the_result():
    result = build_wordcloud((), TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    assert result.tokenizer_version == TOKENIZER_VERSION


def test_unknown_tokenizer_version_raises_value_error():
    article = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="涨停",
        content_scope="none",
        summary=None,
        content=None,
    )

    with pytest.raises(ValueError):
        build_wordcloud(
            (article,),
            "wordcloud-tokenizer-v9",
            STOPWORDS,
            as_of=NOW,
        )


def test_empty_corpus_returns_empty_words_and_zero_scope_counts():
    result = build_wordcloud((), TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    assert isinstance(result.words, tuple)
    assert result.words == ()
    assert _scope_counts(result) == (0, 0, 0)


def test_stopwords_are_removed_without_changing_document_counts():
    articles = (
        _revision(
            ARTICLE_1,
            REVISION_1,
            "wc-1",
            title="涨停的了",
            content_scope="none",
            summary=None,
            content=None,
        ),
        _revision(
            ARTICLE_2,
            REVISION_2,
            "wc-2",
            title="涨停的了",
            content_scope="none",
            summary=None,
            content=None,
        ),
        _revision(
            ARTICLE_3,
            REVISION_3,
            "wc-3",
            title="的",
            content_scope="none",
            summary=None,
            content=None,
        ),
    )

    result = build_wordcloud(articles, TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    assert _terms(result) == {"涨停"}
    word = _word(result, "涨停")
    assert word.document_count == 2
    assert word.article_ids == (ARTICLE_1, ARTICLE_2)
    assert result.text_scope.titles == 3


def test_corpus_of_only_stopwords_emits_no_words_but_keeps_scope_counts():
    article = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="的了",
        content_scope="summary",
        summary="的",
        content=None,
    )

    result = build_wordcloud((article,), TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    assert isinstance(result.words, tuple)
    assert result.words == ()
    assert _scope_counts(result) == (1, 1, 0)


def test_latin_stopwords_match_after_lowercasing_but_never_by_prefix():
    article = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="The then 涨停",
        content_scope="none",
        summary=None,
        content=None,
    )

    result = build_wordcloud(
        (article,),
        TOKENIZER_VERSION,
        frozenset({"the"}),
        as_of=NOW,
    )

    assert _terms(result) == {"then", "涨停"}


def test_wordcloud_output_is_deterministic_across_repeated_builds():
    articles = (
        _revision(
            ARTICLE_1,
            REVISION_1,
            "wc-1",
            title="涨停 alpha",
            summary="摘要",
            content="详情 龙虎榜",
        ),
        _revision(
            ARTICLE_2,
            REVISION_2,
            "wc-2",
            title="涨停 beta",
            content_scope="summary",
            summary="摘要",
            content=None,
        ),
        _revision(
            ARTICLE_3,
            REVISION_3,
            "wc-3",
            title="alpha 龙虎榜",
            content_scope="none",
            summary=None,
            content=None,
        ),
    )

    first = build_wordcloud(articles, TOKENIZER_VERSION, STOPWORDS, as_of=NOW)
    second = build_wordcloud(articles, TOKENIZER_VERSION, STOPWORDS, as_of=NOW)

    assert first == second
    for earlier, later in zip(first.words, second.words, strict=True):
        assert earlier.term == later.term
        assert earlier.document_count == later.document_count
        assert earlier.article_ids == later.article_ids
        assert earlier.sentiment is None
        assert later.sentiment is None


def test_tokenizer_v1_pins_dictionary_words_fallbacks_and_latin_rules():
    article = _revision(
        ARTICLE_1,
        REVISION_1,
        "wc-1",
        title="涨停，龙虎榜！AI 600519？涨跌",
        content_scope="none",
        summary=None,
        content=None,
    )

    result = build_wordcloud((article,), TOKENIZER_VERSION, frozenset(), as_of=NOW)

    assert _terms(result) == {
        "涨停",
        "龙虎榜",
        "ai",
        "600519",
        "涨",
        "跌",
    }
    assert all(word.document_count == 1 for word in result.words)
    assert all(word.article_ids == (ARTICLE_1,) for word in result.words)
    # Unicode codepoint order: digits < Latin < CJK; the single character
    # 涨 sorts before its longer-prefix word 涨停.
    ordered_terms = [word.term for word in result.words]
    assert ordered_terms == ["600519", "ai", "涨", "涨停", "跌", "龙虎榜"]
