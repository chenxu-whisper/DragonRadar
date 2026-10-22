"""Whitelist natural-language query: typed AST, deterministic parser.

Slice 1 surface: pure functions only (models + parser, no HTTP/DB runtime).
Slice 3 adds the optional model-provider seam (model_parser), still pure.
"""

from dragon_radar.query.model_parser import (
    QUERY_AI_DOMAINS,
    ModelQueryParser,
    QuerySnapshotMeta,
    propose_model_ast,
    query_model_parser_from_environment,
)
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
    "QUERY_AI_DOMAINS",
    "ROLE_EXTERNAL_VALUES",
    "Field",
    "ModelQueryParser",
    "Op",
    "ParseOutcome",
    "ParserKind",
    "Predicate",
    "QueryAst",
    "QueryParsed",
    "QuerySnapshotMeta",
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
    "propose_model_ast",
    "quantize_return_ratio",
    "query_model_parser_from_environment",
    "query_records",
    "with_snapshot_line",
    "wire_ast",
]
