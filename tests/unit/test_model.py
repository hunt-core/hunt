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
