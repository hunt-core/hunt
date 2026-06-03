from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# SessionRegistry unit tests (DB calls mocked)
# ---------------------------------------------------------------------------


def _make_conn_ctx(fetchone=None, fetchall=None, keys=None):
    """Return a mock context manager for connection().connect()."""
    conn = MagicMock()
    result = MagicMock()
    result.keys.return_value = keys or []
    result.fetchone.return_value = fetchone
    result.fetchall.return_value = fetchall or []
    conn.execute.return_value = result
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, conn


class TestSessionRegistryRegister:
    def test_register_calls_insert(self):
        from hunt.session.registry import SessionRegistry

        ctx, conn = _make_conn_ctx()
        with patch("hunt.session.registry.connection") as mock_conn:
            mock_conn.return_value.connect.return_value = ctx
            SessionRegistry().register("abc123", 1, "web", "1.2.3.4", "Mozilla/5.0")
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    def test_register_silences_db_error(self):
        from hunt.session.registry import SessionRegistry

        with patch("hunt.session.registry.connection", side_effect=Exception("no db")):
            # Should not raise
            SessionRegistry().register("abc", 1)

    def test_register_truncates_user_agent(self):
        from hunt.session.registry import SessionRegistry

        ctx, conn = _make_conn_ctx()
        long_ua = "A" * 1000
        with patch("hunt.session.registry.connection") as mock_conn:
            mock_conn.return_value.connect.return_value = ctx
            SessionRegistry().register("sid", 1, user_agent=long_ua)
        params = conn.execute.call_args[0][1]
        assert len(params["ua"]) == 512


class TestSessionRegistryDeregister:
    def test_deregister_calls_delete(self):
        from hunt.session.registry import SessionRegistry

        ctx, conn = _make_conn_ctx()
        with patch("hunt.session.registry.connection") as mock_conn:
            mock_conn.return_value.connect.return_value = ctx
            SessionRegistry().deregister("sid42")
        conn.execute.assert_called_once()
        sql = str(conn.execute.call_args[0][0])
        assert "DELETE" in sql.upper()

    def test_deregister_silences_db_error(self):
        from hunt.session.registry import SessionRegistry

        with patch("hunt.session.registry.connection", side_effect=Exception("no db")):
            SessionRegistry().deregister("x")


class TestSessionRegistryTouch:
    def test_touch_calls_update(self):
        from hunt.session.registry import SessionRegistry

        ctx, conn = _make_conn_ctx()
        with patch("hunt.session.registry.connection") as mock_conn:
            mock_conn.return_value.connect.return_value = ctx
            SessionRegistry().touch("sid")
        conn.execute.assert_called_once()
        sql = str(conn.execute.call_args[0][0])
        assert "UPDATE" in sql.upper()


class TestSessionRegistryGet:
    def test_get_returns_dict_when_found(self):
        from hunt.session.registry import SessionRegistry

        ctx, _conn = _make_conn_ctx(fetchone=(1, 2), keys=["a", "b"])
        with patch("hunt.session.registry.connection") as mock_conn:
            mock_conn.return_value.connect.return_value = ctx
            result = SessionRegistry().get("sid")
        assert result == {"a": 1, "b": 2}

    def test_get_returns_none_when_not_found(self):
        from hunt.session.registry import SessionRegistry

        ctx, _conn = _make_conn_ctx(fetchone=None, keys=["id"])
        with patch("hunt.session.registry.connection") as mock_conn:
            mock_conn.return_value.connect.return_value = ctx
            result = SessionRegistry().get("missing")
        assert result is None

    def test_get_silences_db_error(self):
        from hunt.session.registry import SessionRegistry

        with patch("hunt.session.registry.connection", side_effect=Exception("no db")):
            assert SessionRegistry().get("x") is None


class TestSessionRegistryAllSessions:
    def test_returns_rows_and_total(self):
        from hunt.session.registry import SessionRegistry

        conn = MagicMock()
        count_result = MagicMock()
        count_result.fetchone.return_value = (3,)
        rows_result = MagicMock()
        rows_result.keys.return_value = ["id", "user_id"]
        rows_result.fetchall.return_value = [("s1", 1), ("s2", 2)]
        conn.execute.side_effect = [count_result, rows_result]

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch("hunt.session.registry.connection") as mock_conn:
            mock_conn.return_value.connect.return_value = ctx
            rows, total = SessionRegistry().all_sessions(page=1, per_page=25)

        assert total == 3
        assert len(rows) == 2
        assert rows[0] == {"id": "s1", "user_id": 1}

    def test_returns_empty_on_error(self):
        from hunt.session.registry import SessionRegistry

        with patch("hunt.session.registry.connection", side_effect=Exception("no db")):
            rows, total = SessionRegistry().all_sessions()
        assert rows == []
        assert total == 0


