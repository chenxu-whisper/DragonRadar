"""Deterministic empty review sections; market facts remain authorized references."""

from datetime import date

from dragon_radar.review.models import NoteContent

TEMPLATE_VERSION = "private-review-v1"


def template_content(trade_date: date) -> NoteContent:
    return NoteContent(
        title=f"{trade_date.isoformat()} \u590d\u76d8",
        judgment="\u5e02\u573a\u89c2\u5bdf\n\n\u5f85\u9a8c\u8bc1\u4e8b\u9879\n",
    )
