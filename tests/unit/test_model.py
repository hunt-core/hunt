import os
from typing import ClassVar

import pytest

os.environ["DB_CONNECTION"] = "sqlite"
os.environ["DB_DATABASE"] = ":memory:"

from hunt.database.model import Model
from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration


class CreateUsersTable(Migration):
    def up(self):
        Schema.create(
            "users",
            lambda bp: [
                bp.id(),
                bp.string("name"),
                bp.string("email"),
                bp.timestamps(),
            ],
        )

    def down(self):
        Schema.drop_if_exists("users")


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    CreateUsersTable().up()
    yield
    CreateUsersTable().down()


class User(Model):
    table = "users"
    fillable: ClassVar[list[str]] = ["name", "email"]


def test_create_and_find():
    user = User.create({"name": "Alice", "email": "alice@example.com"})
    assert user._exists is True
    assert user._attributes["id"] is not None

    found = User.find(user._attributes["id"])
    assert found is not None
    assert found._attributes["name"] == "Alice"


def test_update():
    user = User.create({"name": "Bob", "email": "bob@example.com"})
    user._attributes["name"] = "Bobby"
    user.save()

    found = User.find(user._attributes["id"])
    assert found._attributes["name"] == "Bobby"


def test_delete():
    user = User.create({"name": "Charlie", "email": "charlie@example.com"})
    uid = user._attributes["id"]
    user.delete()
    assert User.find(uid) is None


def test_where_query():
    User.create({"name": "Dave", "email": "dave@example.com"})
    results = User.where("name", "Dave").get()
    assert len(results) >= 1
    assert all(u._attributes["name"] == "Dave" for u in results)


def test_to_dict():
    user = User({"name": "Eve", "email": "eve@example.com"})
    d = user.to_dict()
    assert d["name"] == "Eve"
    assert d["email"] == "eve@example.com"


# ------------------------------------------------------------------
# Async ORM — same operations via run_in_executor wrappers
# ------------------------------------------------------------------


async def test_async_create_and_find():
    user = await User.async_create({"name": "Async Alice", "email": "aasync@example.com"})
    assert user._exists is True
    uid = user._attributes["id"]

    found = await User.async_find(uid)
    assert found is not None
    assert found._attributes["name"] == "Async Alice"


async def test_async_save_update():
    user = await User.async_create({"name": "Async Bob", "email": "basync@example.com"})
    user._attributes["name"] = "Async Bobby"
    await user.async_save()

    found = await User.async_find(user._attributes["id"])
    assert found._attributes["name"] == "Async Bobby"


async def test_async_delete():
    user = await User.async_create({"name": "Async Charlie", "email": "casync@example.com"})
    uid = user._attributes["id"]
    await user.async_delete()

    found = await User.async_find(uid)
    assert found is None


async def test_async_query_get():
    await User.async_create({"name": "Async Dave", "email": "dasync@example.com"})
    results = await User.query().where("name", "Async Dave").async_get()
    assert len(results) >= 1
    assert all(u._attributes["name"] == "Async Dave" for u in results)


async def test_async_count_and_exists():
    name = "Async Counter"
    await User.async_create({"name": name, "email": "counter@example.com"})
    count = await User.query().where("name", name).async_count()
    assert count >= 1
    exists = await User.query().where("name", name).async_exists()
    assert exists is True


async def test_async_first():
    await User.async_create({"name": "Async First", "email": "first@example.com"})
    result = await User.query().where("name", "Async First").async_first()
    assert result is not None
    assert result._attributes["name"] == "Async First"


async def test_async_update_query():
    user = await User.async_create({"name": "Async Updater", "email": "upd@example.com"})
    uid = user._attributes["id"]
    await User.query().where("id", uid).async_update({"name": "Async Updated"})
    found = await User.async_find(uid)
    assert found._attributes["name"] == "Async Updated"


async def test_async_pluck():
    await User.async_create({"name": "Pluck Target", "email": "pluck@example.com"})
    names = await User.query().where("name", "Pluck Target").async_pluck("name")
    assert "Pluck Target" in names


# ------------------------------------------------------------------
# insert / insert_get_id / async_insert — RETURNING behaviour
# ------------------------------------------------------------------


def test_insert_get_id_returns_id():
    pk = User.query().insert_get_id({"name": "IGI", "email": "igi@example.com"})
    assert pk is not None
    assert isinstance(pk, int)


async def test_async_insert_accepts_returning_kwarg():
    # Before the fix this raised TypeError: async_insert() got an unexpected
    # keyword argument 'returning'.
    pk = await User.query().async_insert({"name": "AsyncRet", "email": "ar@example.com"}, returning="id")
    # SQLite is not PostgreSQL so returning is ignored and lastrowid is used.
    assert pk is not None


def test_insert_pg_returning(monkeypatch):
    """PostgreSQL dialect: RETURNING clause is appended and the pk comes from fetchone."""
    from unittest.mock import MagicMock

    import hunt.database.query_builder as qb_mod

    captured_sql: list[str] = []

    mock_result = MagicMock()
    mock_result.fetchone.return_value = (42,)

    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.dialect.name = "postgresql"
    mock_engine.connect.return_value = mock_conn

    def fake_timed_execute(conn, sql_text, bindings):
        captured_sql.append(str(sql_text))
        return mock_result

    monkeypatch.setattr(qb_mod, "connection", lambda name=None: mock_engine)
    monkeypatch.setattr("hunt.database.debug.timed_execute", fake_timed_execute)

    from hunt.database.query_builder import QueryBuilder

    pk = QueryBuilder("users").insert({"name": "PGUser"}, returning="id")

    assert pk == 42
    assert any("RETURNING" in sql for sql in captured_sql)


def test_insert_get_id_pg(monkeypatch):
    """insert_get_id uses RETURNING on PostgreSQL and returns the correct pk."""
    from unittest.mock import MagicMock

    import hunt.database.query_builder as qb_mod

    mock_result = MagicMock()
    mock_result.fetchone.return_value = (7,)

    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.dialect.name = "postgresql"
    mock_engine.connect.return_value = mock_conn

    monkeypatch.setattr(qb_mod, "connection", lambda name=None: mock_engine)
    monkeypatch.setattr("hunt.database.debug.timed_execute", lambda *a, **kw: mock_result)

    from hunt.database.query_builder import QueryBuilder

    pk = QueryBuilder("users").insert_get_id({"name": "PGUser2"})

    assert pk == 7
