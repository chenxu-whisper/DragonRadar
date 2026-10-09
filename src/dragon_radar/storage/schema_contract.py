"""Stable PostgreSQL catalog capture for release-schema readiness."""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

CONTRACT_SCHEMA = "dragon-radar.postgresql-schema-contract"
CONTRACT_SCHEMA_VERSION = 1
CONTRACT_FIELDS = {
    "schema",
    "schema_version",
    "tables",
    "constraints",
    "indexes",
    "triggers",
}
TABLE_FIELDS = {"name", "columns"}
COLUMN_FIELDS = {"name", "type", "nullable", "identity", "generated"}
CONSTRAINT_FIELDS = {
    "table",
    "name",
    "kind",
    "columns",
    "referenced_table",
    "referenced_columns",
    "expression",
}
INDEX_FIELDS = {"table", "name", "unique", "valid", "key_expressions"}
TRIGGER_FIELDS = {
    "table",
    "name",
    "enabled",
    "timing",
    "events",
    "function",
}
CONSTRAINT_KINDS = {"primary_key", "unique", "foreign_key", "check"}
IDENTITY_VALUES = {"always", "by_default"}
GENERATED_VALUES = {"stored"}
TRIGGER_ENABLED_VALUES = {"origin", "disabled", "replica", "always"}
TRIGGER_TIMINGS = {"BEFORE", "AFTER", "INSTEAD OF"}
TRIGGER_EVENTS = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")

TABLES_AND_COLUMNS_SQL = """
SELECT
    relation.relname AS table_name,
    attribute.attname AS column_name,
    pg_catalog.format_type(attribute.atttypid, attribute.atttypmod) AS data_type,
    NOT attribute.attnotnull AS nullable,
    CASE attribute.attidentity
        WHEN 'a' THEN 'always'
        WHEN 'd' THEN 'by_default'
        ELSE NULL
    END AS identity,
    CASE attribute.attgenerated
        WHEN 's' THEN 'stored'
        ELSE NULL
    END AS generated
FROM pg_catalog.pg_class relation
JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
JOIN pg_catalog.pg_attribute attribute ON attribute.attrelid = relation.oid
WHERE namespace.nspname = current_schema()
  AND relation.relkind IN ('r', 'p')
  AND relation.relname <> 'alembic_version'
  AND attribute.attnum > 0
  AND NOT attribute.attisdropped
ORDER BY relation.relname, attribute.attnum
"""

CONSTRAINTS_SQL = """
SELECT
    relation.relname AS table_name,
    constraint_record.conname AS constraint_name,
    CASE constraint_record.contype
        WHEN 'p' THEN 'primary_key'
        WHEN 'u' THEN 'unique'
        WHEN 'f' THEN 'foreign_key'
        WHEN 'c' THEN 'check'
    END AS kind,
    ARRAY(
        SELECT attribute.attname::text
        FROM unnest(constraint_record.conkey)
            WITH ORDINALITY AS key_column(attnum, position)
        JOIN pg_catalog.pg_attribute attribute
          ON attribute.attrelid = relation.oid
         AND attribute.attnum = key_column.attnum
        ORDER BY key_column.position
    ) AS columns,
    referenced_relation.relname AS referenced_table,
    CASE
        WHEN constraint_record.contype = 'f' THEN ARRAY(
            SELECT attribute.attname::text
            FROM unnest(constraint_record.confkey)
                WITH ORDINALITY AS key_column(attnum, position)
            JOIN pg_catalog.pg_attribute attribute
              ON attribute.attrelid = referenced_relation.oid
             AND attribute.attnum = key_column.attnum
            ORDER BY key_column.position
        )
        ELSE ARRAY[]::text[]
    END AS referenced_columns,
    CASE
        WHEN constraint_record.contype = 'c'
        THEN pg_catalog.pg_get_expr(
            constraint_record.conbin,
            constraint_record.conrelid,
            true
        )
        ELSE NULL
    END AS expression
FROM pg_catalog.pg_constraint constraint_record
JOIN pg_catalog.pg_class relation ON relation.oid = constraint_record.conrelid
JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
LEFT JOIN pg_catalog.pg_class referenced_relation
  ON referenced_relation.oid = constraint_record.confrelid
WHERE namespace.nspname = current_schema()
  AND relation.relname <> 'alembic_version'
  AND constraint_record.contype IN ('p', 'u', 'f', 'c')
ORDER BY relation.relname, constraint_record.conname
"""

