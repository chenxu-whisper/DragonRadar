"""Optional model-provider seam for whitelist query parsing (spec 3.7).

Pure functions only (no IO, no clock, no database runtime). The deterministic
parser in :mod:`dragon_radar.query.parser` is always the baseline; an owner
application may explicitly inject a :class:`ModelQueryParser` adapter. The
model is asked for an AST body *only* when the deterministic parser produced
an unsupported outcome, an adapter is injected, and the target publication
carries the AI outbound license (the ``ai_licensed`` gate; slice-2 resolves
that license via the existing ``get_ai_snapshot(..., ai_domains=...)``
mechanism using :data:`QUERY_AI_DOMAINS`).

Result-type reuse: the seam returns the same mutually exclusive two-state
outcome as the deterministic parser (``ParseOutcome = QueryParsed |
QueryUnsupported``); no second result schema exists.

Outbound context (AC-X02): the adapter receives only the raw text and a
:class:`QuerySnapshotMeta` with three closed fields. It never receives payload
data, names, market facts, or any channel to act on the AST.
"""

from collections.abc import Collection
from datetime import date
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import ConfigDict, field_validator
from pydantic import Field as PydanticField

from dragon_radar.ai.provider import validate_provider_metadata
from dragon_radar.domain.models import FrozenModel

from .models import (
    ROLE_EXTERNAL_VALUES,
    Field,
    Op,
    ParserKind,
    QueryAst,
    UnsupportedCode,
    ast_hash_of,
)
from .parser import (
    ParseOutcome,
    QueryParsed,
    QueryUnsupported,
    UnsupportedItem,
    explain_ast,
)

# AI outbound domain for query parsing (mirrors AI_INPUT_DOMAINS in
# dragon_radar.ai.models): whitelist parsing may only see market-domain
# snapshots. Slice 2 passes this constant to
# PublicationRepository.get_ai_snapshot(connection, snapshot_id,
# ai_domains=QUERY_AI_DOMAINS) after the transaction/authorization boundary.
QUERY_AI_DOMAINS = frozenset({"market"})


