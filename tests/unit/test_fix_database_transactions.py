"""Regression tests for the database audit findings: connections and transactions.

DB-04 (ambient transactions), DB-L10 (connection setup), DB-L12 (debug log
redaction), DB-L11 (pagination URLs) and TEST-03 (testing mixins).
"""

from __future__ import annotations

import asyncio
import importlib
import threading

import pytest
from sqlalchemy import text

from hunt.database import debug
from hunt.database.connection import begin, in_transaction, raw, transaction, use_connection
from hunt.database.model import Model
from hunt.database.query_builder import QueryBuilder
from hunt.database.schema.builder import Schema
from hunt.pagination import PaginationResult

conn_mod = importlib.import_module("hunt.database.connection")


def _reset(monkeypatch, database: str) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_CONNECTION", "sqlite")
    monkeypatch.setenv("DB_DATABASE", database)
    monkeypatch.setattr(conn_mod, "_config", {})
    monkeypatch.setattr(conn_mod, "_connections", {})


def _make_tables() -> None:
    Schema.create("tx_users", lambda bp: (bp.id(), bp.string("name")))
    Schema.create("tx_roles", lambda bp: (bp.id(), bp.string("name")))
    Schema.create("tx_role_user", lambda bp: (bp.integer("tx_user_id"), bp.integer("tx_role_id")))


@pytest.fixture
def db(monkeypatch):
    """In-memory SQLite (one shared DBAPI connection)."""
    _reset(monkeypatch, ":memory:")
    engine = conn_mod.connection()
    _make_tables()
    yield engine
    engine.dispose()


@pytest.fixture
def file_db(monkeypatch, tmp_path):
    """File SQLite with a real connection pool (a second writer would block)."""
    _reset(monkeypatch, str(tmp_path / "app.sqlite"))
    engine = conn_mod.connection()
    _make_tables()
    yield engine
    engine.dispose()


class TxUser(Model):
    table = "tx_users"
    timestamps = False
    fillable = ["name"]

    def roles(self):
        return self.belongs_to_many(TxRole, "tx_role_user", "tx_user_id", "tx_role_id")


class TxRole(Model):
    table = "tx_roles"
    timestamps = False
    fillable = ["name"]


def _count(engine, table: str = "tx_users") -> int:
    """Count rows on a brand-new connection (what another process would see)."""
    with engine.connect() as conn:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar())


