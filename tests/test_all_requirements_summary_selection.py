"""Explicit promoted selectors participate in immutable publication identity."""

import hashlib
import json
from uuid import uuid4

import pytest

from dragon_radar.publication.models import PublicationCommand


def command():
    return PublicationCommand(
        trade_date="2026-11-24",
        market_snapshot_id=uuid4(),
        news_input_id=uuid4(),
    )


def selection():
    return {
        "summary_id": str(uuid4()),
        "summary_revision": 1,
        "article_id": str(uuid4()),
        "article_revision_id": str(uuid4()),
        "article_revision": 1,
    }


def identity(value, selectors):
    from dragon_radar.publication.service import _publication_identity

    return _publication_identity(value, selectors)


def test_empty_selectors_preserve_existing_publication_identity():
    value = command()
    canonical, digest, selectors = identity(value, ())
    assert canonical == value.canonical_json()
    assert digest == hashlib.sha256(canonical.encode()).hexdigest()
    assert selectors == ()


def test_selected_summary_identity_is_order_independent_and_content_bound():
    value = command()
    first, second = selection(), selection()
    canonical, digest, selectors = identity(value, [first, second])
    assert identity(value, [second, first]) == (canonical, digest, selectors)
    assert json.loads(canonical)["summary_selections"] == list(selectors)
    assert digest != identity(value, ())[1]
    assert digest != identity(value, [{**first, "summary_revision": 2}, second])[1]


@pytest.mark.parametrize("case", ["duplicate", "private", "missing-news"])
def test_invalid_summary_selection_fails_before_storage(case):
    value, selected = command(), selection()
    selectors = [selected]
    if case == "duplicate":
        selectors.append(selected)
    elif case == "private":
        selectors[0] = {**selected, "task_id": str(uuid4())}
    else:
        value = value.model_copy(update={"news_input_id": None})
    with pytest.raises(ValueError):
        identity(value, selectors)
