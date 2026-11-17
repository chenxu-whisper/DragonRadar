"""Offline supplier mapping contracts; all records are synthetic."""

import importlib
import json
from decimal import Decimal, localcontext

import pytest
from factories import bar, state
from pydantic import ValidationError


@pytest.fixture
def api():
    # Load inside test execution so a missing API is a test failure.
    class API:
        def __getattr__(self, attribute):
            name = "dragon_radar.ingestion.supplier_mapping"
            assert importlib.util.find_spec(name) is not None, (
                "supplier mapping API is missing"
            )
            return getattr(importlib.import_module(name), attribute)

    return API()


def profile_data(**changes):
    return {
        "schema": "dragon-radar-supplier-mapping/v1",
        "profile_id": "synthetic-profile",
        "source_id": "synthetic",
        "source_version": "s1",
        "bar_fields": {
            key: key
            for key in bar().model_dump()
            if key not in {"source_id", "source_version"}
        },
        "state_fields": {
            key: key
            for key in state().model_dump()
            if key not in {"source_id", "source_version"}
        },
        "bar_constants": {},
        "state_constants": {},
        "value_maps": {},
        "decimal_scales": {},
        **changes,
    }


def normalize(api, *, bars=(), states=(), **changes):
    profile = api.MappingProfile.model_validate(profile_data(**changes))
    return api.normalize_records(profile, bar_rows=bars, state_rows=states)


def issue_keys(result):
    return {(i.kind, i.row_index, i.field, i.code) for i in result.issues}


def test_public_api_returns_canonical_frozen_tuples(api):
    result = normalize(api, bars=[bar().model_dump()], states=[state().model_dump()])
    assert isinstance(result, api.NormalizationResult)
    assert result.bars == (bar(),)
    assert result.states == (state(),)
    assert result.issues == ()
    with pytest.raises(ValidationError, match="frozen"):
        result.bars = ()


def test_injects_declared_source_ignores_unmapped_supplier_columns(api):
    raw = bar().model_dump(exclude={"source_id", "source_version"})
    raw["credential-column"] = "secret-not-in-output"
    result = normalize(api, bars=[raw])
    assert result.bars == (bar(),)
    assert "secret-not-in-output" not in result.model_dump_json()


@pytest.mark.parametrize("kind", ["bar", "state"])
@pytest.mark.parametrize("field", ["source_id", "source_version"])
def test_mapped_source_identity_must_match_profile(api, kind, field):
    raw = (bar() if kind == "bar" else state()).model_dump()
    raw[field] = "different-source"
    data = profile_data()
    data[f"{kind}_fields"][field] = field
    result = normalize(
        api,
        bars=[raw] if kind == "bar" else [],
        states=[raw] if kind == "state" else [],
        **data,
    )
    assert not result.bars and not result.states
    assert issue_keys(result) == {(kind, 0, field, "source_mismatch")}


@pytest.mark.parametrize("field", ["source_id", "source_version"])
def test_value_map_cannot_launder_source_mismatch(api, field):
    raw = bar().model_dump()
    raw[field] = "alias"
    data = profile_data()
    data["bar_fields"][field] = field
    data["value_maps"][field] = {"alias": data[field]}
    result = normalize(api, bars=[raw], **data)
    assert issue_keys(result) == {("bar", 0, field, "source_mismatch")}
    assert result.bars == ()


@pytest.mark.parametrize("kind", ["bar", "state"])
@pytest.mark.parametrize("field", ["source_id", "source_version"])
def test_constant_source_identity_cannot_override_profile(api, kind, field):
    with pytest.raises(ValidationError):
        api.MappingProfile.model_validate(
            profile_data(**{f"{kind}_constants": {field: "different"}})
        )


def test_matching_source_constants_are_allowed(api):
    result = normalize(
        api,
        bars=[bar().model_dump()],
        bar_constants={"source_id": "synthetic", "source_version": "s1"},
    )
    assert result.bars == (bar(),)