# ---------------------------------------------------------------------------
# DB-04
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_name", ["db", "file_db"])
class TestAmbientTransactions:
    def test_rollback_undoes_orm_writes(self, fixture_name, request):
        engine = request.getfixturevalue(fixture_name)
        with pytest.raises(RuntimeError):
            with begin():
                TxUser.create(name="a")
                QueryBuilder("tx_users").insert({"name": "b"})
                QueryBuilder("tx_users").insert_many([{"name": "c"}])
                assert TxUser.query().count() == 3
                raise RuntimeError("abort")
        assert _count(engine) == 0

    def test_commit_persists_orm_writes(self, fixture_name, request):
        engine = request.getfixturevalue(fixture_name)
        with begin():
            user = TxUser.create(name="a")
            user.update(name="b")
            TxUser.create(name="gone").delete()
        assert _count(engine) == 1
        assert TxUser.query().first().name == "b"

    def test_transaction_callback_form(self, fixture_name, request):
        engine = request.getfixturevalue(fixture_name)

        def work(conn):
            TxUser.create(name="a")
            conn.execute(text("INSERT INTO tx_users (name) VALUES ('b')"))
            return "done"

        assert transaction(work) == "done"
        assert _count(engine) == 2

        def failing(conn):
            TxUser.create(name="c")
            raise ValueError("abort")

        with pytest.raises(ValueError):
            transaction(failing)
        assert _count(engine) == 2

    def test_nested_block_is_a_savepoint(self, fixture_name, request):
        engine = request.getfixturevalue(fixture_name)
        with begin():
            TxUser.create(name="outer")
            with pytest.raises(ValueError):
                with begin():
                    TxUser.create(name="inner")
                    raise ValueError("inner abort")
            with begin():
                TxUser.create(name="inner ok")
        names = sorted(u.name for u in TxUser.all().get())
        assert names == ["inner ok", "outer"]
        assert _count(engine) == 2

    def test_outer_rollback_undoes_committed_savepoint(self, fixture_name, request):
        engine = request.getfixturevalue(fixture_name)
        with pytest.raises(RuntimeError):
            with begin():
                with begin():
                    TxUser.create(name="inner")
                raise RuntimeError("outer abort")
        assert _count(engine) == 0

    def test_raw_and_schema_join_the_transaction(self, fixture_name, request):
        engine = request.getfixturevalue(fixture_name)
        with pytest.raises(RuntimeError):
            with begin():
                raw("INSERT INTO tx_users (name) VALUES (:n)", {"n": "a"})
                Schema.create("tx_temp", lambda bp: (bp.id(), bp.string("v")))
                assert Schema.has_table("tx_temp") is True
                raise RuntimeError("abort")
        assert _count(engine) == 0
        assert Schema.has_table("tx_temp") is False

    def test_belongs_to_many_attach_and_detach_join_the_transaction(self, fixture_name, request):
        engine = request.getfixturevalue(fixture_name)
        user = TxUser.create(name="a")
        role = TxRole.create(name="admin")
        from hunt.database.relations.belongs_to_many import BelongsToMany

        rel = BelongsToMany(TxRole, user, "tx_role_user", "tx_user_id", "tx_role_id")
        with pytest.raises(RuntimeError):
            with begin():
                rel.attach(role.id)
                assert len(user.roles()) == 1
                raise RuntimeError("abort")
        assert _count(engine, "tx_role_user") == 0
        rel.attach(role.id)
        with pytest.raises(RuntimeError):
            with begin():
                rel.detach()
                raise RuntimeError("abort")
        assert _count(engine, "tx_role_user") == 1

    def test_update_or_create_is_atomic_inside_a_transaction(self, fixture_name, request):
        engine = request.getfixturevalue(fixture_name)

        def boom(model):
            raise RuntimeError("listener failed")

        class Audited(Model):
            table = "tx_users"
            timestamps = False
            fillable = ["name"]

        Audited._on("created", boom)
        with pytest.raises(RuntimeError):
            with begin():
                Audited.update_or_create({"name": "a"}, {})
        assert _count(engine) == 0

    def test_in_transaction_flag(self, fixture_name, request):
        request.getfixturevalue(fixture_name)
        assert in_transaction() is False
        with begin():
            assert in_transaction() is True
        assert in_transaction() is False

    def test_connection_is_released(self, fixture_name, request):
        engine = request.getfixturevalue(fixture_name)
        if not hasattr(engine.pool, "checkedout"):
            pytest.skip("pool does not report checkouts")
        with pytest.raises(RuntimeError):
            with begin():
                TxUser.create(name="a")
                raise RuntimeError("abort")
        with begin():
            TxUser.create(name="b")
        TxUser.all().get()
        assert engine.pool.checkedout() == 0


