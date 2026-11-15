"""Regression tests for the database audit findings: Model, relations, factory.

DB-03, DB-05, DB-06, DB-07, DB-08, DB-14, DB-L5 and Model.update().
"""

from __future__ import annotations

import contextvars
import importlib
import json

import pytest

from hunt.database import debug
from hunt.database.factory import Factory
from hunt.database.model import MassAssignmentError, Model
from hunt.database.schema.builder import Schema
from hunt.database.soft_deletes import SoftDeletes


@pytest.fixture
def db(monkeypatch):
    """A private in-memory SQLite database as the default connection."""
    conn_mod = importlib.import_module("hunt.database.connection")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_CONNECTION", "sqlite")
    monkeypatch.setenv("DB_DATABASE", ":memory:")
    monkeypatch.setattr(conn_mod, "_config", {})
    monkeypatch.setattr(conn_mod, "_connections", {})
    engine = conn_mod.connection()
    Schema.create(
        "m_users",
        lambda bp: (
            bp.id(),
            bp.string("name"),
            bp.string("password").nullable(),
            bp.boolean("is_admin").default(False),
            bp.integer("tenant_id").nullable(),
            bp.integer("credits").default(0),
            bp.text("meta").nullable(),
            bp.timestamps(),
        ),
    )
    Schema.create(
        "m_posts",
        lambda bp: (bp.id(), bp.integer("user_id").nullable(), bp.string("title"), bp.timestamps(), bp.soft_deletes()),
    )
    Schema.create("m_codes", lambda bp: (bp.string("code", 32).primary_key(), bp.string("label").nullable()))
    Schema.create(
        "m_items",
        lambda bp: (bp.id(), bp.string("code_ref", 32).nullable(), bp.string("name")),
    )
    Schema.create(
        "m_tags", lambda bp: (bp.id(), bp.string("name"), bp.boolean("public").default(True), bp.soft_deletes())
    )
    Schema.create("m_post_tag", lambda bp: (bp.integer("m_post_id"), bp.integer("m_tag_id")))
    yield engine
    engine.dispose()


class MUser(Model):
    table = "m_users"
    fillable = ["name", "password", "tenant_id", "credits", "meta"]
    hidden = ["password"]
    casts = {"meta": "json", "is_admin": "bool"}


class MPost(SoftDeletes, Model):
    table = "m_posts"
    fillable = ["title", "user_id"]

    def tags(self):
        return self.belongs_to_many(MTag, "m_post_tag", "m_post_id", "m_tag_id")


class MTag(SoftDeletes, Model):
    table = "m_tags"
    timestamps = False
    fillable = ["name", "public"]


class MCode(Model):
    table = "m_codes"
    primary_key = "code"
    timestamps = False
    fillable = ["label"]


class MItem(Model):
    table = "m_items"
    timestamps = False
    fillable = ["code_ref", "name"]

    def code(self):
        return self.belongs_to(MCode, "code_ref")


# ---------------------------------------------------------------------------
# DB-03: mass assignment
# ---------------------------------------------------------------------------


