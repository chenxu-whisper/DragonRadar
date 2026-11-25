"""Trigger catalog expressions retain PostgreSQL OLD/NEW relation identity."""

import pytest

from dragon_radar.storage.schema_contract import _trigger_when_expression


@pytest.mark.parametrize(
    ("definition", "expected"),
    [
        (
            "CREATE TRIGGER probe BEFORE UPDATE ON sample FOR EACH ROW "
            "WHEN (old.source_id IS DISTINCT FROM new.source_id) "
            "EXECUTE FUNCTION reject_immutable_mutation()",
            "old.source_id IS DISTINCT FROM new.source_id",
        ),
        (
            # fmt: off
            "CREATE TRIGGER probe BEFORE UPDATE ON sample FOR EACH ROW "
            "WHEN (((old.status = 'draft'::text) "
            "AND (new.status = 'published'::text))) "
            "EXECUTE FUNCTION guard()",
            # fmt: on
            "((old.status = 'draft'::text) AND (new.status = 'published'::text))",
        ),
        (
            'CREATE TRIGGER "WHEN (misleading)" AFTER INSERT ON sample '
            "FOR EACH ROW EXECUTE FUNCTION guard('WHEN (not an expression)')",
            None,
        ),
        (
            "CREATE TRIGGER probe BEFORE UPDATE ON sample FOR EACH ROW "
            "WHEN ((new.value = 'a) '' WHEN (b'::text)) EXECUTE FUNCTION guard()",
            "(new.value = 'a) '' WHEN (b'::text)",
        ),
        (
            "CREATE TRIGGER probe BEFORE UPDATE ON sample FOR EACH ROW "
            "WHEN (false) EXECUTE FUNCTION guard()",
            "false",
        ),
    ],
)
def test_trigger_definition_when_expression(definition, expected):
    assert _trigger_when_expression(definition) == expected


def test_unterminated_trigger_condition_is_not_accepted():
    with pytest.raises(ValueError, match="trigger"):
        _trigger_when_expression("CREATE TRIGGER probe WHEN ((new.value = 1)")