class TestTransactionEdges:
    def test_file_sqlite_orm_write_inside_begin_does_not_lock(self, file_db):
        # Previously the ORM opened a second connection and waited 5 s for the
        # write lock held by begin(), then failed with "database is locked".
        with begin() as conn:
            conn.execute(text("INSERT INTO tx_users (name) VALUES ('a')"))
            TxUser.create(name="b")
            assert TxUser.query().count() == 2
        assert _count(file_db) == 2

    def test_uncommitted_writes_are_invisible_to_other_connections(self, file_db):
        with begin():
            TxUser.create(name="a")
            assert _count(file_db) == 0
        assert _count(file_db) == 1

    def test_connection_is_closed_when_commit_raises(self, file_db):
        ctx = begin()
        ctx.__enter__()
        TxUser.create(name="a")

        class Boom(Exception):
            pass

        def bad_commit():
            raise Boom

        real_tx = ctx._tx
        ctx._tx = type("Tx", (), {"commit": staticmethod(bad_commit), "rollback": real_tx.rollback})()
        with pytest.raises(Boom):
            ctx.__exit__(None, None, None)
        assert file_db.pool.checkedout() == 0
        assert in_transaction() is False

    def test_threads_do_not_share_a_transaction(self, file_db):
        seen: dict[str, object] = {}

        def other_thread():
            seen["in_tx"] = in_transaction()
            seen["count"] = TxUser.query().count()

        with begin():
            TxUser.create(name="a")
            t = threading.Thread(target=other_thread)
            t.start()
            t.join()
        assert seen == {"in_tx": False, "count": 0}

    async def test_asyncio_tasks_do_not_share_a_transaction(self, file_db):
        started = asyncio.Event()
        release = asyncio.Event()
        seen: dict[str, object] = {}

        async def writer():
            with pytest.raises(RuntimeError):
                with begin():
                    TxUser.create(name="a")
                    started.set()
                    await release.wait()
                    raise RuntimeError("abort")

        async def reader():
            await started.wait()
            seen["in_tx"] = in_transaction()
            seen["count"] = TxUser.query().count()
            release.set()

        await asyncio.gather(writer(), reader())
        assert seen == {"in_tx": False, "count": 0}
        assert _count(file_db) == 0

    def test_use_connection_commits_only_outside_a_transaction(self, file_db):
        with use_connection() as conn:
            conn.execute(text("INSERT INTO tx_users (name) VALUES ('a')"))
        assert _count(file_db) == 1
        with pytest.raises(RuntimeError):
            with begin():
                with use_connection() as conn:
                    conn.execute(text("INSERT INTO tx_users (name) VALUES ('b')"))
                raise RuntimeError("abort")
        assert _count(file_db) == 1

    def test_ddl_is_transactional_on_sqlite(self, file_db):
        with pytest.raises(RuntimeError):
            with begin():
                Schema.table("tx_users", lambda bp: bp.string("extra").nullable())
                assert Schema.has_column("tx_users", "extra") is True
                raise RuntimeError("abort")
        assert Schema.has_column("tx_users", "extra") is False


# ---------------------------------------------------------------------------
# DB-L10
# ---------------------------------------------------------------------------


