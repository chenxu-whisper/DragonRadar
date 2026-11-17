"""Task 13 publication-selection regressions kept outside pre-existing dirty files."""

from datetime import date


class _Rows:
    def mappings(self):
        return self

    def all(self):
        return []


class _Connection:
    def __init__(self):
        self.statements = []

    def scalar(self, _statement):
        return 7

    def execute(self, statement):
        self.statements.append(statement)
        return _Rows()


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def begin(self):
        return _Transaction(self.connection)


def test_publication_list_snapshots_applies_through_date_inside_candidate_query(
    monkeypatch,
):
    from dragon_radar.publication.repository import PublicationRepository

    connection = _Connection()
    database = type("Database", (), {"engine": _Engine(connection)})()
    repository = PublicationRepository(database)
    monkeypatch.setattr(repository.market, "_lock", lambda *_args: None)
    monkeypatch.setattr(repository, "_authorized_records", lambda *_args, **_kwargs: [])
    through_date = date(2026, 9, 8)

    assert repository.list_snapshots(through_date=through_date, limit=1) == []

    assert len(connection.statements) == 1
    compiled = connection.statements[0].compile()
    assert through_date in compiled.params.values()
    sql = str(compiled)
    assert "publications.trade_date <=" in sql
    assert "publications.publication_sequence <=" in sql