INDEXES_SQL = """
SELECT
    relation.relname AS table_name,
    index_relation.relname AS index_name,
    index_record.indisunique AS is_unique,
    index_record.indisvalid AS is_valid,
    ARRAY(
        SELECT pg_catalog.pg_get_indexdef(
            index_record.indexrelid,
            key_number,
            true
        )
        FROM generate_series(1, index_record.indnkeyatts) AS key_number
        ORDER BY key_number
    ) AS key_expressions
FROM pg_catalog.pg_index index_record
JOIN pg_catalog.pg_class relation ON relation.oid = index_record.indrelid
JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
JOIN pg_catalog.pg_class index_relation
  ON index_relation.oid = index_record.indexrelid
WHERE namespace.nspname = current_schema()
  AND relation.relname <> 'alembic_version'
ORDER BY relation.relname, index_relation.relname
"""

TRIGGERS_SQL = """
SELECT
    relation.relname AS table_name,
    trigger_record.tgname AS trigger_name,
    trigger_record.tgenabled AS enabled_code,
    trigger_record.tgtype AS trigger_type,
    function_record.proname AS function_name
FROM pg_catalog.pg_trigger trigger_record
JOIN pg_catalog.pg_class relation ON relation.oid = trigger_record.tgrelid
JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
JOIN pg_catalog.pg_proc function_record
  ON function_record.oid = trigger_record.tgfoid
WHERE namespace.nspname = current_schema()
  AND relation.relname <> 'alembic_version'
  AND NOT trigger_record.tgisinternal
ORDER BY relation.relname, trigger_record.tgname
"""


