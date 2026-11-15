"""ADM-01 (remediation step 2): raw session ids are never stored at rest.

The cookie value exists only in the browser and in memory during a request.
Session files, Redis keys and ``user_sessions.id`` hold ``sha256(session_id)``.
Records written by earlier releases (keyed by the raw id) keep working and
are migrated, lazily or with ``hunt session:prune --hash-ids``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import time

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from hunt.session.redis_store import RedisSessionStore
from hunt.session.registry import SessionRegistry, public_id_for
from hunt.session.store import FileSessionStore

SID = "ab" * 32
OTHER = "cd" * 32


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class FakeRedis:
    """The Redis commands the session code uses, on a dict."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None, xx=False):
        if xx and key not in self.data:
            return None
        self.data[key] = value
        return True

    def setex(self, key, ttl, value):
        self.data[key] = value
        return True

    def delete(self, *keys):
        return sum(1 for key in keys if self.data.pop(key, None) is not None)

    def exists(self, *keys):
        return sum(1 for key in keys if key in self.data)

    def rename(self, src, dst):
        if src not in self.data:
            raise RuntimeError("ERR no such key")
        self.data[dst] = self.data.pop(src)
        return True

    def scan_iter(self, match=None):
        return iter([key for key in list(self.data) if match is None or fnmatch.fnmatchcase(key, match)])


class Env:
    """A session backend (file or Redis) plus a user_sessions table."""

    def __init__(self, driver: str, tmp_path, engine) -> None:
        self.driver = driver
        self.dir = tmp_path / "framework" / "sessions"
        self.redis = FakeRedis()
        self.engine = engine

    def store(self):
        if self.driver == "redis":
            store = RedisSessionStore()
            store._redis = lambda: self.redis
            return store
        return FileSessionStore(self.dir)

    def login(self, user_id: int = 7) -> str:
        """Create a signed-in session the way a login does; return the cookie value."""
        store = self.store()
        store.start("")
        store.put("_auth_id", user_id)
        assert store.save() is True
        SessionRegistry().register(store.id, user_id, "web", "198.51.100.7", "UA")
        return store.id

    def load(self, cookie: str):
        store = self.store()
        store.start(cookie)
        return store

    def is_live(self, cookie: str) -> bool:
        store = self.load(cookie)
        return store.id == cookie and store.existed

    def keys(self) -> list[str]:
        """Every name under which something is stored."""
        if self.driver == "redis":
            return sorted(self.redis.data)
        return sorted(p.name for p in self.dir.iterdir()) if self.dir.is_dir() else []

    def at_rest(self) -> str:
        """Everything an attacker reading the storage and the database would see."""
        if self.driver == "redis":
            stored = json.dumps(self.redis.data)
        else:
            stored = "".join(p.name + p.read_text() for p in self.dir.iterdir()) if self.dir.is_dir() else ""
        return stored + repr(self.rows())

    def rows(self) -> list[tuple]:
        with self.engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text("SELECT id, user_id FROM user_sessions ORDER BY id"))]

    def insert_row(self, stored_id: str, user_id: int = 7, last_active_at: int | None = None) -> None:
        with self.engine.connect() as conn:
            conn.execute(
                text("INSERT INTO user_sessions (id, user_id, guard, last_active_at) VALUES (:id, :uid, 'web', :ts)"),
                {"id": stored_id, "uid": user_id, "ts": int(time.time()) if last_active_at is None else last_active_at},
            )
            conn.commit()

    def age_row(self, stored_id: str) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("UPDATE user_sessions SET last_active_at = 1 WHERE id = :id"), {"id": stored_id})
            conn.commit()

    def write_legacy(self, session_id: str, data: dict) -> None:
        """Store a session the way releases before hashed storage did: keyed by the raw id."""
        if self.driver == "redis":
            self.redis.data[f"hunt:session:{session_id}"] = json.dumps(data)
        else:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / session_id).write_text(json.dumps({"data": data, "expires_at": time.time() + 3600}))

    def legacy_key(self, session_id: str) -> str:
        return f"hunt:session:{session_id}" if self.driver == "redis" else session_id

    def hashed_key(self, session_id: str) -> str:
        return f"hunt:session:sha256:{sha(session_id)}" if self.driver == "redis" else sha(session_id)


