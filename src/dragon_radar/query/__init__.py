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
from dragon_radar.query.repository import QueryRepository

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
    "QueryRepository",
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
    "install_query_routes",
    "is_return_ratio_precision_supported",
    "parse_query",
    "propose_model_ast",
    "quantize_return_ratio",
    "query_model_parser_from_environment",
    "query_records",
    "with_snapshot_line",
    "wire_ast",
]


def __getattr__(name: str):
    # Lazy re-export: the route module imports query submodules, so an eager
    # package-level import here cycles whenever http.routes.query is imported
    # before the query package itself is initialized.
    if name == "install_query_routes":
        from dragon_radar.http.routes.query import install_query_routes

        return install_query_routes
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