def _record_sort_key(record: Mapping[str, object]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sorted_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(records, key=_record_sort_key)


def capture_tables_and_columns(connection: Connection) -> list[dict[str, object]]:
    """Capture all application tables and physical columns."""
    tables: dict[str, list[dict[str, object]]] = {}
    for row in connection.execute(text(TABLES_AND_COLUMNS_SQL)).mappings():
        tables.setdefault(str(row["table_name"]), []).append(
            {
                "name": str(row["column_name"]),
                "type": str(row["data_type"]),
                "nullable": bool(row["nullable"]),
                "identity": row["identity"],
                "generated": row["generated"],
            }
        )
    return _sorted_records(
        [{"name": name, "columns": columns} for name, columns in tables.items()]
    )


def capture_constraints(connection: Connection) -> list[dict[str, object]]:
    """Capture PK, UQ, FK, and check constraints without catalog identifiers."""
    records = []
    for row in connection.execute(text(CONSTRAINTS_SQL)).mappings():
        records.append(
            {
                "table": str(row["table_name"]),
                "name": str(row["constraint_name"]),
                "kind": str(row["kind"]),
                "columns": list(row["columns"] or []),
                "referenced_table": row["referenced_table"],
                "referenced_columns": list(row["referenced_columns"] or []),
                "expression": row["expression"],
            }
        )
    return _sorted_records(records)


def capture_indexes(connection: Connection) -> list[dict[str, object]]:
    """Capture all application indexes, including constraint-backed indexes."""
    records = []
    for row in connection.execute(text(INDEXES_SQL)).mappings():
        records.append(
            {
                "table": str(row["table_name"]),
                "name": str(row["index_name"]),
                "unique": bool(row["is_unique"]),
                "valid": bool(row["is_valid"]),
                "key_expressions": list(row["key_expressions"] or []),
            }
        )
    return _sorted_records(records)


def _trigger_timing(trigger_type: int) -> str:
    if trigger_type & 64:
        return "INSTEAD OF"
    if trigger_type & 2:
        return "BEFORE"
    return "AFTER"


def _trigger_events(trigger_type: int) -> list[str]:
    event_bits = (
        (4, "INSERT"),
        (16, "UPDATE"),
        (8, "DELETE"),
        (32, "TRUNCATE"),
    )
    return [event for bit, event in event_bits if trigger_type & bit]


def capture_triggers(connection: Connection) -> list[dict[str, object]]:
    """Capture noninternal trigger behavior and function names."""
    enabled_values = {
        "O": "origin",
        "D": "disabled",
        "R": "replica",
        "A": "always",
    }
    records = []
    for row in connection.execute(text(TRIGGERS_SQL)).mappings():
        trigger_type = int(row["trigger_type"])
        records.append(
            {
                "table": str(row["table_name"]),
                "name": str(row["trigger_name"]),
                "enabled": enabled_values[str(row["enabled_code"])],
                "timing": _trigger_timing(trigger_type),
                "events": _trigger_events(trigger_type),
                "function": str(row["function_name"]),
            }
        )
    return _sorted_records(records)


def capture_schema_contract(connection: Connection) -> dict[str, object]:
    """Capture the normalized current-schema application catalog."""
    return {
        "schema": CONTRACT_SCHEMA,
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "tables": capture_tables_and_columns(connection),
        "constraints": capture_constraints(connection),
        "indexes": capture_indexes(connection),
        "triggers": capture_triggers(connection),
    }


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _require_exact_fields(
    value: object,
    fields: set[str],
    *,
    location: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    missing = sorted(fields - set(value))
    if missing:
        raise ValueError(f"{location} missing field: {missing[0]}")
    unexpected = sorted(set(value) - fields)
    if unexpected:
        raise ValueError(f"{location} unexpected field: {unexpected[0]}")
    return value


def _require_string(value: object, *, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{location} must be a non-empty string")
    return value


def _require_optional_string(value: object, *, location: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, location=location)


def _require_choice(
    value: object,
    choices: set[str],
    *,
    location: str,
) -> str:
    result = _require_string(value, location=location)
    if result not in choices:
        raise ValueError(f"{location} must be one of {sorted(choices)!r}")
    return result


def _require_optional_choice(
    value: object,
    choices: set[str],
    *,
    location: str,
) -> str | None:
    if value is None:
        return None
    return _require_choice(value, choices, location=location)


def _require_bool(value: object, *, location: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{location} must be a boolean")
    return value


def _require_string_list(value: object, *, location: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{location} must be an array of strings")
    return value


def _validate_columns(value: object, *, location: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{location} must be an array")
    columns = []
    for index, raw_column in enumerate(value):
        item_location = f"{location}[{index}]"
        column = _require_exact_fields(
            raw_column,
            COLUMN_FIELDS,
            location=item_location,
        )
        _require_string(column["name"], location=f"{item_location}.name")
        _require_string(column["type"], location=f"{item_location}.type")
        _require_bool(column["nullable"], location=f"{item_location}.nullable")
        _require_optional_choice(
            column["identity"],
            IDENTITY_VALUES,
            location=f"{item_location}.identity",
        )
        _require_optional_choice(
            column["generated"],
            GENERATED_VALUES,
            location=f"{item_location}.generated",
        )
        columns.append(column)
    names = [column["name"] for column in columns]
    if len(names) != len(set(names)):
        raise ValueError(f"{location} contains duplicate column names")
    return columns


def _validate_collection(
    value: object,
    *,
    name: str,
    fields: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    records = [
        _require_exact_fields(item, fields, location=f"{name}[{index}]")
        for index, item in enumerate(value)
    ]
    if records != _sorted_records(records):
        raise ValueError(f"{name} must be sorted")
    return records


def validate_schema_contract(raw_contract: object) -> dict[str, object]:
    """Strictly validate contract shape, field types, version, and ordering."""
    contract = _require_exact_fields(
        raw_contract,
        CONTRACT_FIELDS,
        location="contract",
    )
    if contract["schema"] != CONTRACT_SCHEMA:
        raise ValueError(f"contract schema must be {CONTRACT_SCHEMA!r}")
    version = contract["schema_version"]
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version != CONTRACT_SCHEMA_VERSION
    ):
        raise ValueError(f"contract schema_version must be {CONTRACT_SCHEMA_VERSION}")

    tables = _validate_collection(
        contract["tables"],
        name="tables",
        fields=TABLE_FIELDS,
    )
    for index, table in enumerate(tables):
        _require_string(table["name"], location=f"tables[{index}].name")
        _validate_columns(table["columns"], location=f"tables[{index}].columns")
    table_names = [table["name"] for table in tables]
    if len(table_names) != len(set(table_names)):
        raise ValueError("tables contains duplicate table names")

    constraints = _validate_collection(
        contract["constraints"],
        name="constraints",
        fields=CONSTRAINT_FIELDS,
    )
    for index, constraint in enumerate(constraints):
        location = f"constraints[{index}]"
        for field in ("table", "name"):
            _require_string(constraint[field], location=f"{location}.{field}")
        kind = _require_choice(
            constraint["kind"],
            CONSTRAINT_KINDS,
            location=f"{location}.kind",
        )
        columns = _require_string_list(
            constraint["columns"],
            location=f"{location}.columns",
        )
        referenced_table = _require_optional_string(
            constraint["referenced_table"],
            location=f"{location}.referenced_table",
        )
        referenced_columns = _require_string_list(
            constraint["referenced_columns"],
            location=f"{location}.referenced_columns",
        )
        expression = _require_optional_string(
            constraint["expression"],
            location=f"{location}.expression",
        )
        if kind in {"primary_key", "unique"} and not columns:
            raise ValueError(f"{location}.columns must not be empty")
        if kind == "foreign_key":
            if (
                not columns
                or referenced_table is None
                or len(columns) != len(referenced_columns)
            ):
                raise ValueError(f"{location} has an invalid foreign key")
        elif referenced_table is not None or referenced_columns:
            raise ValueError(f"{location} has unexpected referenced columns")
        if (kind == "check") != (expression is not None):
            raise ValueError(f"{location} has an invalid check expression")
    constraint_names = [
        (constraint["table"], constraint["name"]) for constraint in constraints
    ]
    if len(constraint_names) != len(set(constraint_names)):
        raise ValueError("constraints contains duplicate table/name pairs")

    indexes = _validate_collection(
        contract["indexes"],
        name="indexes",
        fields=INDEX_FIELDS,
    )
    for index, index_record in enumerate(indexes):
        location = f"indexes[{index}]"
        for field in ("table", "name"):
            _require_string(index_record[field], location=f"{location}.{field}")
        for field in ("unique", "valid"):
            _require_bool(index_record[field], location=f"{location}.{field}")
        key_expressions = _require_string_list(
            index_record["key_expressions"],
            location=f"{location}.key_expressions",
        )
        if not key_expressions:
            raise ValueError(f"{location}.key_expressions must not be empty")
    index_names = [(index["table"], index["name"]) for index in indexes]
    if len(index_names) != len(set(index_names)):
        raise ValueError("indexes contains duplicate table/name pairs")

    triggers = _validate_collection(
        contract["triggers"],
        name="triggers",
        fields=TRIGGER_FIELDS,
    )
    for index, trigger in enumerate(triggers):
        location = f"triggers[{index}]"
        for field in ("table", "name", "function"):
            _require_string(trigger[field], location=f"{location}.{field}")
        _require_choice(
            trigger["enabled"],
            TRIGGER_ENABLED_VALUES,
            location=f"{location}.enabled",
        )
        _require_choice(
            trigger["timing"],
            TRIGGER_TIMINGS,
            location=f"{location}.timing",
        )
        events = _require_string_list(
            trigger["events"],
            location=f"{location}.events",
        )
        canonical_events = [event for event in TRIGGER_EVENTS if event in events]
        if not events or events != canonical_events:
            raise ValueError(f"{location}.events must be unique and ordered")
    trigger_names = [(trigger["table"], trigger["name"]) for trigger in triggers]
    if len(trigger_names) != len(set(trigger_names)):
        raise ValueError("triggers contains duplicate table/name pairs")
    return contract


def load_schema_contract(path: Path) -> dict[str, object]:
    """Load and strictly validate a schema contract from disk."""
    try:
        raw_contract = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read schema contract: {exc}") from exc
    return validate_schema_contract(raw_contract)


def load_packaged_schema_contract() -> dict[str, object]:
    """Load the contract packaged with ``dragon_radar.storage``."""
    resource = resources.files("dragon_radar.storage").joinpath("schema_contract.json")
    try:
        raw_contract = json.loads(
            resource.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read packaged schema contract: {exc}") from exc
    return validate_schema_contract(raw_contract)


def write_schema_contract(
    connection: Connection,
    output_path: Path,
) -> dict[str, object]:
    """Capture and atomically write a normalized schema contract."""
    contract = capture_schema_contract(connection)
    validate_schema_contract(contract)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return contract


def compare_schema_contract(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> list[str]:
    """Return stable mismatch descriptions for two normalized contracts."""
    errors = []
    for field in ("schema", "schema_version"):
        if expected.get(field) != actual.get(field):
            errors.append(
                f"{field} mismatch: expected {expected.get(field)!r}, "
                f"found {actual.get(field)!r}"
            )
    for collection in ("tables", "constraints", "indexes", "triggers"):
        expected_records = [
            _record_sort_key(record)
            for record in expected.get(collection, [])
            if isinstance(record, Mapping)
        ]
        actual_records = [
            _record_sort_key(record)
            for record in actual.get(collection, [])
            if isinstance(record, Mapping)
        ]
        if expected_records != actual_records:
            missing = list(
                (Counter(expected_records) - Counter(actual_records)).elements()
            )
            unexpected = list(
                (Counter(actual_records) - Counter(expected_records)).elements()
            )
            first_difference = next(
                (
                    index
                    for index, records in enumerate(
                        zip(expected_records, actual_records, strict=False)
                    )
                    if records[0] != records[1]
                ),
                min(len(expected_records), len(actual_records)),
            )
            errors.append(
                f"{collection} mismatch at index {first_difference}: "
                f"expected_count={len(expected_records)}, "
                f"actual_count={len(actual_records)}, "
                f"missing={missing[:3]}, unexpected={unexpected[:3]}"
            )
    return errors