class TestMassAssignment:
    def test_default_model_is_totally_guarded(self, db):
        class Bare(Model):
            table = "m_users"

        assert Bare.guarded == ["*"]
        with pytest.raises(MassAssignmentError):
            Bare.create(name="x", is_admin=True)
        with pytest.raises(MassAssignmentError):
            Bare().fill({"name": "x"})
        with pytest.raises(MassAssignmentError):
            Bare({"name": "x"})
        assert Bare.query().count() == 0

    def test_empty_fillable_list_blocks_everything(self):
        class Stub(Model):
            table = "m_users"
            fillable: list = []

        with pytest.raises(MassAssignmentError):
            Stub().fill({"anything": 1})
        # Nothing to assign is not an error.
        assert Stub().fill({})._attributes == {}

    def test_explicit_fillable_wins_and_drops_other_keys(self, db):
        user = MUser.create(name="a", is_admin=True, id=99)
        assert user.name == "a"
        assert "is_admin" not in user._attributes
        assert user.id != 99
        assert MUser.find(user.id).is_admin is False

    def test_guarded_list_allows_the_rest(self):
        class G(Model):
            table = "m_users"
            guarded = ["is_admin"]

        m = G().fill({"name": "a", "is_admin": True, "tenant_id": 3})
        assert m._attributes == {"name": "a", "tenant_id": 3}

    def test_empty_guarded_allows_all_but_the_primary_key(self):
        class Open(Model):
            table = "m_users"
            guarded: list = []

        m = Open().fill({"name": "a", "is_admin": True, "id": 7})
        assert m._attributes == {"name": "a", "is_admin": True}

    def test_primary_key_is_fillable_only_when_listed_explicitly(self):
        # Listing the key in `fillable` is a deliberate opt-in, for models
        # whose key is supplied by the application (a code, a UUID).
        class Listed(Model):
            table = "m_users"
            fillable = ["id", "name"]

        assert Listed().fill({"id": 5, "name": "a"})._attributes == {"id": 5, "name": "a"}

        # Unguarding everything does not open the key up.
        class CustomKey(Model):
            table = "m_codes"
            primary_key = "code"
            guarded: list = []

        assert CustomKey().fill({"code": "X", "label": "l"})._attributes == {"label": "l"}

    def test_overriding_guarded_no_longer_exposes_the_primary_key(self):
        class G(Model):
            table = "m_users"
            guarded = ["created_at"]

        assert "id" not in G().fill({"id": 1, "name": "a"})._attributes

    def test_force_fill_and_attribute_assignment_are_unguarded(self, db):
        class Bare(Model):
            table = "m_users"

        m = Bare().force_fill({"name": "a", "is_admin": True})
        m.tenant_id = 4
        m.save()
        row = Bare.find(m.id)
        assert (row.name, bool(row.is_admin), row.tenant_id) == ("a", True, 4)

    def test_update_or_create_and_first_or_create_respect_guards(self, db):
        user, created = MUser.first_or_create({"name": "a"}, {"is_admin": True})
        assert created and MUser.find(user.id).is_admin is False
        updated = MUser.update_or_create({"name": "a"}, {"is_admin": True, "credits": 5})
        fresh = MUser.find(updated.id)
        assert fresh.credits == 5 and fresh.is_admin is False

    def test_create_many_respects_guards(self, db):
        MUser.create_many([{"name": "a", "is_admin": True}, {"name": "b", "is_admin": True}])
        assert [bool(u.is_admin) for u in MUser.all().get()] == [False, False]


class TestFactory:
    def test_factory_create_stores_guarded_attributes(self, db):
        class UserFactory(Factory):
            model = MUser

            def definition(self):
                return {"name": "f", "is_admin": True, "password": "pw"}

        user = UserFactory().create({"tenant_id": 2})
        row = MUser.find(user.id)
        assert row.is_admin is True and row.tenant_id == 2 and row.created_at is not None

    def test_factory_works_for_a_totally_guarded_model(self, db):
        class Bare(Model):
            table = "m_users"

        class BareFactory(Factory):
            model = Bare

            def definition(self):
                return {"name": "f"}

        assert Bare.find(BareFactory().create().id).name == "f"

    def test_make_output_is_a_usable_model(self):
        class UserFactory(Factory):
            model = MUser

            def definition(self):
                return {"name": "f", "password": "pw"}

        made = UserFactory().make()
        assert made.to_dict() == {"name": "f"}
        assert made._exists is False and made._original == {} and made._relations == {}
        assert "pw" not in repr(made)


# ---------------------------------------------------------------------------
# Model.update (cross-area API)
# ---------------------------------------------------------------------------