class TestSessionRegistryRevokeForUser:
    def test_revoke_deletes_data_and_rows(self):
        from hunt.session.registry import SessionRegistry

        reg = SessionRegistry()
        reg.sessions_for_user = MagicMock(return_value=[{"id": "s1"}, {"id": "s2"}])
        reg.deregister = MagicMock()

        ctx, conn = _make_conn_ctx()
        with (
            patch("hunt.session.registry.connection") as mock_conn,
            patch("hunt.session.registry._delete_session_data") as mock_del,
        ):
            mock_conn.return_value.connect.return_value = ctx
            count = reg.revoke_for_user(42)

        assert count == 2
        assert mock_del.call_count == 2
        mock_del.assert_any_call("s1")
        mock_del.assert_any_call("s2")
        conn.execute.assert_called_once()  # one DELETE WHERE user_id

    def test_revoke_returns_zero_when_no_sessions(self):
        from hunt.session.registry import SessionRegistry

        reg = SessionRegistry()
        reg.sessions_for_user = MagicMock(return_value=[])
        count = reg.revoke_for_user(99)
        assert count == 0

    def test_revoke_single_session(self):
        from hunt.session.registry import SessionRegistry

        reg = SessionRegistry()
        reg.get = MagicMock(return_value={"id": "abc", "user_id": 7})
        reg.deregister = MagicMock()

        with patch("hunt.session.registry._delete_session_data") as mock_del:
            result = reg.revoke_session("abc")

        assert result is True
        mock_del.assert_called_once_with("abc")
        reg.deregister.assert_called_once_with("abc")

    def test_revoke_single_session_not_found(self):
        from hunt.session.registry import SessionRegistry

        reg = SessionRegistry()
        reg.get = MagicMock(return_value=None)
        result = reg.revoke_session("ghost")
        assert result is False


class TestDeleteSessionData:
    def test_deletes_file_when_file_driver(self):
        from hunt.session.registry import _delete_session_data

        with (
            patch.dict("os.environ", {"SESSION_DRIVER": "file"}),
            patch("hunt.session.registry._delete_session_file") as mock_file,
        ):
            _delete_session_data("sid123")
        mock_file.assert_called_once_with("sid123")

    def test_deletes_redis_key_when_redis_driver(self):
        from hunt.session.registry import _delete_session_data

        with (
            patch.dict("os.environ", {"SESSION_DRIVER": "redis"}),
            patch("hunt.session.registry._delete_session_redis") as mock_redis,
        ):
            _delete_session_data("sid456")
        mock_redis.assert_called_once_with("sid456")

    def test_silences_errors(self):
        from hunt.session.registry import _delete_session_data

        with (
            patch.dict("os.environ", {"SESSION_DRIVER": "file"}),
            patch("hunt.session.registry._delete_session_file", side_effect=Exception("oops")),
        ):
            _delete_session_data("sid")  # should not raise

    def test_delete_session_redis_calls_get_redis(self):
        from hunt.session.registry import _delete_session_redis

        mock_redis = MagicMock()
        with patch("hunt.redis_connection.get_redis", return_value=mock_redis):
            _delete_session_redis("abc")
        mock_redis.delete.assert_called_once_with("hunt:session:abc")


# ---------------------------------------------------------------------------
# revoke_sessions_for module-level helper
# ---------------------------------------------------------------------------


class TestRevokeSessionsFor:
    def test_delegates_to_registry(self):
        from hunt.session.registry import revoke_sessions_for

        with patch("hunt.session.registry.SessionRegistry") as MockReg:
            instance = MockReg.return_value
            instance.revoke_for_user.return_value = 3
            result = revoke_sessions_for(5, guard="web")

        assert result == 3
        instance.revoke_for_user.assert_called_once_with(5, guard="web")


# ---------------------------------------------------------------------------
# PasswordBroker.reset revoke_sessions flag
# ---------------------------------------------------------------------------