@pytest.fixture(params=["file", "redis"])
def env(request, tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE user_sessions (id VARCHAR(64) PRIMARY KEY, user_id INTEGER, guard VARCHAR(32),"
                " ip_address VARCHAR(45), user_agent TEXT, last_active_at INTEGER)"
            )
        )
        conn.commit()
    environment = Env(request.param, tmp_path, engine)
    monkeypatch.setattr("hunt.session.registry.connection", lambda: engine)
    monkeypatch.setattr("hunt.session.migration.connection", lambda: engine)
    monkeypatch.setattr("hunt.session.session_driver", lambda: request.param)
    monkeypatch.setattr("hunt.support.helpers.storage_path", lambda *parts: str(tmp_path.joinpath(*parts)))
    monkeypatch.setattr("hunt.redis_connection.get_redis", lambda: environment.redis)
    yield environment
    engine.dispose()


# ---------------------------------------------------------------------------
# (a) nothing at rest contains the cookie value
# ---------------------------------------------------------------------------


class TestNothingAtRestHoldsTheCookie:
    def test_after_login_the_cookie_value_is_stored_nowhere(self, env):
        cookie = env.login()

        assert env.keys() == [env.hashed_key(cookie)]
        assert env.rows() == [(sha(cookie), 7)]
        assert cookie not in env.at_rest()
        assert all(cookie not in key for key in env.keys())
        # ... and the session still works with the cookie.
        assert env.load(cookie).get("_auth_id") == 7

    def test_regenerate_leaves_neither_id_behind(self, env):
        cookie = env.login()
        store = env.load(cookie)
        new_cookie = store.regenerate()
        assert store.save() is True

        assert env.keys() == [env.hashed_key(new_cookie)]
        assert cookie not in env.at_rest() and new_cookie not in env.at_rest()

    def test_the_stored_hash_does_not_work_as_a_cookie(self, env):
        """Whoever reads the storage learns sha256(cookie); that must not open the session."""
        cookie = env.login()
        intruder = env.load(sha(cookie))
        assert intruder.id != sha(cookie)
        assert intruder.existed is False and intruder.get("_auth_id") is None
        # The real session is untouched.
        assert env.load(cookie).get("_auth_id") == 7

    def test_file_is_private_and_flagged(self, env):
        if env.driver != "file":
            pytest.skip("file driver only")
        cookie = env.login()
        path = env.dir / sha(cookie)
        assert path.stat().st_mode & 0o777 == 0o600
        assert json.loads(path.read_text())["hashed"] is True

    def test_registry_rows_expose_the_hash_as_public_id(self, env):
        cookie = env.login()
        reg = SessionRegistry()
        row = reg.get(cookie)
        assert row["id"] == row["public_id"] == public_id_for(cookie) == sha(cookie)
        assert reg.sessions_for_user(7)[0]["public_id"] == sha(cookie)
        assert reg.all_sessions()[0][0]["public_id"] == sha(cookie)
        assert reg.find_by_public_id(sha(cookie))["id"] == sha(cookie)
        assert reg.find_by_public_id(cookie) is None

    def test_touch_and_deregister_take_the_raw_id(self, env):
        cookie = env.login()
        env.age_row(sha(cookie))
        reg = SessionRegistry()
        reg.touch(cookie)
        assert reg.get(cookie)["last_active_at"] > 1
        reg.deregister(cookie)
        assert env.rows() == []


# ---------------------------------------------------------------------------
# (b) legacy raw-keyed session data is read and migrated on save
# ---------------------------------------------------------------------------