@pytest.mark.parametrize(
    "value,code",
    [
        (1.25, "inexact_decimal"),
        (True, "inexact_decimal"),
        (False, "inexact_decimal"),
        (float("inf"), "inexact_decimal"),
        (float("nan"), "inexact_decimal"),
        ("NaN", "nonfinite"),
        ("sNaN", "nonfinite"),
        ("Infinity", "nonfinite"),
        (Decimal("-Infinity"), "nonfinite"),
        ("not-a-decimal-secret", "invalid_value"),
    ],
)
def test_rejects_inexact_or_nonfinite_decimal_with_safe_issue(api, value, code):
    raw = bar().model_dump()
    raw["close"] = value
    result = normalize(api, bars=[raw, bar().model_dump()])
    assert result.bars == (bar(),)
    assert issue_keys(result) == {("bar", 0, "close", code)}
    assert "not-a-decimal-secret" not in result.model_dump_json()


@pytest.mark.parametrize("value", [None, "10.123456789123456789123456789", 10])
def test_exact_decimals_and_optional_null_are_preserved(api, value):
    raw = bar().model_dump()
    raw["close"] = value
    result = normalize(api, bars=[raw])
    assert result.issues == ()
    assert result.bars[0].close == (None if value is None else Decimal(value))


def test_explicit_scaling_does_not_round_at_decimal_context_precision(api):
    raw = bar().model_dump()
    raw["volume"] = "123456789012345678901234567890.123456789"
    with localcontext() as context:
        context.prec = 5
        result = normalize(api, bars=[raw], decimal_scales={"volume": "0.001"})
    assert result.issues == ()
    assert result.bars[0].volume == Decimal("123456789012345678901234567.890123456789")
    assert result.bars[0].close == Decimal("10")


def test_scale_does_not_turn_optional_null_into_number(api):
    raw = bar().model_dump()
    raw["amount"] = None
    result = normalize(api, bars=[raw], decimal_scales={"amount": "10000"})
    assert result.bars[0].amount is None


@pytest.mark.parametrize(
    "value,expected", [("Y", True), ("N", False), ("?", None), (None, None)]
)
def test_explicit_boolean_value_maps_preserve_false_and_unknown(api, value, expected):
    raw = state().model_dump()
    raw["suspended"] = value
    result = normalize(
        api, states=[raw], value_maps={"suspended": {"Y": True, "N": False, "?": None}}
    )
    assert result.issues == ()
    assert result.states[0].suspended is expected


@pytest.mark.parametrize("value", ["unexpected-secret", True, False, 1])
def test_value_map_only_matches_actual_raw_strings(api, value):
    raw = state().model_dump()
    raw["suspended"] = value
    result = normalize(
        api,
        states=[raw],
        value_maps={"suspended": {"True": True, "False": False, "1": True}},
    )
    assert result.states == ()
    assert issue_keys(result) == {("state", 0, "suspended", "unknown_value")}
    assert "unexpected-secret" not in result.model_dump_json()


def test_unknown_literal_enum_reports_target_field(api):
    raw = bar().model_dump()
    raw["limit_status"] = "vendor-secret-enum"
    result = normalize(api, bars=[raw])
    assert result.bars == ()
    assert issue_keys(result) == {("bar", 0, "limit_status", "invalid_value")}
    assert "vendor-secret-enum" not in result.model_dump_json()


def test_no_inferred_identity_adjustment_or_state(api):
    data = profile_data()
    for key in ("security_id", "adjustment_factor"):
        del data["bar_fields"][key]
    for key in ("suspended", "is_st", "is_delisting", "listing_date", "board_category"):
        del data["state_fields"][key]
    raw = bar().model_dump()
    raw["code"] = "600000"
    result = normalize(api, bars=[raw], states=[state().model_dump()], **data)
    assert result.bars == ()
    assert issue_keys(result) == {
        ("bar", 0, "security_id", "missing_field"),
        ("bar", 0, "adjustment_factor", "missing_field"),
    }
    mapped = result.states[0]
    assert mapped.suspended is mapped.is_st is mapped.is_delisting is None
    assert mapped.listing_date is mapped.board_category is None


