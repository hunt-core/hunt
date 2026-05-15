from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text

from hunt.database.connection import connection
from hunt.database.schema.builder import Schema

MIGRATIONS_TABLE = "hunt_migrations"


class Migration:
    def up(self) -> None:
        raise NotImplementedError

    def down(self) -> None:
        pass


class Migrator:
    def __init__(self, migrations_path: Path, conn_name: str | None = None) -> None:
        self._path = migrations_path
        self._conn_name = conn_name
        self._ensure_table()

    def _ensure_table(self) -> None:
        Schema.create(
            MIGRATIONS_TABLE,
            lambda bp: (
                bp.id(),
                bp.string("migration"),
                bp.integer("batch").nullable(),
            ),
        )

    def _ran(self) -> list[str]:
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT migration FROM {MIGRATIONS_TABLE} ORDER BY id"))
            return [row[0] for row in result.fetchall()]

    def _pending(self) -> list[Path]:
        if not self._path.is_dir():
            return []
        ran = set(self._ran())
        files = sorted(self._path.glob("*.py"))
        return [f for f in files if f.stem not in ran]

    def _next_batch(self) -> int:
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT MAX(batch) FROM {MIGRATIONS_TABLE}"))
            row = result.fetchone()
            return (row[0] or 0) + 1

    def run(self) -> list[str]:
        pending = self._pending()
        if not pending:
            return []

        batch = self._next_batch()
        ran: list[str] = []
        for file in pending:
            migration = self._load(file)
            migration.up()
            engine = connection(self._conn_name)
            with engine.connect() as conn:
                conn.execute(
                    text(f"INSERT INTO {MIGRATIONS_TABLE} (migration, batch) VALUES (:m, :b)"),
                    {"m": file.stem, "b": batch},
                )
                conn.commit()
            ran.append(file.stem)
        return ran

    def rollback(self) -> list[str]:
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            result = conn.execute(text(f"SELECT MAX(batch) FROM {MIGRATIONS_TABLE}"))
            row = result.fetchone()
            if not row or row[0] is None:
                return []
            last_batch = row[0]
            result = conn.execute(
                text(f"SELECT migration FROM {MIGRATIONS_TABLE} WHERE batch = :b ORDER BY id DESC"),
                {"b": last_batch},
            )
            names = [r[0] for r in result.fetchall()]

        rolled: list[str] = []
        for name in names:
            file = self._path / f"{name}.py"
            if file.exists():
                migration = self._load(file)
                migration.down()
            engine = connection(self._conn_name)
            with engine.connect() as conn:
                conn.execute(
                    text(f"DELETE FROM {MIGRATIONS_TABLE} WHERE migration = :m"),
                    {"m": name},
                )
                conn.commit()
            rolled.append(name)
        return rolled

    def fresh(self) -> list[str]:
        ran = self._ran()
        for name in reversed(ran):
            file = self._path / f"{name}.py"
            if file.exists():
                migration = self._load(file)
                migration.down()
        engine = connection(self._conn_name)
        with engine.connect() as conn:
            conn.execute(text(f"DELETE FROM {MIGRATIONS_TABLE}"))
            conn.commit()
        return self.run()

    def status(self) -> list[dict]:
        ran = set(self._ran())
        files = sorted(self._path.glob("*.py")) if self._path.is_dir() else []
        result = []
        for f in files:
            result.append({"migration": f.stem, "ran": f.stem in ran})
        return result

    @staticmethod
    def _load(file: Path) -> Migration:
        spec = importlib.util.spec_from_file_location(file.stem, file)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[attr-defined]
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, Migration) and obj is not Migration:
                return obj()
        raise ValueError(f"No Migration subclass found in {file}")