class _StrictMetaModel(FrozenModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class QuerySnapshotMeta(_StrictMetaModel):
    """The complete context sent to a model: exactly three closed fields."""

    snapshot_id: UUID
    trade_date: date
    revision: int = PydanticField(ge=0)

    @field_validator("snapshot_id", mode="before")
    @classmethod
    def _normalize_uuid(cls, value: object) -> object:
        # Accept the canonical str form and normalize server-side; anything
        # else falls through to strict UUID validation.
        if isinstance(value, str):
            return UUID(value)
        return value

    @field_validator("revision")
    @classmethod
    def _revision_non_negative_int(cls, value: int) -> int:
        # Same runtime guard wording as with_snapshot_line: bool is an int
        # subclass and must not masquerade as a payload revision.
        if isinstance(value, bool):
            raise ValueError("revision must be an int (bool rejected)")
        return value


@runtime_checkable
class ModelQueryParser(Protocol):
    """Structural adapter contract; the owner app injects an implementation."""

    name: str
    model_version: str

    def propose(self, text: str, snapshot_meta: QuerySnapshotMeta) -> dict | None:
        """Return only a Python-native AST body dict, or ``None`` to decline.

        The body must be exactly ``{version, predicates, sort, limit}`` in
        Python-native form: ``predicates`` is a ``tuple``, each ``field``/``op``
        is a :mod:`dragon_radar.query.models` enum instance, ``sort`` carries
        enum instances, and ``return_ratio`` values are ``Decimal``. Extra keys
        (notably ``ast_hash`` and any free text) are forbidden by
        ``extra='forbid'``.

        JSON round-trip shapes are NOT accepted here: JSON ``list`` instead of
        tuple, enum name/value strings instead of enum instances, and Decimal
        strings or JSON ``float`` instead of ``Decimal`` all fail the same
        strict :class:`QueryAst` re-validation as ``model_ast_rejected``.
        Decoding a real network adapter response into these rich Python types
        is the injector's responsibility at the slice-2 wiring point; this
        seam performs no JSON decoding.
        """


# Deterministic, non-leaking client-facing reasons: no exception class names,
# stack traces, adapter-internal strings, or echoed payloads/dicts.
_REJECTED_REASON = "模型返回的 AST 未通过服务端校验，未采纳"
_REJECTED_ROLE_REASON = "模型返回的 AST 引用了当前快照未启用的角色，未采纳"
_UNAVAILABLE_REASON = "模型解析服务暂不可用，已回退到确定性解析结果"


def _all_roles_enabled(ast: QueryAst, allowed_roles: frozenset[str]) -> bool:
    """Re-check the snapshot role subset on a model-proposed AST.

    The deterministic parser rejects role values outside the injected subset
    (parser._Scanner.allowed_roles); the same authorization boundary applies on
    the model channel, otherwise a model could launder a role the snapshot
    does not enable through QueryAst's global six-enum matrix.
    """

    for predicate in ast.predicates:
        if predicate.field is not Field.ROLE:
            continue
        values = predicate.value if predicate.op is Op.IN else (predicate.value,)
        if any(value not in allowed_roles for value in values):
            return False
    return True


def _append_model_item(
    deterministic: QueryUnsupported, code: UnsupportedCode, reason: str
) -> QueryUnsupported:
    """Append a model-channel item, preserving every deterministic item."""

    return QueryUnsupported(
        unsupported=(
            *deterministic.unsupported,
            UnsupportedItem(code=code, token=None, reason=reason),
        )
    )


def propose_model_ast(
    text: str,
    *,
    deterministic: ParseOutcome,
    adapter: ModelQueryParser | None,
    ai_licensed: bool,
    snapshot_meta: QuerySnapshotMeta,
    role_names: Collection[str] | None = None,
) -> ParseOutcome:
    """Run the optional model seam and return the shared two-state outcome.

    ``role_names`` mirrors :func:`dragon_radar.query.parser.parse_query`:
    ``None`` means the global six-role set (:data:`ROLE_EXTERNAL_VALUES`),
    otherwise the snapshot-enabled subset (normalized to a ``frozenset``).
    The supplied deterministic outcome is never re-parsed, but after a
    proposal passes :class:`QueryAst` validation every role predicate is
    re-checked against this subset (step 7), so a model cannot launder a
    role the snapshot does not enable through the global enum matrix.

    Gate order (fail-closed at every step):

    1. deterministic success -> returned verbatim, the adapter is never
       consulted;
    2. a deterministic outcome whose sole item is ``empty_input`` ->
       returned verbatim even when both gates pass: blank/whitespace input
       is never escalated to the model channel;
    3. no adapter injected, or ``ai_licensed`` not exactly ``True`` ->
       deterministic unsupported outcome returned verbatim (truthy
       non-bools such as ``1`` or ``"yes"`` never open the gate);
    4. adapter metadata/property access or ``propose`` raises -> the
       deterministic outcome plus one ``model_unavailable`` item (only
       ``Exception`` subclasses are contained, never ``BaseException``);
    5. ``propose`` answers ``None`` -> deterministic outcome unchanged;
    6. non-dict answer or a body failing the same :class:`QueryAst` matrix
       (including exotic proposal objects whose methods raise non-
       ``ValueError`` errors during validation) -> deterministic outcome
       plus one ``model_ast_rejected`` item;
    7. a body referencing a role outside the snapshot subset ->
       deterministic outcome plus one ``model_ast_rejected`` item;
    8. a valid body -> accepted :class:`QueryParsed` with ``parser_kind=
       model``, the adapter ``model_version``, a server-recomputed
       ``ast_hash`` and the deterministic explanation generator output.

    Other deterministic unsupported codes (e.g. ``unsupported_logic``,
    ``unknown_field``) remain eligible for model rescue.
    """

    allowed_roles = (
        ROLE_EXTERNAL_VALUES if role_names is None else frozenset(role_names)
    )

    if (
        isinstance(deterministic, QueryParsed)
        or adapter is None
        or ai_licensed is not True
        or (
            isinstance(deterministic, QueryUnsupported)
            and len(deterministic.unsupported) == 1
            and deterministic.unsupported[0].code is UnsupportedCode.EMPTY_INPUT
        )
    ):
        return deterministic

    try:
        # Duck-typed adapter state is untrusted: the same metadata gate as
        # provider configuration proves name/model_version are 1..100-char
        # stripped ASCII-safe strings before any result can carry them.
        validate_provider_metadata(adapter.name, error="Invalid model adapter name")
        model_version = validate_provider_metadata(
            adapter.model_version, error="Invalid model adapter version"
        )
        proposal = adapter.propose(text, snapshot_meta)
    except Exception:
        # Deliberately broad at this trust boundary, but never BaseException:
        # KeyboardInterrupt/SystemExit must propagate. Nothing from the
        # exception reaches the client.
        return _append_model_item(
            deterministic,
            UnsupportedCode.MODEL_UNAVAILABLE,
            _UNAVAILABLE_REASON,
        )

    if proposal is None:
        return deterministic
    if not isinstance(proposal, dict):
        return _append_model_item(
            deterministic,
            UnsupportedCode.MODEL_AST_REJECTED,
            _REJECTED_REASON,
        )

    try:
        # Same model, same field x op x value matrix as the trust boundary in
        # models.py; extra=forbid rejects smuggled ast_hash/unknown keys and
        # strict mode rejects unknown enum values, over-precise ratios, etc.
        ast = QueryAst.model_validate(proposal)
    except Exception:
        # ValidationError (a ValueError subclass) is the expected failure,
        # but exotic proposal objects may raise arbitrary errors while
        # pydantic touches their methods (e.g. a Decimal subclass whose
        # is_finite raises RuntimeError); contain every Exception at this
        # trust boundary so adapter internals never leak. BaseException
        # (KeyboardInterrupt/SystemExit) still propagates.
        return _append_model_item(
            deterministic,
            UnsupportedCode.MODEL_AST_REJECTED,
            _REJECTED_REASON,
        )

    if not _all_roles_enabled(ast, allowed_roles):
        return _append_model_item(
            deterministic,
            UnsupportedCode.MODEL_AST_REJECTED,
            _REJECTED_ROLE_REASON,
        )

    return QueryParsed(
        ast=ast,
        ast_hash=ast_hash_of(ast),  # server-side rehash; never the model's
        explanation=explain_ast(ast),  # deterministic preview, no free text
        parser_kind=ParserKind.MODEL,
        model_version=model_version,
    )


def query_model_parser_from_environment() -> ModelQueryParser | None:
    """Return the configured model adapter.

    Always ``None`` in v1: aligned with
    :func:`dragon_radar.ai.provider.provider_from_environment`, a configured
    name/token without an explicitly installed adapter stays unavailable, and
    no real network adapter is inferred from the environment. The owner app
    injects an adapter explicitly when (if) one ships.
    """

    return None


__all__ = [
    "QUERY_AI_DOMAINS",
    "ModelQueryParser",
    "QuerySnapshotMeta",
    "propose_model_ast",
    "query_model_parser_from_environment",
]
