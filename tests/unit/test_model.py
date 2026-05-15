import os
import pytest

os.environ["DB_CONNECTION"] = "sqlite"
os.environ["DB_DATABASE"] = ":memory:"

from hunt.database.schema.builder import Schema
from hunt.database.schema.migration import Migration
from hunt.database.model import Model


class CreateUsersTable(Migration):
    def up(self):
        Schema.create("users", lambda bp: [
            bp.id(),
            bp.string("name"),
            bp.string("email"),
            bp.timestamps(),
        ])

    def down(self):
        Schema.drop_if_exists("users")


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    CreateUsersTable().up()
    yield
    CreateUsersTable().down()


class User(Model):
    table = "users"
    fillable = ["name", "email"]


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
