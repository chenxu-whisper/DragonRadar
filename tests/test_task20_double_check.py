"""Narrow Task20 regressions for cache concurrency and evidence contracts."""

import runpy
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier, RLock
from types import SimpleNamespace
from uuid import uuid4

from dragon_radar.publication import repository as publication_module
from dragon_radar.publication.repository import PublicationRepository

BUNDLE_VERIFIER = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts" / "verify_task20_bundle.py")
)
validate_bundle = BUNDLE_VERIFIER["validate_bundle"]


class _Mappings:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


def test_market_archive_cache_hit_refreshes_lru_without_sql():
    target = uuid4()
    repository = object.__new__(PublicationRepository)
    repository._market_batch_cache_lock = RLock()
    repository._market_archive_cache = OrderedDict([(target, {"id": target})])
    for _ in range(2):
        other_id = uuid4()
        repository._market_archive_cache[other_id] = {"id": other_id}

    class Connection:
        def execute(self, _statement):
            raise AssertionError("a cache hit must not execute SQL")

    assert repository._market_archives(Connection(), {target}) == {
        target: {"id": target}
    }
    assert next(reversed(repository._market_archive_cache)) == target


def test_snapshot_cache_never_pairs_stale_results_with_new_signature(monkeypatch):
    publication_id = uuid4()
    record = SimpleNamespace(
        id=publication_id,
        topic_snapshot_id=None,
        news_input_id=None,
        regulation_input_id=None,
        role_input_id=None,
    )
    stale = SimpleNamespace(id=publication_id, publication=record, results=("old",))
    repository = object.__new__(PublicationRepository)
    repository._market_batch_cache_lock = RLock()
    repository._result_rows_cache = OrderedDict([(publication_id, ({"value": "new"},))])
    repository._result_signatures = OrderedDict([(publication_id, (("market", "2"),))])
    repository._published_snapshot_cache = OrderedDict()
    repository._published_snapshot_signatures = OrderedDict()
    monkeypatch.setattr(repository, "_result", lambda row: row["value"])

    repository._cache_snapshot(stale)

    assert publication_id not in repository._published_snapshot_cache
    assert publication_id not in repository._published_snapshot_signatures


def test_cached_snapshot_concurrent_eviction_becomes_cache_miss(monkeypatch):
    publication_id = uuid4()
    record = SimpleNamespace(
        id=publication_id,
        topic_snapshot_id=None,
        news_input_id=None,
        regulation_input_id=None,
        role_input_id=None,
        market_snapshot_id=uuid4(),
    )
    market = SimpleNamespace(
        id=record.market_snapshot_id,
        batch_id=uuid4(),
        batch=SimpleNamespace(source_id="source", grant=object()),
    )
    cached = SimpleNamespace(
        id=publication_id, publication=record, market_snapshot=market
    )
    repository = object.__new__(PublicationRepository)
    repository._market_batch_cache_lock = RLock()
    repository._published_snapshot_cache = OrderedDict([(publication_id, cached)])
    repository._published_snapshot_signatures = OrderedDict(
        [(publication_id, (("market", "1"),))]
    )
    repository._result_rows_cache = OrderedDict()
    repository._result_signatures = OrderedDict()
    repository.access_policy = SimpleNamespace(allows=lambda *_args, **_kwargs: True)

    rendezvous = Barrier(2)
    dependency = {
        "source_id": "source",
        "archive_type": "market_import_batch",
        "archive_id": market.batch_id,
        "derived_type": "market",
        "derived_id": publication_id,
        "publication_id": publication_id,
        "current_grant": {},
    }

    class Connection:
        def execute(self, _statement):
            rendezvous.wait(timeout=5)
            rendezvous.wait(timeout=5)
            return _Mappings([dependency])

    monkeypatch.setattr(
        publication_module.SourceGrant,
        "model_validate",
        lambda _value: object(),
    )
    monkeypatch.setattr(
        publication_module,
        "source_has_active_withdrawal",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        publication_module,
        "archive_was_withdrawn",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        repository,
        "_result_rows",
        lambda *_args: (_ for _ in ()).throw(AssertionError("eviction must miss")),
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            repository._cached_market_only_snapshot,
            Connection(),
            record,
        )
        rendezvous.wait(timeout=5)
        for _ in range(32):
            other_id = uuid4()
            other_record = deepcopy(record)
            other_record.id = other_id
            other_snapshot = SimpleNamespace(
                id=other_id,
                publication=other_record,
                market_snapshot=market,
                results=(),
            )
            repository._result_rows_cache[other_id] = ()
            repository._result_signatures[other_id] = (("market", "1"),)
            repository._cache_snapshot(other_snapshot)
        rendezvous.wait(timeout=5)

        assert future.result(timeout=5) is None
        assert publication_id not in repository._published_snapshot_cache
        assert publication_id not in repository._published_snapshot_signatures


def test_evidence_bundle_requires_relative_tracked_content_hashes(tmp_path):
    bundle = tmp_path / "docs" / "acceptance" / "evidence" / "task20"
    bundle.mkdir(parents=True)
    artifact = bundle / "focused-output.txt"
    artifact.write_bytes(b"12 passed\n")
    manifest = bundle / "manifest.json"
    manifest.write_text(
        """
{
  "schema": "dragon-radar.task20-committed-evidence",
  "schema_version": 1,
  "implementation_commit": "1111111111111111111111111111111111111111",
  "execution_provenance": "Executed from a clean checkout of implementation_commit.",
  "files": [
    {
      "path": "focused-output.txt",
      "sha256": "d367a494a1f42c203ab4f4e02edb39df73e9ce42729a079f1dd3200e341b42a5",
      "bytes": 10
    }
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    tracked = {
        "docs/acceptance/evidence/task20/manifest.json",
        "docs/acceptance/evidence/task20/focused-output.txt",
    }

    assert validate_bundle(tmp_path, manifest, tracked) == []

    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "focused-output.txt",
            "../untracked.txt",
        ),
        encoding="utf-8",
    )
    errors = validate_bundle(tmp_path, manifest, tracked)
    assert any("relative" in error.lower() for error in errors)

    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "../untracked.txt",
            "untracked.txt",
        ),
        encoding="utf-8",
    )
    errors = validate_bundle(tmp_path, manifest, tracked)
    assert any("tracked" in error.lower() for error in errors)
