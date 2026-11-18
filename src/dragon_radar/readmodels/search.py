"""Bounded search over validated publication archives, never current inputs."""

from pydantic import ValidationError

from dragon_radar.http.errors import DataUnavailable
from dragon_radar.http.routes.news import _list_article
from dragon_radar.http.routes.news import _payload as news_payload
from dragon_radar.readmodels.topic_details import (
    _Basis,
    _TopicMetadata,
    parse_archived_topic_details,
)


def _topics(payload, snapshot):
    results = [item for item in snapshot.results if item.domain == "topic"]
    if "topic_details" not in payload or not results:
        return None
    if len(results) != 1:
        raise ValueError("Ambiguous topic metadata")
    result = results[0]
    publication = snapshot.publication
    metadata = _TopicMetadata.model_validate(result.payload)
    basis = _Basis.model_validate(
        {
            **payload["sector_flow"]["topic_basis"],
            "market_snapshot_id": publication.market_snapshot_id,
            "topic_snapshot_id": publication.topic_snapshot_id,
            "taxonomy_version": metadata.taxonomy_version,
            "membership_version": metadata.membership_version,
        }
    )
    if (
        result.status != "ready"
        or result.publication_id != publication.id
        or result.input_id != publication.topic_snapshot_id
        or metadata.snapshot_id != publication.topic_snapshot_id
        or metadata.source_version != basis.source_version
    ):
        raise ValueError("Topic metadata binding mismatch")
    details = parse_archived_topic_details(payload, snapshot)
    # Validate descriptors even for empty member lists or an empty sector map.
    archived = payload["sector_flow"]["topic_basis"]
    for field in ("taxonomy_version", "membership_version"):
        if field in archived and archived[field] != getattr(metadata, field):
            raise ValueError("Topic metadata descriptor mismatch")
    return [
        {"sector_id": identity, "name": detail["sector"]["name"]}
        for identity, detail in sorted(details.items())
    ]


def _messages(snapshot):
    results = [item for item in snapshot.results if item.domain == "news"]
    if not results:
        return None
    if len(results) != 1:
        raise ValueError("Ambiguous news archive")
    result = results[0]
    if (
        result.publication_id != snapshot.id
        or result.input_id != snapshot.publication.news_input_id
    ):
        raise ValueError("News publication binding mismatch")
    # Non-ready publication results legitimately have no archived coverage.
    if result.status != "ready":
        return None
    payload, _coverage, _reason = news_payload(snapshot)
    if payload is None:
        return None
    articles = payload["articles"]
    identities = [item["article_id"] for item in articles]
    if len(identities) != len(set(identities)):
        raise ValueError("Ambiguous article identities")
    return [
        _list_article(item)
        for item in sorted(articles, key=lambda item: item["article_id"])
    ]


def _securities(payload):
    if "search" not in payload:
        return None
    search = payload["search"]
    if not isinstance(search, dict):
        raise ValueError("Invalid archived search")
    if "securities" not in search:
        return None
    securities = search["securities"]
    if not isinstance(securities, list):
        raise ValueError("Invalid archived securities")
    seen = set()
    for item in securities:
        if (
            not isinstance(item, dict)
            or set(item) != {"security_id", "code", "name", "exchange"}
            or not isinstance(item["security_id"], str)
            or not item["security_id"].strip()
            or item["security_id"] in seen
            or any(
                item[field] is not None and not isinstance(item[field], str)
                for field in ("code", "name")
            )
            or not isinstance(item["exchange"], str)
            or not item["exchange"]
        ):
            raise ValueError("Invalid archived security identity")
        seen.add(item["security_id"])
    return sorted(securities, key=lambda item: item["security_id"])


def archived_search(payload, snapshot, term, page, size):
    """Validate every present group before matching or slicing a combined page."""
    try:
        results = [item for item in snapshot.results if item.domain == "market"]
        if len(results) > 1:
            raise ValueError("Ambiguous market archive")
        if results:
            stored = results[0]
            if (
                stored.publication_id != snapshot.id
                or stored.input_id != snapshot.publication.market_snapshot_id
                or snapshot.market_snapshot.id != stored.input_id
            ):
                raise ValueError("Market publication binding mismatch")
        originals = {
            "securities": _securities(payload),
            "topics": _topics(payload, snapshot),
            "messages": _messages(snapshot),
        }
    except (ValueError, KeyError, TypeError, AttributeError, ValidationError):
        raise DataUnavailable from None
    fields = {
        "securities": ("security_id", "code", "name"),
        "topics": ("sector_id", "name"),
        "messages": ("article_id", "title"),
    }
    groups = {group: [] for group in originals}
    statuses = {}
    combined = []
    folded = term.casefold()
    for group, items in originals.items():
        if items is None:
            statuses[group] = {
                "status": "unavailable",
                "total": None,
                "reason": f"{group}_materialization_unavailable",
            }
            continue
        matches = []
        for item in items:
            field = next(
                (
                    field
                    for field in fields[group]
                    if item[field] is not None and folded in item[field].casefold()
                ),
                None,
            )
            if field is not None:
                matches.append({**item, "match_field": field})
        statuses[group] = {"status": "ready", "total": len(matches), "reason": None}
        combined.extend((group, item) for item in matches)
    for group, item in combined[(page - 1) * size : page * size]:
        groups[group].append(item)
    return {
        "query": term,
        "groups": groups,
        "group_status": statuses,
        "total": len(combined),
        "page": page,
        "size": size,
    }