class TestModelUpdate:
    def test_update_force_fills_and_saves(self, db):
        user = MUser.create(name="a")
        assert user.update({"is_admin": True}, name="b") is True
        row = MUser.find(user.id)
        assert row.name == "b" and row.is_admin is True

    def test_update_with_no_arguments_just_saves(self, db):
        user = MUser.create(name="a")
        assert user.update() is True

    def test_update_returns_false_when_the_row_is_gone(self, db):
        user = MUser.create(name="a")
        MUser.where("id", user.id).delete()
        assert user.update(name="b") is False


# ---------------------------------------------------------------------------
# DB-05: async_* keeps contextvars
# ---------------------------------------------------------------------------

_current_tenant: contextvars.ContextVar[int | None] = contextvars.ContextVar("test_tenant", default=None)


class TenantUser(Model):
    table = "m_users"
    fillable = ["name", "tenant_id"]

    @classmethod
    def boot(cls):
        def scope(q):
            tenant = _current_tenant.get()
            return q.where("tenant_id", tenant) if tenant is not None else q

        cls.add_global_scope("tenant", scope)


class TestAsyncContext:
    async def test_contextvar_scope_applies_in_async_get(self, db):
        for tenant in (1, 1, 2, 2, 2):
            MUser.create(name=f"t{tenant}", tenant_id=tenant)
        token = _current_tenant.set(1)
        try:
            assert len(TenantUser.all().get()) == 2
            assert len(await TenantUser.all().async_get()) == 2
            assert await TenantUser.all().async_count() == 2
        finally:
            _current_tenant.reset(token)
        assert len(await TenantUser.all().async_get()) == 5

    async def test_async_queries_reach_the_query_log(self, db):
        debug.reset_query_tracker()
        await MUser.all().async_get()
        await MUser.async_find(1)
        assert len(debug.get_query_log()) == 2

    async def test_async_writes_join_the_ambient_transaction(self, db):
        from hunt.database.connection import begin

        with pytest.raises(RuntimeError):
            with begin():
                await MUser.async_create(name="rolled back")
                assert await MUser.where("name", "rolled back").async_count() == 1
                raise RuntimeError("abort")
        assert MUser.where("name", "rolled back").count() == 0


# ---------------------------------------------------------------------------
# DB-06: scope inheritance, boot through the MRO, increment/decrement
# ---------------------------------------------------------------------------


class TestGlobalScopeInheritance:
    def test_subclass_inherits_global_scopes(self, db):
        class Base(Model):
            table = "m_users"
            fillable = ["name", "tenant_id"]

            @classmethod
            def boot(cls):
                cls.add_global_scope("tenant", lambda q: q.where("tenant_id", 1))

        class Child(Base):
            table = "m_users"

        MUser.create(name="a", tenant_id=1)
        MUser.create(name="b", tenant_id=2)
        assert "tenant" in Child._global_scopes
        assert [u.name for u in Child.all().get()] == ["a"]
        # The dicts are separate: a scope added to the child stays on the child.
        Child.add_global_scope("extra", lambda q: q)
        assert "extra" not in Base._global_scopes

    def test_scope_added_after_class_creation_is_inherited_by_later_subclasses(self):
        class Base(Model):
            table = "m_users"

        Base.add_global_scope("late", lambda q: q.where("tenant_id", 1))

        class Child(Base):
            table = "m_users"

        assert "late" in Child._global_scopes

    def test_boot_defined_on_a_plain_mixin_runs_for_each_model(self, db):
        class TenantScoped:
            @classmethod
            def boot(cls):
                cls.add_global_scope("tenant", lambda q: q.where("tenant_id", 1))

        class A(TenantScoped, Model):
            table = "m_users"

        class B(A):
            table = "m_users"

        assert "tenant" in A._global_scopes and "tenant" in B._global_scopes

    def test_increment_and_decrement_apply_global_scopes(self, db):
        a = MUser.create(name="a", tenant_id=1, credits=10)
        b = MUser.create(name="b", tenant_id=2, credits=10)
        token = _current_tenant.set(1)
        try:
            assert TenantUser.query().increment("credits", 5) == 1
            assert TenantUser.query().decrement("credits", 1) == 1
        finally:
            _current_tenant.reset(token)
        assert MUser.find(a.id).credits == 14
        assert MUser.find(b.id).credits == 10

    def test_parent_listeners_fire_for_subclasses_once(self, db):
        calls: list[str] = []

        class Observer:
            def created(self, model):
                calls.append(f"observer:{type(model).__name__}")

        class Base(Model):
            table = "m_users"
            fillable = ["name"]

            @classmethod
            def boot(cls):
                cls.observe(Observer())

        class Child(Base):
            table = "m_users"

        Base._on("created", lambda m: calls.append(f"late:{type(m).__name__}"))
        Child.create(name="c")
        # The boot-registered observer fires once (not once per class in the
        # MRO); the listener added to the parent afterwards also fires.
        assert sorted(calls) == ["late:Child", "observer:Child"]