class TestPasswordBrokerRevokeFlag:
    def test_reset_calls_revoke_when_flag_set(self):
        from hunt.auth.passwords import PasswordBroker

        broker = PasswordBroker()
        broker.token_valid = MagicMock(return_value=True)

        user = MagicMock()
        user._attributes = {"id": 7, "password": "old"}
        broker._find_user = MagicMock(return_value=user)
        broker.delete_token = MagicMock()

        with (
            patch("hunt.auth.manager.hash_password", return_value="hashed"),
            patch("hunt.session.registry.revoke_sessions_for") as mock_revoke,
        ):
            result = broker.reset(
                {"email": "a@b.com", "token": "tok", "password": "new"},
                revoke_sessions=True,
            )

        assert result is True
        mock_revoke.assert_called_once_with(7)

    def test_reset_does_not_revoke_by_default(self):
        from hunt.auth.passwords import PasswordBroker

        broker = PasswordBroker()
        broker.token_valid = MagicMock(return_value=True)

        user = MagicMock()
        user._attributes = {"id": 7, "password": "old"}
        broker._find_user = MagicMock(return_value=user)
        broker.delete_token = MagicMock()

        with (
            patch("hunt.auth.manager.hash_password", return_value="hashed"),
            patch("hunt.session.registry.revoke_sessions_for") as mock_revoke,
        ):
            broker.reset({"email": "a@b.com", "token": "tok", "password": "new"})

        mock_revoke.assert_not_called()


# ---------------------------------------------------------------------------
# _SessionGuard.login registers session
# ---------------------------------------------------------------------------


class TestSessionGuardRegistration:
    def _make_session(self, session_id="newsid"):
        session = MagicMock()
        session.id = session_id
        return session

    def test_login_registers_session(self):
        from hunt.auth.manager import _SessionGuard

        guard = _SessionGuard("web")
        session = self._make_session("sid-new")
        user = MagicMock()
        user._attributes = {"id": 42}

        with (
            patch("hunt.auth.manager._get_session", return_value=session),
            patch("hunt.auth.manager._get_current_request", return_value=None),
            patch("hunt.session.registry.SessionRegistry") as MockReg,
        ):
            guard.login(user)

        MockReg.return_value.register.assert_called_once()
        call_args = MockReg.return_value.register.call_args[0]
        assert call_args[0] == "sid-new"  # session_id
        assert call_args[1] == 42  # user_id
        assert call_args[2] == "web"  # guard name

    def test_logout_deregisters_session(self):
        from hunt.auth.manager import _SessionGuard

        guard = _SessionGuard("web")
        session = self._make_session("sid-old")

        with (
            patch("hunt.auth.manager._get_session", return_value=session),
            patch("hunt.session.registry.SessionRegistry") as MockReg,
        ):
            guard.logout()

        MockReg.return_value.deregister.assert_called_once_with("sid-old")

    def test_login_silences_registry_error(self):
        from hunt.auth.manager import _SessionGuard

        guard = _SessionGuard("web")
        session = self._make_session()
        user = MagicMock()
        user._attributes = {"id": 1}

        with (
            patch("hunt.auth.manager._get_session", return_value=session),
            patch("hunt.auth.manager._get_current_request", return_value=None),
            patch("hunt.session.registry.SessionRegistry", side_effect=Exception("db down")),
        ):
            guard.login(user)  # should not raise

    def test_logout_silences_registry_error(self):
        from hunt.auth.manager import _SessionGuard

        guard = _SessionGuard("web")
        session = self._make_session()

        with (
            patch("hunt.auth.manager._get_session", return_value=session),
            patch("hunt.session.registry.SessionRegistry", side_effect=Exception("db down")),
        ):
            guard.logout()  # should not raise


# ---------------------------------------------------------------------------
# ColumnDef.primary_key() fluent method
# ---------------------------------------------------------------------------


class TestColumnDefPrimaryKey:
    def test_primary_key_sets_flag(self):
        from hunt.database.schema.blueprint import ColumnDef

        col = ColumnDef("id", "VARCHAR", length=64)
        assert col.primary is False
        col.primary_key()
        assert col.primary is True

    def test_primary_key_returns_self(self):
        from hunt.database.schema.blueprint import ColumnDef

        col = ColumnDef("id", "VARCHAR")
        assert col.primary_key() is col

    def test_blueprint_string_primary_key(self):
        from hunt.database.schema.blueprint import Blueprint

        bp = Blueprint("user_sessions")
        col = bp.string("id", 64).primary_key()
        assert col.primary is True
        assert bp.columns[0].name == "id"
        assert bp.columns[0].primary is True
