"""Whitelist natural-language query: typed AST, deterministic parser.

Slice 1 surface: pure functions only (models + parser, no HTTP/DB runtime).
"""

from dragon_radar.query.models import (
    AST_VERSION,
    PARSER_VERSION,
    ROLE_EXTERNAL_VALUES,
    Field,
    Op,
    ParserKind,
    Predicate,
    QueryAst,
    SortDirection,
    SortSpec,
    TypedAstWire,
    UnsupportedCode,
    ast_hash_of,
    canonical_ast,
    is_return_ratio_precision_supported,
    quantize_return_ratio,
    query_records,
    wire_ast,
)
from dragon_radar.query.parser import (
    ParseOutcome,
    QueryParsed,
    QueryUnsupported,
    UnsupportedItem,
    explain_ast,
    parse_query,
    with_snapshot_line,
)

__all__ = [
    "AST_VERSION",
    "PARSER_VERSION",
    "ROLE_EXTERNAL_VALUES",
    "Field",
    "Op",
    "ParseOutcome",
    "ParserKind",
    "Predicate",
    "QueryAst",
    "QueryParsed",
    "QueryUnsupported",
    "SortDirection",
    "SortSpec",
    "TypedAstWire",
    "UnsupportedCode",
    "UnsupportedItem",
    "ast_hash_of",
    "canonical_ast",
    "explain_ast",
    "is_return_ratio_precision_supported",
    "parse_query",
    "quantize_return_ratio",
    "query_records",
    "with_snapshot_line",
    "wire_ast",
]