class TestLegacySessionData:
    def test_legacy_record_is_read_and_migrated_on_save(self, env):
        env.write_legacy(SID, {"_auth_id": 7})
        assert env.keys() == [env.legacy_key(SID)]

        store = env.load(SID)
        assert store.id == SID and store.existed is True and store.get("_auth_id") == 7
        store.put("seen", 1)
        assert store.save() is True

        # Moved to the hashed key; the raw-keyed record is gone.
        assert env.keys() == [env.hashed_key(SID)]
        assert SID not in env.at_rest()
        again = env.load(SID)
        assert again.existed is True and again.get("_auth_id") == 7 and again.get("seen") == 1
        assert again.save() is True
        assert env.keys() == [env.hashed_key(SID)]

    def test_legacy_session_revoked_mid_request_is_not_resurrected(self, env):
        env.write_legacy(SID, {"_auth_id": 7})
        in_flight = env.load(SID)
        assert in_flight.get("_auth_id") == 7
        if env.driver == "redis":
            env.redis.delete(env.legacy_key(SID))
        else:
            (env.dir / SID).unlink()
        assert in_flight.save() is False
        assert env.keys() == []

    def test_legacy_session_migrated_by_a_concurrent_request_still_saves(self, env):
        env.write_legacy(SID, {"_auth_id": 7})
        slow = env.load(SID)
        fast = env.load(SID)
        assert fast.save() is True  # migrates and removes the legacy record
        slow.put("late", 1)
        assert slow.save() is True
        assert env.keys() == [env.hashed_key(SID)]
        assert env.load(SID).get("late") == 1

    def test_regenerating_a_legacy_session_removes_the_legacy_record(self, env):
        env.write_legacy(SID, {"_auth_id": 7})
        store = env.load(SID)
        new_cookie = store.regenerate()
        assert store.save() is True
        assert env.keys() == [env.hashed_key(new_cookie)]
        assert env.is_live(SID) is False

    def test_expired_legacy_file_is_not_adopted(self, env):
        if env.driver != "file":
            pytest.skip("Redis expires keys itself")
        env.dir.mkdir(parents=True)
        (env.dir / SID).write_text(json.dumps({"data": {"_auth_id": 7}, "expires_at": 1}))
        store = env.load(SID)
        assert store.id != SID and store.get("_auth_id") is None
        assert env.keys() == []


# ---------------------------------------------------------------------------
# (c) revocation and pruning delete the session data, knowing only the hash
# ---------------------------------------------------------------------------


class TestRevocationDeletesSessionData:
    def test_revoke_by_public_id(self, env):
        cookie = env.login(7)
        keep = env.login(8)
        reg = SessionRegistry()

        assert reg.revoke_by_public_id(cookie) is False  # a raw session id is not accepted
        assert env.is_live(cookie) is True
        assert reg.revoke_by_public_id(sha(cookie)) is True
        assert reg.revoke_by_public_id(sha(cookie)) is False

        assert env.is_live(cookie) is False and env.is_live(keep) is True
        assert env.keys() == [env.hashed_key(keep)]
        assert env.rows() == [(sha(keep), 8)]

    def test_revoke_for_user(self, env):
        first, second, keep = env.login(7), env.login(7), env.login(8)
        assert SessionRegistry().revoke_for_user(7) == 2
        assert env.is_live(first) is False and env.is_live(second) is False and env.is_live(keep) is True
        assert env.keys() == [env.hashed_key(keep)]
        assert env.rows() == [(sha(keep), 8)]

    def test_revoke_session_takes_the_raw_id(self, env):
        cookie = env.login(7)
        reg = SessionRegistry()
        assert reg.revoke_session(cookie) is True
        assert reg.revoke_session(cookie) is False
        assert env.keys() == [] and env.rows() == []

    def test_revoke_session_also_accepts_a_row_id(self, env):
        """Code written against the old rows (``revoke_session(row["id"])``) keeps revoking."""
        env.login(7)
        reg = SessionRegistry()
        (row,) = reg.sessions_for_user(7)
        assert reg.revoke_session(row["id"]) is True
        assert env.keys() == [] and env.rows() == []

    def test_prune_removes_stale_rows_and_their_data(self, env):
        stale, fresh = env.login(7), env.login(8)
        env.age_row(sha(stale))
        assert SessionRegistry().prune(max_age=3600) == 1
        assert env.is_live(stale) is False and env.is_live(fresh) is True
        assert env.keys() == [env.hashed_key(fresh)]
        assert env.rows() == [(sha(fresh), 8)]
        assert SessionRegistry().prune(max_age=3600) == 0

    def test_in_flight_request_cannot_resurrect_a_revoked_session(self, env):
        cookie = env.login(7)
        in_flight = env.load(cookie)
        assert SessionRegistry().revoke_for_user(7) == 1
        in_flight.put("more", 1)
        assert in_flight.save() is False
        assert env.keys() == []


