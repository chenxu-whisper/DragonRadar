"""Word cloud tests: pure aggregation, publication projection, and EX10 route.

The tests above the publication section pin the ``build_wordcloud`` pure
function (Task 23 / HTTP contract EX10) without a database or HTTP client.
Only licensed text is tokenized — the title for every scope, the summary for
``summary``/``full`` scopes, and the content for ``full`` scope. Future
revisions (``received_at > as_of``) never enter the corpus.

The publication/HTTP section below verifies that publishing materializes the
word cloud into the news payload (schema v2, four ``news_type`` groups) and
that ``GET /api/news/wordcloud`` only selects/slices/projects the archived
block — the read side never recomputes the domain result.

Tokenizer ``wordcloud-tokenizer-v1`` behavior pinned here: ``涨停`` and
``龙虎榜`` are frozen dictionary words kept whole; every other Chinese
fragment in the samples below is out of vocabulary and degrades to single
characters. Latin/digit runs are lowercased, punctuation and whitespace
never become terms, and stopwords are removed after tokenization.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from factories import DAYS, NOW, batch
from httpx import ASGITransport, AsyncClient
from news_factories import article_revision, event_evidence
from pydantic import ValidationError

from dragon_radar.http.errors import DataUnavailable
from dragon_radar.http.routes.news import _validate_wordcloud_block
from dragon_radar.news.wordcloud import (
    WORDCLOUD_GROUPS,
    WordCloudWord,
    build_wordcloud,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


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


# ---------------------------------------------------------------------------
# Fail-closed validation of archived word cloud blocks (EX10 route layer)
# ---------------------------------------------------------------------------

# The mixed-case pair below is sorted ascending as *raw strings* (uppercase
# "B" precedes lowercase "a") but descending once parsed as UUID values,
# pinning that the validator orders article_ids by UUID, not lexicographically.
_UUID_LOWER_A = "00000000-0000-4000-8000-00000000020a"
_UUID_UPPER_B = "00000000-0000-4000-8000-00000000020B"


def _archived_word(
    term="alpha",
    *,
    document_count=1,
    article_ids=(ARTICLE_1,),
    sentiment=None,
):
    return {
        "term": term,
        "document_count": document_count,
        "article_ids": [str(article_id) for article_id in article_ids],
        "sentiment": sentiment,
    }


def _archived_group(*, words=None, text_scope=None):
    return {
        "words": [] if words is None else words,
        "text_scope": (
            {"titles": 0, "summaries": 0, "contents": 0}
            if text_scope is None
            else text_scope
        ),
    }


def _valid_archived_block():
    return {
        "tokenizer_version": TOKENIZER_VERSION,
        "groups": {
            "all": _archived_group(
                words=[
                    _archived_word(
                        "alpha",
                        document_count=2,
                        article_ids=(ARTICLE_1, ARTICLE_2),
                    ),
                    _archived_word("beta", article_ids=(ARTICLE_2,)),
                ],
                text_scope={"titles": 2, "summaries": 1, "contents": 0},
            ),
            "announcement": _archived_group(),
            "news": _archived_group(),
            "research": _archived_group(),
        },
    }


def _malformed_block_not_a_dict(block):
    return ["not-a-dict"]


def _malformed_missing_tokenizer_version(block):
    del block["tokenizer_version"]
    return block


def _malformed_missing_groups(block):
    del block["groups"]
    return block


def _malformed_extra_block_key(block):
    block["extra"] = 1
    return block


def _malformed_wrong_tokenizer_version(block):
    block["tokenizer_version"] = "wordcloud-tokenizer-v2"
    return block


def _malformed_groups_missing_one_group(block):
    del block["groups"]["research"]
    return block


def _malformed_groups_unknown_group(block):
    block["groups"]["company"] = _archived_group()
    return block


def _malformed_group_not_a_dict(block):
    block["groups"]["all"] = []
    return block


def _malformed_group_missing_scope(block):
    del block["groups"]["all"]["text_scope"]
    return block


def _malformed_group_extra_key(block):
    block["groups"]["all"]["extra"] = 1
    return block


def _malformed_words_not_a_list(block):
    block["groups"]["all"]["words"] = ()
    return block


def _malformed_word_not_a_dict(block):
    block["groups"]["all"]["words"] = [["alpha"]]
    return block


def _malformed_word_missing_key(block):
    del block["groups"]["all"]["words"][0]["sentiment"]
    return block


def _malformed_word_extra_key(block):
    block["groups"]["all"]["words"][0]["extra"] = 1
    return block


def _malformed_term_not_a_string(block):
    block["groups"]["all"]["words"][0]["term"] = 7
    return block


def _malformed_term_empty(block):
    block["groups"]["all"]["words"][0]["term"] = ""
    return block


def _malformed_count_bool(block):
    block["groups"]["all"]["words"][0]["document_count"] = True
    return block


def _malformed_count_float(block):
    block["groups"]["all"]["words"][0]["document_count"] = 2.0
    return block


def _malformed_count_zero(block):
    block["groups"]["all"]["words"][0]["document_count"] = 0
    return block


def _malformed_count_negative(block):
    block["groups"]["all"]["words"][0]["document_count"] = -1
    return block


def _malformed_article_ids_not_a_list(block):
    block["groups"]["all"]["words"][0]["article_ids"] = (
        str(ARTICLE_1),
        str(ARTICLE_2),
    )
    return block


def _malformed_article_id_not_a_uuid(block):
    block["groups"]["all"]["words"][0]["article_ids"] = [
        "not-a-uuid",
        str(ARTICLE_2),
    ]
    return block


def _malformed_duplicate_article_ids(block):
    block["groups"]["all"]["words"][0]["article_ids"] = [
        str(ARTICLE_1),
        str(ARTICLE_1),
    ]
    return block


def _malformed_count_does_not_match_article_ids(block):
    block["groups"]["all"]["words"][0]["document_count"] = 1
    return block


def _malformed_article_ids_not_uuid_sorted(block):
    block["groups"]["all"]["words"][0]["article_ids"] = [
        str(ARTICLE_2),
        str(ARTICLE_1),
    ]
    return block


def _malformed_article_ids_string_sorted_but_uuid_unsorted(block):
    block["groups"]["all"]["words"][0]["article_ids"] = [
        _UUID_UPPER_B,
        _UUID_LOWER_A,
    ]
    return block


def _malformed_sentiment_empty_string(block):
    block["groups"]["all"]["words"][0]["sentiment"] = ""
    return block


def _malformed_sentiment_false(block):
    block["groups"]["all"]["words"][0]["sentiment"] = False
    return block


def _malformed_sentiment_label(block):
    block["groups"]["all"]["words"][0]["sentiment"] = "positive"
    return block


def _malformed_words_count_descending(block):
    block["groups"]["all"]["words"] = [
        _archived_word("alpha", article_ids=(ARTICLE_1,)),
        _archived_word(
            "beta",
            document_count=2,
            article_ids=(ARTICLE_1, ARTICLE_2),
        ),
    ]
    return block


def _malformed_words_term_descending_at_equal_count(block):
    block["groups"]["all"]["words"] = [
        _archived_word("beta", article_ids=(ARTICLE_2,)),
        _archived_word("alpha", article_ids=(ARTICLE_1,)),
    ]
    return block


def _malformed_duplicate_term_with_different_counts(block):
    # Equal terms may never appear twice, even when counts differ: a strict
    # ordering check alone accepts [alpha:2, alpha:1].
    block["groups"]["all"]["words"] = [
        _archived_word(
            "alpha",
            document_count=2,
            article_ids=(ARTICLE_1, ARTICLE_2),
        ),
        _archived_word("alpha", article_ids=(ARTICLE_1,)),
    ]
    return block


def _malformed_scope_missing_key(block):
    del block["groups"]["all"]["text_scope"]["titles"]
    return block


def _malformed_scope_extra_key(block):
    block["groups"]["all"]["text_scope"]["extra"] = 0
    return block


def _malformed_scope_negative(block):
    block["groups"]["all"]["text_scope"]["summaries"] = -1
    return block


def _malformed_scope_bool(block):
    block["groups"]["all"]["text_scope"]["titles"] = True
    return block


def _malformed_scope_not_an_int(block):
    block["groups"]["all"]["text_scope"]["titles"] = "1"
    return block


_MALFORMED_ARCHIVED_BLOCKS = (
    _malformed_block_not_a_dict,
    _malformed_missing_tokenizer_version,
    _malformed_missing_groups,
    _malformed_extra_block_key,
    _malformed_wrong_tokenizer_version,
    _malformed_groups_missing_one_group,
    _malformed_groups_unknown_group,
    _malformed_group_not_a_dict,
    _malformed_group_missing_scope,
    _malformed_group_extra_key,
    _malformed_words_not_a_list,
    _malformed_word_not_a_dict,
    _malformed_word_missing_key,
    _malformed_word_extra_key,
    _malformed_term_not_a_string,
    _malformed_term_empty,
    _malformed_count_bool,
    _malformed_count_float,
    _malformed_count_zero,
    _malformed_count_negative,
    _malformed_article_ids_not_a_list,
    _malformed_article_id_not_a_uuid,
    _malformed_duplicate_article_ids,
    _malformed_count_does_not_match_article_ids,
    _malformed_article_ids_not_uuid_sorted,
    _malformed_article_ids_string_sorted_but_uuid_unsorted,
    _malformed_sentiment_empty_string,
    _malformed_sentiment_false,
    _malformed_sentiment_label,
    _malformed_words_count_descending,
    _malformed_words_term_descending_at_equal_count,
    _malformed_duplicate_term_with_different_counts,
    _malformed_scope_missing_key,
    _malformed_scope_extra_key,
    _malformed_scope_negative,
    _malformed_scope_bool,
    _malformed_scope_not_an_int,
)


@pytest.mark.parametrize(
    "mutate",
    _MALFORMED_ARCHIVED_BLOCKS,
    ids=lambda mutate: mutate.__name__,
)
def test_validate_wordcloud_block_rejects_every_malformed_shape(mutate):
    block = mutate(_valid_archived_block())

    with pytest.raises(DataUnavailable):
        _validate_wordcloud_block(block)


def test_validate_wordcloud_block_returns_a_valid_block_unchanged():
    block = _valid_archived_block()

    assert _validate_wordcloud_block(block) is block


# ---------------------------------------------------------------------------
# Publication projection (payload schema v2) and the EX10 read route
# ---------------------------------------------------------------------------

WC2_ANNOUNCE_ID = UUID("00000000-0000-4000-8000-000000000301")
WC2_RESEARCH_ID = UUID("00000000-0000-4000-8000-000000000302")
WC2_FULL_ID = UUID("00000000-0000-4000-8000-000000000303")
WC2_SUMMARY_ID = UUID("00000000-0000-4000-8000-000000000304")
WC2_NONE_ID = UUID("00000000-0000-4000-8000-000000000305")
WC2_LEAK_FULL_ID = UUID("00000000-0000-4000-8000-000000000306")
WC2_LEAK_NONE_ID = UUID("00000000-0000-4000-8000-000000000307")
WC2_STOP_ID = UUID("00000000-0000-4000-8000-000000000308")
WC2_CROSS_FULL_ID = UUID("00000000-0000-4000-8000-000000000309")
WC2_CROSS_NONE_ID = UUID("00000000-0000-4000-8000-000000000310")

WC2_REV_ANNOUNCE = UUID("00000000-0000-4000-8000-000000000311")
WC2_REV_RESEARCH = UUID("00000000-0000-4000-8000-000000000312")
WC2_REV_FULL = UUID("00000000-0000-4000-8000-000000000313")
WC2_REV_SUMMARY = UUID("00000000-0000-4000-8000-000000000314")
WC2_REV_NONE = UUID("00000000-0000-4000-8000-000000000315")
WC2_REV_LEAK_FULL = UUID("00000000-0000-4000-8000-000000000316")
WC2_REV_LEAK_NONE = UUID("00000000-0000-4000-8000-000000000317")
WC2_REV_STOP = UUID("00000000-0000-4000-8000-000000000318")
WC2_REV_CROSS_FULL = UUID("00000000-0000-4000-8000-000000000319")
WC2_REV_CROSS_NONE = UUID("00000000-0000-4000-8000-000000000320")


def _publish_wordcloud(database, repository, articles, key):
    from dragon_radar.news.service import NewsService
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.service import PublicationService

    service = NewsService(database, repository, clock=lambda: NOW)
    for index, article in enumerate(articles):
        service.ingest_article(
            article,
            key=f"{key}-article-{index}",
            event_evidence=event_evidence(),
        )
    snapshot = service.create_snapshot(as_of=NOW, key=f"{key}-snapshot")
    market = repository.publish(
        repository.import_batch(batch(), key=f"{key}-market").id
    )
    publication = PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            topic_snapshot_id=None,
            news_input_id=snapshot.id,
            disabled_domains=("topic", "regulation", "role"),
        ),
        key=f"{key}-publication",
    )
    publications = PublicationRepository(database, repository, clock=lambda: NOW)
    return publication, publications


def _republish_wordcloud(database, repository, key):
    from dragon_radar.news.service import NewsService
    from dragon_radar.publication.models import PublicationCommand
    from dragon_radar.publication.repository import PublicationRepository
    from dragon_radar.publication.service import PublicationService

    service = NewsService(database, repository, clock=lambda: NOW)
    snapshot = service.create_snapshot(as_of=NOW, key=f"{key}-snapshot-2")
    market = repository.publish(
        repository.import_batch(batch(), key=f"{key}-market-2").id
    )
    publication = PublicationService(database, repository, clock=lambda: NOW).publish(
        PublicationCommand(
            trade_date=DAYS[-1],
            market_snapshot_id=market.id,
            topic_snapshot_id=None,
            news_input_id=snapshot.id,
            disabled_domains=("topic", "regulation", "role"),
        ),
        key=f"{key}-publication-2",
    )
    return publication, PublicationRepository(database, repository, clock=lambda: NOW)


def _news_payload(publication, publications):
    result = next(
        item
        for item in publications.list_results(publication.id)
        if item.domain == "news"
    )
    assert result.status == "ready"
    # JSON projection: the read-side validators consume model_dump(mode=json)
    # as well; ImmutableJsonObject freezes arrays as tuples in Python space.
    return result.model_dump(mode="json")["payload"]


def _group(payload, name):
    return payload["wordcloud"]["groups"][name]


def _group_terms(group):
    return {word["term"] for word in group["words"]}


@pytest.fixture
def wordcloud_publication(database, repository):
    articles = (
        _revision(
            WC2_ANNOUNCE_ID,
            WC2_REV_ANNOUNCE,
            "wc2-announce",
            news_type="announcement",
            title="涨停复盘",
            summary="公告摘要 龙虎榜",
            content="公告正文 涨停 独家专访",
        ),
        _revision(
            WC2_RESEARCH_ID,
            WC2_REV_RESEARCH,
            "wc2-research",
            news_type="research",
            title="涨停观察",
            content_scope="none",
            summary=None,
            content=None,
        ),
    )
    publication, publications = _publish_wordcloud(
        database, repository, articles, "wordcloud-main"
    )
    return publication, publications, articles


def test_published_payload_has_versioned_wordcloud_with_four_groups(
    wordcloud_publication,
):
    publication, publications, _articles = wordcloud_publication
    payload = _news_payload(publication, publications)

    assert payload["schema_version"] == 2
    block = payload["wordcloud"]
    assert block["tokenizer_version"] == TOKENIZER_VERSION
    # Exactly the four fixed groups; the JSONB archive canonicalizes object
    # key order, so producer insertion order is asserted via exact membership
    # while word sequences (stored as arrays) keep their order below.
    assert set(block["groups"]) == set(WORDCLOUD_GROUPS)
    assert len(block["groups"]) == 4

    all_words = _group(payload, "all")["words"]
    shared = next(word for word in all_words if word["term"] == "涨停")
    assert shared["document_count"] == 2
    assert shared["article_ids"] == sorted((str(WC2_ANNOUNCE_ID), str(WC2_RESEARCH_ID)))
    assert shared["sentiment"] is None
    announcement = next(
        word
        for word in _group(payload, "announcement")["words"]
        if word["term"] == "涨停"
    )
    research = next(
        word for word in _group(payload, "research")["words"] if word["term"] == "涨停"
    )
    assert (announcement["document_count"], research["document_count"]) == (1, 1)
    assert _group(payload, "news")["words"] == []
    assert all(word["sentiment"] is None for word in all_words)
    # Exact deterministic head over the two-article fixture: 涨停 spans both
    # articles; every other term is single-document and sorts by codepoint.
    assert len(all_words) == 16
    assert [(word["term"], word["document_count"]) for word in all_words[:6]] == [
        ("涨停", 2),
        ("专", 1),
        ("公", 1),
        ("告", 1),
        ("复", 1),
        ("家", 1),
    ]


def test_published_wordcloud_counts_only_licensed_text_fields(database, repository):
    articles = (
        _revision(
            WC2_FULL_ID,
            WC2_REV_FULL,
            "wc2-scope-full",
            news_type="news",
            title="许可标题 涨停",
            summary="简报",
            content="独家专访 涨停",
        ),
        _revision(
            WC2_SUMMARY_ID,
            WC2_REV_SUMMARY,
            "wc2-scope-summary",
            news_type="news",
            title="摘要范围 涨停",
            content_scope="summary",
            summary="仅摘要 龙虎榜",
            content=None,
        ),
        _revision(
            WC2_NONE_ID,
            WC2_REV_NONE,
            "wc2-scope-none",
            news_type="news",
            title="无据可查 涨停",
            content_scope="none",
            summary=None,
            content=None,
        ),
    )
    publication, publications = _publish_wordcloud(
        database, repository, articles, "wordcloud-scope"
    )
    payload = _news_payload(publication, publications)
    groups = payload["wordcloud"]["groups"]

    news_group = groups["news"]
    scope = news_group["text_scope"]
    assert (scope["titles"], scope["summaries"], scope["contents"]) == (3, 2, 1)
    terms = _group_terms(news_group)
    # Licensed title tokens from the none-scope article are present.
    assert {"无", "据", "可", "查"} <= terms
    # Licensed summary/body markers are present only where the field exists.
    assert {"简", "报", "独", "家", "专", "访"} <= terms
    assert (
        next(word for word in news_group["words"] if word["term"] == "涨停")[
            "document_count"
        ]
        == 3
    )
    assert groups["all"]["text_scope"] == scope
    for name in ("announcement", "research"):
        assert groups[name]["words"] == []
        assert groups[name]["text_scope"] == {
            "titles": 0,
            "summaries": 0,
            "contents": 0,
        }


def test_published_wordcloud_applies_frozen_stopwords(database, repository):
    article = _revision(
        WC2_STOP_ID,
        WC2_REV_STOP,
        "wc2-stop",
        news_type="announcement",
        title="涨停的了",
        content_scope="none",
        summary=None,
        content=None,
    )
    publication, publications = _publish_wordcloud(
        database, repository, (article,), "wordcloud-stop"
    )
    payload = _news_payload(publication, publications)

    terms = _group_terms(_group(payload, "announcement"))
    assert "涨停" in terms
    assert "的" not in terms
    assert "了" not in terms


def test_body_markers_cross_the_boundary_only_in_full_scope_publications(
    database, repository
):
    # The same title/body wording is licensed differently across two
    # immutable publications. The scope=none article is published first; the
    # scope=full article (same title plus a body) is ingested afterwards.
    # News snapshots accumulate every visible revision, so the second
    # publication contains both articles — its body markers must never leak
    # back into the frozen first publication, while title markers appear in
    # both.
    common = {"news_type": "announcement", "title": "跨域标题 涨停"}
    none_article = _revision(
        WC2_CROSS_NONE_ID,
        WC2_REV_CROSS_NONE,
        "wc2-cross-none",
        content_scope="none",
        summary=None,
        content=None,
        **common,
    )
    full_article = _revision(
        WC2_CROSS_FULL_ID,
        WC2_REV_CROSS_FULL,
        "wc2-cross-full",
        content_scope="full",
        summary=None,
        content="独家专访",
        **common,
    )
    none_publication, none_publications = _publish_wordcloud(
        database, repository, (none_article,), "wordcloud-cross-none"
    )
    full_publication, full_publications = _publish_wordcloud(
        database, repository, (full_article,), "wordcloud-cross-full"
    )
    none_payload = _news_payload(none_publication, none_publications)
    full_payload = _news_payload(full_publication, full_publications)

    title_markers = {"跨", "域", "标", "题", "涨停"}
    body_markers = {"独", "家", "专", "访"}
    for name in ("all", "announcement"):
        full_terms = _group_terms(_group(full_payload, name))
        none_terms = _group_terms(_group(none_payload, name))
        assert title_markers <= full_terms
        assert body_markers <= full_terms
        assert title_markers <= none_terms
        assert not (body_markers & none_terms)

    full_scope = _group(full_payload, "announcement")["text_scope"]
    none_scope = _group(none_payload, "announcement")["text_scope"]
    # The later snapshot spans both articles (two titles, one body); the
    # frozen first publication keeps its single none-scope article.
    assert full_scope == {"titles": 2, "summaries": 0, "contents": 1}
    assert none_scope == {"titles": 1, "summaries": 0, "contents": 0}


def test_wordcloud_block_is_stable_on_repeated_reads(wordcloud_publication):
    publication, publications, _articles = wordcloud_publication

    first = _news_payload(publication, publications)["wordcloud"]
    second = _news_payload(publication, publications)["wordcloud"]

    assert first == second


def test_wordcloud_block_matches_when_the_same_inputs_are_republished(
    database, repository, wordcloud_publication
):
    publication, publications, _articles = wordcloud_publication
    republished, republished_repository = _republish_wordcloud(
        database, repository, "wordcloud-main"
    )

    first = _news_payload(publication, publications)["wordcloud"]
    second = _news_payload(republished, republished_repository)["wordcloud"]
    assert first == second


def _workbench_app(publications):
    from dragon_radar.http.app import create_workbench_app

    return create_workbench_app(repository=publications)


async def _wordcloud_get(app, params):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        return await client.get("/api/news/wordcloud", params=params)


async def test_wordcloud_route_rejects_missing_required_params(
    wordcloud_publication,
):
    publication, publications, _articles = wordcloud_publication
    app = _workbench_app(publications)

    for params in (
        {"top_n": "20"},
        {"date": str(publication.trade_date)},
    ):
        response = await _wordcloud_get(app, params)
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_PARAM"


async def test_wordcloud_route_rejects_invalid_query_values(wordcloud_publication):
    publication, publications, _articles = wordcloud_publication
    app = _workbench_app(publications)
    common = {
        "date": str(publication.trade_date),
        "snapshot_id": str(publication.id),
    }
    cases = (
        {**common, "top_n": "0"},
        {**common, "top_n": "101"},
        {**common, "top_n": "1.5"},
        {**common, "top_n": "abc"},
        [*common.items(), ("top_n", "1"), ("top_n", "2")],
        {**common, "top_n": "10", "type": "company"},
        {**common, "top_n": "10", "foo": "1"},
    )

    for params in cases:
        response = await _wordcloud_get(app, params)
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_PARAM"


async def test_wordcloud_route_returns_sliced_typed_group_envelope(
    wordcloud_publication,
):
    publication, publications, _articles = wordcloud_publication
    app = _workbench_app(publications)
    params = {
        "date": str(publication.trade_date),
        "snapshot_id": str(publication.id),
        "top_n": "1",
    }

    response = await _wordcloud_get(app, params)
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == str(publication.id)
    assert body["trade_date"] == str(publication.trade_date)
    payload = _news_payload(publication, publications)
    all_words = _group(payload, "all")["words"]
    data = body["data"]
    assert data["type"] == "all"
    assert data["top_n"] == 1
    assert data["total"] == len(all_words)
    assert len(data["words"]) == 1
    word = data["words"][0]
    assert set(word) == {"term", "document_count", "article_ids", "sentiment"}
    assert word == {
        "term": "涨停",
        "document_count": 2,
        "article_ids": sorted((str(WC2_ANNOUNCE_ID), str(WC2_RESEARCH_ID))),
        "sentiment": None,
    }
    assert all(isinstance(value, str) for value in word["article_ids"])
    assert data["text_scope"] == _group(payload, "all")["text_scope"]
    assert data["tokenizer_version"] == TOKENIZER_VERSION

    research_response = await _wordcloud_get(
        app, {**params, "top_n": "100", "type": "research"}
    )
    assert research_response.status_code == 200
    research_data = research_response.json()["data"]
    assert research_data["type"] == "research"
    shared = next(word for word in research_data["words"] if word["term"] == "涨停")
    assert shared["document_count"] == 1
    assert shared["article_ids"] == [str(WC2_RESEARCH_ID)]
    assert all(word["sentiment"] is None for word in research_data["words"])

    repeated = await _wordcloud_get(app, params)
    assert repeated.json() == body


async def test_wordcloud_route_empty_group_has_no_words_and_zero_scope(
    wordcloud_publication,
):
    publication, publications, _articles = wordcloud_publication
    app = _workbench_app(publications)

    response = await _wordcloud_get(
        app,
        {
            "date": str(publication.trade_date),
            "snapshot_id": str(publication.id),
            "top_n": "50",
            "type": "news",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["type"] == "news"
    assert data["total"] == 0
    assert data["words"] == []
    assert data["text_scope"] == {"titles": 0, "summaries": 0, "contents": 0}


async def test_wordcloud_route_without_publication_returns_empty_envelope(
    wordcloud_publication,
):
    _publication, publications, _articles = wordcloud_publication
    app = _workbench_app(publications)

    response = await _wordcloud_get(app, {"date": str(DAYS[0]), "top_n": "20"})

    assert response.status_code == 200
    body = response.json()
    assert body["data"] is None
    assert body["snapshot_id"] is None
    assert body["quality_flags"] == ["no_data"]


async def test_wordcloud_route_rejects_snapshot_date_mismatch(
    wordcloud_publication,
):
    publication, publications, _articles = wordcloud_publication
    app = _workbench_app(publications)

    response = await _wordcloud_get(
        app,
        {
            "date": str(DAYS[0]),
            "snapshot_id": str(publication.id),
            "top_n": "10",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_PARAM"


async def test_wordcloud_route_does_not_leak_unlicensed_text(database, repository):
    articles = (
        _revision(
            WC2_LEAK_FULL_ID,
            WC2_REV_LEAK_FULL,
            "wc2-leak-full",
            news_type="announcement",
            title="公开涨停",
            summary="公开摘要",
            content="独家专访 涨停",
        ),
        _revision(
            WC2_LEAK_NONE_ID,
            WC2_REV_LEAK_NONE,
            "wc2-leak-none",
            news_type="announcement",
            title="无据披露 涨停",
            content_scope="none",
            summary=None,
            content=None,
        ),
    )
    publication, publications = _publish_wordcloud(
        database, repository, articles, "wordcloud-leak"
    )
    app = _workbench_app(publications)
    params = {
        "date": str(publication.trade_date),
        "snapshot_id": str(publication.id),
        "top_n": "100",
    }

    for name in WORDCLOUD_GROUPS:
        response = await _wordcloud_get(app, {**params, "type": name})
        assert response.status_code == 200

    all_response = await _wordcloud_get(app, params)
    data = all_response.json()["data"]
    terms = {word["term"] for word in data["words"]}
    # Licensed body tokens survive; the none-scope article contributes its
    # licensed title tokens and nothing else.
    assert {"独", "家", "专", "访", "无", "据", "披", "露"} <= terms
    assert data["text_scope"] == {"titles": 2, "summaries": 1, "contents": 1}


async def test_news_list_and_detail_keep_contract_under_schema_v2(
    wordcloud_publication,
):
    publication, publications, _articles = wordcloud_publication
    app = _workbench_app(publications)
    common = {
        "date": str(publication.trade_date),
        "snapshot_id": str(publication.id),
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        listed = await client.get("/api/news", params=common)
        detail = await client.get(f"/api/news/{WC2_ANNOUNCE_ID}", params=common)

    assert listed.status_code == 200
    listed_data = listed.json()["data"]
    assert listed_data["total"] == 2
    assert {item["article_id"] for item in listed_data["articles"]} == {
        str(WC2_ANNOUNCE_ID),
        str(WC2_RESEARCH_ID),
    }
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["article_id"] == str(WC2_ANNOUNCE_ID)
    assert detail_data["content_scope"] == "full"
    assert detail_data["content"] == "公告正文 涨停 独家专访"