def test_missing_supplier_column_reports_canonical_target_only(api):
    data = profile_data()
    data["bar_fields"]["close"] = "private-supplier-column"
    result = normalize(api, bars=[bar().model_dump()], **data)
    assert result.bars == ()
    assert issue_keys(result) == {("bar", 0, "close", "missing_field")}
    assert "private-supplier-column" not in result.model_dump_json()


def test_multiple_invalid_fields_are_reported_without_discarding_valid_rows(api):
    raw = bar().model_dump()
    del raw["adjustment_factor"]
    raw["available_at"] = "secret-invalid-date"
    raw["close"] = float("nan")
    result = normalize(api, bars=[bar().model_dump(), raw])
    assert result.bars == (bar(),)
    assert issue_keys(result) == {
        ("bar", 1, "adjustment_factor", "missing_field"),
        ("bar", 1, "available_at", "invalid_value"),
        ("bar", 1, "close", "inexact_decimal"),
    }
    assert "secret-invalid-date" not in result.model_dump_json()


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "wrong"},
        {"profile_id": ""},
        {"source_id": 3},
        {"source_version": True},
        {"unexpected-private-key": "secret"},
        {"bar_fields": {"float_market_cap": "x"}},
        {"bar_fields": {"suspended": "x"}},
        {"state_fields": {"close": "x"}},
        {"bar_fields": {"close": 1}},
        {"bar_fields": {"close": ""}},
        {"bar_constants": {"close": "10"}},
        {"state_constants": {"suspended": False}},
        {"bar_constants": {"unknown-private-target": "secret"}},
        {"value_maps": {"unknown-private-target": {"x": "y"}}},
        {"value_maps": {"suspended": {"N": "false"}}},
        {"value_maps": {"suspended": {False: False}}},
        {"value_maps": {"board_category": {"x": "not-a-board"}}},
        {"value_maps": {"source_id": {"x": "another-source"}}},
        {"value_maps": {"close": {"x": 1.5}}},
        {"decimal_scales": {"suspended": "1"}},
        {"decimal_scales": {"unknown-private-target": "1"}},
        {"decimal_scales": {"volume": 100}},
        {"decimal_scales": {"volume": 1.0}},
        {"decimal_scales": {"volume": True}},
        {"decimal_scales": {"volume": "0"}},
        {"decimal_scales": {"volume": "-1"}},
        {"decimal_scales": {"volume": "NaN"}},
        {"decimal_scales": {"volume": "Infinity"}},
        {"value_maps": []},
        {"bar_constants": {"close": [1, 2]}},
    ],
)
def test_malformed_profiles_fail_at_parse_boundary(api, changes):
    with pytest.raises(ValidationError):
        api.MappingProfile.model_validate(profile_data(**changes))


def test_shared_canonical_map_names_apply_consistently_to_both_record_kinds(api):
    data = profile_data()
    data["value_maps"] = {"security_id": {"vendor-A": "stable-A"}}
    raw_bar, raw_state = bar().model_dump(), state().model_dump()
    raw_bar["security_id"] = raw_state["security_id"] = "vendor-A"
    result = normalize(api, bars=[raw_bar], states=[raw_state], **data)
    assert result.issues == ()
    assert result.bars[0].security_id == result.states[0].security_id == "stable-A"


def test_profile_copies_and_deep_freezes_caller_configuration(api):
    data = profile_data()
    data["value_maps"] = {"suspended": {"N": False}}
    data["decimal_scales"] = {"volume": "100"}
    profile = api.MappingProfile.model_validate(data)
    data["bar_fields"]["close"] = "changed"
    data["value_maps"]["suspended"]["N"] = True
    data["decimal_scales"]["volume"] = "1"
    assert profile.bar_fields["close"] == "close"
    assert profile.value_maps["suspended"]["N"] is False
    assert profile.decimal_scales["volume"] == "100"
    with pytest.raises(TypeError):
        profile.bar_fields["close"] = "changed"
    with pytest.raises(TypeError):
        profile.value_maps["suspended"]["N"] = True
    with pytest.raises(TypeError):
        profile.decimal_scales["volume"] = "1"
    with pytest.raises(ValidationError, match="frozen"):
        profile.source_id = "changed"
    assert api.MappingProfile.model_validate_json(profile.model_dump_json()) == profile
    assert json.loads(profile.model_dump_json())["schema"] == data["schema"]