# ---------------------------------------------------------------------------
# (d) a registry row that still holds a raw id
# ---------------------------------------------------------------------------


class TestLegacyRegistryRow:
    def _legacy(self, env, session_id=SID, user_id=7):
        env.write_legacy(session_id, {"_auth_id": user_id})
        env.insert_row(session_id, user_id)

    def test_public_id_of_a_legacy_row_is_not_the_raw_id(self, env):
        self._legacy(env)
        reg = SessionRegistry()
        rows, total = reg.all_sessions()
        assert total == 1 and rows[0]["public_id"] == sha(SID)
        assert reg.sessions_for_user(7)[0]["public_id"] == sha(SID)
        assert reg.get(SID)["public_id"] == sha(SID)

    def test_legacy_row_is_revocable_by_public_id(self, env):
        self._legacy(env)
        self._legacy(env, OTHER, 8)
        reg = SessionRegistry()
        assert reg.revoke_by_public_id(SID) is False  # the raw id still is not a public id
        assert env.is_live(SID) is True
        assert reg.revoke_by_public_id(sha(SID)) is True
        assert env.is_live(SID) is False and env.is_live(OTHER) is True
        assert env.rows() == [(OTHER, 8)]

    def test_legacy_row_is_revocable_after_the_store_migrated_its_data(self, env):
        self._legacy(env)
        store = env.load(SID)
        assert store.save() is True  # data now under the hash, the row still raw
        assert env.keys() == [env.hashed_key(SID)] and env.rows() == [(SID, 7)]

        reg = SessionRegistry()
        assert reg.all_sessions()[0][0]["public_id"] == sha(SID)
        assert reg.revoke_by_public_id(sha(SID)) is True
        assert env.keys() == [] and env.rows() == []

    def test_legacy_rows_are_revoked_for_user_and_by_raw_id(self, env):
        self._legacy(env)
        self._legacy(env, OTHER, 8)
        reg = SessionRegistry()
        assert reg.revoke_for_user(7) == 1
        assert reg.revoke_session(OTHER) is True
        assert env.keys() == [] and env.rows() == []

    def test_legacy_row_is_pruned_with_its_data(self, env):
        self._legacy(env)
        env.age_row(SID)
        assert SessionRegistry().prune(max_age=3600) == 1
        assert env.keys() == [] and env.rows() == []

    def test_touch_rewrites_a_legacy_row_to_the_hash(self, env):
        self._legacy(env)
        env.age_row(SID)
        reg = SessionRegistry()
        reg.touch(SID)
        assert env.rows() == [(sha(SID), 7)]
        assert reg.get(SID)["last_active_at"] > 1
        reg.touch(SID)
        assert env.rows() == [(sha(SID), 7)]

    def test_deregister_removes_a_legacy_row(self, env):
        self._legacy(env)
        SessionRegistry().deregister(SID)
        assert env.rows() == []


# ---------------------------------------------------------------------------
# (e) the migration
# ---------------------------------------------------------------------------


