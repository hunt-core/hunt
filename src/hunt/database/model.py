from __future__ import annotations

import contextvars
import time
from collections.abc import Callable
from typing import Any, ClassVar

from hunt.database.query_builder import QueryBuilder, _run_sync
from hunt.support.str import Str

# When True, relationship helper methods return the Relation object instead of
# executing the query. Used internally by the eager-loading infrastructure.
_EAGER_MODE: contextvars.ContextVar[bool] = contextvars.ContextVar("eager_mode", default=False)


class ModelMeta(type):
    """Metaclass that injects table name and sets up the class."""

    def __new__(mcs, name: str, bases: tuple, namespace: dict) -> type:
        cls = super().__new__(mcs, name, bases, namespace)
        if name != "Model" and "table" not in namespace:
            cls.table = Str.plural(Str.snake(name)).lower()
        # Each subclass gets its own event listener dict so observers don't bleed across models
        if "_event_listeners" not in namespace:
            cls._event_listeners = {}
        # Each subclass gets its own global scope dict
        if "_global_scopes" not in namespace:
            cls._global_scopes = {}
        if name != "Model" and "boot" in namespace:
            cls.boot()
        return cls


class Model(metaclass=ModelMeta):
    # ------------------------------------------------------------------
    # Class-level configuration
    # ------------------------------------------------------------------
    table: ClassVar[str] = ""
    primary_key: ClassVar[str] = "id"
    fillable: ClassVar[list[str]] = []
    guarded: ClassVar[list[str]] = ["id", "created_at", "updated_at"]
    hidden: ClassVar[list[str]] = []
    appends: ClassVar[list[str]] = []
    casts: ClassVar[dict[str, str]] = {}
    timestamps: ClassVar[bool] = True
    attributes: ClassVar[dict] = {}
    _soft_deletes: ClassVar[bool] = False
    _connection: ClassVar[str | None] = None
    _global_scopes: ClassVar[dict] = {}
    route_key_name: ClassVar[str] = ""

    _event_listeners: ClassVar[dict[str, list]] = {}

    def __init__(self, attributes: dict | None = None) -> None:
        self._attributes: dict[str, Any] = dict(type(self).attributes)
        self._original: dict[str, Any] = {}
        self._exists: bool = False
        self._relations: dict[str, Any] = {}
        if attributes:
            self.fill(attributes)

    # ------------------------------------------------------------------
    # Attribute access
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # Accessor pattern: get_<name>_attribute
        accessor = f"get_{name}_attribute"
        if accessor in type(self).__dict__:
            return type(self).__dict__[accessor](self)
        if name in self.__dict__.get("_attributes", {}):
            return self._cast(name, self._attributes[name])
        if name in self.__dict__.get("_relations", {}):
            return self._relations[name]
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or any(name in klass.__dict__ for klass in type(self).__mro__):
            super().__setattr__(name, value)
            return
        # Mutator pattern: set_<name>_attribute
        mutator = f"set_{name}_attribute"
        if mutator in type(self).__dict__:
            type(self).__dict__[mutator](self, value)
            return
        self._attributes[name] = value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._attributes!r})"

    # ------------------------------------------------------------------
    # Fill
    # ------------------------------------------------------------------

    def fill(self, attributes: dict) -> Model:
        for key, value in attributes.items():
            if self._is_fillable(key):
                self._attributes[key] = value
        return self

    def force_fill(self, attributes: dict) -> Model:
        self._attributes.update(attributes)
        return self

    def _is_fillable(self, key: str) -> bool:
        if self.fillable:
            return key in self.fillable
        return key not in self.guarded

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> bool:
        if self.__dict__.get("timestamps", type(self).timestamps):
            now = int(time.time())
            if not self._exists:
                self._attributes.setdefault("created_at", now)
            self._attributes["updated_at"] = now

        if self._exists:
            self._fire_event("updating")
            dirty = self._dirty()
            if dirty:
                self.query().where(self.primary_key, self._attributes[self.primary_key]).update(dirty)
            self._fire_event("updated")
        else:
            self._fire_event("creating")
            pk = self.query().insert(self._attributes)
            self._attributes[self.primary_key] = pk
            self._exists = True
            self._fire_event("created")

        self._original = dict(self._attributes)
        return True

    def delete(self) -> bool:
        if not self._exists:
            return False
        self._fire_event("deleting")
        if self._soft_deletes:
            now = int(time.time())
            self.query().where(self.primary_key, self._attributes[self.primary_key]).update({"deleted_at": now})
            self._attributes["deleted_at"] = now
        else:
            self.query().where(self.primary_key, self._attributes[self.primary_key]).delete()
            self._exists = False
        self._fire_event("deleted")
        return True

    def restore(self) -> bool:
        if not self._soft_deletes:
            return False
        self.query().with_trashed().where(self.primary_key, self._attributes[self.primary_key]).update(
            {"deleted_at": None}
        )
        self._attributes["deleted_at"] = None
        return True

    def force_delete(self) -> bool:
        self.query().where(self.primary_key, self._attributes[self.primary_key]).delete()
        self._exists = False
        return True

    def replicate(self, except_: list[str] | None = None) -> Model:
        """Return an unsaved copy of this model without the primary key."""
        exclude = set(except_ or []) | {self.primary_key}
        attrs = {k: v for k, v in self._attributes.items() if k not in exclude}
        instance = type(self).__new__(type(self))
        instance._attributes = attrs
        instance._original = {}
        instance._exists = False
        instance._relations = {}
        return instance

    # ------------------------------------------------------------------
    # Dirty tracking
    # ------------------------------------------------------------------

    def _dirty(self) -> dict:
        dirty = {}
        for k, v in self._attributes.items():
            if k not in self._original or self._original[k] != v:
                dirty[k] = v
        return dirty

    def is_dirty(self, *keys: str) -> bool:
        dirty = self._dirty()
        if not keys:
            return bool(dirty)
        return any(k in dirty for k in keys)

    # ------------------------------------------------------------------
    # Class-level query methods
    # ------------------------------------------------------------------

    @classmethod
    def boot(cls) -> None:
        """Override to register global scopes or observers at class definition time."""

    @classmethod
    def add_global_scope(cls, name: str, scope: Callable[[QueryBuilder], QueryBuilder]) -> None:
        cls._global_scopes[name] = scope

    @classmethod
    def without_global_scope(cls, *names: str) -> QueryBuilder:
        return cls.query().without_global_scope(*names)

    @classmethod
    def without_global_scopes(cls) -> QueryBuilder:
        return cls.query().without_global_scopes()

    @classmethod
    def resolve_route_binding(cls, value: Any) -> Model:
        key = cls.route_key_name or cls.primary_key
        instance = cls.query().where(key, value).first()
        if instance is None:
            raise ValueError(f"{cls.__name__} not found with {key}={value!r}")
        return instance

    @classmethod
    def query(cls) -> QueryBuilder:
        qb = QueryBuilder(cls.table, cls, cls._connection)
        qb._global_scope_fns = dict(cls._global_scopes)
        return qb

    @classmethod
    def with_(cls, *relations: str | dict) -> QueryBuilder:
        """Start a query with eager-loaded relations."""
        return cls.query().with_(*relations)

    @classmethod
    def all(cls) -> QueryBuilder:
        return cls.query()

    @classmethod
    def order_by(cls, column: str, direction: str = "asc") -> QueryBuilder:
        return cls.query().order_by(column, direction)

    @classmethod
    def latest(cls, column: str = "created_at") -> QueryBuilder:
        return cls.query().latest(column)

    @classmethod
    def oldest(cls, column: str = "created_at") -> QueryBuilder:
        return cls.query().oldest(column)

    @classmethod
    def limit(cls, n: int) -> QueryBuilder:
        return cls.query().limit(n)

    @classmethod
    def select(cls, *columns: str) -> QueryBuilder:
        return cls.query().select(*columns)

    @classmethod
    def where_raw(cls, sql: str, bindings: list | dict | None = None) -> QueryBuilder:
        return cls.query().where_raw(sql, bindings)

    @classmethod
    def find(cls, id: Any) -> Model | None:
        return cls.query().find(id)

    @classmethod
    def find_or_fail(cls, id: Any) -> Model:
        instance = cls.find(id)
        if instance is None:
            raise ValueError(f"{cls.__name__} not found with id={id}")
        return instance

    @classmethod
    def where(cls, column: str, operator_or_value: Any, value: Any = None) -> QueryBuilder:
        return cls.query().where(column, operator_or_value, value)

    @classmethod
    def where_in(cls, column: str, values: list) -> QueryBuilder:
        return cls.query().where_in(column, values)

    @classmethod
    def where_not_in(cls, column: str, values: list) -> QueryBuilder:
        return cls.query().where_not_in(column, values)

    @classmethod
    def or_where(cls, column: str, operator_or_value: Any, value: Any = None) -> QueryBuilder:
        return cls.query().or_where(column, operator_or_value, value)

    @classmethod
    def where_null(cls, column: str) -> QueryBuilder:
        return cls.query().where_null(column)

    @classmethod
    def where_not_null(cls, column: str) -> QueryBuilder:
        return cls.query().where_not_null(column)

    @classmethod
    def where_group(cls, callback: Callable[[QueryBuilder], QueryBuilder]) -> QueryBuilder:
        return cls.query().where_group(callback)

    @classmethod
    def or_where_group(cls, callback: Callable[[QueryBuilder], QueryBuilder]) -> QueryBuilder:
        return cls.query().or_where_group(callback)

    @classmethod
    def create(cls, data: dict | None = None, **kwargs: Any) -> Model:
        attrs = {**(data or {}), **kwargs}
        instance = cls(attrs)
        instance.save()
        return instance

    @classmethod
    def first_or_create(cls, search: dict, attributes: dict | None = None) -> tuple[Model, bool]:
        qb = cls.query()
        for k, v in search.items():
            qb = qb.where(k, v)
        existing = qb.first()
        if existing:
            return existing, False
        merged = {**search, **(attributes or {})}
        return cls.create(merged), True

    @classmethod
    def update_or_create(cls, search: dict, values: dict) -> Model:
        qb = cls.query()
        for k, v in search.items():
            qb = qb.where(k, v)
        existing = qb.first()
        if existing:
            existing.fill(values)
            existing.save()
            return existing
        return cls.create({**search, **values})

    @classmethod
    def first_or_new(cls, search: dict, attributes: dict | None = None) -> tuple[Model, bool]:
        """Like first_or_create but does not persist the new instance."""
        qb = cls.query()
        for k, v in search.items():
            qb = qb.where(k, v)
        existing = qb.first()
        if existing:
            return existing, False
        return cls({**search, **(attributes or {})}), True

    @classmethod
    def create_many(cls, rows: list[dict]) -> None:
        """Bulk-insert records in a single query. Model events are not fired."""
        now = int(time.time())
        use_timestamps = cls.timestamps
        prepared = []
        for attrs in rows:
            instance = cls()
            instance.fill(attrs)
            if use_timestamps:
                instance._attributes.setdefault("created_at", now)
                instance._attributes["updated_at"] = now
            prepared.append(dict(instance._attributes))
        cls.query().insert_many(prepared)

    def increment(self, column: str, amount: int = 1) -> bool:
        self.query().where(self.primary_key, self._attributes[self.primary_key]).increment(column, amount)
        self._attributes[column] = (self._attributes.get(column) or 0) + amount
        return True

    def decrement(self, column: str, amount: int = 1) -> bool:
        self.query().where(self.primary_key, self._attributes[self.primary_key]).decrement(column, amount)
        self._attributes[column] = (self._attributes.get(column) or 0) - amount
        return True

    # ------------------------------------------------------------------
    # Async persistence — run sync ORM calls in the thread-pool executor so
    # async controller actions do not block the ASGI event loop.
    # ------------------------------------------------------------------

    async def async_save(self) -> bool:
        return await _run_sync(self.save)

    async def async_delete(self) -> bool:
        return await _run_sync(self.delete)

    async def async_restore(self) -> bool:
        return await _run_sync(self.restore)

    @classmethod
    async def async_find(cls, id: Any) -> Model | None:
        return await _run_sync(cls.find, id)

    @classmethod
    async def async_find_or_fail(cls, id: Any) -> Model:
        return await _run_sync(cls.find_or_fail, id)

    @classmethod
    async def async_create(cls, data: dict | None = None, **kwargs: Any) -> Model:
        return await _run_sync(cls.create, data, **kwargs)

    @classmethod
    async def async_first_or_create(cls, search: dict, attributes: dict | None = None) -> tuple[Model, bool]:
        return await _run_sync(cls.first_or_create, search, attributes)

    @classmethod
    async def async_update_or_create(cls, search: dict, values: dict) -> Model:
        return await _run_sync(cls.update_or_create, search, values)

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    def has_one(self, related: type, foreign_key: str | None = None, local_key: str | None = None) -> Any:
        from hunt.database.relations.has_one import HasOne

        fk = foreign_key or f"{Str.snake(type(self).__name__)}_id"
        lk = local_key or self.primary_key
        rel = HasOne(related, self, fk, lk)
        if _EAGER_MODE.get():
            return rel
        return rel.get_result()

    def has_many(self, related: type, foreign_key: str | None = None, local_key: str | None = None) -> Any:
        from hunt.database.relations.has_many import HasMany

        fk = foreign_key or f"{Str.snake(type(self).__name__)}_id"
        lk = local_key or self.primary_key
        rel = HasMany(related, self, fk, lk)
        if _EAGER_MODE.get():
            return rel
        return rel.get_results()

    def belongs_to(self, related: type, foreign_key: str | None = None, owner_key: str | None = None) -> Any:
        from hunt.database.relations.belongs_to import BelongsTo

        fk = foreign_key or f"{Str.snake(related.__name__)}_id"
        ok = owner_key or "id"
        rel = BelongsTo(related, self, fk, ok)
        if _EAGER_MODE.get():
            return rel
        return rel.get_result()

    def belongs_to_many(
        self,
        related: type,
        pivot_table: str | None = None,
        foreign_key: str | None = None,
        related_key: str | None = None,
    ) -> Any:
        from hunt.database.relations.belongs_to_many import BelongsToMany

        pivot = pivot_table or "_".join(
            sorted(
                [
                    Str.snake(type(self).__name__),
                    Str.snake(related.__name__),
                ]
            )
        )
        fk = foreign_key or f"{Str.snake(type(self).__name__)}_id"
        rk = related_key or f"{Str.snake(related.__name__)}_id"
        rel = BelongsToMany(related, self, pivot, fk, rk)
        if _EAGER_MODE.get():
            return rel
        return rel.get_results()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        data = {k: v for k, v in self._attributes.items() if k not in self.hidden}
        # Appended accessors
        for attr in self.appends:
            accessor = f"get_{attr}_attribute"
            if accessor in type(self).__dict__:
                data[attr] = type(self).__dict__[accessor](self)
        # Loaded relations
        for rel_name, rel_value in self._relations.items():
            if isinstance(rel_value, Model):
                data[rel_name] = rel_value.to_dict()
            elif isinstance(rel_value, list):
                data[rel_name] = [r.to_dict() if isinstance(r, Model) else r for r in rel_value]
            else:
                data[rel_name] = rel_value
        return data

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), default=str)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    @classmethod
    def observe(cls, observer: Any) -> None:
        for event in ("creating", "created", "updating", "updated", "deleting", "deleted"):
            if hasattr(observer, event):
                cls._on(event, getattr(observer, event))

    @classmethod
    def _on(cls, event: str, callback: Any) -> None:
        key = f"{cls.__name__}.{event}"
        cls._event_listeners.setdefault(key, []).append(callback)

    def _fire_event(self, event: str) -> None:
        key = f"{type(self).__name__}.{event}"
        for cb in type(self)._event_listeners.get(key, []):
            cb(self)

    # ------------------------------------------------------------------
    # Casting
    # ------------------------------------------------------------------

    def _cast(self, key: str, value: Any) -> Any:
        cast_type = self.casts.get(key)
        if cast_type is None or value is None:
            return value
        if cast_type in ("int", "integer"):
            return int(value)
        if cast_type == "float":
            return float(value)
        if cast_type in ("bool", "boolean"):
            return bool(value)
        if cast_type in ("str", "string"):
            return str(value)
        if cast_type in ("json", "array"):
            import json

            return json.loads(value) if isinstance(value, str) else value
        return value