# ---------------------------------------------------------------------------
# DB-07: trashed lifecycle
# ---------------------------------------------------------------------------


class TestSoftDeleteLifecycle:
    def test_trashed_instance_can_be_saved(self, db):
        post = MPost.create(title="a")
        post.delete()
        trashed = MPost.query().with_trashed().find(post.id)
        trashed.title = "edited"
        assert trashed.save() is True
        assert MPost.query().with_trashed().find(post.id).title == "edited"

    def test_trashed_instance_can_be_restored_and_force_deleted(self, db):
        post = MPost.create(title="a")
        assert post.delete() is True
        assert MPost.find(post.id) is None
        trashed = MPost.query().only_trashed().first()
        assert trashed.restore() is True
        assert MPost.find(post.id) is not None

        again = MPost.find(post.id)
        again.delete()
        gone = MPost.query().with_trashed().find(post.id)
        assert gone.force_delete() is True
        assert MPost.query().with_trashed().find(post.id) is None
        assert gone._exists is False

    def test_increment_on_trashed_instance(self, db):
        Schema.table("m_posts", lambda bp: bp.integer("views").default(0))
        post = MPost.create(title="a")
        post.delete()
        trashed = MPost.query().with_trashed().find(post.id)
        assert trashed.increment("views", 3) is True
        assert MPost.query().with_trashed().find(post.id).views == 3

    def test_save_reports_a_vanished_row(self, db):
        post = MPost.create(title="a")
        MPost.where("id", post.id).force_delete()
        post.title = "b"
        assert post.save() is False
        assert post.delete() is False

    def test_builder_delete_soft_deletes(self, db):
        MPost.create(title="a")
        MPost.create(title="b")
        assert MPost.where("title", "a").delete() == 1
        assert MPost.query().with_trashed().count() == 2
        assert [p.title for p in MPost.all().get()] == ["b"]
        assert MPost.query().only_trashed().first().deleted_at is not None

    def test_builder_force_delete_and_restore(self, db):
        MPost.create(title="a")
        MPost.create(title="b")
        MPost.where("title", "a").delete()
        assert MPost.query().restore() == 1
        assert MPost.query().count() == 2
        assert MPost.where("title", "a").force_delete() == 1
        assert MPost.query().with_trashed().count() == 1

    def test_builder_delete_on_plain_model_is_a_real_delete(self, db):
        MUser.create(name="a")
        assert MUser.where("name", "a").delete() == 1
        assert MUser.query().count() == 0

    def test_instance_operations_ignore_global_scopes(self, db):
        user = MUser.create(name="a", tenant_id=2)
        token = _current_tenant.set(1)
        try:
            loaded = TenantUser.without_global_scopes().find(user.id)
            loaded.name = "renamed"
            assert loaded.save() is True
        finally:
            _current_tenant.reset(token)
        assert MUser.find(user.id).name == "renamed"


# ---------------------------------------------------------------------------
# DB-08: primary keys
# ---------------------------------------------------------------------------