class TestHashIdsMigration:
    def _legacy_install(self, env):
        env.write_legacy(SID, {"_auth_id": 7})
        env.insert_row(SID, 7)
        env.write_legacy(OTHER, {"cart": [1]})  # a guest session: no registry row
        return env.login(9)  # and one that is already hashed

    def test_rewrites_rows_and_stored_sessions(self, env):
        from hunt.session.migration import hash_session_ids

        modern = self._legacy_install(env)
        assert hash_session_ids() == {"rows": 1, "sessions": 2}

        assert env.rows() == sorted([(sha(SID), 7), (sha(modern), 9)])
        assert env.keys() == sorted([env.hashed_key(SID), env.hashed_key(OTHER), env.hashed_key(modern)])
        assert SID not in env.at_rest() and OTHER not in env.at_rest()
        # Nobody is logged out.
        assert env.load(SID).get("_auth_id") == 7
        assert env.load(OTHER).get("cart") == [1]
        assert env.load(modern).get("_auth_id") == 9
        # The migrated session is revocable by its public id.
        assert SessionRegistry().revoke_by_public_id(sha(SID)) is True
        assert env.is_live(SID) is False

    def test_is_idempotent(self, env):
        from hunt.session.migration import hash_session_ids

        self._legacy_install(env)
        hash_session_ids()
        rows, keys, stored = env.rows(), env.keys(), env.at_rest()
        assert hash_session_ids() == {"rows": 0, "sessions": 0}
        assert hash_session_ids() == {"rows": 0, "sessions": 0}
        assert (env.rows(), env.keys()) == (rows, keys)
        assert env.at_rest() == stored  # nothing was rewritten

    def test_completes_a_half_done_run(self, env):
        """Data already moved by the store, row still raw."""
        from hunt.session.migration import hash_session_ids

        env.write_legacy(SID, {"_auth_id": 7})
        env.insert_row(SID, 7)
        assert env.load(SID).save() is True
        assert hash_session_ids() == {"rows": 1, "sessions": 0}
        assert env.rows() == [(sha(SID), 7)]

    def test_raw_row_next_to_its_hashed_twin_is_dropped(self, env):
        from hunt.session.migration import hash_session_ids

        env.write_legacy(SID, {"_auth_id": 7})
        env.insert_row(SID, 7)
        env.insert_row(sha(SID), 7)
        assert hash_session_ids()["rows"] == 1
        assert env.rows() == [(sha(SID), 7)]

    def test_does_not_overwrite_a_session_already_stored_hashed(self, env):
        from hunt.session.migration import hash_session_ids

        if env.driver != "file":
            pytest.skip("Redis RENAME is atomic; the file path needs the exclusive create")
        env.write_legacy(SID, {"_auth_id": 7, "version": "stale"})
        current = FileSessionStore(env.dir)
        current.start(SID)
        current.put("version", "current")
        current.save()
        env.write_legacy(SID, {"_auth_id": 7, "version": "stale"})  # a leftover raw-named copy
        hash_session_ids()
        assert env.keys() == [sha(SID)]
        assert env.load(SID).get("version") == "current"

    def test_missing_table_and_empty_storage_are_fine(self, env):
        from hunt.session.migration import hash_session_ids

        with env.engine.connect() as conn:
            conn.execute(text("DROP TABLE user_sessions"))
            conn.commit()
        assert hash_session_ids() == {"rows": 0, "sessions": 0}

    @pytest.mark.parametrize("argv", [["--hash-ids"], None])
    def test_command_is_idempotent(self, env, monkeypatch, argv):
        from hunt.console.commands.session_prune import session_hash_ids_command, session_prune_command

        monkeypatch.setattr("hunt.console.commands.queue_work.boot_application", lambda: True)
        self._legacy_install(env)
        command, args = (session_prune_command, argv) if argv else (session_hash_ids_command, [])

        first = CliRunner().invoke(command, args)
        assert first.exit_code == 0, first.output
        assert "Hashed 1 user_sessions row(s) and moved 2 stored session(s)" in first.output
        rows, keys = env.rows(), env.keys()
        assert SID not in env.at_rest()

        second = CliRunner().invoke(command, args)
        assert second.exit_code == 0, second.output
        assert "Hashed 0 user_sessions row(s) and moved 0 stored session(s)" in second.output
        assert (env.rows(), env.keys()) == (rows, keys)
        assert env.load(SID).get("_auth_id") == 7

    def test_prune_command_still_prunes(self, env, monkeypatch):
        from hunt.console.commands.session_prune import session_prune_command

        monkeypatch.setattr("hunt.console.commands.queue_work.boot_application", lambda: True)
        cookie = env.login(7)
        env.age_row(sha(cookie))
        result = CliRunner().invoke(session_prune_command, ["--max-age", "3600"])
        assert result.exit_code == 0, result.output
        assert "Pruned 1 expired session(s)" in result.output
        assert env.keys() == [] and env.rows() == []


def test_session_table_id_column_fits_a_sha256_hex_digest():
    from hunt.console.commands.session_table import _MIGRATION_STUB

    assert 'table.string("id", 64).primary_key()' in _MIGRATION_STUB
    assert len(sha(SID)) == 64