class TestConnectionSetup:
    def test_database_url_only_applies_to_the_default_connection(self, monkeypatch, tmp_path):
        monkeypatch.setattr(conn_mod, "_connections", {})
        monkeypatch.setattr(
            conn_mod,
            "_config",
            {
                "default": "main",
                "connections": {
                    "main": {"driver": "sqlite", "database": str(tmp_path / "main.sqlite")},
                    "reports": {"driver": "sqlite", "database": str(tmp_path / "reports.sqlite")},
                },
            },
        )
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'from_url.sqlite'}")
        try:
            assert str(conn_mod.connection().url).endswith("from_url.sqlite")
            assert str(conn_mod.connection("main").url).endswith("from_url.sqlite")
            assert str(conn_mod.connection("reports").url).endswith("reports.sqlite")
        finally:
            for engine in conn_mod._connections.values():
                engine.dispose()

    def test_sqlite_enforces_foreign_keys(self, db):
        with db.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1

    def test_sqlite_file_and_url_engines_enforce_foreign_keys(self, monkeypatch, tmp_path):
        from sqlalchemy.exc import IntegrityError

        for engine in (
            conn_mod._sqlite_engine(str(tmp_path / "a.sqlite")),
            conn_mod._engine_from_config("x", {"url": f"sqlite:///{tmp_path / 'b.sqlite'}"}),
        ):
            try:
                with engine.connect() as conn:
                    assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
                    conn.execute(text("CREATE TABLE p (id INTEGER PRIMARY KEY)"))
                    conn.execute(text("CREATE TABLE c (id INTEGER PRIMARY KEY, p_id INTEGER REFERENCES p(id))"))
                    with pytest.raises(IntegrityError):
                        conn.execute(text("INSERT INTO c (p_id) VALUES (42)"))
            finally:
                engine.dispose()

    def test_server_engines_use_pool_pre_ping(self):
        assert conn_mod._pool_kwargs()["pool_pre_ping"] is True
        engine = conn_mod._server_engine("postgresql+psycopg2", "u", "p", "127.0.0.1", "5432", "d")
        try:
            assert engine.pool._pre_ping is True
        finally:
            engine.dispose()

    def test_engine_cache_builds_one_engine_under_concurrency(self, monkeypatch):
        monkeypatch.setattr(conn_mod, "_connections", {})
        built: list[object] = []
        real = conn_mod._make_engine
        gate = threading.Barrier(8)

        def slow_make(name):
            built.append(name)
            return real(name)

        monkeypatch.setattr(conn_mod, "_make_engine", slow_make)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(conn_mod, "_config", {})
        monkeypatch.setenv("DB_DATABASE", ":memory:")
        results: list[object] = []

        def worker():
            gate.wait()
            results.append(conn_mod.connection("sqlite"))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(built) == 1
        assert len({id(r) for r in results}) == 1
        results[0].dispose()