class TestPrimaryKey:
    def test_find_uses_the_models_primary_key(self, db):
        MCode.query().insert({"code": "X1", "label": "first"})
        assert MCode.find("X1").label == "first"
        assert MCode.find("nope") is None
        assert MCode.query().find("X1").label == "first"

    def test_supplied_string_key_survives_insert(self, db):
        code = MCode().force_fill({"code": "X1", "label": "l"})
        code.save()
        assert code.code == "X1"
        assert code._original["code"] == "X1"
        code.label = "changed"
        assert code.save() is True
        assert MCode.find("X1").label == "changed"

    def test_generated_key_is_still_assigned(self, db):
        user = MUser.create(name="a")
        assert isinstance(user.id, int) and user.id > 0

    def test_changing_the_key_updates_the_old_row(self, db):
        code = MCode().force_fill({"code": "OLD"})
        code.save()
        code.code = "NEW"
        assert code.save() is True
        assert MCode.find("OLD") is None and MCode.find("NEW") is not None

    def test_belongs_to_defaults_to_the_related_primary_key(self, db):
        MCode.query().insert({"code": "X1", "label": "first"})
        item = MItem.create(code_ref="X1", name="i")
        assert item.code().label == "first"
        loaded = MItem.with_("code").get()
        assert loaded[0]._relations["code"].label == "first"

    def test_delete_with_string_key(self, db):
        code = MCode().force_fill({"code": "X1"})
        code.save()
        assert code.delete() is True
        assert MCode.find("X1") is None


# ---------------------------------------------------------------------------
# DB-14: BelongsToMany honours the related model's scopes and constraints
# ---------------------------------------------------------------------------


class TestBelongsToMany:
    def _seed(self):
        post = MPost.create(title="p")
        other = MPost.create(title="other")
        live = MTag.create(name="live")
        hidden = MTag.create(name="hidden", public=False)
        trashed = MTag.create(name="trashed")
        for tag in (live, hidden, trashed):
            _relation(post).attach(tag.id)
        _relation(other).attach(live.id)
        trashed.delete()
        return post, other

    def test_lazy_load_hides_soft_deleted_related_rows(self, db):
        post, _ = self._seed()
        assert sorted(t.name for t in post.tags()) == ["hidden", "live"]

    def test_eager_load_hides_soft_deleted_related_rows(self, db):
        self._seed()
        posts = MPost.with_("tags").order_by("id").get()
        assert sorted(t.name for t in posts[0]._relations["tags"]) == ["hidden", "live"]
        assert [t.name for t in posts[1]._relations["tags"]] == ["live"]

    def test_eager_load_applies_the_constraint(self, db):
        self._seed()
        posts = MPost.with_({"tags": lambda q: q.where("public", True)}).order_by("id").get()
        assert [t.name for t in posts[0]._relations["tags"]] == ["live"]

    def test_constraint_with_or_where_cannot_reach_other_parents(self, db):
        post, _other = self._seed()
        posts = (
            MPost.where("id", post.id).with_({"tags": lambda q: q.where("name", "nope").or_where("name", "live")}).get()
        )
        assert [t.name for t in posts[0]._relations["tags"]] == ["live"]

    def test_pivot_column_is_not_left_on_the_related_model(self, db):
        self._seed()
        posts = MPost.with_("tags").get()
        tag = posts[0]._relations["tags"][0]
        assert not any(k.startswith("_pivot_") for k in tag._attributes)
        assert tag.is_dirty() is False

    def test_related_global_scope_applies(self, db):
        class PublicTag(SoftDeletes, Model):
            table = "m_tags"
            timestamps = False

            @classmethod
            def boot(cls):
                cls.add_global_scope("public", lambda q: q.where("public", True))

        class ScopedPost(SoftDeletes, Model):
            table = "m_posts"

            def tags(self):
                return self.belongs_to_many(PublicTag, "m_post_tag", "m_post_id", "m_tag_id")

        post, _ = self._seed()
        assert [t.name for t in ScopedPost.find(post.id).tags()] == ["live"]

    def test_sync_is_atomic(self, db):
        post, _ = self._seed()
        rel = _relation(post)
        before = sorted(t.name for t in post.tags())

        def boom(related_id, pivot_data=None):
            raise RuntimeError("attach failed")

        rel.attach = boom
        with pytest.raises(RuntimeError):
            rel.sync([1])
        assert sorted(t.name for t in post.tags()) == before


