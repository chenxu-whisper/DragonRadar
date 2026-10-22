"""Deterministic Chinese whitelist query parser (parser_version zh-whitelist-v1).

Pure functions only: no IO, no randomness, no clock. The grammar and the
whole vocabulary are frozen module-level constants; anything outside the
whitelist yields a ``QueryUnsupported`` outcome with a closed-enum code, the
offending token and a human-readable reason. The parser never guesses.

Input shape contract (HTTP routes map these to 400):
``text`` must be a ``str`` of 1..500 characters, otherwise ``ValueError``.
A non-empty but purely blank string (incl. U+3000, normalized by NFKC) is a
normal business outcome carrying the single ``empty_input`` code.
"""

import re
import unicodedata
from collections.abc import Collection
from datetime import date
from decimal import Context, Decimal
from types import MappingProxyType

from pydantic import (
    ConfigDict,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic import Field as PydanticField

from dragon_radar.domain.models import FrozenModel

from .models import (
    AST_VERSION,
    PARSER_VERSION,
    ROLE_EXTERNAL_VALUES,
    Field,
    Op,
    Predicate,
    QueryAst,
    SortDirection,
    SortSpec,
    UnsupportedCode,
    ast_hash_of,
    is_return_ratio_precision_supported,
    quantize_return_ratio,
)

# ---------------------------------------------------------------------------
# Frozen vocabulary (spec 3.3)
# ---------------------------------------------------------------------------

_FIELD_ITEMS_ORDERED = (
    ("股票代码", Field.SECURITY_ID),
    ("证券", Field.SECURITY_ID),
    ("代码", Field.SECURITY_ID),
    ("题材", Field.SECTOR_ID),
    ("板块", Field.SECTOR_ID),
    ("概念", Field.SECTOR_ID),
    ("涨跌幅", Field.RETURN_RATIO),
    ("涨幅", Field.RETURN_RATIO),
    ("连板", Field.BOARDS),
    ("板数", Field.BOARDS),
    ("几板", Field.BOARDS),
    ("角色", Field.ROLE),
    ("定位", Field.ROLE),
)
FIELD_WORDS = MappingProxyType(dict(_FIELD_ITEMS_ORDERED))

EQ_WORDS = ("等于", "是", "为", "=")
IN_WORD = "属于"
GT_WORDS = ("大于", "高于", ">")
GTE_WORDS = ("大于等于", "不低于", "≥", "至少")
LT_WORDS = ("小于", "低于", "<")
LTE_WORDS = ("小于等于", "不高于", "≤", "至多")
# Board counts can be phrased value-first ("2连板以上"): 以上/以下 only exist
# in that construction (the anchor "2连板以上" requires 以上 even though spec
# 3.3 enumerates 大于等于/不低于/≥/至少 as the gte words).
BOARD_SUFFIX_OPS = MappingProxyType({"以上": Op.GTE, "以下": Op.LTE})
CONJUNCTION_CHARS = ("且", "并", "和", ",", "、")
STOPWORDS = ("的股票", "的标的", "股票", "标的")
# Residual fragments after trimming: an element made only of these (and the
# genitive 的) carries no usable sector identity (review m4).
_STOPWORD_FRAGMENTS = ("的股票", "的标的", "股票", "标的", "证券", "的")
_STOPWORDS_BY_LEN = tuple(sorted(STOPWORDS, key=len, reverse=True))
_DIRECTIONLESS_SORT_FIELDS = frozenset({Field.ROLE, Field.SECTOR_ID})
DIRECTION_WORDS = MappingProxyType(
    {
        "从高到低": SortDirection.DESC,
        "从低到高": SortDirection.ASC,
        "降序": SortDirection.DESC,
        "升序": SortDirection.ASC,
    }
)

_ROLE_ITEMS_ORDERED = (
    ("趋势", "trend_leader"),
    ("情绪", "emotion_leader"),
    ("中军", "zhongjun"),
    ("跟风", "genfeng"),
    ("补涨", "buZhang"),
    ("接力", "jieli"),
)
ROLE_WORDS = MappingProxyType(dict(_ROLE_ITEMS_ORDERED))
_ROLE_TO_ZH = MappingProxyType({value: key for key, value in _ROLE_ITEMS_ORDERED})
# English enum values are case-insensitive in the grammar (spec 3.3) but the
# canonical spelling is preserved ("buZhang" stays camelCase, review M2).
_ROLE_EN_CANONICAL = MappingProxyType(
    {value.lower(): value for value in ROLE_EXTERNAL_VALUES}
)

FIELD_LABELS = MappingProxyType(
    {
        Field.SECURITY_ID: "证券代码",
        Field.SECTOR_ID: "题材",
        Field.RETURN_RATIO: "涨幅",
        Field.BOARDS: "连板数",
        Field.ROLE: "角色",
    }
)
OP_LABELS = MappingProxyType(
    {
        Op.EQ: "等于",
        Op.IN: "属于",
        Op.GT: "大于",
        Op.GTE: "大于等于",
        Op.LT: "小于",
        Op.LTE: "小于等于",
    }
)
_NUMERIC_FIELDS = frozenset({Field.RETURN_RATIO, Field.BOARDS})

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_FIELD_ALT = "|".join(
    sorted((word for word, _ in _FIELD_ITEMS_ORDERED), key=len, reverse=True)
)
_DIR_ALT = "|".join(sorted(DIRECTION_WORDS, key=len, reverse=True))
_SORT_RE = re.compile(
    rf"[按依]\s*(?P<field>{_FIELD_ALT})(?:\s*排序)?(?:\s*(?P<dir>{_DIR_ALT}))?"
)
_MARKER_RE = re.compile(r"[按依]")
# Spec 3.3 line 133 freezes limit counters to 条/只; 个 is intentionally NOT
# accepted (Backlog: 「前10个」 falls through to orphan_token).
_LIMIT_RE = re.compile(r"(?:前|最多|限制)\s*(\d{1,3})(?:\s*(?:条|只))?")

# Single source of truth for operator tokens (review m3 / item 12): the regex
# is built from the frozen word tuples above, branches ordered by descending
# length so longer words (大于等于) never get shadowed by prefixes (大于).
_OP_GROUPS = (
    GTE_WORDS,
    LTE_WORDS,
    GT_WORDS,
    LT_WORDS,
    EQ_WORDS,
    (IN_WORD,),
)
_OP_WORDS = tuple(dict.fromkeys(word for group in _OP_GROUPS for word in group))
# Ordered branch tuple (review item 12): stable length-descending sort keeps
# longer alternatives (大于等于) ahead of their prefixes (大于) so the regex
# alternation cannot shadow them, and the tuple itself is a deterministic
# vocabulary lock tests can index branch-by-branch. Escaping is applied only
# when joining into the compiled pattern.
_OP_PATTERN = tuple(sorted(_OP_WORDS, key=len, reverse=True))
_OP_RE = re.compile("|".join(re.escape(word) for word in _OP_PATTERN))
_IN_ZHONG_RE = re.compile(r"在\s*(?P<body>.+?)\s*之中")
_IN_RENYI_RE = re.compile(r"包含\s*(?P<body>.+?)\s*任一")

_PARENS_RE = re.compile(r"[（(）)]")
_OR_RE = re.compile(r"或者|或")
_NEGATION_RE = re.compile(r"非|排除|不(?!高于|低于)")
_ARITH_RE = re.compile(r"--|/\*|\*/|\{\{|\}\}|[+*/]")
_ORPHAN_PUNCT_RE = re.compile(r"['\"`;\\]")
_TIME_RES = (
    re.compile(r"最近|今日|昨天|前天|本周|本月|本年|今年"),
    re.compile(r"近\s*\d+\s*(?:个)?\s*(?:日|天|周|月|年)"),
    re.compile(r"\d+\s*个?\s*交易日"),
    re.compile(r"\d+\s*(?:个)?\s*(?:日|天|周|月|年)(?:内|前|来|以来)?"),
    re.compile(r"过去\s*\d*"),
)
_MOOD_RE = re.compile(
    r"[?？]|请问|请|帮我|帮忙|帮|告诉|查一下|查下|看看|能不能|可不可以|吗|呢"
)

_SEPARATOR_CLASS = r"\s，,、且并和"
_RATIO_RE = re.compile(r"-?\d+(?:\.\d+)?|-?\.\d+")
_PERCENT_RE = re.compile(r"\s*%")
# Percent conversion is an exact exponent shift, never arithmetic division
# (review round 3 BLOCKER): ``raw / Decimal(100)`` under the default context
# precision (28) rounds 29+ digit coefficients and silently collapses e.g.
# ``0.999...(29)%`` onto the grid value ``0.01``. scaleb(-2) moves only the
# exponent with a precision that can hold any <=500-char input coefficient,
# so the integer-digit and six-decimal gates reject over-long literals
# naturally. This context is never used for rounding.
_EXACT_SHIFT_CONTEXT = Context(prec=1000)
_BOARDS_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?|-?[零〇一二两三四五六七八九十百]{1,3}")
_BOARDS_LEADING_RE = re.compile(
    r"(?P<num>\d+|[零〇一二两三四五六七八九十百]{1,3})\s*(?:连板|板)"
    r"(?:\s*(?P<suffix>以上|以下))?"
)
_BOARDS_TRAILING_RE = re.compile(r"\s*(?:连板|板)")
_CN_DIGITS = MappingProxyType(
    {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
)
_SECURITY_ELEMENT_RE = re.compile(r"[A-Za-z0-9_.:-]+")
_SECTOR_ELEMENT_RE = re.compile(r"[A-Za-z0-9_.:\u4e00-\u9fff-]+")
# Inside an IN body this separator splits list elements only: v1 cannot
# start a second predicate at this comma, so 「代码属于 A，代码是 B」 makes
# 「代码是 B」 an illegal element and fails closed (round-3 NIT-D, Backlog).
_LIST_SEP_RE = re.compile(r"\s*[,、]\s*")
_ROLE_EN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
# IN-list board elements are ASCII integers only (Chinese numeral forms live
# in the value-first/scalar constructions handled by _read_boards).
_BOARDS_ELEMENT_RE = re.compile(r"\d+")
_UNKNOWN_RUN_RE = re.compile(rf"[^{_SEPARATOR_CLASS}]{{1,12}}")

# Frozen element boundary phrases (review M1 / D3). A free-text element run
# is a maximal whitespace/punctuation-free regex match; when such a run embeds
# a field leader / sort or limit marker / unspaced conjunction, the input is
# ambiguous (value content vs. glued clause) and parsing fails closed. Field
# leaders are listed longest-first to defeat prefix masking (股票代码 > 代码).
_SORT_MARKER_WORDS = ("按", "依")
_LIMIT_MARKER_WORDS = ("最多", "限制", "前")
_BOUNDARY_PHRASES = tuple(
    sorted(
        (
            *(word for word, _ in _FIELD_ITEMS_ORDERED),
            *_SORT_MARKER_WORDS,
            *_LIMIT_MARKER_WORDS,
            *CONJUNCTION_CHARS,
        ),
        key=len,
        reverse=True,
    )
)

_PREDICATE_LIMIT = 5
_TEXT_LIMIT = 500
_BASIS_LINE = "口径：涨幅取 20 个交易日区间收益，未复权 raw_close，基准 B000300"


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


class _StrictFrozenModel(FrozenModel):
    # Verbatim-identical to dragon_radar.query.models._StrictFrozenModel;
    # duplicated locally so this module does not import underscore-private
    # names across modules (review n5).
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class UnsupportedItem(_StrictFrozenModel):
    code: UnsupportedCode
    token: str | None = None
    reason: StrictStr = PydanticField(min_length=1)

    @field_validator("token", mode="before")
    @classmethod
    def _blank_token_serializes_as_null(cls, value: object) -> object:
        # A blank token carries no display value (spec 3.7 token:null
        # precedent, e.g. an IN list whose last separator masks 「前10」):
        # normalize "" / whitespace-only tokens to None at construction, so
        # wire output is always null rather than an empty string (round-3
        # NIT-A).
        if isinstance(value, str) and not value.strip():
            return None
        return value


class QueryParsed(_StrictFrozenModel):
    ast: QueryAst
    ast_hash: str = PydanticField(pattern=r"^[0-9a-f]{64}$")
    explanation: StrictStr = PydanticField(min_length=1)
    unsupported: tuple[()] = ()

    @model_validator(mode="after")
    def _ast_hash_matches_body(self) -> "QueryParsed":
        if self.ast_hash != ast_hash_of(self.ast):
            raise ValueError("ast_hash must equal ast_hash_of(ast)")
        return self


class QueryUnsupported(_StrictFrozenModel):
    ast: None = None
    ast_hash: None = None
    explanation: None = None
    unsupported: tuple[UnsupportedItem, ...] = PydanticField(min_length=1)


ParseOutcome = QueryParsed | QueryUnsupported


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------


def _fail(code: UnsupportedCode, token: str | None, reason: str) -> QueryUnsupported:
    return QueryUnsupported(
        unsupported=(UnsupportedItem(code=code, token=token, reason=reason),)
    )


def _chinese_int(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if "十" in token:
        head, _, tail = token.partition("十")
        if (head and head not in _CN_DIGITS) or (tail and tail not in _CN_DIGITS):
            return None
        tens = _CN_DIGITS[head] if head else 1
        return tens * 10 + (_CN_DIGITS[tail] if tail else 0)
    total = 0
    for char in token:
        if char not in _CN_DIGITS:
            return None
        total = total * 10 + _CN_DIGITS[char]
    return total


def _is_boundary(text: str, index: int) -> bool:
    if index >= len(text):
        return True
    char = text[index]
    return char.isspace() or char in CONJUNCTION_CHARS


def _ratio_integer_digits(value: Decimal) -> int:
    """Count integer-part digits of a Decimal without context arithmetic.

    ``1E+1`` reports 2 (``10``), ``0.05`` reports 0; used by the parser's
    fast integer gate (review B1) so huge literals never reach ``quantize``.
    """

    sign, digits, exponent = value.as_tuple()
    del sign
    if exponent >= 0:
        return len(digits) + exponent
    return max(len(digits) + exponent, 0)


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _skip_separator(text: str, index: int) -> int:
    while index < len(text):
        char = text[index]
        if char.isspace() or char in CONJUNCTION_CHARS:
            index += 1
            continue
        break
    return index


def _consume_stopword(text: str, index: int) -> int:
    """Consume a stopword only at a clause boundary (never inside 股票代码)."""

    j = _skip_space(text, index)
    for word in _STOPWORDS_BY_LEN:
        if text.startswith(word, j) and _is_boundary(text, j + len(word)):
            return j + len(word)
    return index


def _first_boundary_phrase(run: str) -> int | None:
    """Earliest index of a frozen boundary phrase inside one element run.

    Returns ``None`` for clean runs (whitespace/punctuation already terminate
    the element regex, so only embedded field leaders / sort / limit markers /
    unspaced conjunctions can show up here).
    """

    best: int | None = None
    for word in _BOUNDARY_PHRASES:
        found = run.find(word)
        if found >= 0 and (best is None or found < best):
            best = found
    return best


def _stopword_only(value: str) -> bool:
    """True when ``value`` is empty or made solely of stopword fragments."""

    rest = value
    for fragment in sorted(_STOPWORD_FRAGMENTS, key=len, reverse=True):
        rest = rest.replace(fragment, "")
    return rest == ""


def _trim_stopword(value: str) -> str:
    # Longest-first so 「的股票」 is removed as a whole and never leaves 「的」
    # behind after a shorter 「股票」 trim (review m4).
    for word in _STOPWORDS_BY_LEN:
        if value.endswith(word) and len(value) > len(word):
            return value[: -len(word)]
    return value


def _clause_marker_allowed(text: str, start: int) -> bool:
    """Whether a sort/limit marker at ``start`` begins a real clause.

    Spec 3.3 residue rule (review MINOR-1): the marker (按/依/前/最多/限制)
    may only follow sentence start, whitespace, a conjunction character, or a
    stopword tail (anchor 「…的股票前20」). A marker glued to a value token
    (「A前1」「A按涨幅」) is not masked and stays an orphan residue.
    """

    if start == 0:
        return True
    previous = text[start - 1]
    if previous.isspace() or previous in CONJUNCTION_CHARS:
        return True
    return any(text[:start].endswith(word) for word in STOPWORDS)


def _mask(text: str, start: int, end: int) -> str:
    return text[:start] + (" " * (end - start)) + text[end:]


# ---------------------------------------------------------------------------
# Explanation (deterministic, derived from the AST only)
# ---------------------------------------------------------------------------


def _format_percent(value: Decimal) -> str:
    scaled = (value * Decimal(100)).quantize(Decimal("0.0001"))
    text = format(scaled, "f").rstrip("0").rstrip(".")
    return f"{text}%"


def _render_value(field: Field, value: object) -> str:
    if isinstance(value, tuple):
        # IN lists render with the Chinese enumeration comma for every field
        # (never Python tuple repr, review m6).
        if field is Field.ROLE:
            return "、".join(_ROLE_TO_ZH.get(item, item) for item in value)
        return "、".join(str(item) for item in value)
    if field is Field.RETURN_RATIO:
        return _format_percent(value)  # type: ignore[arg-type]
    if field is Field.BOARDS:
        return str(value)
    if field is Field.ROLE and isinstance(value, str):
        return _ROLE_TO_ZH.get(value, value)
    return str(value)


def explain_ast(ast: QueryAst) -> str:
    """Deterministic Chinese preview.

    Slice 1 deliberately emits no snapshot/version line; the slice-2
    repository appends it via :func:`with_snapshot_line` after
    re-authorization (it alone knows trade_date/revision).
    """

    conditions = " 且 ".join(
        f"{FIELD_LABELS[predicate.field]}{OP_LABELS[predicate.op]}"
        f"{_render_value(predicate.field, predicate.value)}"
        for predicate in ast.predicates
    )
    segments = [conditions]
    if ast.sort is not None:
        direction = "升序" if ast.sort.direction is SortDirection.ASC else "降序"
        segments.append(f"按{FIELD_LABELS[ast.sort.field]}{direction}排列")
    segments.append(f"最多返回 {ast.limit} 条")
    segments.append(_BASIS_LINE)
    return "；".join(segments) + "。"


def with_snapshot_line(
    explanation: str, *, trade_date: date | str, revision: int
) -> str:
    """Append the frozen snapshot line (pure deterministic string helper).

    Caller (slice 2 repository): pass the authorized publication's
    ``trade_date`` and payload ``revision`` only after re-authorization;
    never fabricate either value. Runtime guards keep the slice-2 call site
    honest: ``revision`` must be a non-negative ``int`` (``bool`` rejected),
    ``trade_date`` must be a ``date`` or a non-blank ``str``.
    """

    if isinstance(trade_date, date):
        day = trade_date.isoformat()
    elif isinstance(trade_date, str) and trade_date.strip():
        day = trade_date
    else:
        raise ValueError("trade_date must be a date or a non-empty string")
    # bool is an int subclass: reject it before the int check on purpose.
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError("revision must be an int (bool rejected)")
    if revision < 0:
        raise ValueError("revision must be non-negative")
    return f"{explanation}快照日期：{day}，快照版本：v{revision}。"


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class _Scanner:
    def __init__(self, text: str, role_names: Collection[str]) -> None:
        self.text = text
        self.allowed_roles = frozenset(role_names)

    def run(self) -> ParseOutcome:
        outcome = self._scan_forbidden()
        if outcome is not None:
            return outcome

        work, sort_spec, outcome = self._extract_sort(self.text)
        if outcome is not None:
            return outcome
        work, limit, outcome = self._extract_limit(work)
        if outcome is not None:
            return outcome

        predicates, outcome = self._parse_predicates(work)
        if outcome is not None:
            return outcome
        if not predicates:
            return _fail(
                UnsupportedCode.ORPHAN_TOKEN,
                self.text.strip()[:12],
                "没有可识别的查询条件",
            )
        if len(predicates) > _PREDICATE_LIMIT:
            return _fail(
                UnsupportedCode.ORPHAN_TOKEN,
                None,
                f"最多支持 {_PREDICATE_LIMIT} 个合取条件",
            )

        if limit is None:
            limit = 50
        elif not 1 <= limit <= 100:
            return _fail(
                UnsupportedCode.UNSUPPORTED_VALUE,
                str(limit),
                "上限必须在 1..100 之间",
            )
        sort_spec = sort_spec or self._default_sort(predicates)
        try:
            ast = QueryAst(
                version=AST_VERSION,
                predicates=tuple(predicates),
                sort=sort_spec,
                limit=limit,
            )
        except ValidationError as error:
            item = error.errors()[0]
            location = ".".join(str(part) for part in item["loc"])
            return _fail(
                UnsupportedCode.UNSUPPORTED_VALUE,
                None,
                f"{location}: {item['msg']}",
            )
        return QueryParsed(
            ast=ast,
            ast_hash=ast_hash_of(ast),
            explanation=explain_ast(ast),
        )

    # -- forbidden surfaces -------------------------------------------------

    def _scan_forbidden(self) -> QueryUnsupported | None:
        text = self.text
        if (match := _PARENS_RE.search(text)) is not None:
            return _fail(
                UnsupportedCode.UNSUPPORTED_LOGIC, match.group(), "不支持括号嵌套"
            )
        if (match := _OR_RE.search(text)) is not None:
            # Conservative global pre-scan (review n3, spec D3): OR markers are
            # rejected even glued inside value runs such as
            # 「代码属于 A或者B」 —— v1 lexes no quoted strings and cannot tell
            # a value character from a logic operator. Pinned by tests.
            return _fail(
                UnsupportedCode.UNSUPPORTED_LOGIC, match.group(), "不支持 OR 逻辑"
            )
        if (match := _NEGATION_RE.search(text)) is not None:
            # Same n3 trade-off as above: 非 inside a sector value
            # (「题材为 非主流」) stays conservatively unsupported.
            return _fail(
                UnsupportedCode.UNSUPPORTED_LOGIC,
                match.group(),
                "不支持否定逻辑（不低于/不高于是登记比较词，不在此列）",
            )
        if (match := _ARITH_RE.search(text)) is not None:
            return _fail(
                UnsupportedCode.UNSUPPORTED_EXPRESSION,
                match.group(),
                "不支持算术或模板表达式",
            )
        if (match := _ORPHAN_PUNCT_RE.search(text)) is not None:
            return _fail(
                UnsupportedCode.ORPHAN_TOKEN, match.group(), "不可消费的标点残词"
            )
        for pattern in _TIME_RES:
            if (match := pattern.search(text)) is not None:
                return _fail(
                    UnsupportedCode.UNSUPPORTED_EXPRESSION,
                    match.group(),
                    "v1 时间窗口固定为 20 个交易日，不支持时间窗口表达",
                )
        if (match := _MOOD_RE.search(text)) is not None:
            return _fail(
                UnsupportedCode.UNSUPPORTED_EXPRESSION,
                match.group(),
                "不支持祈使/提问语气",
            )
        return None

    # -- sort / limit extraction -------------------------------------------

    def _extract_sort(
        self, text: str
    ) -> tuple[str, SortSpec | None, QueryUnsupported | None]:
        # A regex match glued to a preceding value token (「A按涨幅」) is not a
        # sort clause: such markers stay in the text and surface as orphans.
        matches = [
            match
            for match in _SORT_RE.finditer(text)
            if _clause_marker_allowed(text, match.start())
        ]
        if len(matches) > 1:
            return (
                text,
                None,
                _fail(
                    UnsupportedCode.ORPHAN_TOKEN, matches[1].group(), "重复的排序子句"
                ),
            )
        sort_spec: SortSpec | None = None
        work = text
        if matches:
            match = matches[0]
            field = FIELD_WORDS[match.group("field")]
            direction_word = match.group("dir")
            if direction_word is None:
                if field in _DIRECTIONLESS_SORT_FIELDS:
                    # Spec 3.3 line 132 only defaults numeric fields to desc
                    # and ID fields to asc; role/sector have no frozen default
                    # direction, so a directionless clause is an incomplete
                    # residue clause and fails closed (round-3 NIT-B: this is
                    # not an out-of-range *value*, hence orphan_token).
                    return (
                        work,
                        None,
                        _fail(
                            UnsupportedCode.ORPHAN_TOKEN,
                            match.group("field"),
                            "按角色/题材排序必须显式指定升序或降序",
                        ),
                    )
                direction = (
                    SortDirection.DESC
                    if field in _NUMERIC_FIELDS
                    else SortDirection.ASC
                )
            else:
                direction = DIRECTION_WORDS[direction_word]
            sort_spec = SortSpec(field=field, direction=direction)
            work = _mask(text, match.start(), match.end())
        # A leftover clause-initial 按/依 means the purported sort field is not
        # whitelisted. Markers glued to a value are skipped here on purpose:
        # they remain residue and are classified by the predicate scanner.
        for marker in _MARKER_RE.finditer(work):
            if not _clause_marker_allowed(work, marker.start()):
                continue
            head_index = _skip_space(work, marker.end())
            head_match = re.match(r"[\u4e00-\u9fffA-Za-z0-9_]{1,12}", work[head_index:])
            token = (
                head_match.group() if head_match else work[head_index : head_index + 4]
            )
            return (
                work,
                None,
                _fail(UnsupportedCode.UNKNOWN_FIELD, token, "排序字段不在白名单内"),
            )
        return work, sort_spec, None

    def _extract_limit(
        self, text: str
    ) -> tuple[str, int | None, QueryUnsupported | None]:
        # Glued markers such as 「A前1」 are not limit clauses (review MINOR-1):
        # they stay in the text and are reported as orphan residue.
        matches = [
            match
            for match in _LIMIT_RE.finditer(text)
            if _clause_marker_allowed(text, match.start())
        ]
        if not matches:
            return text, None, None
        if len(matches) > 1:
            return (
                text,
                None,
                _fail(
                    UnsupportedCode.ORPHAN_TOKEN, matches[1].group(), "重复的上限子句"
                ),
            )
        match = matches[0]
        return _mask(text, match.start(), match.end()), int(match.group(1)), None

    # -- predicates ----------------------------------------------------------

    def _parse_predicates(
        self, work: str
    ) -> tuple[list[Predicate], QueryUnsupported | None]:
        predicates: list[Predicate] = []
        index = _skip_separator(work, 0)
        while index < len(work):
            leading = _BOARDS_LEADING_RE.match(work, index)
            if leading is not None:
                # The D3 conjunction boundary after the match is enforced by
                # _after_predicate for every predicate uniformly; glued residue
                # such as 「2连板以上角色是中军」 is orphaned there with the tail
                # as token, instead of dropping to unknown_field classification.
                number = _chinese_int(leading.group("num"))
                if number is None:
                    # Reachable via Chinese numeral runs containing 百/千/万，
                    # e.g. 「百连板」「一百连板」 (regex admits 百； conversion
                    # rejects): fail-closed unsupported_value.
                    return [], _fail(
                        UnsupportedCode.UNSUPPORTED_VALUE,
                        leading.group("num"),
                        "板数必须是 0..99 的整数",
                    )
                suffix = leading.group("suffix")
                op = BOARD_SUFFIX_OPS[suffix] if suffix else Op.EQ
                predicate, outcome = self._make_predicate(Field.BOARDS, op, number)
                if outcome is not None:
                    return [], outcome
                predicates.append(predicate)
                index, outcome = self._after_predicate(work, leading.end())
                if outcome is not None:
                    return [], outcome
                continue

            field_match = self._match_field(work, index)
            if field_match is not None:
                field, end = field_match
                predicate, new_index, outcome = self._parse_field_predicate(
                    work, field, end
                )
                if outcome is not None:
                    return [], outcome
                predicates.append(predicate)
                index, outcome = self._after_predicate(work, new_index)
                if outcome is not None:
                    return [], outcome
                continue

            return [], self._classify_unknown(work, index)
        return predicates, None

    def _after_predicate(
        self, work: str, index: int
    ) -> tuple[int, QueryUnsupported | None]:
        """Advance past one consumed predicate and enforce the D3 boundary.

        Another clause may follow only via whitespace or one of the frozen
        conjunction chars 且/并/和/,/、, optionally after a boundary-followed
        stopword tail (「的股票」). Anything glued directly to the consumed
        predicate (「涨幅大于5%角色是中军」, 「板数为3角色是中军」) is
        fail-closed orphan_token whose token is the *tail* residue, never the
        consumed head and never unknown_field (spec D3, round-3 MINOR).
        """
        stopped = _consume_stopword(work, index)
        if stopped == index and index < len(work):
            char = work[index]
            if not (char.isspace() or char in CONJUNCTION_CHARS):
                run = _UNKNOWN_RUN_RE.match(work, index)
                token = run.group() if run is not None else work[index : index + 8]
                return index, _fail(
                    UnsupportedCode.ORPHAN_TOKEN,
                    token,
                    "相邻谓词之间必须有合取连接词（且/并/和/逗号/顿号/空白）",
                )
        return _skip_separator(work, stopped), None

    def _match_field(self, work: str, index: int) -> tuple[Field, int] | None:
        for word, field in _FIELD_ITEMS_ORDERED:
            if work.startswith(word, index):
                return field, index + len(word)
        return None

    def _parse_field_predicate(
        self, work: str, field: Field, end: int
    ) -> tuple[Predicate | None, int, QueryUnsupported | None]:
        index = _skip_space(work, end)
        if (match := _IN_ZHONG_RE.match(work, index)) is not None:
            value, outcome = self._read_in_body(field, match.group("body"))
            if outcome is not None:
                return None, index, outcome
            return self._finish_predicate(field, Op.IN, value, match.end())
        if (match := _IN_RENYI_RE.match(work, index)) is not None:
            value, outcome = self._read_in_body(field, match.group("body"))
            if outcome is not None:
                return None, index, outcome
            return self._finish_predicate(field, Op.IN, value, match.end())
        if work.startswith(IN_WORD, index):
            index = _skip_space(work, index + len(IN_WORD))
            value, new_index, outcome = self._read_list(field, work, index)
            if outcome is not None:
                return None, index, outcome
            return self._finish_predicate(field, Op.IN, value, new_index)

        match = _OP_RE.match(work, index)
        if match is None:
            run_match = re.match(rf"[^{_SEPARATOR_CLASS}\d%]{{1,8}}", work[index:])
            token = run_match.group() if run_match else work[index : index + 4]
            return (
                None,
                index,
                _fail(
                    UnsupportedCode.UNKNOWN_OPERATOR,
                    token,
                    f"{FIELD_LABELS[field]}后缺少已登记的操作词",
                ),
            )
        op = self._op_from_word(match.group())
        index = _skip_space(work, match.end())
        value, new_index, outcome = self._read_scalar(field, work, index)
        if outcome is not None:
            return None, index, outcome
        return self._finish_predicate(field, op, value, new_index)

    def _finish_predicate(
        self, field: Field, op: Op, value: object, end: int
    ) -> tuple[Predicate | None, int, QueryUnsupported | None]:
        predicate, outcome = self._make_predicate(field, op, value)
        if outcome is not None:
            return None, end, outcome
        return predicate, end, None

    @staticmethod
    def _op_from_word(word: str) -> Op:
        if word in GTE_WORDS:
            return Op.GTE
        if word in LTE_WORDS:
            return Op.LTE
        if word in GT_WORDS:
            return Op.GT
        if word in LT_WORDS:
            return Op.LT
        return Op.EQ

    def _make_predicate(
        self, field: Field, op: Op, value: object
    ) -> tuple[Predicate | None, QueryUnsupported | None]:
        try:
            return Predicate(field=field, op=op, value=value), None
        except ValidationError:
            # IN-list failures render their members with the Chinese
            # enumeration comma, never Python tuple repr (review n7/m6).
            if isinstance(value, (tuple, list)):
                token = "、".join(str(item) for item in value)[:20]
            else:
                token = str(value)[:20]
            return None, _fail(
                UnsupportedCode.UNSUPPORTED_VALUE,
                token,
                f"{FIELD_LABELS[field]}的值或操作组合不合法",
            )

    # -- value readers -------------------------------------------------------

    def _read_scalar(
        self, field: Field, work: str, index: int
    ) -> tuple[object, int, QueryUnsupported | None]:
        if field is Field.RETURN_RATIO:
            return self._read_ratio(work, index)
        if field is Field.BOARDS:
            return self._read_boards(work, index)
        if field is Field.ROLE:
            return self._read_role_element(work, index)
        if field is Field.SECURITY_ID:
            match = _SECURITY_ELEMENT_RE.match(work, index)
            if match is None:
                return (
                    None,
                    index,
                    _fail(
                        UnsupportedCode.UNSUPPORTED_VALUE,
                        work[index : index + 8],
                        "证券代码格式不合法",
                    ),
                )
            return match.group(), match.end(), None
        match = _SECTOR_ELEMENT_RE.match(work, index)
        if match is None:
            return (
                None,
                index,
                _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    work[index : index + 8],
                    "题材 ID 不能为空",
                ),
            )
        value, outcome = self._read_sector_run(match.group())
        if outcome is not None:
            return None, index, outcome
        return value, match.end(), None

    def _read_sector_run(self, run: str) -> tuple[str | None, QueryUnsupported | None]:
        """Validate one whitespace-free sector element run.

        A run embedding a frozen boundary phrase (field leader, sort/limit
        marker, unspaced conjunction) is ambiguous residue -> orphan_token
        (review M1/D3, fail-closed). After stopword trimming the value must
        not be empty or made solely of stopword fragments (review m4).
        """

        if _first_boundary_phrase(run) is not None:
            return None, _fail(
                UnsupportedCode.ORPHAN_TOKEN,
                run[:20],
                "题材值中混入字段引导词、排序/上限引导或合取词，存在残词",
            )
        value = _trim_stopword(run)
        # Trailing genitive particle 的 (round-3 MINOR): after longest-first
        # stopword trimming, one final 的 is stripped when a non-empty core
        # survives (「AI的」->「AI」, 「好的」->「好」). If the core is empty
        # or pure stopword fragments (「的」「股票的」) the value fails
        # closed. Only the final particle is considered; an interior 的 is
        # preserved (「主线的科技」).
        if value.endswith("的") and len(value) > 1:
            core = value[:-1]
            if _stopword_only(core):
                return None, _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    run[:20],
                    "裁剪尾「的」与停用词后题材 ID 为空",
                )
            value = core
        if _stopword_only(value):
            return None, _fail(
                UnsupportedCode.UNSUPPORTED_VALUE,
                run[:20],
                "裁剪停用词后题材 ID 为空",
            )
        return value, None

    def _read_ratio(
        self, work: str, index: int
    ) -> tuple[Decimal, int, QueryUnsupported | None]:
        match = _RATIO_RE.match(work, index)
        if match is None:
            return (
                Decimal("0"),
                index,
                _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    work[index : index + 12],
                    "涨幅必须是小数或百分数",
                ),
            )
        raw = Decimal(match.group())
        end = match.end()
        if (percent := _PERCENT_RE.match(work, end)) is not None:
            end = percent.end()
            # Exact exponent shift, NOT raw / 100: division rounds the
            # coefficient under the default context precision (28) and would
            # silently rewrite over-long thresholds (0.999...(29)% -> 0.01).
            # scaleb only adjusts the exponent (round-3 BLOCKER).
            raw = raw.scaleb(-2, context=_EXACT_SHIFT_CONTEXT)
        # Fast integer-digit gate judged AFTER percent conversion (review B1):
        # the [-1, 10] domain has at most two integer digits, so anything
        # larger fails without ever reaching Decimal.quantize (which escapes
        # decimal.InvalidOperation under the default context precision for
        # 23+ digit integers). -100% -> -1 and 1000% -> 10 stay valid.
        if _ratio_integer_digits(raw) > 2:
            return (
                Decimal("0"),
                index,
                _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    match.group(),
                    "涨幅必须在 -1..10（即 -100%..1000%）之间",
                ),
            )
        if not raw.is_finite() or not is_return_ratio_precision_supported(raw):
            return (
                Decimal("0"),
                index,
                _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    match.group(),
                    "涨幅超过 6 位小数精度或非有限值",
                ),
            )
        quantized = quantize_return_ratio(raw)
        if quantized == 0:
            # Collapse -0 / -0.000000 onto the canonical positive zero token
            # so equal thresholds share one AST, hash and explanation (M3).
            quantized = Decimal("0.000000")
        if not Decimal(-1) <= quantized <= Decimal(10):
            return (
                quantized,
                index,
                _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    match.group(),
                    "涨幅必须在 -1..10（即 -100%..1000%）之间",
                ),
            )
        return quantized, end, None

    def _read_boards(
        self, work: str, index: int
    ) -> tuple[int, int, QueryUnsupported | None]:
        match = _BOARDS_NUMBER_RE.match(work, index)
        if match is None:
            return (
                0,
                index,
                _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    work[index : index + 8],
                    "板数必须是 0..99 的整数",
                ),
            )
        token = match.group()
        trailing = _BOARDS_TRAILING_RE.match(work, match.end())
        end = trailing.end() if trailing is not None else match.end()
        if "." in token:
            return (
                0,
                index,
                _fail(UnsupportedCode.UNSUPPORTED_VALUE, token, "板数必须是整数"),
            )
        number = _chinese_int(token)
        if number is None or not 0 <= number <= 99:
            return (
                0,
                index,
                _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    token,
                    "板数必须在 0..99 之间",
                ),
            )
        return number, end, None

    def _read_role_element(
        self, work: str, index: int
    ) -> tuple[str, int, QueryUnsupported | None]:
        for word, value in _ROLE_ITEMS_ORDERED:
            if work.startswith(word, index):
                if value not in self.allowed_roles:
                    return (
                        "",
                        index,
                        _fail(
                            UnsupportedCode.UNSUPPORTED_VALUE,
                            word,
                            "该角色不在允许的枚举内",
                        ),
                    )
                return value, index + len(word), None
        match = _ROLE_EN_RE.match(work, index)
        if match is not None:
            canonical = _ROLE_EN_CANONICAL.get(match.group().lower())
            if canonical is not None and canonical in self.allowed_roles:
                return canonical, match.end(), None
        return (
            "",
            index,
            _fail(
                UnsupportedCode.UNSUPPORTED_VALUE,
                work[index : index + 8],
                "角色必须属于六枚举之一",
            ),
        )

    def _read_element(
        self, field: Field, work: str, index: int
    ) -> tuple[object, int, QueryUnsupported | None]:
        if field is Field.ROLE:
            return self._read_role_element(work, index)
        if field is Field.BOARDS:
            return self._read_boards_element(work, index)
        if field is Field.SECURITY_ID:
            match = _SECURITY_ELEMENT_RE.match(work, index)
            if match is None:
                return (
                    None,
                    index,
                    _fail(
                        UnsupportedCode.UNSUPPORTED_VALUE,
                        work[index : index + 8],
                        "列表元素不合法",
                    ),
                )
            return match.group(), match.end(), None
        match = _SECTOR_ELEMENT_RE.match(work, index)
        if match is None:
            return (
                None,
                index,
                _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    work[index : index + 8],
                    "列表元素不合法",
                ),
            )
        # Every IN piece gets the same boundary/stopword validation as a
        # scalar sector run (review item 2, M1/D3): a piece embedding a field
        # leader, sort/limit marker or glued conjunction is orphan residue --
        # the following clause must never be swallowed as sector characters.
        value, outcome = self._read_sector_run(match.group())
        if outcome is not None:
            return None, index, outcome
        return value, match.end(), None

    @staticmethod
    def _boards_element_allowed_end(text: str, index: int) -> bool:
        """Whether a boards IN element may terminate at ``index``.

        Legal ends are the text end, whitespace, a list/conjunction
        separator (，,、且并和) or a frozen stopword tail; anything glued
        after the digits (「2.5」「2x」) is an illegal element.
        """

        if index >= len(text):
            return True
        char = text[index]
        if char.isspace() or char in CONJUNCTION_CHARS or char == "，":
            return True
        return _consume_stopword(text, index) != index

    def _read_boards_element(
        self, text: str, index: int
    ) -> tuple[int, int, QueryUnsupported | None]:
        match = _BOARDS_ELEMENT_RE.match(text, index)
        if match is None or not self._boards_element_allowed_end(text, match.end()):
            # Report the raw element piece (never Python tuple repr, review
            # n7/m6): non-digit or digits glued to residue such as 「2.5」.
            run = _UNKNOWN_RUN_RE.match(text, index)
            token = run.group() if run is not None else text[index : index + 4]
            return (
                0,
                index,
                _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    token,
                    "板数集合元素必须是 0..99 的整数",
                ),
            )
        number = int(match.group())
        if not 0 <= number <= 99:
            return (
                0,
                index,
                _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    match.group(),
                    "板数集合元素必须在 0..99 之间",
                ),
            )
        return number, match.end(), None

    def _read_list(
        self, field: Field, work: str, index: int
    ) -> tuple[tuple[object, ...], int, QueryUnsupported | None]:
        value, next_index, outcome = self._read_element(field, work, index)
        if outcome is not None:
            return (), index, outcome
        values = [value]
        index = next_index
        while (separator := _LIST_SEP_RE.match(work, index)) is not None:
            value, next_index, outcome = self._read_element(
                field, work, separator.end()
            )
            if outcome is not None:
                return (), index, outcome
            values.append(value)
            index = next_index
        return tuple(values), index, None

    def _read_in_body(
        self, field: Field, body: str
    ) -> tuple[tuple[object, ...], QueryUnsupported | None]:
        pieces = [piece.strip() for piece in _LIST_SEP_RE.split(body.strip())]
        pieces = [piece for piece in pieces if piece]
        if not pieces:
            return (), _fail(UnsupportedCode.UNSUPPORTED_VALUE, None, "集合操作缺少值")
        values: list[object] = []
        for piece in pieces:
            element, end, outcome = self._read_element(field, piece, 0)
            if outcome is not None:
                return (), outcome
            if end != len(piece):
                return (), _fail(
                    UnsupportedCode.UNSUPPORTED_VALUE,
                    piece[:20],
                    "集合元素形态不合法",
                )
            values.append(element)
        return tuple(values), None

    # -- unknown token classification ----------------------------------------

    def _classify_unknown(self, work: str, index: int) -> QueryUnsupported:
        probe = work[index : index + 24]
        op_match = _OP_RE.search(probe)
        if op_match is not None:
            head = probe[: op_match.start()].strip()
            if head:
                return _fail(
                    UnsupportedCode.UNKNOWN_FIELD, head[:12], "字段不在白名单内"
                )
        run = _UNKNOWN_RUN_RE.match(work, index)
        token = run.group() if run else work[index : index + 8]
        return _fail(UnsupportedCode.ORPHAN_TOKEN, token, "存在无法消费的残词")

    # -- default sort --------------------------------------------------------

    @staticmethod
    def _default_sort(predicates: list[Predicate]) -> SortSpec:
        fields = {predicate.field for predicate in predicates}
        if Field.RETURN_RATIO in fields:
            return SortSpec(field=Field.RETURN_RATIO, direction=SortDirection.DESC)
        if Field.BOARDS in fields:
            return SortSpec(field=Field.BOARDS, direction=SortDirection.DESC)
        return SortSpec(field=Field.SECURITY_ID, direction=SortDirection.ASC)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_query(
    text: str,
    *,
    role_names: Collection[str] | None = None,
) -> ParseOutcome:
    """Parse whitelist Chinese text into a typed AST. Pure and total.

    ``ValueError`` marks request-shape failures (non-str / empty / >500
    chars) that HTTP routes normalize to 400. A non-empty blank string is a
    normal ``empty_input`` business outcome, not ``ValueError``.
    """

    if not isinstance(text, str) or len(text) == 0:
        raise ValueError("query text must be a non-empty string")
    if len(text) > _TEXT_LIMIT:
        raise ValueError(f"query text must be at most {_TEXT_LIMIT} characters")
    # NFKC is intentionally the only normalization: zero-width characters
    # such as U+200B are NFKC-stable and are deliberately NOT stripped here,
    # so a clause glued through a zero-width space
    # （「涨幅大于5%​角色是中军」） stays fail-closed (round-3 pin, Backlog).
    normalized = unicodedata.normalize("NFKC", text)
    if not normalized.strip():
        return _fail(UnsupportedCode.EMPTY_INPUT, None, "输入在 NFKC 归一后仅含空白")
    allowed = ROLE_EXTERNAL_VALUES if role_names is None else frozenset(role_names)
    return _Scanner(normalized, allowed).run()


__all__ = [
    "PARSER_VERSION",
    "FIELD_WORDS",
    "EQ_WORDS",
    "GT_WORDS",
    "GTE_WORDS",
    "LT_WORDS",
    "LTE_WORDS",
    "BOARD_SUFFIX_OPS",
    "CONJUNCTION_CHARS",
    "STOPWORDS",
    "DIRECTION_WORDS",
    "ROLE_WORDS",
    "ParseOutcome",
    "QueryParsed",
    "QueryUnsupported",
    "UnsupportedItem",
    "explain_ast",
    "parse_query",
    "with_snapshot_line",
]
