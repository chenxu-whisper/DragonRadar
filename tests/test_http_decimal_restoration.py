"""Decimal restoration preserves archive semantics with bounded ABC dispatch."""

from collections import UserDict
from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType

import pytest

from dragon_radar.http import serialization
from dragon_radar.http.errors import DataUnavailable


def test_frozen_json_tree_does_not_dispatch_scalars_through_mapping_abc(monkeypatch):
    checks = []

    class CountingMappingMeta(type):
        def __instancecheck__(cls, value):
            checks.append(type(value))
            return isinstance(value, Mapping)

    class CountingMapping(metaclass=CountingMappingMeta):
        pass

    monkeypatch.setattr(serialization, "Mapping", CountingMapping)
    source = MappingProxyType(
        {
            "history": tuple(
                MappingProxyType(
                    {"close": "1.00000000000000000001", "index": index, "empty": None}
                )
                for index in range(100)
            ),
            "plain": ["unchanged", True, 0.1, Decimal("0.1")],
        }
    )

    result = serialization.restore_decimal_fields(source)

    assert result["history"][0]["close"] == Decimal("1.00000000000000000001")
    assert result["plain"] == ["unchanged", True, 0.1, Decimal("0.1")]
    assert checks == []


def test_generic_mapping_and_container_subclasses_keep_original_semantics():
    class ArchiveList(list):
        pass

    class ArchiveTuple(tuple):
        pass

    class FinancialText(str):
        pass

    source = UserDict(
        {"close": ArchiveList([FinancialText("0.1"), ArchiveTuple(("0.2",))])}
    )
    assert serialization.restore_decimal_fields(source) == {
        "close": [Decimal("0.1"), [Decimal("0.2")]]
    }


def test_restored_outputs_are_independent_of_source_and_other_requests():
    source = MappingProxyType(
        {"rows": (MappingProxyType({"last": "0.1", "label": "original"}),)}
    )
    first = serialization.restore_decimal_fields(source)
    second = serialization.restore_decimal_fields(source)
    first["rows"][0]["label"] = "changed"
    first["rows"].append({"last": Decimal("99")})
    assert second == {"rows": [{"last": Decimal("0.1"), "label": "original"}]}
    assert source["rows"][0]["label"] == "original"
    assert len(source["rows"]) == 1


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "invalid"])
def test_financial_text_rejects_invalid_or_nonfinite_values(value):
    with pytest.raises(DataUnavailable):
        serialization.restore_decimal_fields({"rows": [{"close": value}]})


def test_unknown_scalars_and_nonfinancial_strings_are_preserved():
    marker = object()
    assert serialization.restore_decimal_fields({"other": marker})["other"] is marker
    assert serialization.restore_decimal_fields({"label": "NaN"}) == {"label": "NaN"}