def _relation(post):
    from hunt.database.relations.belongs_to_many import BelongsToMany

    return BelongsToMany(MTag, post, "m_post_tag", "m_post_id", "m_tag_id")


# ---------------------------------------------------------------------------
# DB-L5: casts, repr, accessors, class-attribute-named columns
# ---------------------------------------------------------------------------


class TestCastsAndSerialization:
    def test_json_cast_is_serialised_on_insert_and_update(self, db):
        user = MUser.create(name="a", meta={"theme": "dark"})
        raw = MUser.query().select("meta").where("id", user.id).get()[0]._attributes["meta"]
        assert json.loads(raw) == {"theme": "dark"}
        loaded = MUser.find(user.id)
        assert loaded.meta == {"theme": "dark"}
        loaded.meta = {"theme": "light", "n": [1, 2]}
        assert loaded.save() is True
        assert MUser.find(user.id).meta == {"theme": "light", "n": [1, 2]}

    def test_unchanged_json_is_not_dirty(self, db):
        user = MUser.create(name="a", meta={"a": 1})
        loaded = MUser.find(user.id)
        assert loaded.is_dirty("meta") is False
        loaded.meta = {"a": 1}
        assert loaded.is_dirty("meta") is False

    def test_json_cast_through_create_many(self, db):
        MUser.create_many([{"name": "a", "meta": {"k": 1}}])
        assert MUser.where("name", "a").first().meta == {"k": 1}

    def test_to_dict_applies_casts(self, db):
        user = MUser.create(name="a", meta={"k": 1})
        data = MUser.find(user.id).to_dict()
        assert data["meta"] == {"k": 1}
        assert data["is_admin"] is False
        assert "password" not in data

    def test_repr_omits_hidden_attributes(self, db):
        user = MUser.create(name="a", password="s3cret-hash")
        assert "s3cret-hash" not in repr(user)
        assert "password" not in repr(MUser.find(user.id))
        assert "'name': 'a'" in repr(user)

    def test_accessors_and_mutators_are_inherited(self):
        class Base(Model):
            table = "m_users"
            appends = ["shout"]

            def get_shout_attribute(self):
                return str(self._attributes.get("name", "")).upper()

            def set_name_attribute(self, value):
                self._attributes["name"] = str(value).strip()

        class Child(Base):
            table = "m_users"

        child = Child()
        child.name = "  bob  "
        assert child._attributes["name"] == "bob"
        assert child.shout == "BOB"
        assert child.to_dict()["shout"] == "BOB"

    def test_column_named_like_a_class_attribute_is_stored(self, db):
        Schema.create("m_shadow", lambda bp: (bp.id(), bp.string("appends").nullable(), bp.string("hidden").nullable()))

        class Shadow(Model):
            table = "m_shadow"
            timestamps = False

        row_id = Shadow.query().insert({"appends": "t1", "hidden": "h1"})
        row = Shadow.find(row_id)
        row.hidden = "h2"
        assert row._attributes["hidden"] == "h2"
        assert Shadow.hidden == []
        assert row.save() is True
        fresh = Shadow.find(row_id)
        assert fresh.get_attribute("hidden") == "h2"
        assert fresh.get_attribute("appends") == "t1"
        fresh.set_attribute("appends", "t2").save()
        assert Shadow.find(row_id).get_attribute("appends") == "t2"
