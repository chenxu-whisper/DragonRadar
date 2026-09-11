"""Validated user text, without copied market facts or executable markup."""

import re
from datetime import date

from pydantic import StrictStr, field_validator, model_validator

from dragon_radar.domain.models import FrozenModel


class NoteContent(FrozenModel):
    title: StrictStr
    judgment: StrictStr = ""
    evidence: StrictStr = ""
    invalidation: StrictStr = ""
    verification_date: date | None = None

    @field_validator("title", "judgment", "evidence", "invalidation")
    @classmethod
    def valid_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Text cannot contain NUL")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Text must be valid Unicode") from None
        return value

    @field_validator("verification_date", mode="before")
    @classmethod
    def exact_date(cls, value: object) -> object:
        if value is None or type(value) is date:
            return value
        if isinstance(value, str) and re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value
        ):
            return date.fromisoformat(value)
        raise ValueError("Verification date must be an ISO date")

    @model_validator(mode="after")
    def valid_lengths(self):
        if not self.title.strip() or len(self.title) > 100:
            raise ValueError("Title must contain 1-100 Unicode codepoints")
        if len(self.judgment) + len(self.evidence) + len(self.invalidation) > 20000:
            raise ValueError("Combined body must not exceed 20000 Unicode codepoints")
        return self


def validate_confirmation(content: NoteContent, trade_date: date) -> None:
    if not all(
        value.strip()
        for value in (content.judgment, content.evidence, content.invalidation)
    ):
        raise ValueError("Confirmation requires judgment, evidence and invalidation")
    if content.verification_date is None or content.verification_date < trade_date:
        raise ValueError("Confirmation requires a valid verification date")
