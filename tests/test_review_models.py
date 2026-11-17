from datetime import date

import pytest
from pydantic import ValidationError

from dragon_radar.review.models import NoteContent, validate_confirmation
from dragon_radar.review.templates import TEMPLATE_VERSION, template_content


def test_draft_accepts_unfinished_fields():
    content = NoteContent(title="Research")
    assert content.judgment == ""
    assert content.verification_date is None


@pytest.mark.parametrize("title", ["", " \t ", "x" * 101, "a\x00b", "\ud800"])
def test_invalid_titles_are_rejected(title):
    with pytest.raises(ValidationError):
        NoteContent(title=title)


def test_title_uses_unicode_codepoints():
    content = NoteContent(title="\U0001f4c8" * 100)
    assert len(content.title) == 100


def test_combined_body_limit():
    NoteContent(title="Research", judgment="a" * 10000, evidence="b" * 10000)
    with pytest.raises(ValidationError):
        NoteContent(
            title="Research",
            judgment="a" * 10000,
            evidence="b" * 10000,
            invalidation="c",
        )


@pytest.mark.parametrize("field", ["title", "judgment", "evidence", "invalidation"])
def test_text_fields_do_not_coerce_values(field):
    values = {"title": "Research", field: 123}
    with pytest.raises(ValidationError):
        NoteContent(**values)


@pytest.mark.parametrize("field", ["judgment", "evidence", "invalidation"])
def test_body_rejects_nul_and_preserves_plain_text(field):
    with pytest.raises(ValidationError):
        NoteContent(title="Research", **{field: "a\x00b"})
    content = NoteContent(title="Research", **{field: "<script>\n\ttext</script>"})
    assert getattr(content, field) == "<script>\n\ttext</script>"


def test_extra_fields_and_mutation_are_rejected():
    with pytest.raises(ValidationError):
        NoteContent(title="Research", owner_id="forged")
    content = NoteContent(title="Research")
    with pytest.raises(ValidationError):
        content.title = "changed"


@pytest.mark.parametrize("value", [123, True, "2026-09-11T00:00:00Z", "2026-2-1"])
def test_verification_date_rejects_non_iso_or_coerced_values(value):
    with pytest.raises(ValidationError):
        NoteContent(title="Research", verification_date=value)


def test_verification_date_accepts_iso_and_date():
    assert NoteContent(
        title="Research", verification_date="2026-09-11"
    ).verification_date == date(2026, 9, 11)
    assert NoteContent(
        title="Research", verification_date=date(2026, 9, 11)
    ).verification_date == date(2026, 9, 11)


def test_confirmation_requires_all_content_and_date():
    with pytest.raises(ValueError):
        validate_confirmation(NoteContent(title="Research"), date(2026, 9, 11))
    values = {
        "title": "Research",
        "judgment": "Observation",
        "evidence": "Evidence",
        "invalidation": "Condition",
        "verification_date": "2026-09-11",
    }
    validate_confirmation(NoteContent(**values), date(2026, 9, 11))
    for field in ("judgment", "evidence", "invalidation"):
        with pytest.raises(ValueError):
            validate_confirmation(
                NoteContent(**{**values, field: " \n"}), date(2026, 9, 11)
            )
    with pytest.raises(ValueError):
        validate_confirmation(NoteContent(**values), date(2026, 9, 12))


def test_template_is_deterministic_unconfirmed_and_has_no_market_values():
    first = template_content(date(2026, 9, 11))
    second = template_content(date(2026, 9, 11))
    assert first == second
    assert TEMPLATE_VERSION == "private-review-v1"
    assert first.title == "2026-09-11 复盘"
    assert first.verification_date is None
    assert first.evidence == first.invalidation == ""
    assert "市场观察" in first.judgment
    assert "待验证事项" in first.judgment
    with pytest.raises(ValueError):
        validate_confirmation(first, date(2026, 9, 11))