class TestCliBoot:
    def test_boot_imports_bootstrap_app(self, monkeypatch, tmp_path):
        import sys

        from hunt.console.commands.db.boot import boot_application

        (tmp_path / "bootstrap").mkdir()
        (tmp_path / "bootstrap" / "__init__.py").write_text("")
        (tmp_path / "bootstrap" / "app.py").write_text("BOOTED = True\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "path", list(sys.path))
        for name in ("bootstrap", "bootstrap.app"):
            monkeypatch.delitem(sys.modules, name, raising=False)
        try:
            assert boot_application() is True
            assert sys.modules["bootstrap.app"].BOOTED is True
        finally:
            sys.modules.pop("bootstrap.app", None)
            sys.modules.pop("bootstrap", None)

    def test_boot_falls_back_to_dotenv(self, monkeypatch, tmp_path):
        import os
        import sys

        from hunt.console.commands.db.boot import boot_application

        (tmp_path / ".env").write_text("HUNT_FIX_DB_BOOT_PROBE=from-dotenv\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "path", list(sys.path))
        monkeypatch.delenv("HUNT_FIX_DB_BOOT_PROBE", raising=False)
        try:
            assert boot_application() is False
            assert os.environ["HUNT_FIX_DB_BOOT_PROBE"] == "from-dotenv"
        finally:
            os.environ.pop("HUNT_FIX_DB_BOOT_PROBE", None)

    def test_migrate_warns_about_in_memory_database(self, monkeypatch, tmp_path):
        from click.testing import CliRunner

        from hunt.console.commands.migrate import migrate_group

        _reset(monkeypatch, ":memory:")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.path", list(__import__("sys").path))
        result = CliRunner().invoke(migrate_group, [])
        assert result.exit_code == 0
        assert "in-memory SQLite" in result.output
        assert "Nothing to migrate." in result.output
        conn_mod.connection().dispose()


# ---------------------------------------------------------------------------
# DB-L12
# ---------------------------------------------------------------------------


class TestDebugRedaction:
    def test_sensitive_columns_are_redacted(self):
        sql = "INSERT INTO users (name, password, api_token) VALUES (:_v0, :_v1, :_v2)"
        safe = debug.redact_bindings(sql, {"_v0": "alice", "_v1": "hunter2", "_v2": "tok"})
        assert safe == {"_v0": "alice", "_v1": "[REDACTED]", "_v2": "[REDACTED]"}

    def test_update_where_and_named_binds(self):
        sql = "UPDATE users SET password = :_set_0, name = :_set_1 WHERE remember_token = :w0 AND id = :w1"
        safe = debug.redact_bindings(sql, {"_set_0": "h", "_set_1": "bob", "w0": "t", "w1": 5})
        assert safe == {"_set_0": "[REDACTED]", "_set_1": "bob", "w0": "[REDACTED]", "w1": 5}
        assert debug.redact_bindings("SELECT 1 WHERE x = :secret", {"secret": "s"}) == {"secret": "[REDACTED]"}

    def test_bulk_insert_and_in_lists(self):
        sql = "INSERT INTO users (name, password) VALUES (:_r0_0, :_r0_1), (:_r1_0, :_r1_1)"
        safe = debug.redact_bindings(sql, {"_r0_0": "a", "_r0_1": "p1", "_r1_0": "b", "_r1_1": "p2"})
        assert safe == {"_r0_0": "a", "_r0_1": "[REDACTED]", "_r1_0": "b", "_r1_1": "[REDACTED]"}
        sql = "SELECT * FROM t WHERE token IN (:w0_0, :w0_1) AND id IN (:w1_0)"
        safe = debug.redact_bindings(sql, {"w0_0": "x", "w0_1": "y", "w1_0": 3})
        assert safe == {"w0_0": "[REDACTED]", "w0_1": "[REDACTED]", "w1_0": 3}

    def test_long_and_binary_values_are_shortened(self):
        safe = debug.redact_bindings("SELECT 1 WHERE a = :w0 AND b = :w1", {"w0": "x" * 500, "w1": b"\x00" * 10})
        assert len(safe["w0"]) < 250 and safe["w0"].endswith("...")
        assert safe["w1"] == "<10 bytes>"

    def test_debug_log_does_not_contain_the_secret(self, monkeypatch):
        logged: list[tuple[str, dict]] = []

        class FakeLog:
            @staticmethod
            def debug(message, **context):
                logged.append((message, context))

            @staticmethod
            def warning(message, **context):
                pass

        import hunt.log.manager as log_manager

        monkeypatch.setattr(log_manager, "Log", FakeLog)
        monkeypatch.setenv("APP_DEBUG", "true")
        debug.log_query("UPDATE users SET password = :_set_0 WHERE id = :w0", {"_set_0": "hunter2", "w0": 1}, 0.1)
        assert logged, "query was not logged"
        assert "hunter2" not in str(logged)
        assert "[REDACTED]" in logged[0][1]["bindings"]


# ---------------------------------------------------------------------------
# DB-L11 (pagination URLs)
# ---------------------------------------------------------------------------


class TestPaginationUrls:
    def test_repeated_query_parameters_are_preserved(self):
        page = PaginationResult(data=[], total=30, per_page=10, current_page=1, last_page=3)
        url = page.next_page_url("/posts?tag=a&tag=b&q=x&page=1")
        assert url == "/posts?tag=a&tag=b&q=x&page=2"

    def test_single_parameters_and_blank_values_still_work(self):
        page = PaginationResult(data=[], total=30, per_page=10, current_page=2, last_page=3)
        assert page.prev_page_url("/posts?q=&sort=name") == "/posts?q=&sort=name&page=1"
        assert page.next_page_url("/posts") == "/posts?page=3"


# ---------------------------------------------------------------------------
# TEST-03
# ---------------------------------------------------------------------------


class TestDatabaseTransactionsMixin:
    @pytest.mark.parametrize("fixture_name", ["db", "file_db"])
    def test_orm_writes_are_rolled_back(self, fixture_name, request):
        from hunt.testing.test_case import DatabaseTransactions

        engine = request.getfixturevalue(fixture_name)
        TxUser.create(name="before")
        case = DatabaseTransactions()
        case.setup_method()
        try:
            TxUser.create(name="during")
            QueryBuilder("tx_users").where("name", "before").update({"name": "changed"})
            raw("INSERT INTO tx_users (name) VALUES ('raw')")
            assert TxUser.query().count() == 3
            assert in_transaction() is True
        finally:
            case.teardown_method()
        assert in_transaction() is False
        assert [u.name for u in TxUser.all().get()] == ["before"]
        assert _count(engine) == 1

    def test_inner_transactions_become_savepoints(self, file_db):
        from hunt.testing.test_case import DatabaseTransactions

        case = DatabaseTransactions()
        case.setup_method()
        try:
            with begin():
                TxUser.create(name="kept until teardown")
            with pytest.raises(ValueError):
                with begin():
                    TxUser.create(name="rolled back now")
                    raise ValueError("abort")
            assert [u.name for u in TxUser.all().get()] == ["kept until teardown"]
        finally:
            case.teardown_method()
        assert _count(file_db) == 0

    async def test_async_and_thread_writes_are_isolated_too(self, file_db):
        from hunt.testing.test_case import DatabaseTransactions

        case = DatabaseTransactions()
        case.setup_method()
        try:
            await TxUser.async_create(name="async")
            t = threading.Thread(target=lambda: TxUser.create(name="thread"))
            t.start()
            t.join()
            assert await TxUser.query().async_count() == 2
        finally:
            case.teardown_method()
        assert _count(file_db) == 0

    def test_connection_is_released_and_reusable(self, file_db):
        from hunt.testing.test_case import DatabaseTransactions

        for _ in range(3):
            case = DatabaseTransactions()
            case.setup_method()
            TxUser.create(name="x")
            case.teardown_method()
        assert file_db.pool.checkedout() == 0
        assert conn_mod._pinned == {}
        assert _count(file_db) == 0


class TestRefreshDatabaseMixin:
    def _schema(self):
        from hunt.database.schema.migration import MIGRATIONS_TABLE

        Schema.create("rd_parents", lambda bp: (bp.id(), bp.string("name")))
        Schema.create(
            "rd_children",
            lambda bp: (bp.id(), bp.foreign_id("parent_id").references("id").on("rd_parents")),
        )
        Schema.create(MIGRATIONS_TABLE, lambda bp: (bp.id(), bp.string("migration"), bp.integer("batch").nullable()))
        raw("INSERT INTO rd_parents (name) VALUES ('p')")
        raw("INSERT INTO rd_children (parent_id) VALUES (1)")
        raw(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES ('0001_x', 1)")
        return MIGRATIONS_TABLE

    def test_deletes_in_foreign_key_order_and_keeps_migrations(self, file_db):
        from hunt.testing.test_case import RefreshDatabase

        migrations = self._schema()
        RefreshDatabase().teardown_method()
        assert _count(file_db, "rd_children") == 0
        assert _count(file_db, "rd_parents") == 0
        assert _count(file_db, migrations) == 1

    def test_explicit_list_is_ordered_by_foreign_keys(self, file_db):
        from hunt.testing.test_case import RefreshDatabase

        self._schema()

        class Case(RefreshDatabase):
            refresh_tables = ["rd_parents", "rd_children"]

        Case().teardown_method()
        assert _count(file_db, "rd_children") == 0
        assert _count(file_db, "rd_parents") == 0

    def test_failures_are_surfaced(self, file_db):
        from sqlalchemy.exc import IntegrityError, OperationalError

        from hunt.testing.test_case import RefreshDatabase

        self._schema()

        class OnlyParents(RefreshDatabase):
            refresh_tables = ["rd_parents"]

        with pytest.raises(IntegrityError):
            OnlyParents().teardown_method()
        assert _count(file_db, "rd_parents") == 1

        class Missing(RefreshDatabase):
            refresh_tables = ["rd_no_such_table"]

        with pytest.raises(OperationalError):
            Missing().teardown_method()