def test_invalid_row_structure_has_safe_canonical_issue(api):
    result = normalize(api, bars=["private-not-a-row", None, {"secret": object()}])
    assert result.bars == ()
    assert {i.row_index for i in result.issues} == {0, 1, 2}
    assert all(i.field in type(bar()).model_fields for i in result.issues)
    assert "private-not-a-row" not in result.model_dump_json()


def test_issue_contract_rejects_unsafe_fields(api):
    with pytest.raises(ValidationError):
        api.NormalizationIssue(
            kind="bar", row_index=0, field="supplier-secret", code="invalid_value"
        )


def test_exception_messages_are_never_inspected_or_reported(api):
    class PrivateMessage:
        def __str__(self):
            raise AssertionError("private-exception-message-accessed")

    class PrivateRow(dict):
        def __getitem__(self, key):
            if key == "close":
                raise ValueError(PrivateMessage())
            return super().__getitem__(key)

    result = normalize(api, bars=[PrivateRow(bar().model_dump())])
    assert result.bars == ()
    assert issue_keys(result) == {("bar", 0, "close", "invalid_value")}
    assert "private-exception" not in result.model_dump_json()


def test_scaling_never_silently_underflows_to_zero(api):
    raw = bar().model_dump()
    raw["volume"] = "1e-999999999999999999"
    result = normalize(api, bars=[raw], decimal_scales={"volume": "0.001"})
    assert result.bars == ()
    assert issue_keys(result) == {("bar", 0, "volume", "invalid_value")}


@pytest.mark.parametrize("value", [None, {}, [], b"10"])
def test_invalid_required_decimal_values_are_targeted(api, value):
    raw = bar().model_dump()
    raw["adjustment_factor"] = value
    result = normalize(api, bars=[raw])
    assert result.bars == ()
    assert issue_keys(result) == {("bar", 0, "adjustment_factor", "invalid_value")}


def test_valid_canonical_constants_are_frozen_and_not_remapped(api):
    data = profile_data()
    del data["bar_fields"]["close"]
    data["bar_constants"] = {"close": "10"}
    data["value_maps"] = {"close": {"supplier-price": "20"}}
    profile = api.MappingProfile.model_validate(data)
    result = api.normalize_records(
        profile, bar_rows=[bar().model_dump()], state_rows=[]
    )
    assert result.bars == (bar(),)
    assert result.issues == ()
    with pytest.raises(TypeError):
        profile.bar_constants["close"] = Decimal("20")
    assert api.MappingProfile.model_validate_json(profile.model_dump_json()) == profile


@pytest.mark.parametrize("value", [1.5, True, "NaN", "Infinity", None])
def test_invalid_canonical_decimal_constants_fail_before_normalization(api, value):
    data = profile_data()
    del data["bar_fields"]["adjustment_factor"]
    data["bar_constants"]["adjustment_factor"] = value
    with pytest.raises(ValidationError, match="invalid_mapping_profile"):
        api.MappingProfile.model_validate(data)


@pytest.mark.parametrize(
    "kind,target",
    [("bar", "trade_date"), ("state", "effective_from"), ("state", "listing_date")],
)
@pytest.mark.parametrize("value", [0, 86400, "20261001", "2026-W40-4"])
def test_dates_require_explicit_iso_calendar_dates(api, kind, target, value):
    raw = (bar() if kind == "bar" else state()).model_dump()
    raw[target] = value
    result = normalize(
        api,
        bars=[raw] if kind == "bar" else [],
        states=[raw] if kind == "state" else [],
    )
    assert not result.bars and not result.states
    assert (kind, 0, target, "invalid_value") in issue_keys(result)
